"""操作账本 API：列出 + 撤销（设计文档 §6.9 / §9.2）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/actions", tags=["actions"])


@router.get("")
def list_actions(req: Request, limit: int = 100,
                 target_type: str | None = None, target_id: str | None = None) -> dict:
    state = req.app.state.library
    actions = state.repo.list_actions(
        limit=limit, target_type=target_type, target_id=target_id
    )
    return {"actions": actions, "count": len(actions)}


@router.post("/{act_id}/undo")
def undo_action(req: Request, act_id: str) -> dict:
    """撤销动作：shelve → 移回补书室；classify → 清空分类建议。"""
    state = req.app.state.library
    act = state.repo.get_action(act_id)
    if not act:
        raise HTTPException(status_code=404, detail="动作不存在")
    if act.get("status") == "undone":
        raise HTTPException(status_code=400, detail="动作已撤销")

    try:
        if act["action_type"] == "shelve":
            return state.shelver.undo_shelve(act)
        if act["action_type"] == "classify":
            return state.shelver.undo_classify(act)
        raise HTTPException(status_code=400, detail=f"不支持撤销动作类型: {act['action_type']}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
