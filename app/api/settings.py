"""设置 API（设计文档 §9.2 GET/PUT /api/settings）。

- GET：读 settings.json + profile → UI 可编辑子集（API key 掩码、采集规则、托管程度）
- PUT：写回 settings.json（server.paths 不可改；modelscope key 空值=不改）
- 托管程度（profile.prefs.default_mode）：full|half|manual
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config import DEFAULT_CONFIG_PATH, ROOT_DIR

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    modelscope: dict | None = None
    ollama: dict | None = None
    purchase: dict | None = None
    distill: dict | None = None
    prefs: dict | None = None          # 托管程度等（写 profile 表）


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def _load_settings_json() -> dict:
    return json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")) \
        if DEFAULT_CONFIG_PATH.exists() else {}


def _save_settings_json(raw: dict) -> None:
    DEFAULT_CONFIG_PATH.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@router.get("")
def get_settings(req: Request) -> dict:
    state = req.app.state.library
    cfg = state.cfg
    raw = _load_settings_json()
    profile = state.repo.get_profile()

    purchase = raw.get("purchase", {})
    return {
        "server": {"host": cfg.server.host, "port": cfg.server.port},
        "paths": {"data_dir": str(cfg.paths.data_dir), "vault_dir": str(cfg.paths.vault_dir)},
        "modelscope": {
            "base_url": cfg.modelscope.base_url,
            "chat_model": cfg.modelscope.chat_model,
            "distill_model": cfg.modelscope.distill_model,
            "embed_model": cfg.modelscope.embed_model,
            "api_key_masked": _mask_key(cfg.modelscope.api_key),
            "api_key_set": bool(cfg.modelscope.api_key),
        },
        "ollama": {
            "base_url": cfg.ollama.base_url,
            "enabled": cfg.ollama.enabled,
            "model": cfg.ollama.model,
        },
        "purchase": {
            "max_daily_purchase": int(purchase.get("max_daily_purchase", 5)),
            "no_video_unless_hot": bool(purchase.get("no_video_unless_hot", True)),
            "sources": purchase.get("sources", []),
        },
        "distill": {
            "route_threshold": cfg.distill.route_threshold,
            "route_top_k": cfg.distill.route_top_k,
            "reject_block": cfg.distill.reject_block,
        },
        "prefs": profile.get("prefs", {}),
        "profile_themes": profile.get("themes", {}),
        "direction_pool": profile.get("direction_pool", []),
    }


@router.put("")
def put_settings(req: Request, body: SettingsUpdate) -> dict:
    state = req.app.state.library
    cfg = state.cfg
    raw = _load_settings_json()

    if body.modelscope is not None:
        ms = raw.setdefault("modelscope", {})
        for k in ("base_url", "chat_model", "distill_model", "embed_model"):
            v = body.modelscope.get(k)
            if v:
                ms[k] = v
                setattr(cfg.modelscope, k, v)
        # API key：空值=不改；非空写 data/secrets.json（不入 git）
        key = (body.modelscope.get("api_key") or "").strip()
        if key:
            secrets_path = cfg.paths.data_dir / "secrets.json"
            secrets = {}
            if secrets_path.exists():
                secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
            secrets["modelscope_api_key"] = key
            secrets_path.write_text(
                json.dumps(secrets, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            cfg.modelscope.api_key = key

    if body.ollama is not None:
        ol = raw.setdefault("ollama", {})
        for k in ("base_url", "model"):
            v = body.ollama.get(k)
            if v:
                ol[k] = v
        if "enabled" in body.ollama:
            ol["enabled"] = bool(body.ollama["enabled"])
        cfg.ollama.enabled = bool(ol.get("enabled", cfg.ollama.enabled))
        cfg.ollama.base_url = ol.get("base_url", cfg.ollama.base_url)
        cfg.ollama.model = ol.get("model", cfg.ollama.model)

    if body.purchase is not None:
        raw["purchase"] = {**raw.get("purchase", {}), **body.purchase}

    if body.distill is not None:
        ds = raw.setdefault("distill", {})
        for k in ("route_threshold", "route_top_k", "reject_block"):
            if k in body.distill:
                ds[k] = body.distill[k]

    if body.prefs is not None:
        state.repo.save_profile(prefs=body.prefs)

    _save_settings_json(raw)
    return get_settings(req)


def save_secret_key_if_present(raw_key: str | None, state) -> None:
    """供其他 API 复用：写入 data/secrets.json。"""
    key = (raw_key or "").strip()
    if not key:
        return
    secrets_path = state.cfg.paths.data_dir / "secrets.json"
    secrets = {}
    if secrets_path.exists():
        secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
    secrets["modelscope_api_key"] = key
    secrets_path.write_text(json.dumps(secrets, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    state.cfg.modelscope.api_key = key
