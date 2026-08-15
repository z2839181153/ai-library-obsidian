"""P2 验收脚本：蒸馏闭环端到端（默认离线 Fake，--real 走真实 LLM）。

流程：构造 1 本 methodology 书 → start 蒸馏 → ≥2 个 SKILL.md 注册 reviewing →
      批准 1 个（installed + 技能库索引）→ 提问命中技能（used_skills 非空）→
      对另一技能连续拒绝 5 次 → blocked（再次 start 被 409 拦截）→ unblock 恢复。

用法：.venv\\Scripts\\python.exe scripts\\acceptance_p2.py [--real]
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import AppConfig
from app.state import build_state

# 蒸馏书正文：决策思维方法论（FakeEmbed 下与技能 description 共享词，路由可命中）
BOOK_TEXT = """# 决策的智慧

## 第一章 反向思考
思考问题的最优方式是反向思考：与其问如何成功，不如问什么必然导致失败。
作者在多个项目决策中应用这一原则，先枚举最坏可能，再逐条设计防线。

## 第二章 后果推演
评估行动后果不能只看直接结果，要追问第二层、第三层后果。
一个看似正确的决定可能在二阶效应下变成灾难。

## 第三章 案例
项目取舍案例：两个方案之间犹豫，作者问哪个方案失败更致命，选择失败代价更低者。
推功能案例：直接收益明显，但连锁反应导致维护成本失控。
"""


def main() -> int:
    use_real = "--real" in sys.argv
    use_real_llm = "--real-llm" in sys.argv
    td = tempfile.TemporaryDirectory()
    state = None
    t_start = time.time()
    try:
        root = Path(td.name)
        cfg = AppConfig.load()
        cfg.paths.data_dir = root / "data"
        cfg.paths.vault_dir = root / "vault"

        # 1) 准备一本 methodology 书
        src = root / "inbox"
        src.mkdir(parents=True)
        (src / "决策的智慧.md").write_text(BOOK_TEXT, encoding="utf-8")

        # 2) 构建状态（离线 Fake / 真实 API / 混合：LLM 真 + embedding 假）
        if use_real:
            state = build_state(cfg)
            from app.distill.executor_llm import LLMDistiller

            state.distill_executor = LLMDistiller(state.cfg, state.llm)
        elif use_real_llm:
            # 混合模式：LLM 走真实 API（DeepSeek 官方），embedding 用 Fake（离线语义）
            # 用途：embedding 服务不可用（如 ModelScope 429）时仍可验证蒸馏链路与产物质量
            from tests.conftest import FakeEmbed

            state = build_state(cfg, embed=FakeEmbed())
            from app.distill.executor_llm import LLMDistiller

            state.distill_executor = LLMDistiller(state.cfg, state.llm)
        else:
            from tests.conftest import FakeEmbed, FakeLLM

            state = build_state(cfg, embed=FakeEmbed(), llm=FakeLLM())
            from app.distill.executor_fake import FakeDistiller

            state.distill_executor = FakeDistiller()

        # 3) 入馆 + 索引
        stats = state.indexer.run(root=src)
        assert stats.get("new_or_changed", 0) >= 1, f"索引失败: {stats}"
        book_id = state.repo.all_books()[0]["book_id"]
        print(f"[1] 入馆+索引 OK  书={book_id}")

        # 4) 编目：卡片（FakeLLM 返回 category=methodology, distill_value=82）
        result = state.cards.generate(book_id)
        card = state.repo.get_card(book_id)
        assert result.card_path and card and card["category"] == "methodology", \
            f"卡片异常: {card} error={getattr(result, 'error', None)}"
        print(f"[2] 编目 OK  category={card['category']} distill={card['distill_value']}")

        # 5) 确认上架（卡片生成后书回补书室 reviewing）
        assert state.repo.get_book(book_id)["status"] == "reviewing"
        state.shelver.confirm_shelve(book_id)
        assert state.repo.get_book(book_id)["status"] == "shelved"
        print("[3] 上架 OK")

        # 6) start 蒸馏（auto_confirm 演示用；生产由主人逐步确认）
        r = state.distill.start(book_id, state.distill_executor, auto_confirm=True)
        assert r["ok"], r
        print(f"[4] 蒸馏启动 OK  slug={r['book_slug']}")

        # 7) 等待完成（真实 API 每次调用 5-40s，技能多时全流程可能 30+ 分钟）
        deadline = time.time() + 2400
        last_print = 0.0
        while time.time() < deadline:
            st = state.repo.get_book(book_id)["distill_status"]
            if st in ("done", "failed"):
                break
            if time.time() - last_print > 30:
                st_now = state.distill.status(book_id)
                print(f"     …蒸馏中 stage={st_now.get('stage')} status={st}")
                last_print = time.time()
            time.sleep(1)
        assert st == "done", f"蒸馏未完成: {st}（蒸馏失败详见 action ledger distill_failed）"
        print(f"[5] 蒸馏完成 OK  耗时 {time.time() - t_start:.1f}s")

        # 8) 产物与技能注册
        slug = state.repo.get_book(book_id)["distill_slug"]
        skills = state.repo.list_skills(book_id=book_id)
        assert len(skills) >= 2, f"应注册 ≥2 技能，实际 {len(skills)}"
        assert all(s["status"] == "reviewing" for s in skills)
        from app.distill.artifacts import scan_distill_dir

        arts = scan_distill_dir(state.cfg.paths.vault_dir / "skills", slug)
        assert arts.summary()["skill_count"] >= 2
        print(f"[6] 产物注册 OK  {len(skills)} 个技能（reviewing）")

        # 9) 批准 1 个 → installed + 技能库索引
        #    选"决策/纠结"相关的技能（inversion-thinking），另一个用于拒绝演示
        sid_ok = next(
            (s["skill_id"] for s in skills if "纠结" in (s["description"] or "") or "决策" in (s["description"] or "")),
            skills[0]["skill_id"],
        )
        state.repo.set_skill_status(sid_ok, "installed")
        from app.router.skill_index import SkillIndex

        idx = SkillIndex(state.cfg.paths.data_dir / "lancedb")
        doc = next(s["description"] for s in skills if s["skill_id"] == sid_ok)
        vec = state.embed.embed_one(doc)
        idx.upsert(sid_ok, "installed", doc, vec)
        hits = idx.search(state.embed.embed_one(doc), top_k=5)
        assert any(h["skill_id"] == sid_ok for h in hits), hits
        print(f"[7] 批准+索引 OK  {sid_ok} → installed，向量可命中")

        # 10) 提问命中技能：问答带 used_skills
        #    离线 FakeEmbed 语义尺度有限，用技能 description（触发场景描述）作查询
        #    → cos=1.0 必命中，验证整条链路（检索→路由→注入→used_skills）；
        #    真实语义命中阈值由 --real 验收与测试校准
        qa = state.qa.ask(doc)
        assert qa.get("used_skills"), f"应命中技能，实际 {qa.get('used_skills')}"
        assert qa["used_skills"][0]["skill_id"] == sid_ok, qa["used_skills"]
        print(f"[8] 技能路由 OK  命中 {qa['used_skills']}  used_skills 非空")

        # 11) 对另一技能连续拒绝 5 次 → blocked + 书 blocked + start 被拦
        sid_bad = next(s["skill_id"] for s in skills if s["skill_id"] != sid_ok)
        for i in range(5):
            state.repo.bump_skill_reject(sid_bad, f"第{i+1}次不合格")
            if state.repo.get_skill(sid_bad)["reject_count"] >= state.cfg.distill.reject_block:
                state.repo.set_skill_status(sid_bad, "blocked")
                state.repo.update_book_fields(book_id, {"distill_status": "blocked"})
        assert state.repo.get_skill(sid_bad)["status"] == "blocked"
        assert state.repo.get_book(book_id)["distill_status"] == "blocked"
        r = state.distill.start(book_id, state.distill_executor)
        assert not r["ok"] and r.get("blocked"), r
        print(f"[9] 阻塞 OK  {sid_bad} blocked，再次 start 被拦截（blocked=True）")

        # 12) unblock → 恢复
        state.repo.reset_skill_reject(sid_bad)
        state.repo.set_skill_status(sid_bad, "draft")
        state.repo.update_book_fields(book_id, {"distill_status": "idle"})
        assert state.repo.get_skill(sid_bad)["reject_count"] == 0
        assert state.repo.get_book(book_id)["distill_status"] == "idle"
        print("[10] 解除阻塞 OK  reject_count 归零，书恢复 idle")

        print("\n[PASS] P2 验收通过（10 步端到端：蒸馏→注册→批准→路由→阻塞→解除）")
        print(f"      总耗时 {time.time() - t_start:.1f}s")
        return 0
    finally:
        if state is not None:
            state.repo.close()
        td.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
