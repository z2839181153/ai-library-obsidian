"""Embedding 客户端：ModelScope OpenAI 兼容 /embeddings + 本地缓存（设计文档 §3.2 / §7.1）。

- 批量 64 条；失败重试 2 次。
- 缓存：内容 hash -> 向量，存 SQLite embedding_cache 表（避免重复调用免费 API）。
- 无 API key 时抛 EmbeddingUnavailable（上层降级）。
"""
from __future__ import annotations

import hashlib
import time

from app.config import AppConfig
from app.db.repo import Repo


class EmbeddingUnavailable(Exception):
    pass


class EmbeddingClient:
    def __init__(self, config: AppConfig, repo: Repo, batch_size: int = 64):
        self.cfg = config
        self.repo = repo
        self.batch_size = batch_size
        self._client = None

    @property
    def client(self):
        if self._client is None:
            # embedding 可用独立配置（embed_base_url/embed_api_key）；空=跟随 chat 配置
            base_url = self.cfg.modelscope.embed_base_url or self.cfg.modelscope.base_url
            api_key = self.cfg.modelscope.embed_api_key or self.cfg.modelscope.api_key
            if not api_key:
                raise EmbeddingUnavailable(
                    "未配置 embedding API key（环境变量 MODELSCOPE_API_KEY/EMBED_API_KEY 或 data/secrets.json）"
                )
            from openai import OpenAI

            self._client = OpenAI(
                base_url=base_url,
                api_key=api_key,
            )
        return self._client

    def _hash(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def embed_one(self, text: str) -> list[float]:
        """单条 embedding（带缓存）。"""
        h = self._hash(text)
        cached = self.repo.get_embedding(h)
        if cached is not None:
            return cached
        vec = self._call_api([text])[0]
        self.repo.set_embedding(h, vec, self.cfg.modelscope.embed_model)
        return vec

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        """批量 embedding（带缓存）：返回与 texts 等长的向量列表。"""
        results: list[list[float] | None] = [None] * len(texts)
        to_call: list[tuple[int, str]] = []
        for i, t in enumerate(texts):
            h = self._hash(t)
            cached = self.repo.get_embedding(h)
            if cached is not None:
                results[i] = cached
            else:
                to_call.append((i, t))

        for start in range(0, len(to_call), self.batch_size):
            batch = to_call[start : start + self.batch_size]
            vecs = self._call_api([t for _, t in batch])
            for (i, t), v in zip(batch, vecs):
                results[i] = v
                self.repo.set_embedding(self._hash(t), v, self.cfg.modelscope.embed_model)
        return [r for r in results if r is not None]  # type: ignore[return-value]

    def _call_api(self, texts: list[str]) -> list[list[float]]:
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                resp = self.client.embeddings.create(
                    model=self.cfg.modelscope.embed_model,
                    input=texts,
                )
                ordered = sorted(resp.data, key=lambda d: d.index)
                return [d.embedding for d in ordered]
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        raise EmbeddingUnavailable(f"embedding API 调用失败: {last_err}")
