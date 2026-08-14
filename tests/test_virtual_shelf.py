"""P4-6 虚拟书架测试：/api/books 按标签聚合与过滤。"""
from __future__ import annotations

import json


def _seed_tagged_book(client, title: str, tags: list[str]) -> str:
    """入馆 + 分类（FakeLLM 卡片 tags 固定）→ 手动覆写卡片 tags。"""
    r = client.post("/api/ingest", data={"text": f"# {title}\n\n正文内容。", "title": title})
    assert r.status_code == 200, r.text
    book_id = r.json()["book"]["book_id"]
    # 分类生成卡片（FakeLLM DEFAULT_CARD_JSON）
    cr = client.post(f"/api/books/{book_id}/classify", json={"force": False})
    assert cr.status_code == 200, cr.text
    # 覆写卡片 tags
    state = client.app.state.library
    state.repo.upsert_card({
        "book_id": book_id,
        "tags": json.dumps(tags, ensure_ascii=False),
    })
    state.repo.commit()
    return book_id


def test_books_list_includes_tags(client):
    _seed_tagged_book(client, "机器学习基础", ["人工智能", "知识库"])
    r = client.get("/api/books").json()
    books = [b for b in r["books"] if b["title"] == "机器学习基础"]
    assert books
    assert books[0]["tags"] == ["人工智能", "知识库"]


def test_books_filter_by_tag(client):
    _seed_tagged_book(client, "书A", ["人工智能"])
    _seed_tagged_book(client, "书B", ["哲学"])
    r = client.get("/api/books?tag=人工智能").json()
    titles = [b["title"] for b in r["books"]]
    assert "书A" in titles
    assert "书B" not in titles


def test_books_filter_by_tag_empty(client):
    r = client.get("/api/books?tag=不存在的标签").json()
    assert r["count"] == 0
