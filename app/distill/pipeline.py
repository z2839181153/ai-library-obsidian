"""蒸馏编排状态机（设计文档 §6.4 / 附录 B）。

- PIPELINE_STATE.md 为断点续跑唯一事实源（cangjie-skill 规范），
  每阶段完成更新 checklist；启动时读取并从最后阶段续跑。
- 阶段 0 / 1.5 需要主人确认 → 状态停在 awaiting，等 confirm-stage API。
- 审计：candidates/rejected/测试结果/SKILL.md 复制到 archive/distill-logs/<slug>/；
  action ledger 记录 distill_start / distill_stage / distill_block / skill_approve 等。

执行器协议（DistillExecutor）：pipeline 逐阶段驱动，产物落盘由执行器负责；
pipeline 负责状态流转、审计、账本、主人确认暂停。
"""
from __future__ import annotations

import shutil
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.config import AppConfig
from app.db.repo import Repo
from app.distill.artifacts import DistillArtifacts, build_skill_records, scan_distill_dir
from app.llm.chat import ChatClient

# 阶段常量（状态机）
STAGES = [
    "stage0_reading",
    "stage1_extracting",
    "stage1_5_verifying",
    "stage2_building",
    "stage3_linking",
    "stage4_testing",
    "stage5_delivering",
]
AWAIT_MAP = {
    "stage0_reading": "await_confirm_stage0",
    "stage1_5_verifying": "await_confirm_stage1_5",
}

# 状态机 stage 常量 → executor 方法名（executor 用阶段号命名）
STAGE_METHOD = {
    "stage0_reading": "stage0",
    "stage1_extracting": "stage1",
    "stage1_5_verifying": "stage1_5",
    "stage2_building": "stage2",
    "stage3_linking": "stage3",
    "stage4_testing": "stage4",
    "stage5_delivering": "stage5",
}

# books.distill_status 取值
D_IDLE = "idle"
D_RUNNING = "running"
D_AWAITING = "awaiting"
D_DONE = "done"
D_FAILED = "failed"
D_BLOCKED = "blocked"
D_CANCELLED = "cancelled"


@dataclass
class StageContext:
    """传递给执行器的上下文：输入（书）+ 输出（产物根）。"""

    cfg: AppConfig
    repo: Repo
    llm: ChatClient
    book_id: str
    book_slug: str
    title: str
    author: str
    source: str                     # 来源 URI / 描述
    text_path: Path | None          # 清洗后全文（vault_path 或 chunks 重建）
    out_root: Path                  # vault/skills/<book-slug>/
    auto_confirm: bool = False      # 测试/演示：阶段 0/1.5 免主人确认直接继续


class DistillExecutor(Protocol):
    """执行器协议：每阶段一个方法，负责把产物落盘到 ctx.out_root。"""

    def stage0(self, ctx: StageContext) -> None: ...
    def stage1(self, ctx: StageContext) -> None: ...
    def stage1_5(self, ctx: StageContext) -> None: ...
    def stage2(self, ctx: StageContext) -> None: ...
    def stage3(self, ctx: StageContext) -> None: ...
    def stage4(self, ctx: StageContext) -> None: ...
    def stage5(self, ctx: StageContext) -> None: ...


