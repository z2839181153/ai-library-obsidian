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
    def __init__(self, config: AppConfig, model: str | None = None, timeout: float = 60.0,
                 max_retries: int | None = None, retry_base: float | None = None,
                 retry_max: float | None = None, retry_on_429: bool | None = None):
        self.cfg = config
        self.model = model or config.modelscope.chat_model
        self.timeout = timeout
        # 重试参数可配（settings.json modelscope.chat_retries 等；429 默认不重试直接降级）
        self.max_retries = max_retries if max_retries is not None else config.modelscope.chat_retries
        self.retry_base = retry_base if retry_base is not None else config.modelscope.chat_retry_base
        self.retry_max = retry_max if retry_max is not None else config.modelscope.chat_retry_max
        self.retry_on_429 = retry_on_429 if retry_on_429 is not None else config.modelscope.chat_retry_on_429
        self._client = None

    @staticmethod
    def _is_fatal(e: Exception) -> bool:
        """不可重试的错误码：鉴权失败/资源不存在/限流/余额不足。

        ModelScope 免费 API 的 429（insufficient balance / rate limit）重试无意义，
        立即降级让上层走无 LLM 路径，避免长时间挂起（P3 手工验收实测 50 分钟未返回）。
        """
        code = getattr(e, "status_code", None)
        return code in (401, 403, 404, 429)

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
        """普通对话补全，返回 assistant 文本。失败/空响应重试。

        ModelScope 免费 API 偶发空响应（choices=None，不抛异常）或限流——
        显式检测并当作失败重试。间隔按 attempt 递增（retry_base→retry_max 上限），
        最多 max_retries 次（默认 8）：免费 API 忙碌时可能连续多次空响应，短间隔重试无效。
        401/403/404/429 视为不可重试：429（余额不足/限流）重试无意义，立即抛
        LLMUnavailable 让上层降级（除非 retry_on_429 显式开启）。
        """
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
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
                if self._is_fatal(e) and not self.retry_on_429:
                    code = getattr(e, "status_code", None)
                    raise LLMUnavailable(
                        f"LLM 调用失败（HTTP {code}，不可重试，已降级）: {e}"
                    ) from e
                if attempt >= self.max_retries:
                    break
                time.sleep(min(self.retry_base * (attempt + 1), self.retry_max))
        raise LLMUnavailable(f"chat API 调用失败: {last_err}")

    def chat_json(self, prompt: str, system: str | None = None,
                  max_tokens: int = 4096) -> dict:
        """让模型输出 JSON 并解析；解析失败重试，最终失败抛 LLMOutputError。

        prompt 需自行要求"只输出 JSON"；这里不强依赖 response_format
        （ModelScope 免费模型兼容性不一），靠后处理容错。
        max_tokens 默认 4096：JSON 长输出（如图书卡片）不会被截断。

        容错策略（实测：免费模型偶发输出被截断 / 尾随杂质）：
        - 解析失败自动重试（最多 3 次），重试时放宽 max_tokens（≥8192），
          避免大书卡片（长 summary）被截断成不完整 JSON；
        - 每次重试都走 parse_json_loose（去围栏 / 花括号配对截断）。
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        last_raw: str | None = None
        for attempt in range(3):
            mt = max_tokens if attempt == 0 else max(max_tokens, 8192)
            raw = self.chat(messages, temperature=0.2, max_tokens=mt)
            data = parse_json_loose(raw)
            if data is not None:
                return data
            last_raw = raw
        raise LLMOutputError(f"模型输出无法解析为 JSON: {(last_raw or '')[:300]}")

    def chat_stream(self, messages: list[dict], temperature: float = 0.3,
                    max_tokens: int = 1024):
        """流式对话补全：逐段 yield assistant 文本（P4-5）。

        - 首次 create 失败或整段流为空 → 按 chat() 相同重试策略
        - 401/403/404/429 → 立即抛 LLMUnavailable（429 默认不重试，见 _is_fatal）
        - 流中途断掉（网络抖动）：整个流重试（已 yield 的文本会重复，可接受）
        """
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            collected: list[str] = []
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                )
                for chunk in resp:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    piece = (delta.content or "") if delta else ""
                    if piece:
                        collected.append(piece)
                        yield piece
                if not collected:
                    raise ValueError("模型流式返回空响应（choices 为空）")
                return
            except Exception as e:  # noqa: BLE001
                last_err = e
                if self._is_fatal(e) and not self.retry_on_429:
                    code = getattr(e, "status_code", None)
                    raise LLMUnavailable(
                        f"LLM 调用失败（HTTP {code}，不可重试，已降级）: {e}"
                    ) from e
                if attempt >= self.max_retries:
                    break
                time.sleep(min(self.retry_base * (attempt + 1), self.retry_max))
        raise LLMUnavailable(f"chat_stream API 调用失败: {last_err}")


def parse_json_loose(text: str) -> dict | None:
    """宽松解析模型 JSON 输出：去围栏、花括号配对截断，失败返回 None。

    容错点（实测免费模型输出形态）：
    - ```json ... ``` 围栏包裹；
    - JSON 前后有说明文字/尾随杂质；
    - summary 等字符串值内含 { }，简单 rfind('}') 会误截——改用深度扫描，
      从第一个 '{' 起按配对闭合截取完整对象；
    - 对象中途被截断（max_tokens 不够）→ 无法配对，返回 None（上层重试）。
    """
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
    # 深度扫描：从每个 '{' 起找配对闭合的完整对象
    start = t.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = t.find("{", start + 1)
    return None
