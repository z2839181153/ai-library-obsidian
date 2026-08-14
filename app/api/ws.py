"""WebSocket 路由：/ws/chat（设计文档 §9.3）。

客户端→服务端：
  {"type": "chat", "content": "提问"}           # 非流式，一次性返回（兼容）
  {"type": "ask_stream", "content": "提问", "cv_id"?}  # P4-5：流式逐段返回 + 落库
  {"type": "cancel"}                            # 取消进行中的 ask_stream
  {"type": "ping"}

服务端→客户端：
  {"type": "pong"}
  {"type": "notice", "event": "book_ingested", "book_id", "title"}
  {"type": "notice", "event": "distill_progress", "book_id", "stage", "detail"}
  {"type": "notice", "event": "skill_review_ready", "skill_id", "name"}
  {"type": "chat_start", "query", "refs", "books", "used_skills"}
  {"type": "chat_token", "delta"}               # P4-5 逐段输出
  {"type": "chat_done", "query", "answer", "refs", "books", "used_skills",
   "model_unavailable", "cancelled"?}
"""
from __future__ import annotations

import asyncio
import threading

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.llm.chat import LLMUnavailable

router = APIRouter(tags=["ws"])


async def _iter_sync_gen(gen):
    """把同步生成器包装成 async 生成器：独立线程逐步产出，不阻塞事件循环。

    qa.ask_stream 内部有 LLM 网络调用（可能数秒到数十秒），若直接在事件循环里
    迭代会阻塞 WS 通知广播；放到后台线程 + queue 转发。
    """
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=16)
    sentinel = object()

    def _run():
        try:
            for item in gen:
                loop.call_soon_threadsafe(q.put_nowait, item)
        except Exception as e:  # noqa: BLE001
            loop.call_soon_threadsafe(q.put_nowait, e)
        finally:
            loop.call_soon_threadsafe(q.put_nowait, sentinel)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    while True:
        item = await q.get()
        if item is sentinel:
            break
        if isinstance(item, Exception):
            raise item
        yield item
    await asyncio.to_thread(thread.join)


async def _forward_stream(ws: WebSocket, state, query: str, top_k: int,
                          cv_id: str | None) -> None:
    """把 qa.ask_stream 事件逐帧发给 ws；流式完成后写入对话历史。

    chat_done 延迟到落库之后发送，并携带 cv_id（前端续聊上下文）。
    """
    done_ev = None
    try:
        async for ev in _iter_sync_gen(state.qa.ask_stream(query, top_k=top_k)):
            if ev["type"] == "chat_done":
                done_ev = ev
                break
            await ws.send_json(ev)
    except LLMUnavailable:
        done_ev = {
            "type": "chat_done", "query": query,
            "answer": "（模型不可用：未配置 API key）",
            "refs": [], "books": [], "used_skills": [],
            "model_unavailable": True,
        }

    # 落库（与 /api/ask 一致）：user + assistant 各一条；cv_id 回传前端
    if done_ev is not None:
        try:
            from app.api.conversations import append_message, ensure_conversation

            repo = state.repo
            if not cv_id or not repo.conn.execute(
                "SELECT 1 FROM conversations WHERE cv_id=?", (cv_id,)
            ).fetchone():
                cv_id = ensure_conversation(state, title=query[:40] or "新对话")
            append_message(state, cv_id, "user", query)
            append_message(state, cv_id, "assistant",
                           done_ev.get("answer") or "",
                           refs=done_ev.get("refs", []))
            done_ev["cv_id"] = cv_id
        except Exception:  # noqa: BLE001  落库失败不影响回答
            pass
    if done_ev is not None:
        await ws.send_json(done_ev)


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
            elif mtype == "chat":
                # 兼容：非流式一次性返回
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
            elif mtype == "ask_stream":
                query = (msg.get("content") or "").strip()
                top_k = int(msg.get("top_k", 20))
                if not query:
                    await ws.send_json({"type": "chat_done",
                                        "answer": "（空提问）", "refs": [],
                                        "books": [], "used_skills": [],
                                        "model_unavailable": False})
                    continue
                cv_id = (msg.get("cv_id") or "").strip() or None
                task = asyncio.create_task(
                    _forward_stream(ws, state, query, top_k, cv_id)
                )
                cancelled = False
                try:
                    while True:
                        try:
                            ctrl = await asyncio.wait_for(ws.receive_json(), timeout=0.2)
                        except asyncio.TimeoutError:
                            if task.done():
                                break
                            continue
                        if ctrl.get("type") == "cancel":
                            cancelled = True
                            task.cancel()
                            break
                except WebSocketDisconnect:
                    task.cancel()
                    raise
                if cancelled:
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                    await ws.send_json({
                        "type": "chat_done", "query": query,
                        "answer": "（已取消）", "refs": [],
                        "books": [], "used_skills": [],
                        "model_unavailable": False, "cancelled": True,
                    })
                else:
                    await task
            elif mtype == "subscribe":
                # P4 预留：订阅某书状态变更；当前所有通知走 broadcast
                await ws.send_json({"type": "subscribed", "channel": msg.get("channel", "")})
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(client_id)
