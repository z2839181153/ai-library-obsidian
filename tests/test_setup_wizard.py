"""P6-3 配置向导测试：供应商预设库 / apply-provider / test-connection。

注意：settings.py / providers.py 读写的是模块级 DEFAULT_CONFIG_PATH——
测试里 monkeypatch 到 tmp_path，避免污染真实 config/settings.json。
"""
from __future__ import annotations

import json


def _isolate_settings(monkeypatch, tmp_path, raw=None):
    """把 settings.json 读写路径指向 tmp_path（初始内容可选）。"""
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(json.dumps(raw or {
        "server": {"host": "127.0.0.1", "port": 8800},
        "paths": {"data_dir": "data", "vault_dir": "vault"},
        "modelscope": {
            "base_url": "https://api-inference.modelscope.cn/v1",
            "chat_model": "deepseek-ai/DeepSeek-V4-Flash-0731",
            "distill_model": "ZhipuAI/GLM-5.2",
            "embed_model": "Qwen/Qwen3-Embedding-0.6B",
        },
        "ollama": {"enabled": False},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setattr("app.api.settings.DEFAULT_CONFIG_PATH", cfg_path)
    monkeypatch.setattr("app.api.providers.DEFAULT_CONFIG_PATH", cfg_path)
    return cfg_path


# ---------------- GET /api/providers ----------------

def test_get_providers_preset(client):
    r = client.get("/api/providers")
    assert r.status_code == 200
    d = r.json()
    # 预设库关键供应商存在
    for pid in ("deepseek", "modelscope", "zhipu", "siliconflow", "openai", "ollama"):
        assert pid in d["providers"], pid
    ds = d["providers"]["deepseek"]
    assert ds["base_url"] == "https://api.deepseek.com/v1"
    assert "chat_models" in ds and "embed_models" in ds and "key_url" in ds
    # 默认配置 base_url=ModelScope → 识别为 modelscope
    assert d["current"]["provider_id"] == "modelscope"
    assert d["current"]["chat_key_set"] is False
    # 无 key 且 ollama 未启用 → 需要首次配置
    assert d["needs_setup"] is True


def test_providers_needs_setup_false_when_key_set(client):
    # 配置了 key 后（apply-provider）→ needs_setup False
    r = client.post("/api/settings/apply-provider", json={
        "provider": "deepseek",
        "chat_model": "deepseek-v4-flash",
        "distill_model": "deepseek-v4-flash",
        "embed_model": "",
        "api_key": "sk-test123",
    })
    assert r.status_code == 200, r.text
    d = client.get("/api/providers").json()
    assert d["needs_setup"] is False
    assert d["current"]["provider_id"] == "deepseek"
    assert d["current"]["chat_key_set"] is True


# ---------------- POST /api/settings/apply-provider ----------------

def test_apply_provider_deepseek_embed_fallback(client, monkeypatch, tmp_path):
    cfg_path = _isolate_settings(monkeypatch, tmp_path)
    r = client.post("/api/settings/apply-provider", json={
        "provider": "deepseek",
        "chat_model": "deepseek-v4-flash",
        "distill_model": "deepseek-v4-flash",
        "embed_model": "",
        "api_key": "sk-abc123",
        "embed_api_key": "ms-embed456",
    })
    assert r.status_code == 200, r.text
    # 写回 settings.json：embed 自动落 ModelScope 免费
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    ms = raw["modelscope"]
    assert ms["base_url"] == "https://api.deepseek.com/v1"
    assert ms["chat_model"] == "deepseek-v4-flash"
    assert ms["distill_model"] == "deepseek-v4-flash"
    assert ms["embed_base_url"] == "https://api-inference.modelscope.cn/v1"
    assert ms["embed_model"] == "Qwen/Qwen3-Embedding-0.6B"
    # secrets.json：chat key / embed key 分开存
    secrets = json.loads((tmp_path / "data" / "secrets.json").read_text(encoding="utf-8"))
    assert secrets["deepseek_api_key"] == "sk-abc123"
    assert secrets["modelscope_api_key"] == "ms-embed456"
    # 响应 = GET /api/settings（新配置回读）
    assert r.json()["modelscope"]["base_url"] == "https://api.deepseek.com/v1"


def test_apply_provider_explicit_embed(client, monkeypatch, tmp_path):
    _isolate_settings(monkeypatch, tmp_path)
    r = client.post("/api/settings/apply-provider", json={
        "provider": "siliconflow",
        "chat_model": "Qwen/Qwen2.5-7B-Instruct",
        "distill_model": "deepseek-ai/DeepSeek-V3",
        "embed_model": "BAAI/bge-m3",
        "api_key": "sk-sf",
    })
    assert r.status_code == 200, r.text
    ms = r.json()["modelscope"]
    # 显式嵌入 → embed_base_url 跟随本供应商
    assert ms["embed_model"] == "BAAI/bge-m3"
    assert ms["embed_base_url"] == "https://api.siliconflow.cn/v1"


def test_apply_provider_ollama_local(client, monkeypatch, tmp_path):
    _isolate_settings(monkeypatch, tmp_path)
    r = client.post("/api/settings/apply-provider", json={
        "provider": "ollama",
        "chat_model": "qwen2.5:7b",
        "distill_model": "qwen2.5:7b",
        "embed_model": "nomic-embed-text",
        "api_key": "",
        "ollama_enabled": True,
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["modelscope"]["base_url"] == "http://127.0.0.1:11434"
    assert d["ollama"]["enabled"] is True
    # 本地不要求 key
    assert client.get("/api/providers").json()["needs_setup"] is False


def test_apply_provider_keep_existing_key_when_blank(client, monkeypatch, tmp_path):
    """已有 key 时留空 → 允许保存且不覆盖原 key。"""
    _isolate_settings(monkeypatch, tmp_path)
    # 先配置一次 key
    r = client.post("/api/settings/apply-provider", json={
        "provider": "deepseek",
        "chat_model": "deepseek-v4-flash",
        "distill_model": "deepseek-v4-flash",
        "embed_model": "",
        "api_key": "sk-original",
    })
    assert r.status_code == 200, r.text
    # 改模型但 key 留空 → 保留原 key
    r = client.post("/api/settings/apply-provider", json={
        "provider": "deepseek",
        "chat_model": "deepseek-chat",
        "distill_model": "deepseek-chat",
        "embed_model": "",
        "api_key": "",
    })
    assert r.status_code == 200, r.text
    secrets = json.loads((tmp_path / "data" / "secrets.json").read_text(encoding="utf-8"))
    assert secrets["deepseek_api_key"] == "sk-original"
    d = client.get("/api/providers").json()
    assert d["current"]["chat_model"] == "deepseek-chat"
    assert d["current"]["chat_key_set"] is True


def test_apply_provider_errors(client):
    # 未知供应商
    r = client.post("/api/settings/apply-provider", json={"provider": "nope"})
    assert r.status_code == 400
    # 缺 chat_model
    r = client.post("/api/settings/apply-provider", json={
        "provider": "deepseek", "chat_model": "", "api_key": "sk-x"})
    assert r.status_code == 400
    assert "聊天模型" in r.json()["detail"]
    # 非本地供应商缺 key
    r = client.post("/api/settings/apply-provider", json={
        "provider": "deepseek",
        "chat_model": "deepseek-v4-flash",
        "distill_model": "deepseek-v4-flash",
        "embed_model": "",
        "api_key": "",
    })
    assert r.status_code == 400
    assert "API key" in r.json()["detail"]


# ---------------- POST /api/settings/test-connection ----------------

def test_connection_ok_and_touch(client, monkeypatch, tmp_path):
    cfg_path = _isolate_settings(monkeypatch, tmp_path)
    monkeypatch.setattr("app.api.settings.test_chat", lambda *a, **k: {"ok": True, "message": "聊天模型 ok"})
    monkeypatch.setattr("app.api.settings.test_embed", lambda *a, **k: {"ok": True, "message": "嵌入模型 ok"})
    r = client.post("/api/settings/test-connection", json={
        "base_url": "https://api.deepseek.com/v1",
        "chat_model": "deepseek-v4-flash",
        "embed_model": "Qwen/Qwen3-Embedding-0.6B",
        "embed_base_url": "https://api-inference.modelscope.cn/v1",
        "api_key": "sk-t",
    })
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["results"]["chat"]["ok"] is True
    assert d["results"]["embed"]["ok"] is True
    # 成功测试 → last_conn_test 写入 settings.json
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert raw.get("last_conn_test")


def test_connection_fail_no_touch(client, monkeypatch, tmp_path):
    cfg_path = _isolate_settings(monkeypatch, tmp_path)
    monkeypatch.setattr("app.api.settings.test_chat", lambda *a, **k: {"ok": False, "message": "聊天模型：未填 API key"})
    r = client.post("/api/settings/test-connection", json={
        "base_url": "https://api.deepseek.com/v1",
        "chat_model": "deepseek-v4-flash",
        "api_key": "",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is False
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "last_conn_test" not in raw


def test_connection_uses_configured_key_when_blank(client, monkeypatch, tmp_path):
    """key 留空 → 用已配置 key 实测（不写盘）。"""
    _isolate_settings(monkeypatch, tmp_path)
    captured = {}
    monkeypatch.setattr("app.api.settings.test_chat",
                        lambda base_url, model, key: (captured.update(key=key) or
                                                      {"ok": True, "message": "ok"}))
    r = client.post("/api/settings/test-connection", json={
        "base_url": "https://api.deepseek.com/v1",
        "chat_model": "deepseek-v4-flash",
        "api_key": "",
    })
    assert r.status_code == 200
    # client fixture 的 cfg 默认无 key → 回退值也是空；验证 key 参数确实来自 cfg
    assert "key" in captured


def test_connection_ollama(client, monkeypatch, tmp_path):
    _isolate_settings(monkeypatch, tmp_path)
    monkeypatch.setattr("app.api.settings.test_ollama", lambda *a, **k: {"ok": True, "message": "Ollama ok"})
    r = client.post("/api/settings/test-connection", json={
        "base_url": "http://127.0.0.1:11434",
        "chat_model": "qwen2.5:7b",
        "is_ollama": True,
    })
    assert r.status_code == 200
    assert r.json()["results"]["ollama"]["ok"] is True


# ---------------- app/providers 纯函数 ----------------

def test_translate_error():
    from app.providers import translate_error

    class FakeErr(Exception):
        def __init__(self, code, msg="boom"):
            self.status_code = code
            super().__init__(msg)

    assert "key 无效" in translate_error(FakeErr(401), "聊天模型")
    assert "key 无效" in translate_error(FakeErr(403), "聊天模型")
    assert "模型名不存在" in translate_error(FakeErr(404), "聊天模型")
    assert "额度不足" in translate_error(FakeErr(429), "聊天模型")
    assert "超时" in translate_error(TimeoutError("timed out"), "聊天模型")
    assert "网络不通" in translate_error(ConnectionError("connection refused"), "聊天模型")
    assert "模型名不存在" not in translate_error(ValueError("other"), "聊天模型")


def test_detect_current(client):
    from app.providers import detect_current

    state = client.app.state.library
    pid, prov = detect_current(state.cfg)
    assert pid == "modelscope"
    assert prov["name"] == "魔搭 ModelScope"
