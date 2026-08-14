"""P3 手工验收 3 个 UX 问题的回归测试（2026-08-14 修复）。

1. confirm_shelve 楼层支持按 name 匹配（UI 自由输入名称也能上架）
2. ChatClient 重试参数可配（settings.json modelscope.chat_retries 等）
3. LLM 429（限流/余额不足）立即降级，不再长时间重试挂起
"""
from __future__ import annotations

import json

import pytest

from app.config import AppConfig
from app.llm.chat import ChatClient, LLMUnavailable

from tests.test_shelving import _seed_book


# ---------- 1. 楼层按 name 匹配 ----------

def test_confirm_shelve_floor_by_name(make_library_p1):
    """UI 传楼层名称（如"电子书"）而非 code（1F）也能命中。"""
    state = make_library_p1()
    book_id = _seed_book(state, floor="1F", room="人工智能", shelf="LLM与Agent")

    # 后端按 name 匹配：名字是"电子书"的楼层（code=1F）
    result = state.shelver.confirm_shelve(book_id, floor="电子书", room="人工智能", shelf="LLM与Agent")
    assert result["status"] == "shelved"
    assert result["floor"] == "1F"
    book = state.repo.get_book(book_id)
    assert book["status"] == "shelved"
    assert "1F-电子书" in book["vault_path"]


def test_confirm_shelve_floor_by_unknown_name_raises(make_library_p1):
    """名称也匹配不到 → 报错（与 code 找不到一致）。"""
    state = make_library_p1()
    book_id = _seed_book(state, floor="1F")
    with pytest.raises(ValueError, match="楼层不存在"):
        state.shelver.confirm_shelve(book_id, floor="不存在的楼层", room="人工智能")


# ---------- 2. LLM 重试可配 ----------

class _HttpError(Exception):
    def __init__(self, status_code: int, msg: str = "err"):
        super().__init__(msg)
        self.status_code = status_code


class _FakeCompletions:
    def __init__(self, err: Exception | None = None):
        self.err = err
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.err is not None:
            raise self.err
        # 成功路径：choices 为空也按失败重试（空响应场景）
        return type("R", (), {"choices": []})


class _FakeClient:
    def __init__(self, completions):
        self.chat = type("C", (), {"completions": completions})()


def _make_chat(cfg: AppConfig, completions, **kwargs) -> ChatClient:
    c = ChatClient(cfg, **kwargs)
    c._client = _FakeClient(completions)
    return c


def test_retry_params_read_from_config(tmp_path):
    """settings.json 里 modelscope.chat_retries 等能加载进 AppConfig。"""
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(json.dumps({
        "modelscope": {
            "chat_retries": 3,
            "chat_retry_base": 1.0,
            "chat_retry_max": 5.0,
            "chat_retry_on_429": True,
        }
    }), encoding="utf-8")
    cfg = AppConfig.load(cfg_path)
    assert cfg.modelscope.chat_retries == 3
    assert cfg.modelscope.chat_retry_base == 1.0
    assert cfg.modelscope.chat_retry_max == 5.0
    assert cfg.modelscope.chat_retry_on_429 is True
    # 默认值兜底
    assert AppConfig().modelscope.chat_retries == 8


def test_chat_retries_respected():
    """max_retries 可配：失败 N 次后抛 LLMUnavailable，总调用 = max_retries + 1。"""
    cfg = AppConfig()
    comp = _FakeCompletions(err=_HttpError(500))
    c = _make_chat(cfg, comp, max_retries=2, retry_base=0.001, retry_max=0.01)
    with pytest.raises(LLMUnavailable):
        c.chat([{"role": "user", "content": "hi"}])
    assert comp.calls == 3  # 首次 + 2 次重试


def test_chat_retry_on_429_when_enabled():
    """retry_on_429=True 时 429 也按普通失败重试。"""
    cfg = AppConfig()
    comp = _FakeCompletions(err=_HttpError(429))
    c = _make_chat(cfg, comp, max_retries=1, retry_base=0.001, retry_max=0.01,
                   retry_on_429=True)
    with pytest.raises(LLMUnavailable):
        c.chat([{"role": "user", "content": "hi"}])
    assert comp.calls == 2


# ---------- 3. 429 立即降级（不重试） ----------

def test_429_immediate_degrade_no_retry():
    """默认（retry_on_429=False）：429 只调用 1 次就抛 LLMUnavailable，避免长时间挂起。"""
    cfg = AppConfig()
    comp = _FakeCompletions(err=_HttpError(429, "insufficient balance"))
    c = _make_chat(cfg, comp)  # 默认 max_retries=8
    with pytest.raises(LLMUnavailable) as ei:
        c.chat([{"role": "user", "content": "hi"}])
    assert comp.calls == 1  # 不重试
    assert "429" in str(ei.value)


def test_401_immediate_degrade():
    """鉴权失败同样不重试。"""
    cfg = AppConfig()
    comp = _FakeCompletions(err=_HttpError(401))
    c = _make_chat(cfg, comp)
    with pytest.raises(LLMUnavailable):
        c.chat([{"role": "user", "content": "hi"}])
    assert comp.calls == 1
