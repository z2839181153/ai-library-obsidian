"""书 API：列表/详情/补书室/分类建议/确认上架/原文阅读（设计文档 §9.2）。"""
from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
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
               q: str | None = None, tag: str | None = None,
               recent_read: bool = False, limit: int | None = None) -> dict:
    """列书。status=reviewing 即补书室（建议区=有分类建议，待定区=无）。

    tag=xxx：虚拟书架按标签过滤（匹配 catalog_cards.tags / books.tags JSON 元素）。
    recent_read=true：按最近阅读时间倒序（阅览室"继续阅读"），不含删除书。
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
    if recent_read:
        conds.append("status != 'deleted' AND last_read_at IS NOT NULL")
        sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY last_read_at DESC"
    else:
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY updated_at DESC"
    if limit and limit > 0:
        sql += f" LIMIT {int(limit)}"
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
            "last_read_at": d.get("last_read_at"),
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


# ---------- P5-3 离线读原文 ----------

def _resolve_raw_path(state, book: dict) -> Path | None:
    """定位 archive/raw 不可变副本：raw_path（绝对/相对 data_dir）+ content_hash 兜底。

    raw 文件是内容寻址的（archive/raw/<h2>/<hash>，无扩展名）。
    """
    if not book:
        return None
    raw = book.get("raw_path") or ""
    if raw:
        p = Path(raw)
        if p.exists():
            return p
        cand = state.cfg.paths.data_dir / raw
        if cand.exists():
            return cand
    h = book.get("content_hash")
    if h:
        p = state.cfg.paths.data_dir / "archive" / "raw" / h[:2] / h
        if p.exists():
            return p
    return None


def _split_sections(text: str) -> list[dict]:
    """按 `## ` 标题切分文本为阅读节；无标题时整段为"全文"。"""
    sections: list[dict] = []
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
    return sections


def _parse_raw_text(raw: Path, media_type: str) -> str:
    """按 media_type 解析原始副本（raw 无扩展名，不能按后缀路由）。

    返回清洗前原文文本；解析失败返回 ""（调用方降级为空正文）。
    """
    from app.ingest.parsers import parse_html, parse_markdown, parse_pdf, parse_text

    mt = (media_type or "").lower()
    try:
        if mt in ("pdf",):
            return parse_pdf(raw).text
        if mt in ("html", "htm", "web"):
            return parse_html(raw).text
        if mt in ("markdown", "md"):
            return parse_markdown(raw).text
        if mt in ("text", "txt", "chat", "other", ""):
            return parse_text(raw).text
    except Exception:  # noqa: BLE001  解析失败不阻塞阅读
        return ""
    return ""


@router.get("/{book_id}/content")
def book_content(req: Request, book_id: str) -> dict:
    """原文阅读：vault book.md → chunks 拼接 → archive/raw 原始副本（P5-3 离线兜底）。

    读取即记录 last_read_at（P5-4 阅览室"继续阅读"依据）。
    纯本地读取，不调 LLM——无额度/断网场景正文照常可读。
    返回 source：vault | chunks | raw（前端据此显示"离线原文"徽标）。
    """
    state = req.app.state.library
    book = state.repo.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书不存在")

    # 阅读标记：任何一次读取都更新最近阅读时间（soft 更新，不刷新 updated_at）
    try:
        state.repo.mark_book_read(book_id)
    except Exception:  # noqa: BLE001 阅读标记失败不影响阅读
        pass

    # 1) 已上架：读 vault 的 book.md
    text = None
    if book.get("vault_path"):
        vp = state.cfg.paths.vault_dir / book["vault_path"] / "book.md"
        if vp.exists():
            text = vp.read_text(encoding="utf-8")

    sections: list[dict] = []
    source = "vault"
    if text is not None:
        sections = _split_sections(text)
    else:
        # 2) 未上架：优先 chunks 拼接
        rows = state.repo.conn.execute(
            "SELECT section, content, seq FROM chunks WHERE book_id=? ORDER BY seq", (book_id,)
        ).fetchall()
        if rows:
            source = "chunks"
            for r in rows:
                sections.append({"title": r["section"] or f"片段{r['seq']}",
                                 "content": r["content"]})
        else:
            # 3) 无 chunks（未编目/未索引）：读 archive/raw 原始副本（P5-3 离线兜底）
            raw = _resolve_raw_path(state, book)
            raw_text = _parse_raw_text(raw, book.get("media_type") or "") if raw else ""
            if raw_text.strip():
                source = "raw"
                sections = _split_sections(raw_text)
                if not sections:
                    sections = [{"title": "全文", "content": raw_text.strip()}]

    return {
        "book_id": book_id,
        "title": book.get("title"),
        "sections": sections,
        "source": source,
        "raw_available": _resolve_raw_path(state, book) is not None,
    }


# media_type → 原始文件扩展名/MIME（raw 无后缀，Content-Type 靠它推断）
_RAW_EXT = {
    "markdown": (".md", "text/markdown; charset=utf-8"),
    "md": (".md", "text/markdown; charset=utf-8"),
    "text": (".txt", "text/plain; charset=utf-8"),
    "txt": (".txt", "text/plain; charset=utf-8"),
    "html": (".html", "text/html; charset=utf-8"),
    "htm": (".html", "text/html; charset=utf-8"),
    "web": (".html", "text/html; charset=utf-8"),
    "pdf": (".pdf", "application/pdf"),
    "epub": (".epub", "application/epub+zip"),
}
_FALLBACK_MIME = "application/octet-stream"


@router.get("/{book_id}/raw-file")
def book_raw_file(req: Request, book_id: str):
    """P5-3 原文件查看：返回 archive/raw 不可变副本二进制。

    PDF 用浏览器内置 viewer 直接渲染（iframe / 新标签页）；
    其余格式按 MIME 内联显示或下载。离线可用，不调 LLM。
    """
    state = req.app.state.library
    book = state.repo.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书不存在")
    raw = _resolve_raw_path(state, book)
    if raw is None or not raw.exists():
        raise HTTPException(status_code=404, detail="原始副本不存在（该书未留下不可变副本）")

    mt = (book.get("media_type") or "").lower()
    ext, mime = _RAW_EXT.get(mt, ("", _FALLBACK_MIME))
    filename = f"{(book.get('title') or book_id)}{ext}"
    # inline：浏览器内置 viewer 在 iframe/新标签页直接渲染（attachment 会强制下载）
    return FileResponse(raw, media_type=mime, filename=filename,
                        content_disposition_type="inline")
