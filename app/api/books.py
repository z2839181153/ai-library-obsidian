"""书 API：列表/详情/补书室/分类建议/确认上架/原文阅读（设计文档 §9.2）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/books", tags=["books"])


class ClassifyRequest(BaseModel):
    force: bool = False


class ConfirmRequest(BaseModel):
    floor: str | None = None      # 楼层 code（如 1F）或 floor_id；缺省用建议
    room: str | None = None
    shelf: str | None = None


@router.get("")
def list_books(req: Request, status: str | None = None,
               floor: str | None = None, room: str | None = None,
               q: str | None = None) -> dict:
    """列书。status=reviewing 即补书室（建议区=有分类建议，待定区=无）。"""
    state = req.app.state.library
    sql = "SELECT * FROM books"
    conds, params = [], []
    if status:
        conds.append("status=?")
        params.append(status)
    if floor:
        conds.append("suggest_floor=? OR vault_path LIKE ?")
        params.extend([floor, f"{floor}-%"])
    if room:
        conds.append("suggest_room=? OR vault_path LIKE ?")
        params.extend([room, f"%/{room}/%"])
    if q:
        conds.append("(title LIKE ? OR book_id LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY updated_at DESC"
    rows = state.repo.conn.execute(sql, params).fetchall()

    books = []
    for r in rows:
        d = dict(r)
        card = state.repo.get_card(d["book_id"])
        books.append({
            "book_id": d["book_id"],
            "title": d["title"],
            "media_type": d["media_type"],
            "status": d["status"],
            "suggest": {
                "floor": d.get("suggest_floor") or "",
                "room": d.get("suggest_room") or "",
                "shelf": d.get("suggest_shelf") or "",
            },
            "vault_path": d.get("vault_path") or "",
            "private": bool(d.get("private")),
            "distill_value": card["distill_value"] if card else None,
            "has_card": card is not None,
            "updated_at": d.get("updated_at"),
        })
    return {"books": books, "count": len(books)}


@router.get("/{book_id}")
def get_book(req: Request, book_id: str) -> dict:
    state = req.app.state.library
    book = state.repo.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书不存在")
    card = state.repo.get_card(book_id)
    card_parsed = None
    if card:
        card_parsed = dict(card)
        for k in ("chapters", "concepts", "tags", "skills"):
            import json

            card_parsed[k] = json.loads(card_parsed.get(k) or "[]")
    return {
        "book": book,
        "card": card_parsed,
        "actions": state.repo.list_actions(target_type="book", target_id=book_id, limit=10),
    }


@router.post("/{book_id}/classify")
def classify_book(req: Request, book_id: str, body: ClassifyRequest) -> dict:
    """生成图书卡片 + 分类建议（补书室入口）。"""
    state = req.app.state.library
    if not state.repo.get_book(book_id):
        raise HTTPException(status_code=404, detail="书不存在")
    try:
        result = state.cards.generate(book_id, force=body.force)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if result.error:
        raise HTTPException(status_code=503, detail=result.error)
    return {
        "book_id": book_id,
        "skipped": result.skipped,
        "private_skip": result.private_skip,
        "card_path": str(result.card_path) if result.card_path else None,
        "suggest": result.suggest,
    }


@router.post("/{book_id}/confirm")
def confirm_book(req: Request, book_id: str, body: ConfirmRequest) -> dict:
    """主人确认上架（可覆盖分类建议）。"""
    state = req.app.state.library
    try:
        result = state.shelver.confirm_shelve(
            book_id, floor=body.floor, room=body.room, shelf=body.shelf
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return result


@router.get("/{book_id}/content")
def book_content(req: Request, book_id: str) -> dict:
    """原文阅读：按 chunk seq 分节返回（shelved 优先读 vault 文件）。"""
    state = req.app.state.library
    book = state.repo.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书不存在")

    # shelved：读 vault 的 book.md；未上架：chunks 拼接
    text = None
    if book.get("vault_path"):
        vp = state.cfg.paths.vault_dir / book["vault_path"] / "book.md"
        if vp.exists():
            text = vp.read_text(encoding="utf-8")

    sections = []
    if text is not None:
        cur, cur_title = [], "全文"
        for line in text.splitlines():
            if line.startswith("## "):
                if cur:
                    sections.append({"title": cur_title, "content": "\n".join(cur).strip()})
                cur_title = line[3:].strip()
                cur = []
            else:
                cur.append(line)
        if cur:
            sections.append({"title": cur_title, "content": "\n".join(cur).strip()})
    else:
        rows = state.repo.conn.execute(
            "SELECT section, content, seq FROM chunks WHERE book_id=? ORDER BY seq", (book_id,)
        ).fetchall()
        for r in rows:
            sections.append({"title": r["section"] or f"片段{r['seq']}", "content": r["content"]})

    return {"book_id": book_id, "title": book.get("title"), "sections": sections}
