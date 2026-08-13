"""检索 API：索引运行/状态 + 混合检索（设计文档 §9.2 端点）。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["index", "search"])


class IndexRunRequest(BaseModel):
    rebuild: bool = False
    source_dir: str | None = None  # 缺省用 config.paths.vault_dir/books


class SearchRequest(BaseModel):
    query: str
    top_k: int = 20
    book_ids: list[str] | None = None


@router.post("/index/run")
def run_index(req: Request, body: IndexRunRequest) -> dict:
    state = req.app.state.library
    root = Path(body.source_dir) if body.source_dir else state.cfg.paths.vault_dir / "books"
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
    try:
        stats = state.indexer.run(root, rebuild=body.rebuild)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"索引失败: {e}") from e
    return {"ok": True, "stats": stats}


@router.get("/index/status")
def index_status(req: Request) -> dict:
    state = req.app.state.library
    st = state.repo.get_state()
    total_chunks = state.repo.conn.execute(
        "SELECT COUNT(*) AS c FROM chunks"
    ).fetchone()["c"]
    return {
        "revision": st["revision"] if st else 0,
        "status": st["status"] if st else "missing",
        "built_at": st["built_at"] if st else None,
        "total_chunks": total_chunks,
    }


@router.get("/index/check")
def index_check(req: Request) -> dict:
    state = req.app.state.library
    result = state.indexer.check()
    return {"ok": result["ok"], "rebuild_required": result["rebuild_required"],
            "counts": result["counts"], "issues": result["issues"]}


@router.post("/search")
def search(req: Request, body: SearchRequest) -> dict:
    if not body.query.strip():
        raise HTTPException(status_code=422, detail="query 不能为空")
    state = req.app.state.library
    return state.searcher.search(body.query, top_k=body.top_k, book_ids=body.book_ids)
