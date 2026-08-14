"""问答 API：POST /api/ask（设计文档 §6.5 / §9.2）。

P1 同步返回（SSE/WS 流式见 /ws/chat）。
P3 起：每次提问落库 conversations+messages（对话历史），返回 cv_id。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.conversations import append_message, ensure_conversation

router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    query: str
    top_k: int = 20
    cv_id: str | None = None      # 续聊；缺省自动创建新对话


@router.post("/ask")
def ask(req: Request, body: AskRequest) -> dict:
    if not body.query.strip():
        raise HTTPException(status_code=422, detail="query 不能为空")
    state = req.app.state.library

    cv_id = body.cv_id
    if not cv_id or not state.repo.conn.execute(
        "SELECT 1 FROM conversations WHERE cv_id=?", (cv_id,)
    ).fetchone():
        cv_id = ensure_conversation(state, title=body.query.strip()[:40] or "新对话")

    result = state.qa.ask(body.query, top_k=body.top_k)

    append_message(state, cv_id, "user", body.query)
    append_message(state, cv_id, "assistant",
                   result.get("answer") or "",
                   refs=result.get("refs", []))

    result["cv_id"] = cv_id
    return result
