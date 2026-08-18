"""P6-3 供应商 API：GET /api/providers。

返回：预设库（供三步向导步骤一选供应商）+ 当前生效配置识别
（供应商 / 模型 / key 掩码 / 是否需要首次配置 / 上次测试时间）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request

from app.config import DEFAULT_CONFIG_PATH
from app.providers import detect_current, load_providers, mask_key

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("")
def get_providers(req: Request) -> dict:
    state = req.app.state.library
    cfg = state.cfg
    raw = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")) \
        if DEFAULT_CONFIG_PATH.exists() else {}

    current_id, current_provider = detect_current(cfg)

    # 是否需要首次配置：聊天 key 未配置 且 Ollama 未启用（本地通道也没有）
    needs_setup = not cfg.modelscope.api_key and not cfg.ollama.enabled

    return {
        "providers": load_providers(),
        "current": {
            "provider_id": current_id,
            "provider": current_provider,
            "base_url": cfg.modelscope.base_url,
            "chat_model": cfg.modelscope.chat_model,
            "distill_model": cfg.modelscope.distill_model,
            "embed_model": cfg.modelscope.embed_model,
            "embed_base_url": cfg.modelscope.embed_base_url or cfg.modelscope.base_url,
            "chat_key_set": bool(cfg.modelscope.api_key),
            "chat_key_masked": mask_key(cfg.modelscope.api_key),
            "embed_key_set": bool(cfg.modelscope.embed_api_key),
            "embed_key_masked": mask_key(cfg.modelscope.embed_api_key),
            "ollama_enabled": cfg.ollama.enabled,
            "last_conn_test": raw.get("last_conn_test", ""),
        },
        "needs_setup": needs_setup,
    }
