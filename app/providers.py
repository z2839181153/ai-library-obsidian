"""P6-3 供应商预设库与连接测试（配置向导后端核心）。

- 预设库：config/providers.json（一份 JSON，前端经 GET /api/providers 获取）
- detect_current()：按 settings.json 的 base_url 反查当前生效供应商
- 连接测试：chat / embedding / ollama 三路最小请求实测，
  把 401/404/429/超时/网络错误翻译成大白话（供前端展示）
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from app.config import ROOT_DIR

PROVIDERS_PATH = ROOT_DIR / "config" / "providers.json"

MODELSCOPE_BASE = "https://api-inference.modelscope.cn/v1"
MODELSCOPE_EMBED = "Qwen/Qwen3-Embedding-0.6B"


def load_providers() -> dict:
    """读取预设库；文件缺失/损坏时返回空 dict（前端仍可手动配置）。"""
    if not PROVIDERS_PATH.exists():
        return {}
    try:
        return json.loads(PROVIDERS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def detect_current(cfg) -> tuple[str | None, dict | None]:
    """按 modelscope.base_url 反查当前生效供应商。

    返回 (provider_id, provider_dict)；未命中返回 (None, None)（自定义配置）。
    """
    providers = load_providers()
    base_url = (cfg.modelscope.base_url or "").rstrip("/")
    for pid, p in providers.items():
        if (p.get("base_url") or "").rstrip("/") == base_url:
            return pid, p
    return None, None


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def _base_err(e: Exception) -> str:
    return (e.__class__.__name__ + ": " + str(e)) if str(e) else e.__class__.__name__


def translate_error(e: Exception, kind: str) -> str:
    """把连接异常翻译成大白话（P6-3 关键体验）。"""
    code = getattr(e, "status_code", None)
    if code in (401, 403):
        return f"{kind}：API key 无效或无权限（HTTP {code}）"
    if code == 404:
        return f"{kind}：模型名不存在或该服务不支持此模型（HTTP 404）"
    if code == 429:
        return f"{kind}：额度不足或请求过于频繁（HTTP 429）"
    text = str(e).lower()
    if "timed out" in text or "timeout" in text or isinstance(e, TimeoutError):
        return f"{kind}：连接超时，请检查网络"
    if "connection" in text or "network" in text or "refused" in text \
            or "resolve" in text or isinstance(e, (ConnectionError, OSError)):
        return f"{kind}：网络不通（无法连接服务），请检查网络或代理"
    return f"{kind}：{_base_err(e)}"


def test_chat(base_url: str, model: str, api_key: str, timeout: float = 8.0) -> dict:
    """最小 chat 请求实测。返回 {ok, message}。"""
    if not api_key:
        return {"ok": False, "message": "聊天模型：未填 API key"}
    if not model:
        return {"ok": False, "message": "聊天模型：未选择模型"}
    try:
        from openai import OpenAI

        client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
        )
        if not resp.choices:
            return {"ok": False, "message": "聊天模型：服务返回空响应"}
        return {"ok": True, "message": f"聊天模型 {model}：连接正常"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": translate_error(e, "聊天模型")}


def test_embed(base_url: str, model: str, api_key: str, timeout: float = 8.0) -> dict:
    """最小 embedding 请求实测。"""
    if not model:
        return {"ok": False, "message": "嵌入模型：未选择模型"}
    if not api_key:
        return {"ok": False, "message": "嵌入模型：未填 API key"}
    try:
        from openai import OpenAI

        client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        resp = client.embeddings.create(model=model, input="hi")
        if not resp.data or not resp.data[0].embedding:
            return {"ok": False, "message": "嵌入模型：服务返回空结果"}
        return {"ok": True, "message": f"嵌入模型 {model}：连接正常"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": translate_error(e, "嵌入模型")}


def test_ollama(base_url: str, model: str, timeout: float = 5.0) -> dict:
    """Ollama 本地探测：GET /api/tags。"""
    if not model:
        return {"ok": False, "message": "Ollama：未选择模型"}
    url = base_url.rstrip("/") + "/api/tags"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = {m.get("name", "") for m in data.get("models", [])}
        if model in models:
            return {"ok": True, "message": f"Ollama {model}：已安装且运行中"}
        hint = f"Ollama 服务正常，但模型 {model} 未安装"
        if models:
            hint += f"（已安装：{', '.join(sorted(models)[:5])}）"
        return {"ok": False, "message": hint}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": translate_error(e, "Ollama")}
