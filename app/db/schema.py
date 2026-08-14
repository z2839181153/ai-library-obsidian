"""SQLite schema（设计文档 §5.3）。

- P0 实现：books / chunks / chunks_fts(FTS5) / index_state / embedding_cache
- P1 实现：floors / rooms / shelves / catalog_cards / actions / conversations / messages
- P2 实现：skills（蒸馏产物注册表）
"""
from __future__ import annotations

import sqlite3
import time
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
  distill_slug TEXT,                -- P2: vault/skills/<slug> 蒸馏产物根
  distill_status TEXT,              -- P2: idle|running|awaiting|done|failed|blocked
  deleted_at  TEXT,                 -- P4: 档案馆软删除时间（30 天可恢复）
  last_read_at TEXT,                -- P5: 最近阅读时间（阅览室"继续阅读"）
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

-- ---------- P1：楼层/房间/书架（目录的 DB 镜像） ----------
CREATE TABLE IF NOT EXISTS floors (
  floor_id   TEXT PRIMARY KEY,        -- fl_*
  name       TEXT NOT NULL,
  code       TEXT,                    -- 1F
  media_type TEXT,                    -- pdf/epub/web/chat/video/other
  description TEXT,
  ord        INTEGER DEFAULT 0,
  custom     INTEGER DEFAULT 0,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS rooms (
  room_id    TEXT PRIMARY KEY,        -- rm_*
  floor_id   TEXT REFERENCES floors(floor_id),
  name       TEXT NOT NULL,
  description TEXT,
  ord        INTEGER DEFAULT 0,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS shelves (
  shelf_id   TEXT PRIMARY KEY,        -- sh_*
  room_id    TEXT REFERENCES rooms(room_id),
  name       TEXT NOT NULL,
  description TEXT,
  ord        INTEGER DEFAULT 0,
  created_at TEXT
);

-- ---------- P1：图书卡片（LLM 生成，catalog/bk_*.md 的 DB 镜像） ----------
CREATE TABLE IF NOT EXISTS catalog_cards (
  book_id      TEXT PRIMARY KEY REFERENCES books(book_id),
  summary      TEXT,                  -- 200 字摘要
  chapters     TEXT,                  -- JSON [{title, summary, ref}]
  concepts     TEXT,                  -- JSON [{term, definition, ref}]
  distill_value INTEGER,              -- 0-100 蒸馏价值分
  distill_reason TEXT,                -- 为什么值得/不值得蒸馏
  category     TEXT,                  -- methodology|reference|narrative|data
  tags         TEXT,                  -- JSON array
  skills       TEXT,                  -- JSON [{skill_id, name, status}]（P2 用）
  generated_at TEXT,
  model        TEXT                   -- 生成用的模型
);

-- ---------- P1：操作账本（主人权利机制核心，可撤销） ----------
CREATE TABLE IF NOT EXISTS actions (
  act_id      TEXT PRIMARY KEY,       -- act_*
  agent       TEXT,                   -- admin|purchaser|archivist|distiller|system|owner
  action_type TEXT,                   -- classify|shelve|archive|delete|ingest|...
  target_type TEXT,                   -- book/skill/conversation/...
  target_id   TEXT,
  params      TEXT,                   -- JSON（动作参数）
  undo_params TEXT,                   -- JSON（撤销所需逆操作）
  status      TEXT DEFAULT 'done',    -- doing|done|undone|failed
  reason      TEXT,                   -- 动作理由（进日报）
  created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_actions_target ON actions(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status);

-- ---------- P1：对话（建表，会话存储 P3 聊天实现） ----------
CREATE TABLE IF NOT EXISTS conversations (
  cv_id        TEXT PRIMARY KEY,
  title        TEXT,
  archived_book_id TEXT,              -- 归档为书后关联
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS messages (
  msg_id        TEXT PRIMARY KEY,
  cv_id         TEXT REFERENCES conversations(cv_id),
  role          TEXT,                 -- user|assistant|system
  content       TEXT,
  refs          TEXT,                 -- JSON [[wikilink]] 引用列表
  private       INTEGER DEFAULT 0,
  created_at    TEXT
);

-- ---------- P3：采购/日报/画像（设计文档 §5.3 purchase/reports/profile） ----------
CREATE TABLE IF NOT EXISTS recommendations (
  rec_id        TEXT PRIMARY KEY,     -- rec_*
  date          TEXT,                 -- 2026-08-14
  title         TEXT NOT NULL,
  url           TEXT,
  source        TEXT,                 -- arxiv|hn|zhihu|rss|manual|...
  score         REAL,
  reason        TEXT,                 -- 荐书理由（进日报，AI 可解释）
  status        TEXT DEFAULT 'pending', -- pending|collected|ignored|not_interested
  feedback_note TEXT,
  created_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_rec_date ON recommendations(date);
CREATE INDEX IF NOT EXISTS idx_rec_status ON recommendations(status);

CREATE TABLE IF NOT EXISTS daily_reports (
  report_id TEXT PRIMARY KEY,         -- rep_*
  date      TEXT,
  rtype     TEXT,                     -- purchase|ingest|distill|system
  content   TEXT,                     -- JSON（卡片渲染数据）
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_report_date ON daily_reports(date, rtype);

CREATE TABLE IF NOT EXISTS profile (
  id        INTEGER PRIMARY KEY CHECK (id = 1),  -- 单行
  themes    TEXT,                     -- JSON {主题: 数量}
  direction_pool TEXT,                -- JSON [{topic, weight, source, first_seen}]
  prefs     TEXT,                     -- JSON {default_mode, max_daily_purchase, no_video_unless_hot, auto_score_threshold}
  updated   TEXT
);

-- ---------- P2：蒸馏产物注册表（设计文档 §5.3 skills 表） ----------
CREATE TABLE IF NOT EXISTS skills (
  skill_id     TEXT PRIMARY KEY,      -- sk_*
  book_id      TEXT REFERENCES books(book_id),
  name         TEXT NOT NULL,
  slug         TEXT,
  path         TEXT,                  -- skills/<book-slug>/<skill-slug>/SKILL.md
  description  TEXT,                  -- trigger 条件（进向量索引做路由）
  status       TEXT DEFAULT 'draft',
               -- draft|reviewing|approved|rejected|installed|blocked
  reject_count INTEGER DEFAULT 0,     -- 连续拒绝次数（≥5 自动阻塞）
  last_reject_reason TEXT,
  test_prompts TEXT,                  -- JSON（darwin 兼容）
  created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_skills_book ON skills(book_id);
CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status);
"""

# 默认 4 个内置楼层（设计文档 §5.2；仅当 floors 表为空时种子插入）
DEFAULT_FLOORS = [
    {
        "floor_id": "fl_1f_ebook", "name": "电子书", "code": "1F",
        "media_type": "pdf", "description": "PDF/EPUB 电子书", "ord": 1, "custom": 0,
    },
    {
        "floor_id": "fl_2f_web", "name": "网页公众号", "code": "2F",
        "media_type": "web", "description": "网页/公众号文章", "ord": 2, "custom": 0,
    },
    {
        "floor_id": "fl_3f_chat", "name": "聊天记录", "code": "3F",
        "media_type": "chat", "description": "聊天记录（房间按人）", "ord": 3, "custom": 0,
    },
    {
        "floor_id": "fl_4f_video", "name": "视频转写", "code": "4F",
        "media_type": "video", "description": "视频转写/字幕", "ord": 4, "custom": 0,
    },
]

# 来源媒介 → 默认楼层 code（设计文档 §6.2：楼层=来源媒介固定映射）
MEDIA_TO_FLOOR = {
    "pdf": "1F", "epub": "1F", "ebook": "1F", "markdown": "1F", "text": "1F",
    "web": "2F", "html": "2F",
    "chat": "3F",
    "video": "4F", "srt": "4F", "vtt": "4F",
}


def floor_for_media_type(media_type: str) -> str | None:
    """按来源媒介返回默认楼层 code；未知媒介返回 None（待定区）。"""
    return MEDIA_TO_FLOOR.get((media_type or "").strip().lower())


def seed_default_floors(conn: sqlite3.Connection) -> None:
    """floors 表为空时插入默认 4 楼层。幂等。"""
    count = conn.execute("SELECT COUNT(*) AS c FROM floors").fetchone()["c"]
    if count:
        return
    now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    for f in DEFAULT_FLOORS:
        conn.execute(
            "INSERT INTO floors (floor_id, name, code, media_type, description, ord, custom, created_at) "
            "VALUES (:floor_id, :name, :code, :media_type, :description, :ord, :custom, :created_at)",
            {**f, "created_at": now},
        )
    conn.commit()


def connect(db_path: Path) -> sqlite3.Connection:
    """打开（必要时创建）数据库，启用 WAL 与 FTS5 触发器维护。

    check_same_thread=False：本地单进程服务，读写可能来自 FastAPI 线程池；
    写入串行由 Repo._write_lock 保证（单写入者，设计文档 §6.9）。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)

    # 旧库迁移：chunks 无 fts_content 列时补充
    cols = [r[1] for r in conn.execute("PRAGMA table_info(chunks)")]
    if "fts_content" not in cols:
        conn.execute("ALTER TABLE chunks ADD COLUMN fts_content TEXT")

    # 旧库迁移（P2）：books 补蒸馏字段
    bcols = [r[1] for r in conn.execute("PRAGMA table_info(books)")]
    if "distill_slug" not in bcols:
        conn.execute("ALTER TABLE books ADD COLUMN distill_slug TEXT")
    if "distill_status" not in bcols:
        conn.execute("ALTER TABLE books ADD COLUMN distill_status TEXT")
    # 旧库迁移（P4）：books 补软删除时间
    if "deleted_at" not in bcols:
        conn.execute("ALTER TABLE books ADD COLUMN deleted_at TEXT")
    # 旧库迁移（P5）：books 补最近阅读时间
    if "last_read_at" not in bcols:
        conn.execute("ALTER TABLE books ADD COLUMN last_read_at TEXT")

    # 默认楼层种子（幂等）
    seed_default_floors(conn)

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
