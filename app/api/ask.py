"""问答 API：POST /api/ask（设计文档 §6.5 / §9.2；P1 同步返回，SSE 留 P3）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    query: str
    top_k: int = 20


@router.post("/ask")
def ask(req: Request, body: AskRequest) -> dict:
    if not body.query.strip():
        raise HTTPException(status_code=422, detail="query 不能为空")
    state = req.app.state.library
    return state.qa.ask(body.query, top_k=body.top_k)
