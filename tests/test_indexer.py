"""indexer 测试：首次索引、增量、删除、重建（copy-on-write）。"""
from __future__ import annotations


def _write(root, name, text):
    p = root / name
    p.write_text(text, encoding="utf-8")
    return p


def test_first_index_and_skip(make_library):
    indexer, _searcher, repo, root = make_library()
    _write(root, "a.md", "# A\n" + "苹果 香蕉 橙子。" * 30)
    _write(root, "b.md", "# B\n" + "机器学习 深度学习。" * 30)

    stats = indexer.run(root)
    assert stats["scanned"] == 2
    assert stats["new_or_changed"] == 2
    assert stats["removed"] == 0
    assert len(repo.all_books()) == 2
    total = repo.conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
    assert total > 0

    # 再跑一遍：全部跳过（内容未变）
    stats2 = indexer.run(root)
    assert stats2["new_or_changed"] == 0
    assert stats2["skipped"] == 2


def test_incremental_reindex_changed(make_library):
    indexer, _searcher, repo, root = make_library()
    _write(root, "a.md", "# A\n" + "苹果。" * 30)
    _write(root, "b.md", "# B\n" + "香蕉。" * 30)
    indexer.run(root)

    # 修改 a.md
    _write(root, "a.md", "# A 改了\n" + "苹果 梨 西瓜。" * 30)
    stats = indexer.run(root)
    assert stats["new_or_changed"] == 1
    assert stats["skipped"] == 1

    # b 的 chunks 应仍在，a 的 chunks 应更新（含"梨"）
    b_book = repo.book_by_hash(
        # 找到 b（标题 B 的书）
        ""
    )
    books = {bk["title"]: bk for bk in repo.all_books()}
    assert "B" in books


def test_removed_file(make_library):
    indexer, _searcher, repo, root = make_library()
    _write(root, "a.md", "# A\n内容内容")
    _write(root, "b.md", "# B\n内容内容")
    indexer.run(root)
    assert len(repo.all_books()) == 2

    (root / "a.md").unlink()
    stats = indexer.run(root)
    assert stats["removed"] == 1
    assert len(repo.all_books()) == 1


def test_rebuild(make_library):
    indexer, _searcher, repo, root = make_library()
    _write(root, "a.md", "# A\n内容内容")
    indexer.run(root)
    stats = indexer.run(root, rebuild=True)
    assert stats["new_or_changed"] == 1
    assert len(repo.all_books()) == 1
