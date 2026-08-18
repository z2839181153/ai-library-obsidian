"""设置 API（设计文档 §9.2 GET/PUT /api/settings）。

- GET：读 settings.json + profile → UI 可编辑子集（API key 掩码、采集规则、托管程度）
- PUT：写回 settings.json（server.paths 不可改；modelscope key 空值=不改）
- 托管程度（profile.prefs.default_mode）：full|half|manual
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config import DEFAULT_CONFIG_PATH, ROOT_DIR
from app.providers import (MODELSCOPE_BASE, MODELSCOPE_EMBED,
                           load_providers, test_chat, test_embed, test_ollama)

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
            "embed_base_url": cfg.modelscope.embed_base_url,
            "chat_retries": cfg.modelscope.chat_retries,
            "chat_retry_base": cfg.modelscope.chat_retry_base,
            "chat_retry_max": cfg.modelscope.chat_retry_max,
            "chat_retry_on_429": cfg.modelscope.chat_retry_on_429,
            "api_key_masked": _mask_key(cfg.modelscope.api_key),
            "api_key_set": bool(cfg.modelscope.api_key),
            "embed_api_key_masked": _mask_key(cfg.modelscope.embed_api_key),
            "embed_api_key_set": bool(cfg.modelscope.embed_api_key),
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
        for k in ("base_url", "chat_model", "distill_model", "embed_model", "embed_base_url"):
            v = body.modelscope.get(k)
            if v:
                ms[k] = v
                setattr(cfg.modelscope, k, v)
        # LLM 重试参数可配（chat_retries/chat_retry_base/chat_retry_max/chat_retry_on_429）
        for k in ("chat_retries", "chat_retry_base", "chat_retry_max"):
            if k in body.modelscope and body.modelscope.get(k) is not None:
                ms[k] = body.modelscope[k]
                setattr(cfg.modelscope, k, float(body.modelscope[k]) if k != "chat_retries"
                        else int(body.modelscope[k]))
        if "chat_retry_on_429" in body.modelscope:
            ms["chat_retry_on_429"] = bool(body.modelscope["chat_retry_on_429"])
            cfg.modelscope.chat_retry_on_429 = ms["chat_retry_on_429"]
        # API key：空值=不改；非空写 data/secrets.json（不入 git）
        #   api_key        → chat/distill（secrets.deepseek_api_key）
        #   embed_api_key  → embedding（secrets.modelscope_api_key）
        key = (body.modelscope.get("api_key") or "").strip()
        if key:
            secrets_path = cfg.paths.data_dir / "secrets.json"
            secrets = {}
            if secrets_path.exists():
                secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
            secrets["deepseek_api_key"] = key
            secrets_path.write_text(
                json.dumps(secrets, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            cfg.modelscope.api_key = key
        emb_key = (body.modelscope.get("embed_api_key") or "").strip()
        if emb_key:
            secrets_path = cfg.paths.data_dir / "secrets.json"
            secrets = {}
            if secrets_path.exists():
                secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
            secrets["modelscope_api_key"] = emb_key
            secrets_path.write_text(
                json.dumps(secrets, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            cfg.modelscope.embed_api_key = emb_key

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


# ---------------- P6-3 配置向导：apply-provider / test-connection ----------------


class ApplyProviderBody(BaseModel):
    provider: str                       # 预设库 key
    chat_model: str = ""
    distill_model: str = ""
    embed_model: str = ""               # 空 = 自动落 ModelScope 免费
    api_key: str = ""
    embed_api_key: str = ""
    ollama_enabled: bool = False        # 本地通道：显式启用 Ollama


class TestConnectionBody(BaseModel):
    base_url: str = ""
    chat_model: str = ""
    embed_model: str = ""
    embed_base_url: str = ""
    api_key: str = ""
    embed_api_key: str = ""
    is_ollama: bool = False             # 本地供应商走 /api/tags 探测


def _write_secret(cfg, key_name: str, value: str) -> None:
    value = (value or "").strip()
    if not value:
        return
    secrets_path = cfg.paths.data_dir / "secrets.json"
    secrets = {}
    if secrets_path.exists():
        secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
    secrets[key_name] = value
    secrets_path.write_text(json.dumps(secrets, ensure_ascii=False, indent=2),
                            encoding="utf-8")


def _touch_conn_test(raw: dict) -> None:
    """记录最近一次成功测试连接的时间（settings.json 顶层，非敏感）。"""
    raw["last_conn_test"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@router.post("/apply-provider")
def apply_provider(req: Request, body: ApplyProviderBody) -> dict:
    """P6-3：一步写入供应商配置（settings.json + secrets.json）。

    - base_url/chat_model/distill_model 来自预设库 + 用户选择
    - embed_model 为空 → 自动落 ModelScope 免费（embed_base_url/embed_model 固定）
    - key 只写 data/secrets.json（不入 settings.json、不入 git）
    """
    state = req.app.state.library
    cfg = state.cfg
    providers = load_providers()
    prov = providers.get(body.provider)
    if not prov:
        raise HTTPException(status_code=400, detail=f"未知供应商: {body.provider}")

    raw = _load_settings_json()
    ms = raw.setdefault("modelscope", {})

    # 聊天/蒸馏配置
    if not body.chat_model:
        raise HTTPException(status_code=400, detail="请选择聊天模型")
    if not body.distill_model:
        raise HTTPException(status_code=400, detail="请选择蒸馏模型")
    ms["base_url"] = prov["base_url"]
    ms["chat_model"] = body.chat_model
    ms["distill_model"] = body.distill_model

    # 嵌入配置：显式选模型 → 用本供应商；空 → 自动 ModelScope 免费
    if body.embed_model:
        ms["embed_model"] = body.embed_model
        ms["embed_base_url"] = prov["base_url"]
    else:
        ms["embed_model"] = MODELSCOPE_EMBED
        ms["embed_base_url"] = MODELSCOPE_BASE
    # 同步到运行中 cfg（后续请求立即生效）
    cfg.modelscope.base_url = ms["base_url"]
    cfg.modelscope.chat_model = ms["chat_model"]
    cfg.modelscope.distill_model = ms["distill_model"]
    cfg.modelscope.embed_model = ms["embed_model"]
    cfg.modelscope.embed_base_url = ms["embed_base_url"]

    # Ollama 本地通道：向导仅在选 Ollama 时启用；非本地配置不动它（保留隐私通道）
    if body.ollama_enabled:
        if "ollama" not in raw:
            raw["ollama"] = {}
        raw["ollama"]["enabled"] = True
        cfg.ollama.enabled = True

    # 密钥（仅本地供应商可全部留空；非本地：既没填、也没已配置 key 才拒绝）
    if prov.get("local"):
        if body.api_key or body.embed_api_key:
            pass  # 本地也允许填（如转发代理 key），照写
    elif not body.api_key and not body.embed_api_key and not cfg.modelscope.api_key:
        raise HTTPException(status_code=400, detail="请填写 API key（聊天 key 必填；嵌入 key 在嵌入走本供应商时必填）")
    _write_secret(cfg, "deepseek_api_key", body.api_key)
    _write_secret(cfg, "modelscope_api_key", body.embed_api_key)
    if body.api_key:
        cfg.modelscope.api_key = body.api_key
    if body.embed_api_key:
        cfg.modelscope.embed_api_key = body.embed_api_key

    _save_settings_json(raw)
    return get_settings(req)


@router.post("/test-connection")
def test_connection(req: Request, body: TestConnectionBody) -> dict:
    """P6-3：最小请求实测（chat / embed / ollama），翻译成大白话错误。

    不写盘任何 key；全部成功或任一成功时记录 last_conn_test（settings.json 顶层）。
    """
    state = req.app.state.library
    base_url = (body.base_url or "").rstrip("/")
    results: dict = {}
    ok_count = 0

    if body.is_ollama or "ollama" in base_url:
        results["ollama"] = test_ollama(base_url, body.chat_model)
        if results["ollama"]["ok"]:
            ok_count += 1
    else:
        # key 留空 → 用已配置 key 实测（不写盘；便于「留空不修改」场景验证现状）
        chat_key = body.api_key or state.cfg.modelscope.api_key
        embed_key = body.embed_api_key or body.api_key or state.cfg.modelscope.embed_api_key or state.cfg.modelscope.api_key
        results["chat"] = test_chat(base_url, body.chat_model, chat_key)
        if results["chat"]["ok"]:
            ok_count += 1
        # 嵌入：有模型名才测（空=自动 ModelScope 免费，不在此测）
        if body.embed_model:
            embed_base = (body.embed_base_url or base_url).rstrip("/")
            results["embed"] = test_embed(embed_base, body.embed_model, embed_key)
            if results["embed"]["ok"]:
                ok_count += 1

    if ok_count > 0:
        raw = _load_settings_json()
        _touch_conn_test(raw)
        _save_settings_json(raw)

    return {
        "results": results,
        "ok": ok_count > 0,
        "tested_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
