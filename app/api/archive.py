"""档案馆 API（设计文档 §9.1 🗄档案馆 / §11 P4）。

- GET  /api/archive/summary       各 tab 统计（原始副本/已删除/日报/蒸馏日志）
- GET  /api/archive/raw           原始不可变副本列表（关联书）
- GET  /api/archive/deleted       已删除书（30 天倒计时）
- POST /api/archive/{book_id}/restore  恢复已删除书
- GET  /api/archive/reports       历史日报（转发 /api/daily-reports）
- GET  /api/archive/distill-logs  蒸馏过程记录目录
- GET  /api/archive/backup        备份导出（zip：data + vault）

软删除端点：
- POST /api/books/{book_id}/delete  主人删除书 → 档案馆（可恢复 30 天）
"""
from __future__ import annotations

import datetime
import io
import json
import shutil
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/archive", tags=["archive"])

RESTORE_DAYS = 30


def _raw_scan(state) -> list[dict]:
    """扫描 archive/raw/<h2>/<hash> 原始副本；关联 books.content_hash。"""
    raw_root = state.cfg.paths.data_dir / "archive" / "raw"
    items = []
    if not raw_root.exists():
        return items
    hash_to_book = {b["content_hash"]: b for b in state.repo.all_books()
                    if b.get("content_hash")}
    for f in sorted(raw_root.rglob("*")):
        if not f.is_file():
            continue
        h = f.name
        book = hash_to_book.get(h)
        stat = f.stat()
        items.append({
            "hash": h,
            "path": str(f.relative_to(raw_root)).replace("\\", "/"),
            "size": stat.st_size,
            "modified_at": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "book_id": book["book_id"] if book else None,
            "book_title": book.get("title") if book else None,
            "linked": bool(book),
        })
    return items


@router.get("/summary")
def archive_summary(req: Request) -> dict:
    state = req.app.state.library
    raw = _raw_scan(state)
    deleted = state.repo.list_deleted_books()
    reports = state.repo.list_reports(limit=1)
    logs_root = state.cfg.paths.vault_dir / "archive" / "distill-logs"
    log_dirs = [d.name for d in logs_root.iterdir() if d.is_dir()] \
        if logs_root.exists() else []
    return {
        "raw_count": len(raw),
        "deleted_count": len(deleted),
        "report_count": len(state.repo.list_reports(limit=10000)),
        "distill_log_count": len(log_dirs),
        "restore_days": RESTORE_DAYS,
        "backup_ready": True,
    }


@router.get("/raw")
def archive_raw(req: Request) -> dict:
    return {"items": _raw_scan(req.app.state.library), "count": len(_raw_scan(req.app.state.library))}


@router.get("/deleted")
def archive_deleted(req: Request) -> dict:
    """已删除书列表 + 剩余恢复天数。"""
    state = req.app.state.library
    now = datetime.datetime.now()
    items = []
    for b in state.repo.list_deleted_books():
        days_left = None
        if b.get("deleted_at"):
            try:
                dt = datetime.datetime.fromisoformat(b["deleted_at"])
                if dt.tzinfo is not None:
                    dt = dt.replace(tzinfo=None)
                days_left = max(0, RESTORE_DAYS - (now - dt).days)
            except ValueError:
                days_left = RESTORE_DAYS
        items.append({
            "book_id": b["book_id"],
            "title": b.get("title") or b["book_id"],
            "media_type": b.get("media_type") or "",
            "deleted_at": b.get("deleted_at"),
            "days_left": days_left,
            "vault_path": b.get("vault_path") or "",
            "reason": None,
        })
    return {"items": items, "count": len(items), "restore_days": RESTORE_DAYS}


@router.post("/{book_id}/restore")
def archive_restore(req: Request, book_id: str) -> dict:
    """恢复已删除书到补书室（incoming）。"""
    state = req.app.state.library
    book = state.repo.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书不存在")
    if book.get("status") != "deleted":
        raise HTTPException(status_code=400, detail="书未处于已删除状态")
    restored = state.repo.restore_book(book_id, prev_status="incoming")
    state.repo.insert_action({
        "agent": "owner",
        "action_type": "restore",
        "target_type": "book",
        "target_id": book_id,
        "params": {"title": book.get("title", book_id)},
        "undo_params": {},
        "reason": f"主人从档案馆恢复《{book.get('title', book_id)}》",
    })
    return {"ok": True, "book": restored}


@router.get("/reports")
def archive_reports(req: Request, date: str | None = None) -> dict:
    """历史日报（复用 purchase 的 daily-reports 数据）。"""
    state = req.app.state.library
    reports = state.repo.list_reports(date=date, limit=200)
    return {"reports": reports, "count": len(reports)}


@router.get("/distill-logs")
def archive_distill_logs(req: Request) -> dict:
    """蒸馏过程记录：vault/archive/distill-logs/<slug>/ 目录树。"""
    state = req.app.state.library
    logs_root = state.cfg.paths.vault_dir / "archive" / "distill-logs"
    items = []
    if logs_root.exists():
        for d in sorted(logs_root.iterdir()):
            if not d.is_dir():
                continue
            files = []
            for f in sorted(d.rglob("*")):
                if f.is_file():
                    files.append(str(f.relative_to(d)).replace("\\", "/"))
            items.append({
                "slug": d.name,
                "files": files,
                "count": len(files),
            })
    return {"items": items, "count": len(items)}


@router.get("/backup")
def archive_backup(req: Request) -> StreamingResponse:
    """备份导出：data（库/向量/原始副本）+ vault（馆藏/技能）打包 zip。"""
    state = req.app.state.library
    buf = io.BytesIO()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, root in (("data", state.cfg.paths.data_dir),
                           ("vault", state.cfg.paths.vault_dir)):
            if not root.exists():
                continue
            for f in root.rglob("*"):
                if f.is_file() and not any(part.startswith(".") for part in f.parts):
                    arc = f"{name}/{f.relative_to(root)}".replace("\\", "/")
                    zf.write(f, arc)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="ai-library-backup-{stamp}.zip"'},
    )
