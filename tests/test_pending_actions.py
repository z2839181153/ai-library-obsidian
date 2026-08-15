"""补书室操作测试：建议区 → 送回待定区（unclassify）；待定区/已上架书删除（软删除）。

对应需求：
- 补书室建议区的书「送回待定区」→ POST /api/books/{id}/unclassify
- 待定区 / 已上架的书「删除」→ POST /api/books/{id}/delete（P4-2 已有，这里补状态分支）
"""
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


def _classify(state, client, book_id):
    """生成分类建议 → status=reviewing。"""
    r = client.post(f"/api/books/{book_id}/classify", json={})
    assert r.status_code == 200, r.text
    return r.json()


def test_unclassify_reviewing_to_incoming(tmp_path):
    """建议区书送回待定区：状态 reviewing→incoming、建议清空、卡片解除关联、账本记录。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    bid = _ingest_one(state, tmp_path)
    with TestClient(app) as c:
        _classify(state, c, bid)
        d = c.get(f"/api/books/{bid}").json()
        assert d["book"]["status"] == "reviewing"
        assert d["book"]["suggest_room"]  # 有建议

        r = c.post(f"/api/books/{bid}/unclassify")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "incoming"

        d = c.get(f"/api/books/{bid}").json()
        assert d["book"]["status"] == "incoming"
        assert d["book"]["suggest_floor"] == ""
        assert d["book"]["suggest_room"] == ""
        assert d["book"]["suggest_shelf"] == ""
        assert d["book"]["card_path"] == ""  # 解除卡片关联

        # 操作账本记录 unclassify
        acts = state.repo.list_actions(target_type="book", target_id=bid, limit=10)
        assert any(a["action_type"] == "unclassify" for a in acts)

        # 列表视角：建议区消失、待定区出现
        all_books = c.get("/api/books").json()["books"]
        by_id = {b["book_id"]: b for b in all_books}
        assert by_id[bid]["status"] == "incoming"
        assert by_id[bid]["suggest"]["room"] == ""


def test_unclassify_after_shelved_rejected(tmp_path):
    """已上架（shelved）书不能送回待定区 → 400。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    bid = _ingest_one(state, tmp_path)
    with TestClient(app) as c:
        _classify(state, c, bid)
        # 确认上架（用建议）
        r = c.post(f"/api/books/{bid}/confirm", json={})
        assert r.status_code == 200, r.text
        assert c.post(f"/api/books/{bid}/unclassify").status_code == 400


def test_unclassify_incoming_rejected(tmp_path):
    """待定区（incoming，无建议）书送不回去 → 400。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    bid = _ingest_one(state, tmp_path)
    with TestClient(app) as c:
        assert c.get(f"/api/books/{bid}").json()["book"]["status"] == "incoming"
        assert c.post(f"/api/books/{bid}/unclassify").status_code == 400


def test_unclassify_not_found(tmp_path):
    """不存在的书 → 404。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    with TestClient(app) as c:
        assert c.post("/api/books/nope/unclassify").status_code == 404


def test_delete_incoming_and_reviewing(tmp_path):
    """待定区（incoming）与建议区（reviewing）的书均可删除 → 软删除进档案馆。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    b1 = _ingest_one(state, tmp_path, "甲书.md", "# 甲书\n\n## 章\n\n内容\n")
    b2 = _ingest_one(state, tmp_path, "乙书.md", "# 乙书\n\n## 章\n\n内容\n")
    with TestClient(app) as c:
        _classify(state, c, b2)  # b2 进建议区

        # incoming 书删除
        r = c.post(f"/api/books/{b1}/delete")
        assert r.status_code == 200
        assert r.json()["book"]["status"] == "deleted"

        # reviewing 书删除
        r = c.post(f"/api/books/{b2}/delete")
        assert r.status_code == 200
        assert r.json()["book"]["status"] == "deleted"

        # 两区都不再出现（按状态过滤）
        inc = c.get("/api/books", params={"status": "incoming"}).json()["books"]
        rev = c.get("/api/books", params={"status": "reviewing"}).json()["books"]
        assert all(b["book_id"] != b1 for b in inc)
        assert all(b["book_id"] != b2 for b in rev)
        deleted = c.get("/api/archive/deleted").json()["items"]
        assert {d["book_id"] for d in deleted} == {b1, b2}


def test_reclassify_after_unclassify_force(tmp_path):
    """送回待定区后可重新生成建议（force=true 覆盖旧卡片）。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    bid = _ingest_one(state, tmp_path)
    with TestClient(app) as c:
        _classify(state, c, bid)
        c.post(f"/api/books/{bid}/unclassify")

        # 旧卡片仍存在（保留参考）→ 幂等会 skip；force=true 重新生成
        r = c.post(f"/api/books/{bid}/classify", json={"force": True})
        assert r.status_code == 200, r.text
        assert r.json()["skipped"] is False
        d = c.get(f"/api/books/{bid}").json()
        assert d["book"]["status"] == "reviewing"
        assert d["book"]["suggest_room"]  # 建议回来了
