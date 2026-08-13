"""SQLite schema：P0 核心表（设计文档 §5.3）。

P0 实现：books / chunks / chunks_fts(FTS5) / index_state / embedding_cache
楼层/房间/书架/技能/对话等表在对应阶段添加。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS books (
  book_id     TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  author      TEXT,
  slug        TEXT,
  media_type  TEXT,
  source_uri  TEXT,
  content_hash TEXT,
  raw_path    TEXT,
  vault_path  TEXT,
  card_path   TEXT,
  status      TEXT NOT NULL DEFAULT 'incoming',
  suggest_floor TEXT, suggest_room TEXT, suggest_shelf TEXT,
  confirm_by  TEXT,
  private     INTEGER DEFAULT 0,
  tags        TEXT,
  meta        TEXT,
  created_at  TEXT,
  updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
  chunk_id  TEXT PRIMARY KEY,
  book_id   TEXT REFERENCES books(book_id),
  section   TEXT,
  seq       INTEGER,
  content   TEXT NOT NULL,
  fts_content TEXT,        -- jieba 分词后的文本（供 FTS5 匹配）
  token_cnt INTEGER,
  vector_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_book ON chunks(book_id);

-- FTS5 外部内容表（fts_content 列映射 chunks.fts_content，rowid 对齐）
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  fts_content,
  content='chunks',
  content_rowid='rowid'
);

CREATE TABLE IF NOT EXISTS index_state (
  revision   INTEGER PRIMARY KEY,
  active     INTEGER DEFAULT 1,
  status     TEXT,
  changed_book_ids TEXT,
  built_at   TEXT
);

-- embedding 缓存：content_hash -> 向量（避免重复调用 API）
CREATE TABLE IF NOT EXISTS embedding_cache (
  content_hash TEXT PRIMARY KEY,
  dim          INTEGER NOT NULL,
  vector       BLOB NOT NULL,
  model        TEXT,
  created_at   TEXT
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """打开（必要时创建）数据库，启用 WAL 与 FTS5 触发器维护。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)

    # 旧库迁移：chunks 无 fts_content 列时补充
    cols = [r[1] for r in conn.execute("PRAGMA table_info(chunks)")]
    if "fts_content" not in cols:
        conn.execute("ALTER TABLE chunks ADD COLUMN fts_content TEXT")

    # FTS5 同步触发器：chunks 增删改时同步 chunks_fts
    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
          INSERT INTO chunks_fts(rowid, fts_content) VALUES (new.rowid, new.fts_content);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
          INSERT INTO chunks_fts(chunks_fts, rowid, fts_content)
          VALUES('delete', old.rowid, old.fts_content);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
          INSERT INTO chunks_fts(chunks_fts, rowid, fts_content)
          VALUES('delete', old.rowid, old.fts_content);
          INSERT INTO chunks_fts(rowid, fts_content) VALUES (new.rowid, new.fts_content);
        END;
        """
    )
    conn.commit()
    return conn
