"""蒸馏产物扫描与解析（设计文档附录 B / cangjie-skill 输出规范）。

扫描 `vault/skills/<book-slug>/`（cangjie 的 `books/<slug>/` 输出根），
识别规范产物并做质量校验，输出技能注册数据（供 skills 表 + 技能库索引）。

产物规范（cangjie-skill SKILL.md）：
- PIPELINE_STATE.md / BOOK_OVERVIEW.md / verified.md / rejected/
- candidates/<type>.md（5 类提取器原始候选）
- <skill-slug>/SKILL.md（六段 R/I/A1/A2/E/B + frontmatter）
- <skill-slug>/test-prompts.json（darwin 兼容，须含诱饵）
- <skill-slug>/test-results.md
- INDEX.md / GLOSSARY.md / DIGEST.md
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# SKILL.md 六段：R / I / A1 / A2 / E / B（cangjie 质量红线）
REQUIRED_SECTIONS = ["R", "I", "A1", "A2", "E", "B"]

# test-prompts 三类用例
TRIGGER_TYPES = {"should_trigger", "should_not_trigger", "edge_case"}


@dataclass
class TestPrompts:
    skill: str
    version: str
    test_cases: list[dict] = field(default_factory=list)
    minimum_pass_rate: float = 0.8
    raw: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict:
        return {
            t: sum(1 for c in self.test_cases if c.get("type") == t)
            for t in TRIGGER_TYPES
        }


@dataclass
class SkillArtifact:
    slug: str
    path: Path                      # SKILL.md 绝对路径
    name: str = ""                  # frontmatter name（缺省用 slug）
    description: str = ""           # frontmatter description（路由 trigger）
    source_book: str = ""
    body: str = ""                  # frontmatter 之后的正文
    missing_sections: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    test_prompts: TestPrompts | None = None
    test_results: str = ""          # test-results.md 全文（审阅用）
    test_results_path: Path | None = None


@dataclass
class DistillArtifacts:
    root: Path                       # vault/skills/<book-slug>/
    book_slug: str
    exists: bool = False
    pipeline_state: str = ""
    has_book_overview: bool = False
    has_verified: bool = False
    has_index: bool = False
    has_glossary: bool = False
    has_digest: bool = False
    candidates: list[str] = field(default_factory=list)   # 相对路径
    rejected: list[str] = field(default_factory=list)
    skills: list[SkillArtifact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)     # 产物级 warning

    def summary(self) -> dict:
        return {
            "book_slug": self.book_slug,
            "exists": self.exists,
            "stage_hint": self._stage_hint(),
            "skill_count": len(self.skills),
            "skills": [{
                "slug": s.slug,
                "name": s.name or s.slug,
                "description": s.description,
                "missing_sections": s.missing_sections,
                "warnings": s.warnings,
                "test_prompts": s.test_prompts.counts if s.test_prompts else None,
            } for s in self.skills],
            "warnings": self.warnings,
        }

    def _stage_hint(self) -> str:
        """按产物推断当前到达的阶段（cangjie 5 阶段）。"""
        if not self.exists:
            return "not_started"
        if self.has_digest:
            return "stage5_delivering"
        if self.skills:
            return "stage4_testing"
        if self.has_index:
            return "stage3_linking"
        if self.has_verified:
            return "stage2_building"
        if self.candidates:
            return "stage1_extracting"
        if self.has_book_overview:
            return "stage0_reading"
        return "unknown"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 --- 围栏 frontmatter，返回 (meta, body)。无 frontmatter 返回 ({}, text)。"""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    meta_text, body = m.group(1), m.group(2)
    try:
        meta = yaml.safe_load(meta_text) or {}
    except yaml.YAMLError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, body


def _find_sections(body: str) -> list[str]:
    """按 '## X —' 标题找六段。"""
    found = set()
    for m in re.finditer(r"^##\s+([A-Za-z0-9]+)\s*[—\-–]", body, re.M):
        found.add(m.group(1).upper())
    return [s for s in REQUIRED_SECTIONS if s not in found]


