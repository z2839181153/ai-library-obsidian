"""P3 WebSocket 测试：/ws/chat 的 ping / chat / ask_stream。"""
from __future__ import annotations


def test_ws_ping(client):
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "ping"})
        msg = ws.receive_json()
        assert msg["type"] == "pong"


def test_ws_chat(client):
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "chat", "content": "什么是混合检索？"})
        start = ws.receive_json()
        assert start["type"] == "chat_start"
        done = ws.receive_json()
        assert done["type"] == "chat_done"
        assert done["answer"]
        assert "refs" in done
        assert "used_skills" in done


def test_ws_ask_stream_alias(client):
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "ask_stream", "content": "你好"})
        assert ws.receive_json()["type"] == "chat_start"
        assert ws.receive_json()["type"] == "chat_done"


def test_ws_empty_query(client):
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "chat", "content": "   "})
        done = ws.receive_json()
        assert done["type"] == "chat_done"
        assert "空提问" in done["answer"]
