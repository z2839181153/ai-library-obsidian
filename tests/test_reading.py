"""P5-4 阅览室顶层入口测试：最近阅读（last_read_at 记录 + recent_read 列表）。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import AppConfig
from app.state import build_state


def _make_state(tmp_path):
    cfg = AppConfig()
    cfg.paths.data_dir = tmp_path / "data"
    cfg.paths.vault_dir = tmp_path / "vault"
    from tests.conftest import FakeEmbed, FakeLLM

    return build_state(cfg, embed=FakeEmbed(), llm=FakeLLM())


def _make_app(state):
    from fastapi import FastAPI

    from app import __version__
    from app.api import (actions, archive, ask, books, conversations, dashboard,
                         distill, floors, health, index, ingest, purchase, settings,
                         skills, starmap, ws)

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
    app.include_router(archive.router, prefix="/api")
    app.include_router(ws.router)
    return app


def _ingest_one(state, tmp_path, name="测试书.md", text="# 测试书\n\n## 章节\n\n内容。\n"):
    from app.ingest.cleaner import ingest_file

    from app.api.ingest import _register_and_index

    src = tmp_path / name
    src.write_text(text, encoding="utf-8")
    ingested = ingest_file(src, state.cfg.paths.data_dir / "archive" / "raw")
    return _register_and_index(state, ingested)["book"]["book_id"]


def test_content_marks_last_read(tmp_path):
    """读取原文后 last_read_at 非空，recent_read 列表返回该书。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    c = TestClient(app)

    bid = _ingest_one(state, tmp_path)
    # 未读：recent_read 为空
    assert c.get("/api/books?recent_read=true").json()["books"] == []

    r = c.get(f"/api/books/{bid}/content")
    assert r.status_code == 200

    books = c.get("/api/books?recent_read=true").json()["books"]
    assert len(books) == 1
    assert books[0]["book_id"] == bid
    assert books[0]["last_read_at"]


def test_recent_read_ordered_by_time(tmp_path):
    """两次读取：后读的书排前面。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    c = TestClient(app)

    b1 = _ingest_one(state, tmp_path, "甲.md", "# 甲\n\n## 章\n\n甲内容。\n")
    b2 = _ingest_one(state, tmp_path, "乙.md", "# 乙\n\n## 章\n\n乙内容。\n")

    c.get(f"/api/books/{b1}/content")
    c.get(f"/api/books/{b2}/content")

    books = c.get("/api/books?recent_read=true").json()["books"]
    assert [b["book_id"] for b in books] == [b2, b1]


def test_recent_read_excludes_deleted_and_limit(tmp_path):
    """recent_read 排除删除书；limit 生效。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    c = TestClient(app)

    b1 = _ingest_one(state, tmp_path, "甲.md", "# 甲\n\n## 章\n\n甲内容。\n")
    b2 = _ingest_one(state, tmp_path, "乙.md", "# 乙\n\n## 章\n\n乙内容。\n")
    c.get(f"/api/books/{b1}/content")
    c.get(f"/api/books/{b2}/content")

    # limit=1 → 只有最近一本
    books = c.get("/api/books?recent_read=true&limit=1").json()["books"]
    assert [b["book_id"] for b in books] == [b2]

    # 软删除 b2 → recent_read 只剩 b1
    c.post(f"/api/books/{b2}/delete")
    books = c.get("/api/books?recent_read=true").json()["books"]
    assert [b["book_id"] for b in books] == [b1]
