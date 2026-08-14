"""借书证画像 API（设计文档 §5.4 / §9.2；P4-4 设置完善）。

- GET /api/profile/stats：藏书分布（楼层/房间/标签/主题）+ 方向池 + 收藏/拒绝历史 + 偏好
- PUT /api/profile/direction-pool：编辑采购方向池（增删改权重）
- PUT /api/profile/themes：编辑主题画像（用户可手动调整）

前端：设置页"画像" tab —— 藏书分布饼图（ECharts）+ 方向池编辑 + 收藏/拒绝历史。
"""
from __future__ import annotations

import json
from collections import Counter

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/profile", tags=["profile"])


def _room_from_path(vault_path: str) -> str:
    """从 vault_path 提取房间名：books/1F-电子书/机器学习/入门/机器学习基础 → 机器学习。"""
    if not vault_path:
        return "（未上架）"
    parts = vault_path.strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "books":
        return parts[2]
    return "（未分房间）"


def _tags_counter(state) -> dict[str, int]:
    """从 catalog_cards.tags（+ books.tags 兜底）聚合标签计数。"""
    counter: Counter = Counter()
    for r in state.repo.conn.execute(
        "SELECT tags FROM catalog_cards WHERE tags IS NOT NULL AND tags != ''"
    ):
        try:
            tags = json.loads(r["tags"] or "[]")
        except json.JSONDecodeError:
            tags = []
        for t in tags:
            counter[t] += 1
    # books.tags 兜底（暂无卡片的书）
    for r in state.repo.conn.execute(
        "SELECT tags FROM books WHERE tags IS NOT NULL AND tags != '' "
        "AND book_id NOT IN (SELECT book_id FROM catalog_cards)"
    ):
        try:
            tags = json.loads(r["tags"] or "[]")
        except json.JSONDecodeError:
            tags = []
        for t in tags:
            counter[t] += 1
    return dict(counter.most_common())


@router.get("/stats")
def profile_stats(req: Request) -> dict:
    """借书证画像统计（只读 SQL 聚合，无 LLM）。"""
    state = req.app.state.library
    conn = state.repo.conn
    profile = state.repo.get_profile()

    # ① 藏书分布：按楼层
    floors_dist = []
    for f in state.repo.list_floors():
        code = f["code"]
        n = conn.execute(
            "SELECT COUNT(*) c FROM books WHERE status='shelved' AND "
            "(vault_path LIKE ? OR vault_path LIKE ?)",
            (f"%/{code}-%", f"%/{code}/%"),
        ).fetchone()["c"]
        floors_dist.append({
            "floor_id": f["floor_id"], "code": code, "name": f["name"],
            "media_type": f["media_type"], "count": n,
        })

    # ② 藏书分布：按房间（shelved 书的 vault_path 解析）
    rooms_counter: Counter = Counter()
    for r in conn.execute("SELECT vault_path FROM books WHERE status='shelved'"):
        rooms_counter[_room_from_path(r["vault_path"])] += 1
    rooms_dist = [{"name": k, "count": v} for k, v in rooms_counter.most_common(20)]

    # ③ 藏书分布：按标签
    tags_dist = [{"tag": k, "count": v} for k, v in _tags_counter(state).items()]

    # ④ 主题画像（profile.themes；空则用标签兜底）
    themes = profile.get("themes", {}) or {}
    if not themes:
        themes = _tags_counter(state)
    themes_dist = [{"topic": k, "count": v} for k, v in themes.items()]

    # ⑤ 采购方向池
    direction_pool = profile.get("direction_pool", []) or []

    # ⑥ 收藏/拒绝历史（recommendations 按日期聚合）
    rows = conn.execute(
        "SELECT date, status, COUNT(*) AS c FROM recommendations "
        "GROUP BY date, status ORDER BY date"
    ).fetchall()
    by_date: dict[str, dict] = {}
    for r in rows:
        d = by_date.setdefault(r["date"], {
            "date": r["date"], "pending": 0, "collected": 0,
            "ignored": 0, "not_interested": 0,
        })
        if r["status"] in d:
            d[r["status"]] = int(r["c"])
    rec_history = list(by_date.values())

    totals = {"total": 0, "pending": 0, "collected": 0, "ignored": 0, "not_interested": 0}
    for r in conn.execute("SELECT status, COUNT(*) AS c FROM recommendations GROUP BY status"):
        if r["status"] in totals:
            totals[r["status"]] = int(r["c"])
        totals["total"] += int(r["c"])

    # ⑦ 最近推荐明细（收藏/拒绝列表展示）
    recent_recs = state.repo.list_recommendations(limit=30)

    return {
        "floors_dist": floors_dist,
        "rooms_dist": rooms_dist,
        "tags_dist": tags_dist,
        "themes_dist": themes_dist,
        "direction_pool": direction_pool,
        "rec_history": rec_history,
        "rec_totals": totals,
        "recent_recs": recent_recs,
        "prefs": profile.get("prefs", {}),
        "updated": profile.get("updated"),
    }


class DirectionPoolItem(BaseModel):
    topic: str
    weight: int = 1
    source: str = "manual"
    first_seen: str = ""


class DirectionPoolUpdate(BaseModel):
    direction_pool: list[DirectionPoolItem]


@router.put("/direction-pool")
def update_direction_pool(req: Request, body: DirectionPoolUpdate) -> dict:
    """编辑采购方向池（增删改权重；前端列表编辑）。"""
    state = req.app.state.library
    cleaned = []
    for item in body.direction_pool:
        topic = (item.topic or "").strip()
        if not topic:
            continue
        cleaned.append({
            "topic": topic,
            "weight": max(0, int(item.weight)),
            "source": (item.source or "manual").strip() or "manual",
            "first_seen": (item.first_seen or "").strip(),
        })
    state.repo.save_profile(direction_pool=cleaned)
    state.repo.insert_action({
        "agent": "owner",
        "action_type": "profile_update",
        "target_type": "profile",
        "target_id": "direction_pool",
        "params": {"count": len(cleaned)},
        "undo_params": {},
        "reason": f"主人编辑采购方向池（{len(cleaned)} 条）",
    })
    return {"ok": True, "direction_pool": cleaned}


class ThemesUpdate(BaseModel):
    themes: dict[str, int]


@router.put("/themes")
def update_themes(req: Request, body: ThemesUpdate) -> dict:
    """编辑主题画像（用户可手动调整藏书主题权重）。"""
    state = req.app.state.library
    themes = {k: max(0, int(v)) for k, v in body.themes.items() if k and k.strip()}
    state.repo.save_profile(themes=themes)
    return {"ok": True, "themes": themes}
