"""T1：P1 schema 扩展 + 默认楼层种子。"""
from __future__ import annotations

import sqlite3

from app.db.schema import connect, floor_for_media_type

P1_TABLES = {
    "floors", "rooms", "shelves", "catalog_cards", "actions",
    "conversations", "messages",
}


def _conn(tmp_path):
    return connect(tmp_path / "data" / "t.db")


def test_p1_tables_exist(tmp_path):
    conn = _conn(tmp_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert P1_TABLES <= names


def test_default_floors_seeded(tmp_path):
    conn = _conn(tmp_path)
    floors = [dict(r) for r in conn.execute("SELECT * FROM floors ORDER BY ord")]
    assert [f["code"] for f in floors] == ["1F", "2F", "3F", "4F"]
    assert floors[0]["name"] == "电子书"
    assert floors[2]["name"] == "聊天记录"


def test_seed_idempotent(tmp_path):
    c1 = _conn(tmp_path)
    c2 = _conn(tmp_path)
    assert c1.execute("SELECT COUNT(*) c FROM floors").fetchone()["c"] == 4
    assert c2.execute("SELECT COUNT(*) c FROM floors").fetchone()["c"] == 4


def test_floor_for_media_type():
    assert floor_for_media_type("pdf") == "1F"
    assert floor_for_media_type("markdown") == "1F"
    assert floor_for_media_type("html") == "2F"
    assert floor_for_media_type("chat") == "3F"
    assert floor_for_media_type("video") == "4F"
    assert floor_for_media_type("unknown") is None
    assert floor_for_media_type("") is None
