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

    def update_book_fields(self, book_id: str, fields: dict) -> None:
        """部分更新 books 行；自动刷新 updated_at。"""
        fields = dict(fields)
        fields.pop("book_id", None)
        if not fields:
            return
        fields["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._write_lock:
            self.conn.execute(
                f"UPDATE books SET {sets} WHERE book_id=?",
                list(fields.values()) + [book_id],
            )
            self.conn.commit()

    def all_books(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM books")]

    def delete_book(self, book_id: str) -> None:
        with self._write_lock:
            self.conn.execute("DELETE FROM books WHERE book_id=?", (book_id,))
            self.conn.commit()

    # ---------- P4: 档案馆软删除（30 天可恢复） ----------

    def soft_delete_book(self, book_id: str) -> dict:
        """软删除：status→deleted + deleted_at 记录（书行保留，可恢复）。"""
        book = self.get_book(book_id)
        if not book:
            raise ValueError(f"书不存在: {book_id}")
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        with self._write_lock:
            self.conn.execute(
                "UPDATE books SET status='deleted', deleted_at=?, updated_at=? WHERE book_id=?",
                (now, now, book_id),
            )
            self.conn.commit()
        return self.get_book(book_id)

    def restore_book(self, book_id: str, prev_status: str | None = None) -> dict:
        """恢复软删除书：deleted_at 清空，状态回补书室（默认 incoming）。"""
        book = self.get_book(book_id)
        if not book:
            raise ValueError(f"书不存在: {book_id}")
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        status = prev_status or "incoming"
        with self._write_lock:
            self.conn.execute(
                "UPDATE books SET status=?, deleted_at=NULL, updated_at=? WHERE book_id=?",
                (status, now, book_id),
            )
            self.conn.commit()
        return self.get_book(book_id)

    def list_deleted_books(self) -> list[dict]:
        """已删除（档案馆可恢复列表），按删除时间倒序。"""
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM books WHERE status='deleted' ORDER BY deleted_at DESC"
        )]

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

    def floor_by_name(self, name: str) -> Optional[dict]:
        """按楼层名称匹配（UI 下拉/自由输入都允许传名称）。"""
        row = self.conn.execute(
            "SELECT * FROM floors WHERE name=?", (name,)
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

    # ---------- P2: skills（蒸馏产物注册表） ----------

    def upsert_skill(self, skill: dict) -> str:
        """写入技能记录；skill_id 缺省时生成 sk_*。返回 skill_id。"""
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        skill = dict(skill)
        skill_id = skill.get("skill_id") or new_id("sk")
        skill["skill_id"] = skill_id
        skill.setdefault("status", "draft")
        skill.setdefault("reject_count", 0)
        skill.setdefault("created_at", now)
        skill["updated_at"] = now
        cols = ", ".join(skill.keys())
        marks = ", ".join("?" for _ in skill)
        with self._write_lock:
            self.conn.execute(
                f"INSERT OR REPLACE INTO skills ({cols}) VALUES ({marks})",
                list(skill.values()),
            )
            self.conn.commit()
        return skill_id

    def get_skill(self, skill_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM skills WHERE skill_id=?", (skill_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_skills(self, status: Optional[str] = None,
                    book_id: Optional[str] = None) -> list[dict]:
        sql = "SELECT * FROM skills"
        conds, params = [], []
        if status:
            conds.append("status=?")
            params.append(status)
        if book_id:
            conds.append("book_id=?")
            params.append(book_id)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY updated_at DESC"
        rows = self.conn.execute(sql, params)
        out = []
        for r in rows:
            d = dict(r)
            if d.get("test_prompts"):
                try:
                    d["test_prompts"] = json.loads(d["test_prompts"])
                except json.JSONDecodeError:
                    pass
            out.append(d)
        return out

    def update_skill(self, skill_id: str, fields: dict) -> None:
        """部分更新 skills 行；自动刷新 updated_at。"""
        fields = dict(fields)
        fields.pop("skill_id", None)
        if not fields:
            return
        fields["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._write_lock:
            self.conn.execute(
                f"UPDATE skills SET {sets} WHERE skill_id=?",
                list(fields.values()) + [skill_id],
            )
            self.conn.commit()

    def set_skill_status(self, skill_id: str, status: str) -> None:
        self.update_skill(skill_id, {"status": status})

    def bump_skill_reject(self, skill_id: str, reason: str) -> int:
        """拒绝一次：reject_count+1，记录原因。返回新计数。"""
        with self._write_lock:
            self.conn.execute(
                "UPDATE skills SET reject_count = reject_count + 1, "
                "last_reject_reason=?, updated_at=? WHERE skill_id=?",
                (reason, time.strftime("%Y-%m-%dT%H:%M:%S+08:00"), skill_id),
            )
            self.conn.commit()
            row = self.conn.execute(
                "SELECT reject_count FROM skills WHERE skill_id=?", (skill_id,)
            ).fetchone()
            return int(row["reject_count"]) if row else 0

    def reset_skill_reject(self, skill_id: str) -> None:
        self.update_skill(skill_id, {"reject_count": 0, "last_reject_reason": ""})

    # ---------- P2: catalog_cards.skills 回填 ----------

    def set_card_skills(self, book_id: str, skills: list[dict]) -> None:
        """把书关联技能列表写进卡片（JSON [{skill_id, name, status}]）。"""
        with self._write_lock:
            self.conn.execute(
                "UPDATE catalog_cards SET skills=? WHERE book_id=?",
                (json.dumps(skills, ensure_ascii=False), book_id),
            )
            self.conn.commit()

    # ---------- P3: recommendations（采购推荐） ----------

    def insert_recommendation(self, rec: dict) -> str:
        """写入推荐；rec_id 缺省生成 rec_*。返回 rec_id。"""
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        rec = dict(rec)
        rec_id = rec.get("rec_id") or new_id("rec")
        rec["rec_id"] = rec_id
        rec.setdefault("status", "pending")
        rec.setdefault("created_at", now)
        cols = ", ".join(rec.keys())
        marks = ", ".join("?" for _ in rec)
        with self._write_lock:
            self.conn.execute(
                f"INSERT OR REPLACE INTO recommendations ({cols}) VALUES ({marks})",
                list(rec.values()),
            )
            self.conn.commit()
        return rec_id

    def list_recommendations(self, date: str | None = None,
                             status: str | None = None,
                             limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM recommendations"
        conds, params = [], []
        if date:
            conds.append("date=?")
            params.append(date)
        if status:
            conds.append("status=?")
            params.append(status)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY score DESC, created_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params)]

    def get_recommendation(self, rec_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM recommendations WHERE rec_id=?", (rec_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_recommendation(self, rec_id: str, fields: dict) -> None:
        """部分更新推荐行。"""
        fields = dict(fields)
        fields.pop("rec_id", None)
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._write_lock:
            self.conn.execute(
                f"UPDATE recommendations SET {sets} WHERE rec_id=?",
                list(fields.values()) + [rec_id],
            )
            self.conn.commit()

    def recommendation_stats(self, date: str) -> dict:
        """某日推荐状态统计（配额执行率用）。"""
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS c FROM recommendations WHERE date=? GROUP BY status",
            (date,),
        ).fetchall()
        stats = {"total": 0, "pending": 0, "collected": 0,
                 "ignored": 0, "not_interested": 0}
        for r in rows:
            stats[r["status"]] = int(r["c"])
            stats["total"] += int(r["c"])
        return stats

    # ---------- P3: daily_reports（日报） ----------

    def insert_report(self, report: dict) -> str:
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        report = dict(report)
        report_id = report.get("report_id") or new_id("rep")
        report["report_id"] = report_id
        report.setdefault("created_at", now)
        if isinstance(report.get("content"), (dict, list)):
            report["content"] = json.dumps(report["content"], ensure_ascii=False)
        cols = ", ".join(report.keys())
        marks = ", ".join("?" for _ in report)
        with self._write_lock:
            self.conn.execute(
                f"INSERT OR REPLACE INTO daily_reports ({cols}) VALUES ({marks})",
                list(report.values()),
            )
            self.conn.commit()
        return report_id

    def list_reports(self, date: str | None = None,
                     rtype: str | None = None,
                     limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM daily_reports"
        conds, params = [], []
        if date:
            conds.append("date=?")
            params.append(date)
        if rtype:
            conds.append("rtype=?")
            params.append(rtype)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        out = []
        for r in self.conn.execute(sql, params):
            d = dict(r)
            try:
                d["content"] = json.loads(d.get("content") or "{}")
            except json.JSONDecodeError:
                d["content"] = {}
            out.append(d)
        return out

    def latest_report(self, rtype: str | None = None) -> Optional[dict]:
        sql = "SELECT * FROM daily_reports"
        params: list = []
        if rtype:
            sql += " WHERE rtype=?"
            params.append(rtype)
        sql += " ORDER BY created_at DESC LIMIT 1"
        row = self.conn.execute(sql, params).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["content"] = json.loads(d.get("content") or "{}")
        except json.JSONDecodeError:
            d["content"] = {}
        return d

    # ---------- P3: profile（画像，单行） ----------

    def get_profile(self) -> dict:
        row = self.conn.execute("SELECT * FROM profile WHERE id=1").fetchone()
        if not row:
            return {"themes": {}, "direction_pool": [], "prefs": {}}
        d = dict(row)
        for k in ("themes", "direction_pool", "prefs"):
            try:
                d[k] = json.loads(d.get(k) or ("{}" if k != "direction_pool" else "[]"))
            except json.JSONDecodeError:
                d[k] = {} if k != "direction_pool" else []
        return d

    def save_profile(self, themes: dict | None = None,
                     direction_pool: list | None = None,
                     prefs: dict | None = None) -> None:
        """合并更新单行画像。"""
        cur = self.get_profile()
        new_themes = themes if themes is not None else cur.get("themes", {})
        new_pool = direction_pool if direction_pool is not None else cur.get("direction_pool", [])
        new_prefs = prefs if prefs is not None else cur.get("prefs", {})
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        with self._write_lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO profile (id, themes, direction_pool, prefs, updated) "
                "VALUES (1, ?, ?, ?, ?)",
                (json.dumps(new_themes, ensure_ascii=False),
                 json.dumps(new_pool, ensure_ascii=False),
                 json.dumps(new_prefs, ensure_ascii=False),
                 now),
            )
            self.conn.commit()
