"""书 API：列表/详情/补书室/分类建议/确认上架/原文阅读（设计文档 §9.2）。"""
from __future__ import annotations

import concurrent.futures
import json

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
               q: str | None = None, tag: str | None = None) -> dict:
    """列书。status=reviewing 即补书室（建议区=有分类建议，待定区=无）。

    tag=xxx：虚拟书架按标签过滤（匹配 catalog_cards.tags / books.tags JSON 元素）。
    """
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
    if tag:
        # JSON 数组元素匹配（如 ["人工智能", "知识库"] → %"人工智能"%）
        conds.append(
            "(book_id IN (SELECT book_id FROM catalog_cards WHERE tags LIKE ?) "
            "OR tags LIKE ?)"
        )
        params.extend([f'%"{tag}"%', f'%"{tag}"%'])
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY updated_at DESC"
    rows = state.repo.conn.execute(sql, params).fetchall()

    books = []
    for r in rows:
        d = dict(r)
        card = state.repo.get_card(d["book_id"])
        tags = []
        if card and card.get("tags"):
            try:
                tags = json.loads(card["tags"] or "[]")
            except json.JSONDecodeError:
                tags = []
        if not tags and d.get("tags"):
            try:
                tags = json.loads(d.get("tags") or "[]")
            except json.JSONDecodeError:
                tags = []
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
            "tags": tags,
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


@router.post("/{book_id}/delete")
def delete_book(req: Request, book_id: str) -> dict:
    """主人删除书 → 档案馆（软删除，30 天可恢复）。"""
    state = req.app.state.library
    book = state.repo.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书不存在")
    if book.get("status") == "deleted":
        raise HTTPException(status_code=400, detail="书已在档案馆")
    deleted = state.repo.soft_delete_book(book_id)
    state.repo.insert_action({
        "agent": "owner",
        "action_type": "delete",
        "target_type": "book",
        "target_id": book_id,
        "params": {"title": book.get("title", book_id)},
        "undo_params": {"prev_status": book.get("status") or "incoming"},
        "reason": f"主人删除《{book.get('title', book_id)}》（可恢复 30 天）",
    })
    return {"ok": True, "book": deleted}


# ---------- P4-3 阅览室联动：相关技能 / 相关笔记 ----------

def _book_room(book: dict) -> str:
    """从 vault_path（books/<楼层>/<房间>/<书架>/<书名>）第二段取房间；未上架用建议房间。"""
    vp = book.get("vault_path") or ""
    if vp:
        parts = vp.split("/")
        if len(parts) >= 3 and parts[2]:
            return parts[2]
    return book.get("suggest_room") or ""


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _with_timeout(fn, timeout: float = 5.0):
    """在后台线程执行 fn，超时返回 None（线程继续跑完，不阻塞请求）。

    用于 related 的 embedding/混合检索：无 key 或 API 慢（如 ModelScope 429）
    时降级跳过相似部分，同房间/同书结果立即返回。
    """
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(fn)
    try:
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        return None
    except Exception:  # noqa: BLE001  调用失败同样降级
        return None
    finally:
        ex.shutdown(wait=False)


def _books_in_room(state, room: str, exclude: str) -> list[dict]:
    """同房间（vault_path 段匹配 或 建议房间相同）的非删除书，排除 exclude。"""
    esc = _escape_like(room)
    rows = state.repo.conn.execute(
        "SELECT * FROM books "
        "WHERE status != 'deleted' AND book_id != ? "
        "AND (vault_path LIKE ? ESCAPE '\\' OR suggest_room = ?) "
        "ORDER BY updated_at DESC",
        (exclude, f"%/{esc}/%", room),
    ).fetchall()
    return [dict(r) for r in rows]


def _skill_dto(s: dict, book_title: str, relation: str,
               similarity: float | None = None) -> dict:
    return {
        "skill_id": s["skill_id"],
        "name": s.get("name") or s["skill_id"],
        "slug": s.get("slug") or "",
        "status": s.get("status"),
        "description": s.get("description") or "",
        "book_id": s.get("book_id") or "",
        "book_title": book_title,
        "relation": relation,
        "similarity": similarity,
    }


