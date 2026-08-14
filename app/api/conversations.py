"""对话 API（设计文档 §6.7 / §9.2）：历史 + 归档为书。

- GET /api/conversations：对话列表（今天/昨天/更早分组所需字段）
- GET /api/conversations/{cv_id}：全部消息
- POST /api/conversations/{cv_id}/archive：对话归档为书（走 ingest 管线）
"""
from __future__ import annotations

import datetime
import json
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.db.repo import new_id

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _day_label(dt_str: str) -> str:
    """'今天' / '昨天' / 'YYYY-MM-DD'。"""
    try:
        d = datetime.date.fromisoformat(dt_str[:10])
    except ValueError:
        return dt_str[:10]
    today = datetime.date.today()
    if d == today:
        return "今天"
    if d == today - datetime.timedelta(days=1):
        return "昨天"
    return d.isoformat()


def ensure_conversation(state, title: str = "新对话") -> str:
    """创建（或复用最近的空）对话，返回 cv_id。"""
    now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    cv_id = new_id("cv")
    state.repo.conn.execute(
        "INSERT INTO conversations (cv_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (cv_id, title, now, now),
    )
    state.repo.commit()
    return cv_id


def append_message(state, cv_id: str, role: str, content: str,
                   refs: list | None = None, private: bool = False) -> str:
    """写入一条消息，刷新对话 updated_at。返回 msg_id。"""
    now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    msg_id = new_id("msg")
    state.repo.conn.execute(
        "INSERT INTO messages (msg_id, cv_id, role, content, refs, private, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (msg_id, cv_id, role, content,
         json.dumps(refs or [], ensure_ascii=False), 1 if private else 0, now),
    )
    state.repo.conn.execute(
        "UPDATE conversations SET updated_at=? WHERE cv_id=?", (now, cv_id)
    )
    state.repo.commit()
    return msg_id


@router.get("")
def list_conversations(req: Request) -> dict:
    """对话列表（含消息数，前端按 updated_at 分组：今天/昨天/更早）。"""
    state = req.app.state.library
    rows = state.repo.conn.execute(
        "SELECT cv_id, title, created_at, updated_at, "
        "(SELECT COUNT(*) FROM messages m WHERE m.cv_id=c.cv_id) AS msg_count "
        "FROM conversations c ORDER BY updated_at DESC LIMIT 200"
    ).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["group"] = _day_label(d["updated_at"] or "")
        items.append(d)
    return {"conversations": items, "count": len(items)}


@router.get("/{cv_id}")
def get_conversation(req: Request, cv_id: str) -> dict:
    state = req.app.state.library
    row = state.repo.conn.execute(
        "SELECT * FROM conversations WHERE cv_id=?", (cv_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="对话不存在")
    msgs = []
    for r in state.repo.conn.execute(
        "SELECT * FROM messages WHERE cv_id=? ORDER BY created_at", (cv_id,)
    ):
        d = dict(r)
        try:
            d["refs"] = json.loads(d.get("refs") or "[]")
        except json.JSONDecodeError:
            d["refs"] = []
        msgs.append(d)
    return {"conversation": dict(row), "messages": msgs}


@router.post("/{cv_id}/archive")
def archive_conversation(req: Request, cv_id: str) -> dict:
    """对话归档为书：导出 md → 走 ingest 管线（media_type=chat）→ 三楼聊天记录。"""
    state = req.app.state.library
    row = state.repo.conn.execute(
        "SELECT * FROM conversations WHERE cv_id=?", (cv_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="对话不存在")
    msgs = state.repo.conn.execute(
        "SELECT * FROM messages WHERE cv_id=? ORDER BY created_at", (cv_id,)
    ).fetchall()
    if not msgs:
        raise HTTPException(status_code=400, detail="对话为空，无法归档")

    title = row["title"] or f"对话 {cv_id}"
    lines = [f"# {title}", ""]
    for m in msgs:
        m = dict(m)
        who = "主人" if m["role"] == "user" else ("AI" if m["role"] == "assistant" else "系统")
        lines.append(f"## {who}（{m['created_at'][:16]}）")
        lines.append(m["content"])
        try:
            refs = json.loads(m.get("refs") or "[]")
        except json.JSONDecodeError:
            refs = []
        for ref in refs:
            link = ref.get("link") if isinstance(ref, dict) else ref
            lines.append(f"- 引用：{link}")
        lines.append("")
    md = "\n".join(lines)

    from app.ingest.cleaner import ingest_file as do_ingest

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / f"{cv_id}.md"
        tmp.write_text(md, encoding="utf-8")
        ingested = do_ingest(tmp, state.cfg.paths.data_dir / "archive" / "raw")

    from app.api.ingest import _register_and_index

    result = _register_and_index(state, ingested)
    state.repo.conn.execute(
        "UPDATE conversations SET archived_book_id=? WHERE cv_id=?",
        (result["book"]["book_id"], cv_id),
    )
    state.repo.commit()
    state.repo.insert_action({
        "agent": "owner",
        "action_type": "conversation_archive",
        "target_type": "conversation",
        "target_id": cv_id,
        "params": {"book_id": result["book"]["book_id"], "title": title},
        "undo_params": {},
        "reason": f"主人把对话《{title}》归档为书",
    })
    return {"ok": True, "book": result["book"], "cv_id": cv_id}
