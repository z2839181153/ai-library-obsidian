"""P4-2 档案馆测试：软删除/恢复/原始副本/日报/蒸馏日志/备份导出。"""
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


def test_archive_summary_empty(tmp_path):
    """空库 summary 全 0，restore_days=30。"""
    app = _make_app(_make_state(tmp_path))
    with TestClient(app) as c:
        d = c.get("/api/archive/summary").json()
        assert d["raw_count"] == 0
        assert d["deleted_count"] == 0
        assert d["restore_days"] == 30


def test_archive_raw_lists_copies(tmp_path):
    """入馆后原始副本出现在 raw 列表，且关联书。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    bid = _ingest_one(state, tmp_path)

    with TestClient(app) as c:
        d = c.get("/api/archive/raw").json()
        assert d["count"] == 1
        item = d["items"][0]
        assert item["book_id"] == bid
        assert item["linked"] is True


def test_soft_delete_and_restore(tmp_path):
    """POST /books/{id}/delete → deleted + 档案馆列表出现；restore → incoming。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    bid = _ingest_one(state, tmp_path)

    with TestClient(app) as c:
        # 删除
        r = c.post(f"/api/books/{bid}/delete")
        assert r.status_code == 200
        assert r.json()["book"]["status"] == "deleted"

        # 档案馆已删除列表
        d = c.get("/api/archive/deleted").json()
        assert d["count"] == 1
        assert d["items"][0]["book_id"] == bid
        assert d["items"][0]["days_left"] == 30

        # 重复删除 → 400
        assert c.post(f"/api/books/{bid}/delete").status_code == 400

        # 恢复
        r = c.post(f"/api/archive/{bid}/restore")
        assert r.status_code == 200
        assert r.json()["book"]["status"] == "incoming"

        d = c.get("/api/archive/deleted").json()
        assert d["count"] == 0

        # 恢复不存在的书 → 404；未删除的书 restore → 400
        assert c.post("/api/archive/nope/restore").status_code == 404
        assert c.post(f"/api/archive/{bid}/restore").status_code == 400


def test_archive_reports_endpoint(tmp_path):
    """日报端点返回（含写入一条）。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    state.repo.insert_report({
        "report_id": "rep_test",
        "date": "2026-08-14",
        "rtype": "system",
        "content": '{"title": "测试日报"}',
    })
    with TestClient(app) as c:
        d = c.get("/api/archive/reports").json()
        assert d["count"] >= 1
        assert any(r["report_id"] == "rep_test" for r in d["reports"])


def test_archive_distill_logs(tmp_path):
    """蒸馏过程记录目录扫描（空目录→0；有文件→列出）。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    with TestClient(app) as c:
        d = c.get("/api/archive/distill-logs").json()
        assert d["count"] == 0

    logs = state.cfg.paths.vault_dir / "archive" / "distill-logs" / "demo-book"
    (logs / "verified.md").parent.mkdir(parents=True, exist_ok=True)
    (logs / "verified.md").write_text("ok", encoding="utf-8")
    (logs / "test-prompts.md").write_text("prompt", encoding="utf-8")

    with TestClient(app) as c:
        d = c.get("/api/archive/distill-logs").json()
        assert d["count"] == 1
        assert d["items"][0]["slug"] == "demo-book"
        assert d["items"][0]["count"] == 2


def test_archive_backup_zip(tmp_path):
    """备份导出 zip 含 data/vault 文件。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    _ingest_one(state, tmp_path)

    with TestClient(app) as c:
        r = c.get("/api/archive/backup")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert b"PK" in r.content[:4]

    # 校验 zip 内容包含 data/library.db 与 archive/raw
    import io
    import zipfile

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert any(n.startswith("data/library.db") for n in names)
    assert any("/archive/raw/" in n for n in names)
