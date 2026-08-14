"""LLM Chat 客户端：ModelScope OpenAI 兼容补全（设计文档 §3.2）。

- chat(): 普通对话补全（默认 DeepSeek-V4-Flash）
- chat_json(): 让模型输出 JSON 并容错解析（去 ```json 围栏、取首尾花括号）
- 无 API key → LLMUnavailable（上层降级，与 EmbeddingUnavailable 风格一致）
- 私密内容：调用方负责在 private=1 时跳过本模块
"""
from __future__ import annotations

import json
import re
import time

from app.config import AppConfig


class LLMUnavailable(Exception):
    pass


class LLMOutputError(Exception):
    """模型输出无法解析为 JSON。"""


class ChatClient:
    def __init__(self, config: AppConfig, model: str | None = None, timeout: float = 60.0):
        self.cfg = config
        self.model = model or config.modelscope.chat_model
        self.timeout = timeout
        self._client = None

    @property
    def client(self):
        if self._client is None:
            if not self.cfg.modelscope.api_key:
                raise LLMUnavailable(
                    "未配置 MODELSCOPE_API_KEY（环境变量或 data/secrets.json）"
                )
            from openai import OpenAI

            self._client = OpenAI(
                base_url=self.cfg.modelscope.base_url,
                api_key=self.cfg.modelscope.api_key,
                timeout=self.timeout,
            )
        return self._client

    def chat(self, messages: list[dict], temperature: float = 0.3,
             max_tokens: int = 1024) -> str:
        """普通对话补全，返回 assistant 文本。失败/空响应重试 4 次。

        ModelScope 免费 API 偶发空响应（choices=None，不抛异常）或限流——
        显式检测并当作失败重试（间隔递增 1.5s→12s）；max_tokens 显式给出。
        """
        last_err: Exception | None = None
        for attempt in range(5):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                if not resp.choices or not resp.choices[0].message.content:
                    raise ValueError("模型返回空响应（choices 为空）")
                return resp.choices[0].message.content.strip()
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        raise LLMUnavailable(f"chat API 调用失败: {last_err}")

    def chat_json(self, prompt: str, system: str | None = None) -> dict:
        """让模型输出 JSON 并解析；解析失败抛 LLMOutputError。

        prompt 需自行要求"只输出 JSON"；这里不强依赖 response_format
        （ModelScope 免费模型兼容性不一），靠后处理容错。
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        raw = self.chat(messages, temperature=0.2)
        data = parse_json_loose(raw)
        if data is None:
            raise LLMOutputError(f"模型输出无法解析为 JSON: {raw[:300]}")
        return data


def parse_json_loose(text: str) -> dict | None:
    """宽松解析模型 JSON 输出：去围栏、找首尾花括号、失败返回 None。"""
    t = (text or "").strip()
    if not t:
        return None
    # 去 ```json ... ``` 围栏
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # 取第一个 { 到最后一个 }
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(t[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None
