"""数据访问层：P0 books/chunks/index_state/embedding_cache + P1 floors/rooms/shelves/catalog_cards/actions。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Iterable, Optional

from app.db.schema import connect


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Repo:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._write_lock = threading.RLock()  # 单写入者（设计文档 §6.9）

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = connect(self.db_path)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ---------- books ----------

    def upsert_book(self, book: dict) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        book = dict(book)
        book.setdefault("created_at", now)
        book["updated_at"] = now
        cols = ", ".join(book.keys())
        marks = ", ".join("?" for _ in book)
        with self._write_lock:
            self.conn.execute(
                f"INSERT INTO books ({cols}) VALUES ({marks}) "
                "ON CONFLICT(book_id) DO UPDATE SET "
                + ", ".join(f"{k}=excluded.{k}" for k in book if k != "book_id"),
                list(book.values()),
            )
            self.conn.commit()

    def get_book(self, book_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM books WHERE book_id=?", (book_id,)
        ).fetchone()
        return dict(row) if row else None

    def book_by_hash(self, content_hash: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM books WHERE content_hash=?", (content_hash,)
        ).fetchone()
        return dict(row) if row else None

    def all_books(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM books")]

    def delete_book(self, book_id: str) -> None:
        with self._write_lock:
            self.conn.execute("DELETE FROM books WHERE book_id=?", (book_id,))
            self.conn.commit()

    # ---------- chunks ----------

    def insert_chunk(self, chunk: dict) -> None:
        with self._write_lock:
            self.conn.execute(
                "INSERT INTO chunks (chunk_id, book_id, section, seq, content, fts_content, token_cnt, vector_id) "
                "VALUES (:chunk_id, :book_id, :section, :seq, :content, :fts_content, :token_cnt, :vector_id)",
                chunk,
            )

    def delete_chunks_by_book(self, book_id: str) -> None:
        with self._write_lock:
            self.conn.execute("DELETE FROM chunks WHERE book_id=?", (book_id,))

    def commit(self) -> None:
        with self._write_lock:
            self.conn.commit()

    # ---------- index_state ----------

    def latest_revision(self) -> Optional[int]:
        row = self.conn.execute(
            "SELECT MAX(revision) AS r FROM index_state"
        ).fetchone()
        return row["r"] if row and row["r"] is not None else 0

    def set_state(self, revision: int, status: str, changed: Iterable[str]) -> None:
        with self._write_lock:
            self.conn.execute(
                "INSERT INTO index_state (revision, active, status, changed_book_ids, built_at) "
                "VALUES (?, 1, ?, ?, ?)",
                (revision, status, json.dumps(list(changed)),
                 time.strftime("%Y-%m-%dT%H:%M:%S+08:00")),
            )
            self.conn.commit()

    def get_state(self) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM index_state ORDER BY revision DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    # ---------- embedding cache ----------

    def get_embedding(self, content_hash: str) -> Optional[list[float]]:
        row = self.conn.execute(
            "SELECT vector, dim FROM embedding_cache WHERE content_hash=?",
            (content_hash,),
        ).fetchone()
        if not row:
            return None
        raw = row["vector"]
        dim = row["dim"]
        import struct

        return list(struct.unpack(f"<{dim}f", raw))

    def set_embedding(self, content_hash: str, vector: list[float], model: str) -> None:
        import struct

        dim = len(vector)
        blob = struct.pack(f"<{dim}f", *vector)
        with self._write_lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO embedding_cache (content_hash, dim, vector, model, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (content_hash, dim, blob, model,
                 time.strftime("%Y-%m-%dT%H:%M:%S+08:00")),
            )
            self.conn.commit()

    # ---------- P1: floors / rooms / shelves ----------

    def list_floors(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM floors ORDER BY ord, created_at"
        )]

    def get_floor(self, floor_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM floors WHERE floor_id=?", (floor_id,)
        ).fetchone()
        return dict(row) if row else None

    def floor_by_code(self, code: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM floors WHERE code=?", (code,)
        ).fetchone()
        return dict(row) if row else None

    def insert_floor(self, floor: dict) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        with self._write_lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO floors (floor_id, name, code, media_type, description, ord, custom, created_at) "
                "VALUES (:floor_id, :name, :code, :media_type, :description, :ord, :custom, :created_at)",
                {**floor, "created_at": now},
            )
            self.conn.commit()

    def list_rooms(self, floor_id: Optional[str] = None) -> list[dict]:
        if floor_id:
            rows = self.conn.execute(
                "SELECT * FROM rooms WHERE floor_id=? ORDER BY ord, created_at", (floor_id,)
            )
        else:
            rows = self.conn.execute("SELECT * FROM rooms ORDER BY ord, created_at")
        return [dict(r) for r in rows]

    def get_room(self, room_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM rooms WHERE room_id=?", (room_id,)
        ).fetchone()
        return dict(row) if row else None

    def room_by_name(self, floor_id: str, name: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM rooms WHERE floor_id=? AND name=?", (floor_id, name)
        ).fetchone()
        return dict(row) if row else None

    def insert_room(self, room: dict) -> str:
        """插入房间；同楼层同名已存在则直接返回已有 room_id。"""
        existing = self.room_by_name(room["floor_id"], room["name"])
        if existing:
            return existing["room_id"]
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        room_id = room.get("room_id") or new_id("rm")
        with self._write_lock:
            self.conn.execute(
                "INSERT INTO rooms (room_id, floor_id, name, description, ord, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (room_id, room["floor_id"], room["name"], room.get("description", ""),
                 room.get("ord", 0), now),
            )
            self.conn.commit()
        return room_id

    def list_shelves(self, room_id: Optional[str] = None) -> list[dict]:
        if room_id:
            rows = self.conn.execute(
                "SELECT * FROM shelves WHERE room_id=? ORDER BY ord, created_at", (room_id,)
            )
        else:
            rows = self.conn.execute("SELECT * FROM shelves ORDER BY ord, created_at")
        return [dict(r) for r in rows]

    def get_shelf(self, shelf_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM shelves WHERE shelf_id=?", (shelf_id,)
        ).fetchone()
        return dict(row) if row else None

    def shelf_by_name(self, room_id: str, name: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM shelves WHERE room_id=? AND name=?", (room_id, name)
        ).fetchone()
        return dict(row) if row else None

    def insert_shelf(self, shelf: dict) -> str:
        """插入书架；同房间同名已存在则直接返回已有 shelf_id。"""
        existing = self.shelf_by_name(shelf["room_id"], shelf["name"])
        if existing:
            return existing["shelf_id"]
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        shelf_id = shelf.get("shelf_id") or new_id("sh")
        with self._write_lock:
            self.conn.execute(
                "INSERT INTO shelves (shelf_id, room_id, name, description, ord, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (shelf_id, shelf["room_id"], shelf["name"], shelf.get("description", ""),
                 shelf.get("ord", 0), now),
            )
            self.conn.commit()
        return shelf_id

    # ---------- P1: catalog_cards ----------

    def upsert_card(self, card: dict) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        card = dict(card)
        card.setdefault("generated_at", now)
        cols = ", ".join(card.keys())
        marks = ", ".join("?" for _ in card)
        with self._write_lock:
            self.conn.execute(
                f"INSERT OR REPLACE INTO catalog_cards ({cols}) VALUES ({marks})",
                list(card.values()),
            )
            self.conn.commit()

    def get_card(self, book_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM catalog_cards WHERE book_id=?", (book_id,)
        ).fetchone()
        return dict(row) if row else None

    def delete_card(self, book_id: str) -> None:
        with self._write_lock:
            self.conn.execute("DELETE FROM catalog_cards WHERE book_id=?", (book_id,))
            self.conn.commit()

    # ---------- P1: actions（操作账本） ----------

    def insert_action(self, action: dict) -> str:
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        act_id = action.get("act_id") or new_id("act")
        with self._write_lock:
            self.conn.execute(
                "INSERT INTO actions (act_id, agent, action_type, target_type, target_id, params, undo_params, status, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (act_id, action.get("agent", "system"), action.get("action_type"),
                 action.get("target_type"), action.get("target_id"),
                 json.dumps(action.get("params") or {}, ensure_ascii=False),
                 json.dumps(action.get("undo_params") or {}, ensure_ascii=False),
                 action.get("status", "done"), action.get("reason", ""), now),
            )
            self.conn.commit()
        return act_id

    def list_actions(self, limit: int = 100, target_type: Optional[str] = None,
                     target_id: Optional[str] = None) -> list[dict]:
        sql = "SELECT * FROM actions"
        conds, params = [], []
        if target_type:
            conds.append("target_type=?")
            params.append(target_type)
        if target_id:
            conds.append("target_id=?")
            params.append(target_id)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params)
        out = []
        for r in rows:
            d = dict(r)
            d["params"] = json.loads(d["params"] or "{}")
            d["undo_params"] = json.loads(d["undo_params"] or "{}")
            out.append(d)
        return out

    def get_action(self, act_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM actions WHERE act_id=?", (act_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["params"] = json.loads(d["params"] or "{}")
        d["undo_params"] = json.loads(d["undo_params"] or "{}")
        return d

    def set_action_status(self, act_id: str, status: str) -> None:
        with self._write_lock:
            self.conn.execute(
                "UPDATE actions SET status=? WHERE act_id=?", (status, act_id)
            )
            self.conn.commit()
