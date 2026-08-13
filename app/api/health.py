"""健康检查：验证服务与依赖是否就绪。"""
from __future__ import annotations

from fastapi import APIRouter

from app import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    """服务健康检查（M0 仅验证进程可运行；P0 起加入索引/DB 状态）。"""
    return {
        "status": "ok",
        "version": __version__,
        "service": "ai-library",
    }
