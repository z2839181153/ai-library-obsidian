"""占星室 API（设计文档 §9.1 🌌占星室 / §11 P4）。

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
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, Request

router = APIRouter(tags=["starmap"])


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
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
    }
