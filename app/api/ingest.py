"""入馆 API（设计文档 §9.2 POST /api/ingest）。

支持：
- multipart 文件上传（md/txt/html/pdf）
- JSON 文本入馆（{text, title?}）
- URL 抓取占位（无搜索/抓取 key，返回 501 说明）
- P5-2 批量入馆 POST /api/ingest/batch（同格式 ≤10 本/次；批量去重 →
  批量 embedding（批 64）→ 异步索引 + WS 进度；429 先词法后补向量）

流程：上传 → ingest_file（解析+清洗+不可变副本）→ 去重检测 → 写 books
（status=incoming）→ 自动跑索引 → WS 通知（book_ingested）→ action ledger。
"""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

router = APIRouter(tags=["ingest"])

SUPPORTED_SUFFIX = {".md", ".markdown", ".txt", ".html", ".htm", ".pdf"}

# P5-2 批量入馆：所选格式 → 允许扩展名（一次只能传同一格式）
BATCH_MAX = 10
FORMAT_SUFFIXES = {
    "markdown": {".md", ".markdown"},
    "text": {".txt"},
    "html": {".html", ".htm"},
    "pdf": {".pdf"},
}
FORMAT_LABELS = {
    "markdown": "Markdown（.md）",
    "text": "纯文本（.txt）",
    "html": "网页/HTML（.html）",
    "pdf": "PDF 电子书（.pdf）",
}


class TextIngestRequest(BaseModel):
    text: str
    title: str = "未命名笔记"
    private: bool = False


def _register_book(state, ingested, private: bool = False,
                   title_override: str | None = None, notify: bool = True) -> dict:
    """登记书（books 行 + action ledger + WS 通知），不索引。返回 book dict。

    notify=False：批量入馆时跳过逐本 book_ingested 广播（改由调用方发
    batch_ingested 汇总通知），避免 10 条 toast 刷屏。
    """
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

    if notify:
        # WS 通知（补书室有新书）
        state.ws.broadcast({
            "type": "notice",
            "event": "book_ingested",
            "book_id": book["book_id"],
            "title": book["title"],
            "status": "incoming",
        })
    return state.repo.get_book(book["book_id"])


def _register_and_index(state, ingested, private: bool = False,
                        title_override: str | None = None) -> dict:
    """登记书 + 自动索引 + WS 通知。返回 {book, created}。"""
    from app.ingest.cleaner import IngestedBook  # noqa: F401

    # 去重：content_hash 已存在
    existing = state.repo.book_by_hash(ingested.content_hash)
    if existing:
        return {"book": state.repo.get_book(existing["book_id"]),
                "created": False, "duplicate": True}

    book = _register_book(state, ingested, private=private,
                          title_override=title_override)

    # 自动索引（单书 chunk + embedding；Fake/真实均可）
    try:
        stats = state.indexer.index_book(book["book_id"], ingested)
    except Exception as e:  # noqa: BLE001
        # 索引失败不阻塞入馆（书已登记，可稍后重跑索引）
        stats = {"error": str(e)}

    return {"book": book,
            "created": True, "duplicate": False, "index_stats": stats}


def _start_background_index(state, registered: list[tuple[str, object]]) -> None:
    """批量索引放后台线程：清洗登记后立即返回，索引完成再广播进度。

    registered: [(book_id, ingested), ...]
    WS 事件：batch_index_progress（逐本）/ batch_index_done（汇总）。
    复用 P3 ConnectionManager 线程安全广播（call_soon_threadsafe）。
    """
    def _work() -> None:
        try:
            stats = state.indexer.index_books(registered)
            for pb in stats.get("per_book", []):
                state.ws.broadcast({
                    "type": "notice",
                    "event": "batch_index_progress",
                    "book_id": pb["book_id"],
                    "chunks": pb["chunks"],
                    "vectors": pb["vectors"],
                })
            state.ws.broadcast({
                "type": "notice",
                "event": "batch_index_done",
                "books": len(registered),
                "chunks": stats.get("chunks", 0),
                "fallback": stats.get("fallback", False),
            })
        except Exception as e:  # noqa: BLE001
            state.ws.broadcast({
                "type": "notice",
                "event": "batch_index_done",
                "books": 0,
                "error": str(e),
            })

    t = threading.Thread(target=_work, daemon=True)
    t.start()


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


@router.post("/ingest/batch")
def ingest_batch(req: Request, files: list[UploadFile] | None = None,
                 format: str | None = Form(None),
                 private: bool = Form(False)) -> dict:
    """批量入馆（P5-2，设计文档 §6.1.1）。

    硬约束：每次 ≤10 本；上传前必须选格式；一次只能同一格式（混格式 400 拒绝）。
    流程：校验 → 批量 sha256 去重（重复标 duplicate 不阻断）→ 批量清洗登记 →
    立即返回逐本状态，索引放后台线程 + WS 进度广播；
    embedding 失败先落词法索引（FTS5），书标 vector_pending 向量后台补。
    """
    state = req.app.state.library

    if not files:
        raise HTTPException(status_code=400, detail="需要上传文件")
    if len(files) > BATCH_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"一次最多上传 {BATCH_MAX} 本（当前 {len(files)} 本）",
        )
    fmt = (format or "").strip().lower()
    if fmt not in FORMAT_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"必须选择格式（可选: {', '.join(FORMAT_SUFFIXES)}）",
        )
    allowed = FORMAT_SUFFIXES[fmt]
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in allowed:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"混格式或与所选格式不一致: {f.filename or '(无文件名)'} "
                    f"（{fmt} 允许 {'/'.join(sorted(allowed))}，一次只能上传同一格式）"
                ),
            )

    results: list[dict] = []
    registered: list[tuple[str, object]] = []
    seen_hashes: set[str] = set()

    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        for f in files:
            fname = f.filename or f"batch_upload_{len(results)}"
            try:
                tmp = tmpdir / fname
                tmp.write_bytes(f.file.read())

                from app.ingest.cleaner import sha256_file

                content_hash = sha256_file(tmp)
                if content_hash in seen_hashes:
                    results.append({
                        "filename": fname, "status": "duplicate",
                        "duplicate": True, "error": "本批内重复",
                    })
                    continue
                seen_hashes.add(content_hash)

                existing = state.repo.book_by_hash(content_hash)
                if existing:
                    results.append({
                        "filename": fname, "book_id": existing["book_id"],
                        "title": existing["title"], "status": "duplicate",
                        "duplicate": True, "error": "已在馆内",
                    })
                    continue

                ingested = _ingest_path(state, tmp)
            except Exception as e:  # noqa: BLE001  单本失败不阻断其余
                results.append({
                    "filename": fname, "status": "error",
                    "duplicate": False, "error": str(e),
                })
                continue

            book = _register_book(state, ingested, private=private, notify=False)
            results.append({
                "filename": fname, "book_id": book["book_id"],
                "title": book["title"], "status": "registered",
                "duplicate": False, "error": None,
            })
            registered.append((book["book_id"], ingested))

    # WS 汇总通知（补书室新书，一次）
    if registered:
        state.ws.broadcast({
            "type": "notice",
            "event": "batch_ingested",
            "count": len(registered),
            "books": [{"book_id": bid, "title": ing.title}
                      for bid, ing in registered],
        })
        # 异步索引：登记完成后立即返回，索引在后台 + WS 进度
        _start_background_index(state, registered)

    return {
        "ok": True,
        "accepted": len(registered),
        "duplicates": sum(1 for r in results if r["duplicate"]),
        "errors": sum(1 for r in results if r["status"] == "error"),
        "total": len(files),
        "results": results,
    }


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
