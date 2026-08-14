"""P4-1 占星室测试：/api/starmap 图数据（书/技能/主题/档案/对话 + 关联）。"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.config import AppConfig
from app.state import build_state


def _make_state(tmp_path, embed=None, llm=None):
    cfg = AppConfig()
    cfg.paths.data_dir = tmp_path / "data"
    cfg.paths.vault_dir = tmp_path / "vault"
    from tests.conftest import FakeEmbed, FakeLLM

    return build_state(cfg, embed=embed or FakeEmbed(), llm=llm or FakeLLM())


def _make_app(state):
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
    return app


def test_starmap_empty(tmp_path):
    """空库：返回空 nodes/links 与 0 计数。"""
    app = _make_app(_make_state(tmp_path))
    with TestClient(app) as c:
        r = c.get("/api/starmap")
        assert r.status_code == 200
        d = r.json()
        assert d["nodes"] == []
        assert d["links"] == []
        assert d["counts"]["book"] == 0


def test_starmap_books_and_links(tmp_path):
    """入馆 1 本书（incoming）→ 有书节点 + archive 原始副本节点 + 关联。"""
    state = _make_state(tmp_path)
    app = _make_app(state)

    # 手工入馆：写文件 → ingest 管线
    from app.ingest.cleaner import ingest_file

    from app.api.ingest import _register_and_index

    src = tmp_path / "book.md"
    src.write_text("# 测试书\n\n## 第一章\n\n神经网络介绍。\n", encoding="utf-8")
    ingested = ingest_file(src, state.cfg.paths.data_dir / "archive" / "raw")
    result = _register_and_index(state, ingested)
    assert result["created"] is True
    bid = result["book"]["book_id"]

    with TestClient(app) as c:
        d = c.get("/api/starmap").json()
        types = {n["type"] for n in d["nodes"]}
        assert "book" in types
        assert "archive" in types
        book_node = next(n for n in d["nodes"] if n["type"] == "book")
        assert book_node["id"] == bid
        assert book_node["status"] == "incoming"
        # 书 ↔ 原始副本
        arc_links = [l for l in d["links"] if l["relation"] == "raw_copy"]
        assert any(l["source"] == bid for l in arc_links)


def test_starmap_theme_and_skill_links(tmp_path):
    """上架后 → book↔theme；注册技能后 → book↔skill。"""
    state = _make_state(tmp_path)
    app = _make_app(state)

    # 入馆 + 确认上架
    from app.ingest.cleaner import ingest_file

    from app.api.ingest import _register_and_index

    src = tmp_path / "ml.md"
    src.write_text("# 机器学习\n\n## 基础\n\n监督学习与无监督学习。\n", encoding="utf-8")
    ingested = ingest_file(src, state.cfg.paths.data_dir / "archive" / "raw")
    result = _register_and_index(state, ingested)
    bid = result["book"]["book_id"]

    state.shelver.confirm_shelve(bid, floor="1F", room="人工智能", shelf="入门")

    # 注册一个技能（关联本书）
    from app.db.repo import new_id

    sk_id = new_id("sk")
    state.repo.upsert_skill({
        "skill_id": sk_id,
        "book_id": bid,
        "name": "机器学习技能",
        "slug": "ml-skill",
        "path": f"vault/skills/{bid}/ml-skill/SKILL.md",
        "description": "当用户询问机器学习时使用",
        "status": "approved",
    })

    with TestClient(app) as c:
        d = c.get("/api/starmap").json()
        themes = [n for n in d["nodes"] if n["type"] == "theme"]
        skills = [n for n in d["nodes"] if n["type"] == "skill"]
        assert any(n["name"] == "人工智能" for n in themes)
        assert any(n["id"] == sk_id for n in skills)

        relations = {l["relation"] for l in d["links"]}
        assert "shelved_in" in relations
        assert "distilled" in relations
        # book↔theme 与 book↔skill 都连到这本书
        assert any(l["source"] == bid and l["relation"] == "shelved_in" for l in d["links"])
        assert any(l["source"] == bid and l["relation"] == "distilled" for l in d["links"])


def test_starmap_conversation_links(tmp_path):
    """对话消息引用书 → conversation↔book 关联；归档 → archived。"""
    state = _make_state(tmp_path)
    app = _make_app(state)

    # 造一本书
    from app.ingest.cleaner import ingest_file

    from app.api.ingest import _register_and_index
    from app.api.conversations import append_message, ensure_conversation

    src = tmp_path / "qa.md"
    src.write_text("# 问答书\n\n## 内容\n\n可引用的资料。\n", encoding="utf-8")
    ingested = ingest_file(src, state.cfg.paths.data_dir / "archive" / "raw")
    result = _register_and_index(state, ingested)
    bid = result["book"]["book_id"]

    # 对话引用这本书
    cv_id = ensure_conversation(state, "测试对话")
    append_message(state, cv_id, "assistant", "参考 [[catalog/%s]]" % bid,
                   refs=[{"book_id": bid, "link": f"[[catalog/{bid}]]"}])

    with TestClient(app) as c:
        d = c.get("/api/starmap").json()
        convs = [n for n in d["nodes"] if n["type"] == "conversation"]
        assert any(n["id"] == cv_id for n in convs)
        assert any(l["source"] == cv_id and l["target"] == bid
                   and l["relation"] == "referenced" for l in d["links"])
