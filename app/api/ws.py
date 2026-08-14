"""WebSocket 路由：/ws/chat（设计文档 §9.3）。

客户端→服务端：
  {"type": "chat", "content": "提问"}          # 走 qa.ask，返回完整回答
  {"type": "ask_stream", "content": "提问"}    # 兼容；同 chat（逐字流式留 P4）
  {"type": "ping"}

服务端→客户端：
  {"type": "pong"}
  {"type": "notice", "event": "book_ingested", "book_id", "title"}
  {"type": "notice", "event": "distill_progress", "book_id", "stage", "detail"}
  {"type": "notice", "event": "skill_review_ready", "skill_id", "name"}
  {"type": "chat_start", "query"}              # 开始回答
  {"type": "chat_done", "answer", "refs", "used_skills", "model_unavailable"}
"""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.llm.chat import LLMUnavailable

router = APIRouter(tags=["ws"])


@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket) -> None:
    state = ws.app.state.library
    manager = state.ws
    client_id = await manager.connect(ws)
    try:
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type", "")
            if mtype == "ping":
                await ws.send_json({"type": "pong"})
            elif mtype in ("chat", "ask_stream"):
                query = (msg.get("content") or "").strip()
                if not query:
                    await ws.send_json({"type": "chat_done",
                                        "answer": "（空提问）", "refs": [],
                                        "used_skills": [], "model_unavailable": False})
                    continue
                await ws.send_json({"type": "chat_start", "query": query})
                try:
                    result = state.qa.ask(query, top_k=int(msg.get("top_k", 20)))
                except LLMUnavailable:
                    result = {"answer": "（模型不可用：未配置 API key）", "refs": [],
                              "used_skills": [], "model_unavailable": True}
                await ws.send_json({
                    "type": "chat_done",
                    "query": query,
                    "answer": result.get("answer", ""),
                    "refs": result.get("refs", []),
                    "books": result.get("books", []),
                    "used_skills": result.get("used_skills", []),
                    "model_unavailable": result.get("model_unavailable", False),
                })
            elif mtype == "subscribe":
                # P4 预留：订阅某书状态变更；当前所有通知走 broadcast
                await ws.send_json({"type": "subscribed", "channel": msg.get("channel", "")})
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(client_id)
