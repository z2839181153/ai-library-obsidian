"""P3 入馆 API 测试：文件上传 / 文本入馆 / 去重 / 索引 / WS 通知。"""
from __future__ import annotations


def test_ingest_file_creates_book(client):
    r = client.post("/api/ingest",
                    files={"file": ("测试书.md", "# 测试书\n\n## 第一章\n内容。".encode(), "text/markdown")})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["created"] is True
    book = data["book"]
    assert book["status"] == "incoming"
    assert book["title"] == "测试书"
    assert not data["duplicate"]

    # 已登记 + 有 chunk（索引）
    st = client.get("/api/books").json()
    assert st["count"] == 1


def test_ingest_duplicate_detected(client):
    content = "# 重复书\n\n内容。".encode()
    r1 = client.post("/api/ingest", files={"file": ("a.md", content, "text/markdown")})
    assert r1.json()["created"] is True
    r2 = client.post("/api/ingest", files={"file": ("b.md", content, "text/markdown")})
    assert r2.status_code == 200
    data = r2.json()
    assert data["duplicate"] is True
    assert data["created"] is False
    st = client.get("/api/books").json()
    assert st["count"] == 1


def test_ingest_text(client):
    r = client.post("/api/ingest", data={"text": "# 文本书\n\n正文", "title": "我的笔记"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["created"] is True
    assert data["book"]["title"] == "我的笔记"


def test_ingest_unsupported_format(client):
    r = client.post("/api/ingest", files={"file": ("evil.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400
    assert "不支持的格式" in r.json()["detail"]


def test_ingest_empty_rejected(client):
    r = client.post("/api/ingest", data={})
    assert r.status_code == 422


def test_ingest_records_action(client):
    r = client.post("/api/ingest", data={"text": "# 记账书\n\n正文"})
    book_id = r.json()["book"]["book_id"]
    acts = client.get("/api/actions").json()["actions"]
    assert any(a["action_type"] == "ingest" and a["target_id"] == book_id for a in acts)


def test_ingest_ws_notice(client):
    """入馆后 ConnectionManager 收到 book_ingested 广播。"""
    state = client.app.state.library
    received = []

    class FakeWS:
        def __init__(self):
            self.manager = state.ws

        def send_json(self, msg):
            received.append(msg)

    # 用 manager.send 模拟一个客户端连接
    # 直接验证 broadcast 后队列有消息（通过注册临时 client）
    from app.ws.manager import ConnectionManager

    manager = state.ws
    # 手动塞一个假 client（绕过 websocket accept）
    import asyncio

    q = asyncio.Queue()
    fake_id = "ws_fake_test"
    with manager._lock:
        manager._clients[fake_id] = {"ws": object(), "queue": q,
                                     "loop": asyncio.new_event_loop()}

    r = client.post("/api/ingest", data={"text": "# 通知书\n\n正文"})
    assert r.json()["created"] is True

    # queue 里应有 notice
    msgs = []
    while not q.empty():
        msgs.append(q.get_nowait())
    assert any(m.get("type") == "notice" and m.get("event") == "book_ingested" for m in msgs)
    with manager._lock:
        manager._clients.pop(fake_id, None)
