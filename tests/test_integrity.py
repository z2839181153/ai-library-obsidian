"""完整性检测测试：正常索引后 ok=True；破坏 FTS 后 rebuild_required=True。"""
from __future__ import annotations


def test_check_ok_after_index(make_library):
    indexer, _searcher, _repo, root = make_library()
    (root / "a.md").write_text("# A\n苹果香蕉内容", encoding="utf-8")
    indexer.run(root)
    result = indexer.check()
    assert result["ok"] is True
    assert result["rebuild_required"] is False
    assert result["counts"]["fts_ok"] is True
    assert result["counts"]["chunks"] == result["counts"]["vectors"]


def test_check_detects_fts_corruption(make_library):
    indexer, _searcher, repo, root = make_library()
    (root / "a.md").write_text("# A\n苹果香蕉内容", encoding="utf-8")
    indexer.run(root)

    # 破坏 FTS：drop 后重建空表（索引丢失，content 表仍在）
    repo.conn.execute("DROP TABLE chunks_fts")
    repo.conn.executescript(
        "CREATE VIRTUAL TABLE chunks_fts USING fts5("
        "fts_content, content='chunks', content_rowid='rowid');"
    )
    repo.conn.commit()

    result = indexer.check()
    assert result["ok"] is False
    assert result["rebuild_required"] is True
    assert result["counts"]["fts_ok"] is False


def test_check_detects_missing_chunks(make_library):
    indexer, _searcher, repo, root = make_library()
    (root / "a.md").write_text("# A\n苹果香蕉内容", encoding="utf-8")
    indexer.run(root)

    # 破坏 chunks 表（直接删 chunk 行）
    repo.conn.execute("DELETE FROM chunks")
    repo.conn.commit()

    result = indexer.check()
    assert result["ok"] is False
    assert result["rebuild_required"] is True
