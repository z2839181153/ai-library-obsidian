"""P3 对话 API 测试：ask 落库 / 列表 / 详情 / 归档为书。"""
from __future__ import annotations


def test_ask_records_conversation(client):
    r = client.post("/api/ask", json={"query": "神经网络是什么？"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("cv_id")
    assert data["answer"]

    # 落库：对话 + 2 条消息
    cv = client.get(f"/api/conversations/{data['cv_id']}").json()
    assert cv["messages"][0]["role"] == "user"
    assert cv["messages"][1]["role"] == "assistant"
    assert cv["messages"][1]["refs"] is not None


def test_conversations_list(client):
    client.post("/api/ask", json={"query": "问题一"})
    client.post("/api/ask", json={"query": "问题二"})
    r = client.get("/api/conversations")
    assert r.status_code == 200
    assert r.json()["count"] == 2
    groups = {c["group"] for c in r.json()["conversations"]}
    assert "今天" in groups


def test_conversation_not_found(client):
    r = client.get("/api/conversations/cv_nonexistent")
    assert r.status_code == 404


def test_archive_conversation(client):
    client.post("/api/ask", json={"query": "如何搭建知识库？"})
    lst = client.get("/api/conversations").json()["conversations"]
    cv_id = lst[0]["cv_id"]
    r = client.post(f"/api/conversations/{cv_id}/archive")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["book"]["media_type"] == "markdown"   # 导出 md 入馆
    assert data["book"]["status"] == "incoming"
    # 对话标记归档
    cv = client.get(f"/api/conversations/{cv_id}").json()["conversation"]
    assert cv["archived_book_id"] == data["book"]["book_id"]


def test_archive_empty_conversation_rejected(client):
    from app.db.repo import new_id
    from app.api.conversations import ensure_conversation

    state = client.app.state.library
    cv_id = ensure_conversation(state, title="空对话")
    r = client.post(f"/api/conversations/{cv_id}/archive")
    assert r.status_code == 400
