"""检索测试：词法（FTS5）召回、混合检索、书聚合。"""
from __future__ import annotations


def _write(root, name, text):
    p = root / name
    p.write_text(text, encoding="utf-8")
    return p


def test_lexical_search_finds_term(make_library):
    indexer, searcher, repo, root = make_library()
    _write(root, "bike.md", "# 自行车维修手册\n\n如何维修自行车链条和刹车。")
    _write(root, "ml.md", "# 机器学习入门\n\n介绍神经网络与深度学习。")
    indexer.run(root)

    result = searcher.search("自行车维修")
    assert result["books"], "词法检索应命中"
    top = result["books"][0]
    assert "自行车" in top["title"] or any(
        "自行车" in c["content"] for c in top["hit_chunks"]
    )


def test_top10_recall(make_library):
    """验收：含特定术语的查询 top-10 召回率 >= 90%（10 篇样本中 9 篇命中）。"""
    indexer, searcher, repo, root = make_library()
    # 10 篇"自行车"相关 + 10 篇干扰
    for i in range(10):
        _write(root, f"bike{i}.md", f"# 自行车笔记{i}\n\n自行车维修技巧：链条、刹车、轮胎。编号{i}。")
    for i in range(10):
        _write(root, f"other{i}.md", f"# 无关笔记{i}\n\n烹饪食谱与园艺。编号{i}。")
    indexer.run(root)

    result = searcher.search("自行车链条刹车", top_k=10)
    bike_hits = sum(
        1 for b in result["books"] if b["book_id"].startswith("bk_") and "bike" in b["title"]
    )
    # 书标题含"自行车笔记"
    bike_hits = sum(1 for b in result["books"] if "自行车笔记" in b["title"])
    assert bike_hits >= 9, f"召回不足: {bike_hits}/10 -> {[b['title'] for b in result['books']]}"


def test_book_filter(make_library):
    indexer, searcher, repo, root = make_library()
    _write(root, "bike.md", "# 自行车\n维修链条")
    _write(root, "ml.md", "# 机器学习\n神经网络")
    indexer.run(root)

    ml_book = next(b for b in repo.all_books() if "机器学习" in b["title"])
    result = searcher.search("机器学习", book_ids=[ml_book["book_id"]])
    assert len(result["books"]) == 1
    assert result["books"][0]["book_id"] == ml_book["book_id"]
