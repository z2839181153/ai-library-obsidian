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


# FakeLLM 默认卡片 JSON：字段与设计文档 §5.5 / §6.3 对齐
DEFAULT_CARD_JSON = {
    "title": "AI图书馆测试书",
    "one_liner": "这是一本关于人工智能的测试书。",
    "summary": "本书介绍人工智能基础概念与方法，包括检索增强、知识库构建等。",
    "chapters": [
        {"title": "第一章 基础", "summary": "介绍基本概念", "ref": "1"},
        {"title": "第二章 检索", "summary": "混合检索方法", "ref": "2"},
    ],
    "concepts": [
        {"term": "混合检索", "definition": "词法与向量融合检索", "ref": "2"},
        {"term": "图书卡片", "definition": "书级摘要与索引", "ref": "1"},
    ],
    "tags": ["人工智能", "知识库"],
    "distill_value": 82,
    "category": "methodology",
    "distill_reason": "方法论密度高、有可执行框架，建议蒸馏",
    "room": "人工智能",
    "shelf": "LLM与Agent",
}


class FakeLLM:
    """确定性伪 chat 客户端：返回固定卡片 JSON（可注入 responder 定制）。

    chat_json 返回 DEFAULT_CARD_JSON 的副本；chat 返回简单文本。
    responder 签名：fn(prompt, system) -> dict（用于个别测试定制）。
    """

    def __init__(self, responder=None):
        self.responder = responder
        self.calls: list[str] = []

    def chat(self, messages: list[dict], temperature: float = 0.3) -> str:
        self.calls.append(messages[-1]["content"])
        return "（AI图书馆测试回答）参考 [[catalog/bk_test]]。"

    def chat_json(self, prompt: str, system: str | None = None) -> dict:
        self.calls.append(prompt)
        if self.responder is not None:
            return self.responder(prompt, system)
        return dict(DEFAULT_CARD_JSON)


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
def make_library_p1(tmp_path):
    """P1 完整状态工厂：make_library_p1() -> LibraryState（FakeEmbed + FakeLLM）。"""

    def _make():
        cfg = AppConfig()
        cfg.paths.data_dir = tmp_path / "data"
        cfg.paths.vault_dir = tmp_path / "vault"
        return build_state(cfg, embed=FakeEmbed(), llm=FakeLLM())

    return _make


@pytest.fixture()
def client(tmp_path):
    """注入 FakeEmbed + FakeLLM 的 API TestClient（挂全部 P1 路由）。"""
    cfg = AppConfig()
    cfg.paths.data_dir = tmp_path / "data"
    cfg.paths.vault_dir = tmp_path / "vault"
    state = build_state(cfg, embed=FakeEmbed(), llm=FakeLLM())
    from fastapi import FastAPI

    from app import __version__
    from app.api import (actions, ask, books, conversations, dashboard, distill,
                         floors, health, index, ingest, purchase, settings, skills, starmap, ws)

    app = FastAPI(title="AI Library Test", version=__version__)
    app.state.library = state
    app.include_router(health.router, prefix="/api")
    app.include_router(index.router, prefix="/api")
    app.include_router(books.router, prefix="/api")
    app.include_router(actions.router, prefix="/api")
    app.include_router(floors.router, prefix="/api")
    app.include_router(ask.router, prefix="/api")
    app.include_router(distill.router, prefix="/api")
    app.include_router(skills.router, prefix="/api")
    app.include_router(ingest.router, prefix="/api")
    app.include_router(settings.router, prefix="/api")
    app.include_router(dashboard.router, prefix="/api")
    app.include_router(purchase.router, prefix="/api")
    app.include_router(conversations.router, prefix="/api")
    app.include_router(starmap.router, prefix="/api")
    app.include_router(ws.router)
    with TestClient(app) as c:
        yield c
