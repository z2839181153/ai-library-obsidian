"""P4-3 阅览室联动测试：/api/books/{id}/related（相关技能 + 相关笔记）。"""
from __future__ import annotations

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
                         floors, health, index, ingest, purchase, settings, skills,
                         starmap, ws)

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


def _ingest(state, tmp_path, name: str, content: str) -> str:
    """写文件 → 入馆索引 → 返回 book_id。"""
    from app.api.ingest import _register_and_index
    from app.ingest.cleaner import ingest_file

    src = tmp_path / f"{name}.md"
    src.write_text(content, encoding="utf-8")
    ingested = ingest_file(src, state.cfg.paths.data_dir / "archive" / "raw")
    result = _register_and_index(state, ingested)
    assert result["created"] is True
    return result["book"]["book_id"]


def _shelve(state, book_id: str, room: str, shelf: str = "入门") -> None:
    state.shelver.confirm_shelve(book_id, floor="1F", room=room, shelf=shelf)


def _add_skill(state, book_id: str, name: str, status: str = "installed") -> str:
    from app.db.repo import new_id

    sk_id = new_id("sk")
    state.repo.upsert_skill({
        "skill_id": sk_id,
        "book_id": book_id,
        "name": name,
        "slug": sk_id,
        "path": f"vault/skills/{book_id}/{sk_id}/SKILL.md",
        "description": f"{name}问答技能",
        "status": status,
    })
    return sk_id


def test_related_404(tmp_path):
    """不存在的书 → 404。"""
    app = _make_app(_make_state(tmp_path))
    with TestClient(app) as c:
        r = c.get("/api/books/nonexist/related")
        assert r.status_code == 404


def test_related_empty(tmp_path):
    """单本书、无同房间无技能 → 空列表 + 房间解析正确。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    bid = _ingest(state, tmp_path, "a", "# 机器学习基础\n\n## 简介\n\n神经网络入门。\n")
    _shelve(state, bid, "人工智能")
    with TestClient(app) as c:
        d = c.get(f"/api/books/{bid}/related").json()
        assert d["room"] == "人工智能"
        assert d["skills"] == []
        assert d["notes"] == []


def test_related_same_room_notes_and_skills(tmp_path):
    """同房间书 + 同书/同房间技能返回；被拒技能不出现；异房间书不出现；不含自己。"""
    state = _make_state(tmp_path)
    app = _make_app(state)

    a = _ingest(state, tmp_path, "a", "# 机器学习基础\n\n## 监督学习\n\n线性回归与分类。\n")
    b = _ingest(state, tmp_path, "b", "# 机器学习进阶\n\n## 无监督\n\n聚类与降维。\n")
    c = _ingest(state, tmp_path, "c", "# 园艺指南\n\n## 种植\n\n浇水与施肥。\n")
    _shelve(state, a, "人工智能")
    _shelve(state, b, "人工智能")
    _shelve(state, c, "园艺")

    sk_a = _add_skill(state, a, "机器学习入门技能")
    sk_b = _add_skill(state, b, "机器学习进阶技能", status="approved")
    sk_rej = _add_skill(state, a, "被拒技能", status="rejected")

    with TestClient(app) as c:
        d = c.get(f"/api/books/{a}/related").json()
        skills = {s["skill_id"]: s for s in d["skills"]}
        assert sk_a in skills and skills[sk_a]["relation"] == "same_book"
        assert sk_b in skills and skills[sk_b]["relation"] == "same_room"
        assert skills[sk_b]["book_title"] == "机器学习进阶"
        assert sk_rej not in skills

        notes = {n["book_id"]: n for n in d["notes"]}
        assert b in notes and notes[b]["relation"] == "same_room"
        assert a not in notes
        assert c not in notes


def test_related_similar_notes(tmp_path):
    """内容/标题词重叠、不同房间的书以 similar（向量/词法命中）出现。"""
    state = _make_state(tmp_path)
    app = _make_app(state)

    a = _ingest(state, tmp_path, "a", "# 机器学习基础\n\n## 简介\n\n机器学习 神经网络 监督学习 分类。\n")
    b = _ingest(state, tmp_path, "b", "# 神经网络详解\n\n## 内容\n\n机器学习 神经网络 监督学习 反向传播。\n")
    _shelve(state, a, "人工智能")
    _shelve(state, b, "深度学习")  # 不同房间 → 只能以 similar 出现

    with TestClient(app) as c:
        d = c.get(f"/api/books/{a}/related").json()
        notes = {n["book_id"]: n for n in d["notes"]}
        assert b in notes
        assert notes[b]["relation"] == "similar"
        assert notes[b]["score"] is not None
        assert notes[b]["snippet"] != ""


def test_related_excludes_deleted(tmp_path):
    """软删除的书不进入相关笔记。"""
    state = _make_state(tmp_path)
    app = _make_app(state)

    a = _ingest(state, tmp_path, "a", "# 机器学习基础\n\n## 简介\n\n机器学习 神经网络。\n")
    b = _ingest(state, tmp_path, "b", "# 机器学习进阶\n\n## 内容\n\n机器学习 神经网络 深度学习。\n")
    _shelve(state, a, "人工智能")
    _shelve(state, b, "人工智能")
    state.repo.soft_delete_book(b)

    with TestClient(app) as c:
        d = c.get(f"/api/books/{a}/related").json()
        notes = [n["book_id"] for n in d["notes"]]
        assert b not in notes