def parse_test_prompts(path: Path) -> TestPrompts:
    tp = TestPrompts(skill="", version="")
    raw_text = _read(path)
    if not raw_text.strip():
        tp.warnings.append("test-prompts.json 为空")
        return tp
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError:
        tp.warnings.append("test-prompts.json 不是合法 JSON")
        return tp
    tp.raw = raw
    tp.skill = str(raw.get("skill", ""))
    tp.version = str(raw.get("version", ""))
    try:
        tp.minimum_pass_rate = float(raw.get("minimum_pass_rate", 0.8))
    except (TypeError, ValueError):
        tp.minimum_pass_rate = 0.8
    cases = raw.get("test_cases", [])
    if not isinstance(cases, list):
        tp.warnings.append("test_cases 不是数组")
        return tp
    for c in cases:
        if not isinstance(c, dict) or "type" not in c or "prompt" not in c:
            tp.warnings.append("存在缺 type/prompt 的测试用例")
            continue
        tp.test_cases.append(c)

    counts = tp.counts
    if counts["should_trigger"] < 3:
        tp.warnings.append(f"should_trigger 仅 {counts['should_trigger']} 条（要求 ≥3）")
    if counts["should_not_trigger"] < 2:
        tp.warnings.append(f"诱饵 should_not_trigger 仅 {counts['should_not_trigger']} 条（要求 ≥2）")
    if counts["edge_case"] < 1:
        tp.warnings.append("缺 edge_case 用例")
    # 跨 skill 混淆诱饵（硬性要求：至少 1 条 should_not_trigger 指向同书兄弟 skill）
    has_sibling_bait = any(
        c.get("type") == "should_not_trigger"
        and ("sibling" in str(c.get("notes", "")).lower()
             or "兄弟" in str(c.get("notes", ""))
             or "同书" in str(c.get("notes", "")))
        for c in tp.test_cases
    )
    if not has_sibling_bait:
        tp.warnings.append("诱饵中缺少跨 skill 混淆场景（notes 应注明同书兄弟 skill）")
    return tp


def scan_distill_dir(skills_root: Path, book_slug: str) -> DistillArtifacts:
    """扫描 vault/skills/<book-slug>/ 的产物。"""
    root = Path(skills_root) / book_slug
    out = DistillArtifacts(root=root, book_slug=book_slug)
    if not root.is_dir():
        return out
    out.exists = True

    ps = root / "PIPELINE_STATE.md"
    if ps.is_file():
        out.pipeline_state = _read(ps)[:4000]

    out.has_book_overview = (root / "BOOK_OVERVIEW.md").is_file()
    out.has_verified = (root / "verified.md").is_file()
    out.has_index = (root / "INDEX.md").is_file()
    out.has_glossary = (root / "GLOSSARY.md").is_file()
    out.has_digest = (root / "DIGEST.md").is_file()

    cand_dir = root / "candidates"
    if cand_dir.is_dir():
        out.candidates = sorted(
            str(p.relative_to(root)).replace("\\", "/")
            for p in cand_dir.iterdir() if p.is_file()
        )
    rej_dir = root / "rejected"
    if rej_dir.is_dir():
        out.rejected = sorted(
            str(p.relative_to(root)).replace("\\", "/")
            for p in rej_dir.iterdir() if p.is_file()
        )

    # 扫描 <skill-slug>/SKILL.md
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            continue
        art = SkillArtifact(slug=child.name, path=skill_md)
        text = _read(skill_md)
        meta, body = _parse_frontmatter(text)
        art.name = str(meta.get("name") or child.name)
        art.description = str(meta.get("description") or "").strip()
        art.source_book = str(meta.get("source_book") or "")
        art.body = body.strip()
        art.missing_sections = _find_sections(body)
        if art.missing_sections:
            art.warnings.append(f"缺六段: {', '.join(art.missing_sections)}")
        if not art.description:
            art.warnings.append("frontmatter 缺 description（路由 trigger 依赖它）")
        # 长度红线：原文引用 ≤150 字——只提示，不阻断（由执行器保证）
        if len(art.body) < 200:
            art.warnings.append("正文过短（疑似未完成构造）")

        tp_path = child / "test-prompts.json"
        if tp_path.is_file():
            art.test_prompts = parse_test_prompts(tp_path)
            art.warnings.extend(art.test_prompts.warnings)
        else:
            art.warnings.append("缺 test-prompts.json（质量红线：必须含诱饵）")

        tr_path = child / "test-results.md"
        if tr_path.is_file():
            art.test_results_path = tr_path
            art.test_results = _read(tr_path)[:4000]

        out.skills.append(art)

    if not out.skills:
        out.warnings.append("未发现任何 <skill-slug>/SKILL.md")
    return out


def build_skill_records(artifacts: DistillArtifacts, book_id: str) -> list[dict]:
    """把产物转成 skills 表注册数据（sk_* id 由 repo.upsert_skill 生成）。"""
    records = []
    for s in artifacts.skills:
        # 相对 vault_dir（skills/<slug>/<skill>/SKILL.md）
        rel = str(s.path.relative_to(artifacts.root.parent.parent)).replace("\\", "/")
        records.append({
            "book_id": book_id,
            "name": s.name or s.slug,
            "slug": s.slug,
            "path": rel,
            "description": s.description,
            "status": "reviewing",
            "test_prompts": json.dumps(
                s.test_prompts.raw if s.test_prompts else {}, ensure_ascii=False
            ),
        })
    return records
