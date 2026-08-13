"""数据访问层：P0 books / chunks / index_state / embedding_cache。"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Optional

from app.db.schema import connect


class Repo:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

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
        self.conn.execute("DELETE FROM books WHERE book_id=?", (book_id,))
        self.conn.commit()

    # ---------- chunks ----------

    def insert_chunk(self, chunk: dict) -> None:
        self.conn.execute(
            "INSERT INTO chunks (chunk_id, book_id, section, seq, content, fts_content, token_cnt, vector_id) "
            "VALUES (:chunk_id, :book_id, :section, :seq, :content, :fts_content, :token_cnt, :vector_id)",
            chunk,
        )

    def delete_chunks_by_book(self, book_id: str) -> None:
        self.conn.execute("DELETE FROM chunks WHERE book_id=?", (book_id,))

    def commit(self) -> None:
        self.conn.commit()

    # ---------- index_state ----------

    def latest_revision(self) -> Optional[int]:
        row = self.conn.execute(
            "SELECT MAX(revision) AS r FROM index_state"
        ).fetchone()
        return row["r"] if row and row["r"] is not None else 0

    def set_state(self, revision: int, status: str, changed: Iterable[str]) -> None:
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
        self.conn.execute(
            "INSERT OR REPLACE INTO embedding_cache (content_hash, dim, vector, model, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (content_hash, dim, blob, model,
             time.strftime("%Y-%m-%dT%H:%M:%S+08:00")),
        )
        self.conn.commit()
