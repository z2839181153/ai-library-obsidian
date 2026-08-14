"""蒸馏 API（设计文档 §6.4 / 附录 B）：开始 / 状态 / 阶段确认。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.distill.executor_llm import LLMDistiller
from app.llm.chat import LLMUnavailable

router = APIRouter(prefix="/distill", tags=["distill"])


class StartRequest(BaseModel):
    force: bool = False
    auto_confirm: bool = False     # 演示/测试：阶段 0/1.5 免主人确认（生产默认 False）


class ConfirmRequest(BaseModel):
    decision: str = "continue"      # continue | skip | cancel


def _get_executor(state):
    """返回蒸馏执行器：测试注入优先，否则真实 LLM 执行器。"""
    if getattr(state, "distill_executor", None) is not None:
        return state.distill_executor
    return LLMDistiller(state.cfg, state.llm)


@router.post("/{book_id}/start")
def start_distill(req: Request, book_id: str, body: StartRequest) -> dict:
    """主人确认开始蒸馏。触发条件：已上架 + methodology + distill_value≥60（可 force）。"""
    state = req.app.state.library
    try:
        executor = _get_executor(state)
        result = state.distill.start(book_id, executor, force=body.force,
                                     auto_confirm=body.auto_confirm)
    except LLMUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"蒸馏启动失败: {e}") from e
    if not result.get("ok"):
        code = 409 if result.get("blocked") else (404 if "书不存在" in result.get("error", "") else 400)
        raise HTTPException(status_code=code, detail=result.get("error", "蒸馏失败"))
    return result


@router.get("/{book_id}/status")
def distill_status(req: Request, book_id: str) -> dict:
    """蒸馏进度（轮询；WS 推送留 P3）。"""
    state = req.app.state.library
    return state.distill.status(book_id)


@router.post("/{book_id}/confirm-stage")
def confirm_stage(req: Request, book_id: str, body: ConfirmRequest) -> dict:
    """阶段 0 / 1.5 主人确认：continue / skip / cancel。"""
    state = req.app.state.library
    result = state.distill.confirm_stage(book_id, body.decision)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "确认失败"))
    return result