@router.get("/{book_id}/related")
def related(req: Request, book_id: str, top_n: int = 6) -> dict:
    """P4-3 阅览室右侧面板：相关技能（同书蒸馏/同房间/描述相似）+ 相关笔记（同房间/向量相似）。

    全部只读、本地计算；embedding / 技能索引不可用时静默降级（同房间仍返回）。
    """
    state = req.app.state.library
    book = state.repo.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书不存在")

    room = _book_room(book)
    card = state.repo.get_card(book_id)
    # 检索 query：标题 + 卡片摘要（无卡片仅标题）
    query_text = " ".join(
        p for p in (book.get("title") or "", (card or {}).get("summary") or "") if p
    ) or book_id

    active_status = ("draft", "reviewing", "approved", "installed")

    # ---------- 相关技能 ----------
    skills: list[dict] = []
    seen_skills: set[str] = set()

    def _add_skill(s: dict, relation: str, similarity: float | None = None) -> None:
        if not s or s["skill_id"] in seen_skills or s.get("status") not in active_status:
            return
        seen_skills.add(s["skill_id"])
        b = state.repo.get_book(s.get("book_id")) if s.get("book_id") else None
        skills.append(_skill_dto(s, (b or {}).get("title") or "", relation, similarity))

    # 1a 同书蒸馏技能
    for s in state.repo.list_skills(book_id=book_id):
        _add_skill(s, "same_book")
    # 1b 同房间其他书的技能
    if room:
        for b in _books_in_room(state, room, exclude=book_id):
            for s in state.repo.list_skills(book_id=b["book_id"]):
                _add_skill(s, "same_room")
    # 1c/2b 共用 embedding 探测：3s 超时 → 降级为纯词法（跳过技能向量相似）
    qvec = _with_timeout(lambda: state.embed.embed_one(query_text), timeout=3.0)

    if qvec is not None:
        # 1c 描述相似（技能库向量余弦；索引缺失时静默跳过）
        try:
            for h in state.skill_index.search(qvec, top_k=top_n * 2):
                _add_skill(state.repo.get_skill(h["skill_id"]), "similar",
                           similarity=round(h["_distance"], 3))
        except Exception:  # noqa: BLE001
            pass

    # ---------- 相关笔记 ----------
    notes: list[dict] = []
    seen_notes: set[str] = set()

    def _add_note(b: dict, relation: str, score: float | None = None,
                  section: str = "", snippet: str = "") -> None:
        if not b or b["book_id"] in seen_notes:
            return
        seen_notes.add(b["book_id"])
        notes.append({
            "book_id": b["book_id"],
            "title": b.get("title") or b["book_id"],
            "status": b.get("status"),
            "vault_path": b.get("vault_path") or "",
            "relation": relation,
            "score": score,
            "section": section,
            "snippet": snippet,
        })

    # 2a 同房间书
    if room:
        for b in _books_in_room(state, room, exclude=book_id):
            _add_note(b, "same_room")
    # 2b 向量相似 chunk：embedding 可用 → 混合检索；否则纯词法（均 3s 超时兜底）
    if qvec is not None:
        res = _with_timeout(lambda: state.searcher.search(query_text, top_k=top_n * 3),
                            timeout=3.0)
    else:
        res = _with_timeout(lambda: state.searcher.search_lexical(query_text, top_k=top_n * 3),
                            timeout=3.0)
    if res:
        for item in res["books"]:
            if item["book_id"] == book_id:
                continue
            b = state.repo.get_book(item["book_id"])
            if b is None or b.get("status") == "deleted":
                continue
            chunk = (item.get("hit_chunks") or [{}])[0]
            _add_note(b, "similar", score=item.get("score"),
                      section=chunk.get("section") or "",
                      snippet=(chunk.get("content") or "")[:160])

    # 排序：同房间优先，相似按分数降序；技能同书 > 同房间 > 相似
    rel_rank = {"same_room": 0, "similar": 1}
    notes.sort(key=lambda x: (rel_rank.get(x["relation"], 9), -(x["score"] or 0)))
    sk_rank = {"same_book": 0, "same_room": 1, "similar": 2}
    skills.sort(key=lambda x: (sk_rank.get(x["relation"], 9), -(x.get("similarity") or 0)))

    return {
        "book_id": book_id,
        "room": room,
        "skills": skills[:top_n],
        "notes": notes[:top_n],
    }


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
