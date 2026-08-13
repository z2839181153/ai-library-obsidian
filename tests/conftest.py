"""pytest 共享夹具。

- 临时目录隔离（tmp_path）
- FakeEmbed：确定性伪向量（不调用真实 API）
- make_library：临时 config + Repo + VectorStore + Indexer + Searcher
- client：注入 FakeEmbed 的 FastAPI TestClient（API 测试用）
"""
from __future__ import annotations

import hashlib
import random

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.db.repo import Repo
from app.retrieval.indexer import Indexer
from app.retrieval.searcher import Searcher
from app.state import build_state
from app.vec.vector_store import VectorStore

DIM = 64


class FakeEmbed:
    """确定性伪 embedding：jieba 词袋哈希（相同词 -> 相近向量）。

    用于无 API key 时验证管线与检索；维度 64，词方向由词 hash 决定。
    """

    def __init__(self, dim: int = DIM):
        self.dim = dim

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_one(self, text: str) -> list[float]:
        return self._vec(text)

    def _vec(self, text: str) -> list[float]:
        import math

        import jieba

        v = [0.0] * self.dim
        for w in jieba.cut(text):
            if not w.strip():
                continue
            h = hashlib.sha256(w.encode("utf-8")).digest()
            idx = h[0] % self.dim
            sign = 1.0 if (h[1] & 1) else -1.0
            v[idx] += sign
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]


@pytest.fixture()
def make_library(tmp_path):
    """工厂：make_library(source_dir=None) -> (indexer, searcher, repo, root)。"""

    def _make(source_dir=None):
        cfg = AppConfig()
        cfg.paths.data_dir = tmp_path / "data"
        cfg.paths.vault_dir = tmp_path / "vault"
        repo = Repo(cfg.paths.data_dir / "library.db")
        vec = VectorStore(cfg.paths.data_dir / "lancedb")
        indexer = Indexer(repo, vec, FakeEmbed())
        searcher = Searcher(repo, vec, FakeEmbed())
        root = source_dir or (tmp_path / "books")
        root.mkdir(parents=True, exist_ok=True)
        return indexer, searcher, repo, root

    return _make


@pytest.fixture()
def client(tmp_path):
    """注入 FakeEmbed 的 API TestClient。"""
    cfg = AppConfig()
    cfg.paths.data_dir = tmp_path / "data"
    cfg.paths.vault_dir = tmp_path / "vault"
    state = build_state(cfg, embed=FakeEmbed())
    from fastapi import FastAPI

    from app import __version__
    from app.api import health, index

    app = FastAPI(title="AI Library Test", version=__version__)
    app.state.library = state
    app.include_router(health.router, prefix="/api")
    app.include_router(index.router, prefix="/api")
    with TestClient(app) as c:
        yield c
