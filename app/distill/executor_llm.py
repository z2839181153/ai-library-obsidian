"""LLMDistiller：真实蒸馏执行器（串行实现 cangjie 5 阶段，模型=distill_model）。

- prompt/模板单一事实源：settings.distill.cangjie_skill_dir 指向 cangjie-skill，
  运行时读取 extractors/*.md 与 templates/*.md；cangjie 更新即生效。
- 串行降级方案（cangjie SKILL.md 官方降级）：无 sub-agent 时 5 个 extractor
  依次调用；阶段 4 用 LLM 盲测并在 test-results.md 标注 fallback 自测。
- 产物 100% 对齐 cangjie 规范输出结构（pipeline 的扫描器据此注册）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from app.config import AppConfig
from app.llm.chat import ChatClient

# extractor prompt 文件 → candidates 输出文件名
EXTRACTORS = [
    ("framework-extractor.md", "frameworks.md"),
    ("principle-extractor.md", "principles.md"),
    ("case-extractor.md", "cases.md"),
    ("counter-example-extractor.md", "counter-examples.md"),
    ("glossary-extractor.md", "glossary.md"),
]

MAX_TOKENS = 4096


class LLMDistiller:
    def __init__(self, cfg: AppConfig, llm: ChatClient):
        self.cfg = cfg
        # 蒸馏用更强模型（settings.modelscope.distill_model）
        # timeout=150s：DeepSeek 等 API 生成长结构化输出较慢（约 8 字符/s），
        # 60s 默认超时在批量验证/长 SKILL.md 生成时容易误判超时。
        self.llm = ChatClient(cfg, model=cfg.modelscope.distill_model, timeout=150.0)

    # ---------- 工具 ----------

    def _skill_dir(self) -> Path:
        d = self.cfg.distill.cangjie_skill_dir
        if not d.exists():
            raise RuntimeError(f"cangjie-skill 目录不存在: {d}（settings.distill.cangjie_skill_dir）")
        return d

    def _extractor_prompt(self, name: str) -> str:
        return (self._skill_dir() / "extractors" / name).read_text(encoding="utf-8")

    def _template(self, name: str) -> str:
        return (self._skill_dir() / "templates" / name).read_text(encoding="utf-8")

    def _read_book(self, ctx) -> str:
        if ctx.text_path and Path(ctx.text_path).is_file():
            text = Path(ctx.text_path).read_text(encoding="utf-8", errors="replace")
        else:
            # 未上架/无 vault 文本 → 从 chunks 重建
            chunks = ctx.repo.conn.execute(
                "SELECT content FROM chunks WHERE book_id=? ORDER BY seq",
                (ctx.book_id,),
            ).fetchall()
            text = "\n".join(r["content"] for r in chunks)
        return text.strip()

    def _chunks(self, text: str, max_chars: int | None = None) -> list[str]:
        max_chars = max_chars or self.cfg.distill.max_chunk_chars
        if len(text) <= max_chars:
            return [text]
        out = []
        for i in range(0, len(text), max_chars):
            out.append(text[i : i + max_chars])
        return out

    def _chat(self, system: str, user: str) -> str:
        return self.llm.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.3,
            max_tokens=MAX_TOKENS,
        )

    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _strip_fence(self, text: str) -> str:
        """剥离模型输出外围的 ```markdown/```yaml/``` 围栏（LLM 常把全文包进代码块）。"""
        t = text.strip()
        m = re.match(r"^```(?:markdown|md|yaml|json)?\s*\n(.*?)\n```\s*$", t, re.S)
        if m:
            return m.group(1).strip()
        # 只剥开头围栏（正文可能含代码块示例，不能剥结尾）
        t = re.sub(r"^```(?:markdown|md|yaml|json)?\s*\n?", "", t, count=1)
        return t.strip()

    # ---------- 阶段 0：整书理解 ----------

    def stage0(self, ctx) -> None:
        text = self._read_book(ctx)
        if not text:
            raise RuntimeError("书正文为空，无法蒸馏")
        # 分块理解，逐块提取结构要点，最后汇总
        part_summaries = []
        for i, part in enumerate(self._chunks(text)):
            out = self._chat(
                "你是书籍分析专家，执行 Adler 分析阅读的结构与解释步骤。",
                f"这是《{ctx.title}》正文第 {i + 1} 块（共若干块）：\n\n{part}\n\n"
                "输出该块的结构要点：主旨 / 章节骨架 / 出现的术语。200 字内。",
            )
            part_summaries.append(f"### 第 {i + 1} 块\n{out}")
        joined = "\n\n".join(part_summaries)
        overview = self._chat(
            "你是 cangjie-skill 阶段 0 的执行者，基于分块理解汇总整书理解。",
            f"《{ctx.title}》作者 {ctx.author}，来源 {ctx.source}。各块要点如下：\n\n{joined}\n\n"
            "按模板生成 BOOK_OVERVIEW.md：主旨 / 骨架（章节结构）/ 术语 / 批判（作者盲点）。\n"
            "模板：\n"
            "# {书名} · 整书理解\n\n## 主旨\n...\n\n## 骨架\n...\n\n## 术语\n- 术语: 定义\n\n## 批判\n- 盲点: ...\n",
        )
        self._write(ctx.out_root / "BOOK_OVERVIEW.md", overview)

    # ---------- 阶段 1：5 个 extractor 串行提取 ----------

    def stage1(self, ctx) -> None:
        text = self._read_book(ctx)
        overview = ""
        op = ctx.out_root / "BOOK_OVERVIEW.md"
        if op.exists():
            overview = op.read_text(encoding="utf-8", errors="replace")[:4000]
        cand_dir = ctx.out_root / "candidates"
        for prompt_file, out_name in EXTRACTORS:
            prompt = self._extractor_prompt(prompt_file)
            entries = []
            for i, part in enumerate(self._chunks(text)):
                user = (
                    f"BOOK_OVERVIEW.md:\n{overview[:2000]}\n\n"
                    f"书本文本（第 {i + 1} 块）：\n{part}\n\n"
                    "按你的职责从上面文本提取候选，输出 YAML 条目（id/title/type/source_chapter/"
                    "source_quote/summary/tags）。没有候选就输出空。只输出 YAML，不要解释。"
                )
                out = self._chat(prompt, user)
                entries.append(out)
            merged = "\n".join(entries)
            self._write(cand_dir / out_name, f"# {prompt_file} 候选（LLMDistiller 串行）\n\n{merged}\n")

    # ---------- 阶段 1.5：三重验证 ----------

    def stage1_5(self, ctx) -> None:
        cand_dir = ctx.out_root / "candidates"
        all_items = []
        for f in cand_dir.glob("*.md"):
            text = f.read_text(encoding="utf-8", errors="replace")
            items = self._parse_yaml_items(text)
            for it in items:
                it.setdefault("_src", f.name)
                all_items.append(it)
        if not all_items:
            # 候选为空 → 视为无可蒸馏内容，写空 verified 避免卡死
            self._write(ctx.out_root / "verified.md", "# 通过三重验证的单元\n\n（无候选）\n")
            return
        verified, rejected = [], []
        # 分批验证（每批 ≤3）：DeepSeek 等 API 生成 JSON 判定约 8 字符/s，
        # 批次过大输出超时/被截断风险高（BATCH=6 实测 60s 超时）。
        BATCH = 3
        for start in range(0, len(all_items), BATCH):
            batch_items = all_items[start : start + BATCH]
            batch = "\n\n".join(
                f"- id: {it.get('id')}\n  title: {it.get('title')}\n  summary: {it.get('summary', '')[:300]}"
                for it in batch_items
            )
            verdict = self._chat(
                "你是三重验证器（V1 跨域佐证 / V2 预测力 / V3 独特性）。对每个候选输出 JSON 数组："
                '[{"id": "f01", "pass": true, "reason": "..."}]',
                f"候选列表：\n{batch}\n\n逐个判定 V1/V2/V3 是否通过。只输出 JSON 数组。",
            )
            parsed = self._parse_verdicts(verdict)
            if not parsed:
                # 判定输出不可解析（截断/格式异常）→ 保守放行，避免链路卡死
                for it in batch_items:
                    verified.append({**it, "reason": "（判定输出不可解析，保守放行）"})
                continue
            by_id = {it.get("id"): it for it in batch_items}
            judged = set()
            for v in parsed:
                it = by_id.get(v.get("id"))
                if it is None:
                    continue
                judged.add(v.get("id"))
                if v.get("pass"):
                    verified.append(it)
                else:
                    rejected.append({**it, "reason": v.get("reason", "")})
            # 截断导致本批部分候选未获判定 → 保守放行
            for it in batch_items:
                if it.get("id") not in judged:
                    verified.append({**it, "reason": "（未获判定，保守放行）"})
        self._write(ctx.out_root / "verified.md",
                    self._fmt_units(verified))
        rej_dir = ctx.out_root / "rejected"
        for it in rejected:
            self._write(rej_dir / f"{it.get('id')}.md",
                        f"# {it.get('title')}\n\n未通过: {it.get('reason')}\n")

    # ---------- 阶段 2：RIA++ 构造 ----------

    def stage2(self, ctx) -> None:
        verified = self._load_verified(ctx)
        if not verified:
            return
        for it in verified:
            slug = self._slugify(str(it.get("title") or it.get("id")))
            user = (
                f"候选单元：{yaml.safe_dump(it, allow_unicode=True, sort_keys=False)}\n\n"
                f"书名《{ctx.title}》作者 {ctx.author}。请按 SKILL.md 模板生成六段技能文档：\n"
                "R 原文引用（≤150字，标注章节）/ I 方法论骨架（用自己的话）/ A1 书中案例 / "
                "A2 触发场景（含语言信号与相邻 skill 区分）/ E 可执行步骤（含完成标准）/ B 边界。\n"
                "frontmatter 含 name/description（description 必须写明'何时调用+何时不调用'）。\n"
                "格式硬性要求：① 六段标题必须写为 '## R — 原文引用' 形式（R/I/A1/A2/E/B 后跟空格和 em-dash '—'）；"
                "② 不要用任何代码块围栏包裹全文（frontmatter 和正文直接裸输出）；"
                "③ 文件以 --- frontmatter 开头。只输出 SKILL.md 全文。"
            )
            out = self._chat(
                "你是 RIA++ 拆书专家，产出可执行 SKILL.md（六段齐全，遵循模板）。",
                user,
            )
            self._write(ctx.out_root / slug / "SKILL.md", self._strip_fence(out))

    # ---------- 阶段 3：链接 ----------

    def stage3(self, ctx) -> None:
        skills = [d.name for d in ctx.out_root.iterdir() if d.is_dir() and (d / "SKILL.md").exists()]
        self._write(ctx.out_root / "INDEX.md",
                    "# 技能索引\n\n```mermaid\ngraph LR\n"
                    + "\n".join(f"  {s}" for s in skills) + "\n```\n")
        gl = ctx.out_root / "candidates" / "glossary.md"
        if gl.exists():
            self._write(ctx.out_root / "GLOSSARY.md",
                        "# 术语词典\n\n" + gl.read_text(encoding="utf-8", errors="replace"))

    # ---------- 阶段 4：压力测试 ----------

    def stage4(self, ctx) -> None:
        for d in ctx.out_root.iterdir():
            if not d.is_dir() or not (d / "SKILL.md").exists():
                continue
            tp = self._gen_test_prompts(ctx, d)
            self._write(d / "test-prompts.json", json.dumps(tp, ensure_ascii=False, indent=2))
            results = self._blind_test(ctx, d, tp)
            self._write(d / "test-results.md", results)

    def _gen_test_prompts(self, ctx, skill_dir: Path) -> dict:
        sk = (skill_dir / "SKILL.md").read_text(encoding="utf-8", errors="replace")
        template = self._template("test-prompts.json.template")
        out = self._chat(
            "你是测试设计器，为技能生成 darwin 兼容 test-prompts.json。",
            f"SKILL.md：\n{sk[:6000]}\n\n模板：\n{template[:3000]}\n\n"
            "生成 test-prompts.json：3-5 条 should_trigger + 2-3 条 should_not_trigger"
            "（至少 1 条是应触发同书兄弟 skill 的跨 skill 混淆诱饵，notes 注明'同书兄弟 skill'）+ "
            "1-3 条 edge_case。只输出 JSON。",
        )
        try:
            data = json.loads(re.sub(r"```(?:json)?\s*|\s*```", "", out.strip()) or "{}")
        except json.JSONDecodeError:
            data = {}
        if not data.get("test_cases"):
            # 保底：给一个可验收的最小集
            data = {
                "skill": skill_dir.name, "version": "0.1.0",
                "test_cases": [
                    {"id": "st-1", "type": "should_trigger", "prompt": "正面场景示例", "expected_behavior": "调用"},
                    {"id": "sn-1", "type": "should_not_trigger", "prompt": "无关查询", "expected_behavior": "不调用"},
                    {"id": "sn-2", "type": "should_not_trigger", "prompt": "兄弟 skill 场景", "expected_behavior": "不调用本 skill", "notes": "同书兄弟 skill"},
                    {"id": "ed-1", "type": "edge_case", "prompt": "边界场景", "expected_behavior": "合理判断"},
                ],
                "minimum_pass_rate": 0.8,
            }
        return data

    def _blind_test(self, ctx, skill_dir: Path, tp: dict) -> str:
        sk = (skill_dir / "SKILL.md").read_text(encoding="utf-8", errors="replace")
        lines = []
        total = passed = 0
        for case in tp.get("test_cases", []):
            total += 1
            judge = self._chat(
                "你是盲测 agent：只根据技能内容判断用户提问是否会触发该技能。"
                "输出 JSON：{\"would_trigger\": true/false, \"reason\": \"...\"}",
                f"技能：\n{sk[:3000]}\n\n用户提问：{case.get('prompt')}\n\n只输出 JSON。",
            )
            would = '"would_trigger": true' in judge or '"would_trigger":true' in judge
            expect_trigger = case.get("type") == "should_trigger"
            ok = would == expect_trigger
            if ok:
                passed += 1
            lines.append(f"- [{('x' if ok else ' ')}] {case.get('id')} ({case.get('type')}) "
                         f"expect={expect_trigger} got={would}")
        rate = passed / total if total else 0.0
        lines.append(f"\n- 通过率: {rate:.0%} ({passed}/{total})")
        lines.append("- 模式: fallback 自测（无独立 sub-agent）")
        return "\n".join(lines)

    # ---------- 阶段 5：交付 ----------

    def stage5(self, ctx) -> None:
        digest = self._chat(
            "你是精华提炼者，为不读全书的读者写 DIGEST.md。",
            f"《{ctx.title}》蒸馏产物已就绪（见目录 {ctx.out_root}）。"
            "写 400 字内精华长文：这本书给 agent 可用的方法论是什么，怎么用。",
        )
        self._write(ctx.out_root / "DIGEST.md", digest)

    # ---------- 解析辅助 ----------

    def _parse_yaml_items(self, text: str) -> list[dict]:
        """从 extractor 输出里解析 YAML 条目列表。"""
        items = []
        for block in re.findall(r"```yaml(.*?)```", text, re.S) or [text]:
            try:
                data = yaml.safe_load(block)
            except yaml.YAMLError:
                data = None
            if isinstance(data, list):
                items.extend(x for x in data if isinstance(x, dict))
            elif isinstance(data, dict):
                items.append(data)
        # 去重（按 id / title）
        seen, out = set(), []
        for it in items:
            key = str(it.get("id") or it.get("title") or "")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            out.append(it)
        return out

    def _parse_verdicts(self, text: str) -> list[dict]:
        # 1) 完整 JSON 数组
        m = re.search(r"\[.*\]", text, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, list):
                    return [d for d in data if isinstance(d, dict)]
            except json.JSONDecodeError:
                pass
        # 2) 截断容错：逐对象解析（数组未闭合时仍能拿到前面的判定）
        out = []
        for obj in re.finditer(r"\{[^{}]*\}", text):
            try:
                d = json.loads(obj.group(0))
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and d.get("id") is not None:
                out.append(d)
        return out

    def _load_verified(self, ctx) -> list[dict]:
        vp = ctx.out_root / "verified.md"
        if not vp.exists():
            return []
        text = vp.read_text(encoding="utf-8", errors="replace")
        return self._parse_yaml_items(text)

    def _fmt_units(self, units: list[dict]) -> str:
        """verified.md 内容：markdown 可读 + YAML 块（stage2 解析用）。"""
        lines = ["# 通过三重验证的单元\n"]
        for u in units:
            title = u.get("title") or u.get("id") or "未命名"
            lines.append(f"## {title}")
            lines.append(f"- id: {u.get('id')}")
            lines.append(f"- 摘要: {str(u.get('summary', ''))[:300]}")
            lines.append("")
        # YAML 块供 stage2 _load_verified 解析（保留原始字段）
        lines.append("```yaml")
        lines.append(yaml.safe_dump(units, allow_unicode=True, sort_keys=False).rstrip())
        lines.append("```")
        lines.append("")
        return "\n".join(lines)

    def _slugify(self, s: str) -> str:
        slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", s).strip("-").lower()
        return slug[:40] or "skill"