class DistillPipeline:
    def __init__(self, cfg: AppConfig, repo: Repo, llm: ChatClient):
        self.cfg = cfg
        self.repo = repo
        self.llm = llm
        self._lock = threading.RLock()  # 同一本书蒸馏任务串行
        self._running: set[str] = set()  # 正在后台跑的 book_id
        self._executors: dict[str, DistillExecutor] = {}  # 每本书的执行器（阶段确认续跑用）

    # ---------- 对外 API ----------

    def start(self, book_id: str, executor: DistillExecutor,
              force: bool = False, auto_confirm: bool = False) -> dict:
        """触发一本书的蒸馏。校验触发条件 → 建产物根 → 后台线程跑。"""
        with self._lock:
            if book_id in self._running:
                return {"ok": False, "error": "该书蒸馏任务正在运行中"}
            book = self.repo.get_book(book_id)
            if not book:
                return {"ok": False, "error": "书不存在"}
            card = self.repo.get_card(book_id) or {}
            if book.get("status") != "shelved":
                return {"ok": False, "error": "仅已上架的书可以蒸馏"}
            if not force:
                if card.get("category") != "methodology":
                    return {"ok": False, "error": f"卡片分类为 {card.get('category') or '未知'}，非方法论类书"}
                if (card.get("distill_value") or 0) < 60:
                    return {"ok": False, "error": f"蒸馏价值分 {card.get('distill_value')} < 60，不建议蒸馏"}
                if self._book_blocked(book_id):
                    return {"ok": False, "error": "该书存在被阻塞的技能（拒绝≥5次），需主人解除后才可重蒸", "blocked": True}

            slug = self._make_slug(book)
            ctx = self._build_context(book, slug)
            ctx.auto_confirm = auto_confirm
            self._executors[book_id] = executor
            ctx.out_root.mkdir(parents=True, exist_ok=True)
            self.repo.update_book_fields(book_id, {"distill_slug": slug, "distill_status": D_RUNNING})
            self.repo.insert_action({
                "agent": "distiller", "action_type": "distill_start",
                "target_type": "book", "target_id": book_id,
                "params": {"book_slug": slug, "force": force},
                "reason": f"开始蒸馏《{book.get('title')}》",
            })
            self._save_state(ctx.out_root, {"done": [], "confirmed": [], "awaiting": [], "stage": None})
            self._running.add(book_id)
            t = threading.Thread(target=self._run_safe, args=(book_id, ctx, executor), daemon=True)
            t.start()
            return {"ok": True, "book_id": book_id, "book_slug": slug}

    def status(self, book_id: str) -> dict:
        book = self.repo.get_book(book_id)
        if not book:
            return {"ok": False, "error": "书不存在"}
        slug = book.get("distill_slug")
        if not slug:
            return {"ok": True, "book_id": book_id, "distill_status": D_IDLE,
                    "stage": None, "artifacts": None}
        artifacts = scan_distill_dir(self.cfg.paths.vault_dir / "skills", slug)
        awaiting_stage = None
        if book.get("distill_status") == D_AWAITING:
            state = self._load_state(self.cfg.paths.vault_dir / "skills" / slug)
            awaiting_stage = state.get("current_wait")
        return {
            "ok": True,
            "book_id": book_id,
            "book_slug": slug,
            "distill_status": book.get("distill_status"),
            "stage": artifacts._stage_hint() if artifacts.exists else "not_started",
            "awaiting": book.get("distill_status") == D_AWAITING,
            "awaiting_stage": awaiting_stage,
            "artifacts": artifacts.summary() if artifacts.exists else None,
            "skills": [s.get("skill_id") for s in self.repo.list_skills(book_id=book_id)],
        }

    def confirm_stage(self, book_id: str, decision: str) -> dict:
        """阶段 0 / 1.5 主人确认：continue（继续）/ skip（跳过确认继续）/ cancel（取消任务）。"""
        if decision not in ("continue", "skip", "cancel"):
            return {"ok": False, "error": "decision 必须是 continue|skip|cancel"}
        book = self.repo.get_book(book_id)
        if not book:
            return {"ok": False, "error": "书不存在"}
        if book.get("distill_status") != D_AWAITING:
            return {"ok": False, "error": f"当前不在等待确认状态（{book.get('distill_status')}）"}
        slug = book.get("distill_slug")
        state = self._load_state(Path(self.cfg.paths.vault_dir / "skills") / slug)
        current_wait = state.get("current_wait")
        if not current_wait:
            return {"ok": False, "error": "没有待确认的阶段"}
        if decision == "cancel":
            self.repo.update_book_fields(book_id, {"distill_status": D_CANCELLED})
            self.repo.insert_action({
                "agent": "owner", "action_type": "distill_cancel",
                "target_type": "book", "target_id": book_id,
                "params": {"book_slug": slug}, "reason": "主人取消蒸馏",
            })
            return {"ok": True, "decision": "cancel"}
        # continue / skip → 继续主循环（标记等待项已确认）
        waiting = state.get("awaiting", [])
        state["awaiting"] = [w for w in waiting if w != state.get("current_wait")]
        # current_wait(await_confirm_*) 反查对应 stage，标记 confirmed
        confirmed_stage = next(
            (k for k, v in AWAIT_MAP.items() if v == state.get("current_wait")), None
        )
        if confirmed_stage:
            state["confirmed"] = list(set(state.get("confirmed", [])) | {confirmed_stage})
        self._save_state(Path(self.cfg.paths.vault_dir / "skills") / slug, state)
        with self._lock:
            executor = self._executors.get(book_id)
            if executor is None:
                return {"ok": False, "error": "续跑缺少执行器（请重新 start）"}
            # 旧线程已在暂停分支 return（finally 里 discard 可能尚未执行），
            # 这里直接启动续跑线程，不再检查 _running。
            self._running.add(book_id)
            t = threading.Thread(target=self._run_safe,
                                 args=(book_id, self._build_context(book, slug), executor),
                                 daemon=True)
            t.start()
        return {"ok": True, "decision": decision}

    # ---------- 内部 ----------

    def _run_safe(self, book_id: str, ctx: StageContext, executor: DistillExecutor | None) -> None:
        try:
            self._run(book_id, ctx, executor)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self.repo.update_book_fields(book_id, {"distill_status": D_FAILED})
            self.repo.insert_action({
                "agent": "distiller", "action_type": "distill_failed",
                "target_type": "book", "target_id": book_id,
                "params": {"error": traceback.format_exc()[-2000:]}, "reason": "蒸馏异常",
            })
        finally:
            with self._lock:
                self._running.discard(book_id)

    def _run(self, book_id: str, ctx: StageContext, executor: DistillExecutor | None) -> None:
        # 断点续跑：读 PIPELINE_STATE.md，从最后一个未完成阶段继续
        state = self._load_state(ctx.out_root)
        done = set(state.get("done", []))
        remaining = [s for s in STAGES if s not in done]
        if not remaining:
            self.repo.update_book_fields(book_id, {"distill_status": D_DONE})
            return
        if executor is None:
            # 续跑时若无 executor 传入，需要外部提供——API 层负责传
            raise RuntimeError("续跑缺少 executor")

        for stage in remaining:
            # 阶段 0 / 1.5 需要主人确认（第一次到达时暂停；auto_confirm 免确认）
            if (stage in AWAIT_MAP and stage not in done
                    and stage not in state.get("confirmed", []) and not ctx.auto_confirm):
                await_status = AWAIT_MAP[stage]
                self.repo.update_book_fields(book_id, {"distill_status": D_AWAITING})
                state["current_wait"] = await_status
                state["awaiting"] = [await_status]
                self._save_state(ctx.out_root, state)
                self.repo.insert_action({
                    "agent": "distiller", "action_type": "distill_stage",
                    "target_type": "book", "target_id": book_id,
                    "params": {"stage": await_status}, "reason": f"等待主人确认（{await_status}）",
                })
                return  # 等待 confirm_stage 唤醒
            # 只有需要主人确认的阶段才记入 confirmed
            if stage in AWAIT_MAP:
                state["confirmed"] = list(set(state.get("confirmed", [])) | {stage})
            self._save_state(ctx.out_root, state)

            # 执行阶段
            method = STAGE_METHOD.get(stage)
            if method is None:
                raise RuntimeError(f"未知阶段: {stage}")
            getattr(executor, method)(ctx)
            done.add(stage)
            state["done"] = list(done)
            state["stage"] = stage
            self._save_state(ctx.out_root, state)
            self.repo.update_book_fields(book_id, {"distill_status": D_RUNNING})
            self.repo.insert_action({
                "agent": "distiller", "action_type": "distill_stage",
                "target_type": "book", "target_id": book_id,
                "params": {"stage": stage}, "reason": f"完成 {stage}",
            })
            self._audit_copy(ctx)

        # 全部完成 → 扫描产物注册技能
        self._register_skills(ctx)
        self.repo.update_book_fields(book_id, {"distill_status": D_DONE})
        self.repo.insert_action({
            "agent": "distiller", "action_type": "distill_done",
            "target_type": "book", "target_id": book_id,
            "params": {"book_slug": ctx.book_slug}, "reason": "蒸馏完成，技能待审阅",
        })

    def _register_skills(self, ctx: StageContext) -> None:
        """产物注册进 skills 表（status=reviewing）+ 回填卡片。"""
        arts = scan_distill_dir(ctx.out_root.parent, ctx.book_slug)
        records = build_skill_records(arts, ctx.book_id)
        registered = []
        for rec in records:
            sid = self.repo.upsert_skill(rec)
            registered.append({"skill_id": sid, "name": rec["name"], "status": rec["status"]})
        if registered:
            self.repo.set_card_skills(ctx.book_id, registered)
        self.repo.insert_action({
            "agent": "distiller", "action_type": "distill_register",
            "target_type": "book", "target_id": ctx.book_id,
            "params": {"skills": registered}, "reason": f"注册 {len(registered)} 个技能待审阅",
        })

    def _audit_copy(self, ctx: StageContext) -> None:
        """产物复制到 archive/distill-logs/<slug>/（审计）。"""
        src = ctx.out_root
        dst = self.cfg.paths.vault_dir / "archive" / "distill-logs" / ctx.book_slug
        if not src.exists():
            return
        dst.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(src, dst, dirs_exist_ok=True)
        except OSError:
            pass

    def _book_blocked(self, book_id: str) -> bool:
        skills = self.repo.list_skills(book_id=book_id)
        return any(s.get("status") == "blocked" for s in skills)

    def _make_slug(self, book: dict) -> str:
        base = (book.get("slug") or book.get("title") or book["book_id"]).strip()
        import re
        slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", base).strip("-").lower()
        slug = slug[:40] or "book"
        slug = f"{slug}-{book['book_id'][-6:]}"
        return slug

    def _build_context(self, book: dict, slug: str) -> StageContext:
        out_root = self.cfg.paths.vault_dir / "skills" / slug
        text_path = None
        if book.get("vault_path"):
            p = Path(book["vault_path"])
            if p.is_file():
                text_path = p
        return StageContext(
            cfg=self.cfg, repo=self.repo, llm=self.llm,
            book_id=book["book_id"], book_slug=slug,
            title=book.get("title") or book["book_id"],
            author=book.get("author") or "",
            source=book.get("source_uri") or "",
            text_path=text_path, out_root=out_root,
        )

    # ---------- PIPELINE_STATE.md ----------

    def _state_path(self, root: Path) -> Path:
        return root / "PIPELINE_STATE.md"

    def _load_state(self, root: Path) -> dict:
        p = self._state_path(root)
        if not p.exists():
            return {"done": [], "confirmed": [], "awaiting": [], "stage": None, "current_wait": None}
        text = p.read_text(encoding="utf-8", errors="replace")
        state = {"done": [], "confirmed": [], "awaiting": [], "stage": None, "current_wait": None}
        done_count = 0
        for line in text.splitlines():
            if re_match(r"^- \[x\]\s+阶段\s+\S+", line):
                done_count += 1
            m = re_match(r"^- \[x\]\s+确认\s+(\S+)", line)
            if m:
                state["confirmed"].append(m.group(1))
            m2 = re_match(r"^- \[ \]\s+确认\s+(\S+)", line)
            if m2:
                state["awaiting"].append(m2.group(1))
                state["current_wait"] = m2.group(1)
        state["done"] = STAGES[:done_count]
        return state

    def _save_state(self, root: Path, state: dict) -> None:
        root.mkdir(parents=True, exist_ok=True)
        done_set = set(state.get("done", []))
        lines = [
            "# 蒸馏流水线状态（PIPELINE_STATE）",
            f"- 更新: {time.strftime('%Y-%m-%dT%H:%M:%S+08:00')}",
            f"- 当前阶段: {state.get('stage') or 'not_started'}",
            "",
            "## 阶段进度",
        ]
        stage_names = {
            "stage0_reading": "阶段 0 整书理解 → BOOK_OVERVIEW.md",
            "stage1_extracting": "阶段 1 并行提取 → candidates/",
            "stage1_5_verifying": "阶段 1.5 三重验证 → verified.md",
            "stage2_building": "阶段 2 RIA++ 构造 → SKILL.md",
            "stage3_linking": "阶段 3 Zettelkasten 链接 → INDEX.md",
            "stage4_testing": "阶段 4 压力测试 → test-prompts.json",
            "stage5_delivering": "阶段 5 交付 → DIGEST.md",
        }
        for st in STAGES:
            mark = "x" if st in done_set else " "
            lines.append(f"- [{mark}] {stage_names[st]}")
        lines.append("")
        lines.append("## 主人确认")
        for st in state.get("confirmed", []):
            lines.append(f"- [x] 确认 {st}")
        for w in state.get("awaiting", []):
            lines.append(f"- [ ] 确认 {w}")
        self._state_path(root).write_text("\n".join(lines) + "\n", encoding="utf-8")


def re_match(pattern: str, text: str):
    import re
    return re.match(pattern, text)
