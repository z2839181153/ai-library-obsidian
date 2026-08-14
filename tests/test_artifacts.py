"""T2：蒸馏产物扫描解析（app.distill.artifacts）。"""
from __future__ import annotations

from app.distill.artifacts import (
    DistillArtifacts,
    build_skill_records,
    parse_test_prompts,
    scan_distill_dir,
)
from app.distill.executor_fake import FakeDistiller


class _Ctx:
    title = "测试书"
    author = "测试作者"
    book_id = "bk_t"
    book_slug = "test-book"


def _make_artifacts(tmp_path) -> tuple[DistillArtifacts, _Ctx]:
    ctx = _Ctx()
    ctx.out_root = tmp_path / "vault" / "skills" / "test-book"
    fake = FakeDistiller()
    for stage in ("stage0", "stage1", "stage1_5", "stage2", "stage3", "stage4", "stage5"):
        getattr(fake, stage)(ctx)
    arts = scan_distill_dir(tmp_path / "vault" / "skills", "test-book")
    return arts, ctx


def test_scan_finds_all_artifacts(tmp_path):
    arts, _ = _make_artifacts(tmp_path)
    assert arts.exists
    assert arts.has_book_overview
    assert arts.has_verified
    assert arts.has_index
    assert arts.has_glossary
    assert arts.has_digest
    assert len(arts.candidates) == 5          # 5 类 extractor
    assert len(arts.rejected) == 1
    assert len(arts.skills) == 2              # 2 个技能
    assert arts._stage_hint() == "stage5_delivering"


def test_skill_artifacts_sections_and_prompts(tmp_path):
    arts, _ = _make_artifacts(tmp_path)
    for s in arts.skills:
        assert s.missing_sections == []       # 六段齐全
        assert s.description                     # frontmatter description 非空
        assert s.test_prompts is not None
        counts = s.test_prompts.counts
        assert counts["should_trigger"] >= 3
        assert counts["should_not_trigger"] >= 2
        assert counts["edge_case"] >= 1
        assert s.test_results_path is not None
        assert s.warnings == []               # Fake 产物无 warning


def test_missing_sections_detected(tmp_path):
    ctx = _Ctx()
    ctx.out_root = tmp_path / "vault" / "skills" / "bad-book"
    (ctx.out_root / "s1").mkdir(parents=True)
    (ctx.out_root / "s1" / "SKILL.md").write_text(
        "---\nname: s1\ndescription: 触发条件\n---\n\n## R — 原文\n> x\n\n"
        "## I — 解释\n没有 E 和 B 段\n",
        encoding="utf-8",
    )
    arts = scan_distill_dir(tmp_path / "vault" / "skills", "bad-book")
    assert arts.skills[0].missing_sections == ["A1", "A2", "E", "B"]
    assert any("缺六段" in w for w in arts.skills[0].warnings)


def test_missing_bait_warning(tmp_path):
    tp_path = tmp_path / "test-prompts.json"
    tp_path.write_text(
        '{"skill":"s1","test_cases":['
        '{"id":"a","type":"should_trigger","prompt":"x"},'
        '{"id":"b","type":"should_not_trigger","prompt":"y"},'
        '{"id":"c","type":"should_not_trigger","prompt":"z"}]}',
        encoding="utf-8",
    )
    tp = parse_test_prompts(tp_path)
    assert any("跨 skill" in w for w in tp.warnings)      # 诱饵缺同书兄弟场景
    assert any("edge_case" in w for w in tp.warnings)


def test_build_skill_records(tmp_path):
    arts, _ = _make_artifacts(tmp_path)
    records = build_skill_records(arts, "bk_t")
    assert len(records) == 2
    for rec in records:
        assert rec["book_id"] == "bk_t"
        assert rec["status"] == "reviewing"
        assert rec["path"].startswith("skills/test-book/")
        assert rec["test_prompts"] and '"should_trigger"' in rec["test_prompts"]
