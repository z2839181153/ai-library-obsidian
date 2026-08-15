"""占星室 API（设计文档 §9.1 🌌占星室 / §11 P4 / §11 P5-5 星图书↔书语义边）。

GET /api/starmap → 图数据（nodes + links），供前端 ECharts graph 渲染。

节点类型：
- book       书（status: incoming/reviewing=补书室未归位星；shelved=已归位）
- skill      技能（distill 产物）
- theme      主题（房间 rooms）
- archive    档案（原始不可变副本）
- conversation 对话

链接：
- book ↔ theme：按 vault_path / suggest_room 解析房间归属
- book ↔ skill：skills.book_id 关联
- book ↔ archive：books.raw_path 指向 archive/raw/<hash> 原始副本
- conversation ↔ book：消息 refs 中引用的书
- book ↔ book（P5-5）：卡片级向量两两相似（semantic）+ 规则边（same_room 同房间 /
  same_tag 同标签 / references 正文引用 [[catalog/bk_*]]）；top-k 截断防毛线球；
  embedding 不可用时词法兜底（jieba 词袋余弦），向量结果走 SQLite embedding_cache
  本地缓存。
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import re
import time
from pathlib import Path

from fastapi import APIRouter, Request

router = APIRouter(tags=["starmap"])

# ---------- P5-5 书↔书语义边参数 ----------
_BOOK_TOP_K = 5        # 每本书最多保留的语义邻居数（top-k 截断防毛线球）
_RULE_TOP_K = 8        # 每本书最多保留的规则边总数（同房间/同标签/引用 共享上限）
_SIM_THRESHOLD = 0.45  # 卡片向量余弦相似度阈值（Qwen3-Embedding 尺度）
_LEX_THRESHOLD = 0.30  # 词法兜底（jieba 词袋余弦）阈值
_EMBED_TIMEOUT = 4.0   # embedding 批处理超时（超时/异常 → 词法兜底）
_BOOK_BOOK_RELATIONS = ("semantic", "same_room", "same_tag", "references")
_WIKILINK_RE = re.compile(r"\[\[catalog/(bk_[A-Za-z0-9]+)")


def _with_timeout(fn, timeout: float = _EMBED_TIMEOUT):
    """后台线程执行 fn，超时/异常返回 None（线程继续跑完，不阻塞请求）。

    与 books.py related 的 embedding 探测同一模式：无 key / 429 / 慢 API 时
    降级为词法兜底，保证 /api/starmap 秒级返回。
    """
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(fn)
    try:
        return fut.result(timeout=timeout)
    except Exception:  # noqa: BLE001
        return None
    finally:
        ex.shutdown(wait=False)


def _card_text(book: dict, card: dict | None) -> str:
    """卡片级语义文本：标题 + 摘要 + 标签 + 类别（无卡片仅标题）。"""
    parts: list[str] = []
    if book.get("title"):
        parts.append(book["title"])
    if card:
        if card.get("summary"):
            parts.append(card["summary"])
        try:
            tags = json.loads(card.get("tags") or "[]")
            if tags:
                parts.append(" ".join(tags))
        except json.JSONDecodeError:
            pass
        if card.get("category"):
            parts.append(card["category"])
    return " ".join(parts)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _lexical_vector(text: str) -> dict[str, float]:
    """jieba 词袋（L2 归一化）——纯本地词法相似度兜底，无 API 调用。"""
    import jieba

    counts: dict[str, float] = {}
    for w in jieba.cut(text):
        w = w.strip()
        if not w:
            continue
        counts[w] = counts.get(w, 0.0) + 1.0
    norm = math.sqrt(sum(v * v for v in counts.values())) or 1.0
    return {k: v / norm for k, v in counts.items()}


def _lexical_cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    return sum(v * big.get(k, 0.0) for k, v in small.items())


def _semantic_edges(items: list, vecs: list, sim_fn, threshold: float,
                    top_k: int) -> list[dict]:
    """卡片向量两两相似 → top-k 截断（每本书最多 top_k 条语义边）。

    items: [(book, vector)] 对齐列表；vecs 传 list（向量余弦）或 dict（词袋余弦）。
    贪心按相似度降序连边：先满足高相似对，且每本书度数 ≤ top_k，防毛线球。
    """
    n = len(items)
    pairs: list[tuple[float, int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            sim = sim_fn(vecs[i], vecs[j])
            if sim >= threshold:
                pairs.append((sim, i, j))
    pairs.sort(key=lambda x: x[0], reverse=True)

    edge_count = {b["book_id"]: 0 for b, _ in items}
    seen: set[tuple[str, str]] = set()
    links: list[dict] = []
    for sim, i, j in pairs:
        a, b = items[i][0]["book_id"], items[j][0]["book_id"]
        if (a, b) in seen or (b, a) in seen:
            continue
        if edge_count[a] >= top_k or edge_count[b] >= top_k:
            continue
        seen.add((a, b))
        seen.add((b, a))
        edge_count[a] += 1
        edge_count[b] += 1
        links.append({
            "source": a, "target": b, "relation": "semantic",
            "similarity": round(sim, 4),
        })
    return links


def _book_references(conn, book_ids: list[str]) -> dict[str, set[str]]:
    """扫描各书 chunks 正文里的 [[catalog/bk_xxx]] 引用 → {book_id: {ref_book_id}}。"""
    refs: dict[str, set[str]] = {}
    if not book_ids:
        return refs
    ph = ",".join("?" for _ in book_ids)
    rows = conn.execute(
        f"SELECT book_id, content FROM chunks WHERE book_id IN ({ph})",
        book_ids,
    ).fetchall()
    for r in rows:
        found = _WIKILINK_RE.findall(r["content"] or "")
        if found:
            refs.setdefault(r["book_id"], set()).update(found)
    return refs


def _greedy_group_edges(members, links, relation, edge_count, seen_pairs,
                        top_k: int) -> None:
    """组内（同房间/同标签）连边：每本书最多 top_k 条，成对去重（防毛线球）。"""
    members = sorted(set(members))
    for i, a in enumerate(members):
        for b in members[i + 1:]:
            key = (a, b)
            if key in seen_pairs:
                continue
            if edge_count[a] >= top_k or edge_count[b] >= top_k:
                continue
            seen_pairs.add(key)
            edge_count[a] += 1
            edge_count[b] += 1
            links.append({"source": a, "target": b, "relation": relation})


# 书↔书边整体缓存（本地缓存：数据未变则直接复用，embedding 也走 SQLite embedding_cache）
_book_book_cache: dict = {"signature": None, "edges": None, "source": None}


def _book_book_edges(state, books: list[dict], book_index: dict) -> tuple[list[dict], str]:
    """计算全部书↔书边（语义 + 规则），返回 (links, semantic_source)。

    semantic_source: embedding（卡片向量）| lexical（词法兜底）| none（书不足 2 本）。
    已删除书（status=deleted）不参与书↔书边。
    """
    active = [b for b in books if b.get("status") != "deleted"]
    if len(active) < 2:
        return [], "none"

    cards = {b["book_id"]: state.repo.get_card(b["book_id"]) for b in active}
    texts = {b["book_id"]: _card_text(b, cards[b["book_id"]]) for b in active}

    # 签名：books 关键字段 + 卡片语义文本（变化才重算）
    sig = hashlib.sha256(
        json.dumps(
            [
                (b["book_id"], b.get("updated_at"), b.get("status"),
                 b.get("vault_path"), b.get("suggest_room"), texts[b["book_id"]])
                for b in active
            ],
            ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    if _book_book_cache["signature"] == sig and _book_book_cache["edges"] is not None:
        return [dict(e) for e in _book_book_cache["edges"]], _book_book_cache["source"]

    links: list[dict] = []
    sem_source = "none"

    # 1) 语义边：卡片级向量两两相似（批量 embed + 本地缓存；失败/超时 → 词法兜底）
    sem_books = [b for b in active if texts[b["book_id"]].strip()]
    if len(sem_books) >= 2:
        sem_texts = [texts[b["book_id"]] for b in sem_books]
        vecs = _with_timeout(
            lambda: state.embed.embed_many(sem_texts), timeout=_EMBED_TIMEOUT
        )
        if vecs is not None and len(vecs) == len(sem_books):
            sem_source = "embedding"
            links.extend(_semantic_edges(
                [(b, vecs[i]) for i, b in enumerate(sem_books)],
                vecs, _cosine, _SIM_THRESHOLD, _BOOK_TOP_K,
            ))
        else:
            sem_source = "lexical"
            lex_vecs = [_lexical_vector(t) for t in sem_texts]
            links.extend(_semantic_edges(
                [(b, lex_vecs[i]) for i, b in enumerate(sem_books)],
                lex_vecs, _lexical_cosine, _LEX_THRESHOLD, _BOOK_TOP_K,
            ))

    # 2) 规则边：同房间 / 同标签 / 引用（每本书规则边总数 ≤ _RULE_TOP_K）
    room_groups: dict[str, list[str]] = {}
    for b in active:
        room = None
        vp = b.get("vault_path") or ""
        if vp:
            parts = [p for p in vp.split("/") if p]
            if len(parts) >= 3:
                room = parts[2]
        if not room and b.get("suggest_room"):
            room = b["suggest_room"]
        if room:
            room_groups.setdefault(room, []).append(b["book_id"])

    tag_groups: dict[str, list[str]] = {}
    for b in active:
        for t in (book_index[b["book_id"]].get("tags") or []):
            tag_groups.setdefault(t, []).append(b["book_id"])

    refs = _book_references(state.repo.conn, [b["book_id"] for b in active])

    edge_count = {b["book_id"]: 0 for b in active}
    seen_pairs: set[tuple[str, str]] = set()
    for group in room_groups.values():
        _greedy_group_edges(group, links, "same_room", edge_count, seen_pairs, _RULE_TOP_K)
    for group in tag_groups.values():
        _greedy_group_edges(group, links, "same_tag", edge_count, seen_pairs, _RULE_TOP_K)
    for a, targets in refs.items():
        for t in targets:
            if t == a or t not in book_index or t not in edge_count:
                continue
            key = tuple(sorted((a, t)))
            if key in seen_pairs:
                continue
            if edge_count[a] >= _RULE_TOP_K or edge_count[t] >= _RULE_TOP_K:
                continue
            seen_pairs.add(key)
            edge_count[a] += 1
            edge_count[t] += 1
            links.append({"source": a, "target": t, "relation": "references"})

    _book_book_cache["signature"] = sig
    _book_book_cache["edges"] = [dict(e) for e in links]
    _book_book_cache["source"] = sem_source
    return [dict(e) for e in links], sem_source


def _ts(dt_str: str | None) -> float:
    """'YYYY-MM-DDTHH:MM:SS+08:00' → epoch；解析失败返回 0。"""
    if not dt_str:
        return 0.0
    s = dt_str.replace("Z", "+00:00")
    try:
        # 兼容带时区与不带时区
        if "+" in s[10:]:
            from datetime import datetime

            return datetime.fromisoformat(s).timestamp()
        return time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return 0.0


def _room_name_from_vault(vault_path: str) -> str | None:
    """vault_path: books/1F-电子书/机器学习/入门/机器学习基础 → 房间=机器学习。"""
    parts = [p for p in vault_path.split("/") if p]
    if len(parts) >= 3:
        return parts[2]
    return None


@router.get("/starmap")
def starmap(req: Request) -> dict:
    state = req.app.state.library
    repo = state.repo
    conn = repo.conn
    nodes: list[dict] = []
    links: list[dict] = []
    book_index: dict[str, dict] = {}   # book_id -> node
    room_nodes: dict[str, dict] = {}   # room_id -> node
    skill_nodes: dict[str, dict] = {}  # skill_id -> node
    arc_nodes: dict[str, dict] = {}    # hash -> node

    # ---------- 主题节点（房间） ----------
    for rm in repo.list_rooms():
        node = {
            "id": rm["room_id"],
            "type": "theme",
            "name": rm["name"],
            "floor_id": rm.get("floor_id", ""),
            "created_at": rm.get("created_at", ""),
        }
        nodes.append(node)
        room_nodes[rm["room_id"]] = node
    # 楼层信息（前端着色用）
    floors = {f["floor_id"]: f for f in repo.list_floors()}
    for node in nodes:
        if node["type"] == "theme":
            f = floors.get(node.get("floor_id", ""))
            node["floor_name"] = f["name"] if f else ""
            node["floor_code"] = f["code"] if f else ""

    # ---------- 书节点 ----------
    books = repo.all_books()
    for b in books:
        card = repo.get_card(b["book_id"])
        node = {
            "id": b["book_id"],
            "type": "book",
            "name": b.get("title") or b["book_id"],
            "status": b.get("status") or "incoming",
            "media_type": b.get("media_type") or "",
            "created_at": b.get("created_at") or "",
            "ts": _ts(b.get("created_at")),
            "tags": [],
            "distill_value": card["distill_value"] if card else None,
            "category": card["category"] if card else None,
            "vault_path": b.get("vault_path") or "",
            "private": bool(b.get("private")),
            "has_card": card is not None,
        }
        if card:
            try:
                node["tags"] = json.loads(card.get("tags") or "[]")
            except json.JSONDecodeError:
                node["tags"] = []
        nodes.append(node)
        book_index[b["book_id"]] = node

        # book ↔ theme：按 vault_path（shelved）或 suggest（reviewing）
        theme_node = None
        if b.get("vault_path"):
            rm_name = _room_name_from_vault(b["vault_path"])
            if rm_name:
                for rm in repo.list_rooms():
                    if rm["name"] == rm_name:
                        theme_node = room_nodes.get(rm["room_id"])
                        break
        elif b.get("suggest_room"):
            for rm in repo.list_rooms():
                if rm["name"] == b["suggest_room"]:
                    theme_node = room_nodes.get(rm["room_id"])
                    break
        if theme_node:
            links.append({
                "source": b["book_id"], "target": theme_node["id"],
                "relation": "shelved_in" if b.get("status") == "shelved" else "suggested",
            })

        # book ↔ archive：原始副本
        raw = b.get("raw_path") or b.get("source_uri") or ""
        if raw and (Path(raw).exists() or (state.cfg.paths.data_dir / raw).exists()):
            h = (b.get("content_hash") or Path(raw).name)
            arc_id = f"arc_{h[:16]}"
            if arc_id not in arc_nodes:
                arc_node = {
                    "id": arc_id,
                    "type": "archive",
                    "name": f"原始副本·{b.get('title') or b['book_id']}",
                    "hash": h,
                    "created_at": b.get("created_at") or "",
                }
                nodes.append(arc_node)
                arc_nodes[arc_id] = arc_node
            links.append({
                "source": b["book_id"], "target": arc_id,
                "relation": "raw_copy",
            })

    # ---------- 技能节点 ----------
    for sk in repo.list_skills():
        node = {
            "id": sk["skill_id"],
            "type": "skill",
            "name": sk.get("name") or sk["skill_id"],
            "status": sk.get("status") or "draft",
            "book_id": sk.get("book_id") or "",
            "created_at": sk.get("created_at") or "",
        }
        nodes.append(node)
        skill_nodes[sk["skill_id"]] = node
        if sk.get("book_id") and sk["book_id"] in book_index:
            links.append({
                "source": sk["book_id"], "target": sk["skill_id"],
                "relation": "distilled",
            })

    # ---------- 对话节点 + 对话↔书 ----------
    cv_rows = conn.execute(
        "SELECT cv_id, title, created_at, updated_at, archived_book_id "
        "FROM conversations ORDER BY updated_at DESC LIMIT 100"
    ).fetchall()
    for cv in cv_rows:
        node = {
            "id": cv["cv_id"],
            "type": "conversation",
            "name": cv["title"] or f"对话 {cv['cv_id']}",
            "created_at": cv["created_at"] or "",
            "updated_at": cv["updated_at"] or "",
        }
        nodes.append(node)
        # 对话归档为书
        if cv["archived_book_id"] and cv["archived_book_id"] in book_index:
            links.append({
                "source": cv["cv_id"], "target": cv["archived_book_id"],
                "relation": "archived",
            })
        # 消息 refs 引用
        msg_rows = conn.execute(
            "SELECT refs FROM messages WHERE cv_id=? AND refs IS NOT NULL AND refs != ''",
            (cv["cv_id"],),
        ).fetchall()
        for m in msg_rows:
            try:
                refs = json.loads(m["refs"])
            except json.JSONDecodeError:
                continue
            for ref in refs:
                bid = None
                if isinstance(ref, dict):
                    bid = ref.get("book_id")
                if bid and bid in book_index:
                    links.append({
                        "source": cv["cv_id"], "target": bid,
                        "relation": "referenced",
                    })

    # ---------- P5-5：书↔书语义边 + 规则边 ----------
    bb_links, sem_source = _book_book_edges(state, books, book_index)
    links.extend(bb_links)

    bb_by_rel: dict[str, int] = {r: 0 for r in _BOOK_BOOK_RELATIONS}
    for l in bb_links:
        bb_by_rel[l["relation"]] = bb_by_rel.get(l["relation"], 0) + 1

    return {
        "nodes": nodes,
        "links": links,
        "counts": {
            "book": sum(1 for n in nodes if n["type"] == "book"),
            "skill": sum(1 for n in nodes if n["type"] == "skill"),
            "theme": sum(1 for n in nodes if n["type"] == "theme"),
            "archive": sum(1 for n in nodes if n["type"] == "archive"),
            "conversation": sum(1 for n in nodes if n["type"] == "conversation"),
        },
        "book_edges": {
            **bb_by_rel,
            "total": len(bb_links),
            "semantic_source": sem_source,  # embedding | lexical | none
        },
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
    }
