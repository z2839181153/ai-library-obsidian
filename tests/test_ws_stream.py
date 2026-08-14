"""P4-5 WebSocket 流式聊天测试：/ws/chat ask_stream 逐段输出 / 降级 / 取消 / 落库。"""
from __future__ import annotations

from app.llm.chat import LLMUnavailable


class RaisingLLM:
    """模拟无 API key：任何调用抛 LLMUnavailable。"""

    def chat(self, messages, temperature=0.3):
        raise LLMUnavailable("no key")

    def chat_stream(self, messages, temperature=0.3, max_tokens=1024):
        raise LLMUnavailable("no key")

    def chat_json(self, prompt, system=None):
        raise LLMUnavailable("no key")


def _seed_book(state, book_id="bk_ws1", title="机器学习入门",
               content="神经网络与梯度下降。"):
    state.repo.upsert_book({
        "book_id": book_id, "title": title, "media_type": "pdf",
        "status": "shelved", "content_hash": f"h_{book_id}",
        "raw_path": "", "vault_path": "books/1F-电子书/人工智能/机器学习", "card_path": "",
    })
    state.repo.insert_chunk({
        "chunk_id": f"ck_{book_id}_0", "book_id": book_id, "section": "第1章",
        "seq": 0, "content": content, "fts_content": "神经网络 梯度下降",
        "token_cnt": 10, "vector_id": f"ck_{book_id}_0",
    })
    state.vec.upsert([{
        "chunk_id": f"ck_{book_id}_0", "book_id": book_id,
        "vector": state.embed.embed_one(content),
    }])
    state.repo.upsert_card({
        "book_id": book_id, "summary": "介绍机器学习基础与神经网络。",
        "chapters": "[]", "concepts": "[]", "distill_value": 70,
        "distill_reason": "", "category": "methodology", "tags": "[]",
        "skills": "[]", "model": "fake",
    })
    state.repo.commit()


def _drain_stream(ws):
    """读取 ask_stream 的全部帧直到 chat_done，返回 (frames, done_frame)。"""
    frames = []
    while True:
        f = ws.receive_json()
        frames.append(f)
        if f["type"] == "chat_done":
            return frames, f


def test_ws_ask_stream_tokens(client):
    _seed_book(client.app.state.library)
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "ask_stream", "content": "神经网络"})
        frames, done = _drain_stream(ws)
        types = [f["type"] for f in frames]
        assert types[0] == "chat_start"
        assert "chat_token" in types
        assert done["answer"]
        # 逐段拼接 == 最终 answer
        joined = "".join(f["delta"] for f in frames if f["type"] == "chat_token")
        assert joined == done["answer"]
        assert "测试回答" in done["answer"]


def test_ws_ask_stream_model_unavailable(client):
    state = client.app.state.library
    _seed_book(state)
    state.qa.llm = RaisingLLM()
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "ask_stream", "content": "神经网络"})
        frames, done = _drain_stream(ws)
        assert done["model_unavailable"] is True
        assert "模型不可用" in done["answer"]


def test_ws_ask_stream_cancel(client):
    state = client.app.state.library
    _seed_book(state)

    # 慢速流式 LLM：给"取消"留出窗口（FakeLLM 太快，瞬间完成无法取消）
    import time

    class SlowStreamLLM:
        def chat_stream(self, messages, temperature=0.3, max_tokens=1024):
            for piece in ["第一段", "第二段", "第三段"]:
                time.sleep(0.05)
                yield piece

    state.qa.llm = SlowStreamLLM()
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "ask_stream", "content": "神经网络"})
        start = ws.receive_json()
        assert start["type"] == "chat_start"
        tok = ws.receive_json()
        assert tok["type"] == "chat_token"
        ws.send_json({"type": "cancel"})
        # drain 直到 chat_done（可能还有已缓冲 token 帧）
        cancelled = False
        while True:
            f = ws.receive_json()
            if f["type"] == "chat_done":
                cancelled = f.get("cancelled") is True
                break
        assert cancelled is True


def test_ws_ask_stream_records_conversation(client):
    state = client.app.state.library
    _seed_book(state)
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "ask_stream", "content": "神经网络"})
        _drain_stream(ws)
    convs = client.get("/api/conversations").json()["conversations"]
    assert len(convs) >= 1
    cv_id = convs[0]["cv_id"]
    d = client.get(f"/api/conversations/{cv_id}").json()
    roles = [m["role"] for m in d["messages"]]
    assert roles == ["user", "assistant"]
    assert d["messages"][1]["refs"]
