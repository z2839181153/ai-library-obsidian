"""入馆 API（设计文档 §9.2 POST /api/ingest）。

支持：
- multipart 文件上传（md/txt/html/pdf）
- JSON 文本入馆（{text, title?}）
- URL 抓取占位（无搜索/抓取 key，返回 501 说明）

流程：上传 → ingest_file（解析+清洗+不可变副本）→ 去重检测 → 写 books
（status=incoming）→ 自动跑索引 → WS 通知（book_ingested）→ action ledger。
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

router = APIRouter(tags=["ingest"])

SUPPORTED_SUFFIX = {".md", ".markdown", ".txt", ".html", ".htm", ".pdf"}


class TextIngestRequest(BaseModel):
    text: str
    title: str = "未命名笔记"
    private: bool = False


def _register_and_index(state, ingested, private: bool = False,
                        title_override: str | None = None) -> dict:
    """登记书 + 自动索引 + WS 通知。返回 {book, created}。"""
    from app.ingest.cleaner import IngestedBook  # noqa: F401

    # 去重：content_hash 已存在
    existing = state.repo.book_by_hash(ingested.content_hash)
    if existing:
        return {"book": state.repo.get_book(existing["book_id"]),
                "created": False, "duplicate": True}

    book = {
        "book_id": ingested.book_id,
        "title": title_override or ingested.title,
        "author": ingested.author,
        "slug": Path(ingested.raw_path).stem,
        "media_type": ingested.media_type,
        "source_uri": str(ingested.raw_path),
        "content_hash": ingested.content_hash,
        "raw_path": str(ingested.raw_path),
        "vault_path": "",
        "card_path": "",
        "status": "incoming",
        "private": 1 if private else 0,
        "meta": __import__("json").dumps(ingested.meta, ensure_ascii=False),
    }
    state.repo.upsert_book(book)

    # 自动索引（单书 chunk + embedding；Fake/真实均可）
    try:
        stats = state.indexer.index_book(book["book_id"], ingested)
    except Exception as e:  # noqa: BLE001
        # 索引失败不阻塞入馆（书已登记，可稍后重跑索引）
        stats = {"error": str(e)}

    state.repo.insert_action({
        "agent": "system",
        "action_type": "ingest",
        "target_type": "book",
        "target_id": book["book_id"],
        "params": {"title": book["title"], "media_type": book["media_type"],
                   "content_hash": book["content_hash"]},
        "undo_params": {},
        "reason": f"入馆《{book['title']}》（{book['media_type']}）",
    })

    # WS 通知（补书室有新书）
    state.ws.broadcast({
        "type": "notice",
        "event": "book_ingested",
        "book_id": book["book_id"],
        "title": book["title"],
        "status": "incoming",
    })
    return {"book": state.repo.get_book(book["book_id"]),
            "created": True, "duplicate": False, "index_stats": stats}


@router.post("/ingest")
def ingest_file(req: Request, file: UploadFile | None = None,
                text: str | None = Form(None), title: str | None = Form(None),
                private: bool = Form(False)) -> dict:
    """入馆：multipart 文件 或 form text。"""
    state = req.app.state.library

    if file is not None and file.filename:
        suffix = Path(file.filename).suffix.lower()
        if suffix not in SUPPORTED_SUFFIX:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的格式: {suffix}（支持: {', '.join(sorted(SUPPORTED_SUFFIX))}）",
            )
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / file.filename
            tmp.write_bytes(file.file.read())
            try:
                ingested = _ingest_path(state, tmp)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
        return _register_and_index(state, ingested, private=private, title_override=title)

    if text is not None and text.strip():
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "ingest.md"
            tmp.write_text(text, encoding="utf-8")
            try:
                ingested = _ingest_path(state, tmp)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
        return _register_and_index(state, ingested, private=private, title_override=title)

    raise HTTPException(status_code=422, detail="需要上传文件或提供 text 文本")


@router.post("/ingest/url")
def ingest_url(req: Request, body: dict) -> dict:
    """URL 抓取入馆（P3 占位：无搜索/抓取服务，返回 501）。"""
    raise HTTPException(
        status_code=501,
        detail="URL 抓取尚未实现（需配置抓取服务）；请先下载文件后上传。",
    )


def _ingest_path(state, path: Path):
    from app.ingest.cleaner import ingest_file as do_ingest

    return do_ingest(path, state.cfg.paths.data_dir / "archive" / "raw")
