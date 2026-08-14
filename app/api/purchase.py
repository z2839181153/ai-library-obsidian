"""采购员 API（设计文档 §6.6 / §9.2）。

- GET /api/purchase/today：今日配额卡片 + 推荐清单（pending）
- POST /api/purchase/generate：手动生成今日推荐（无搜索 key，用内置热门源种子 + 方向池启发式）
- POST /api/purchase/{rec_id}/collect：收藏入馆（走 ingest 管线）
- POST /api/purchase/{rec_id}/feedback：忽略 / 不感兴趣（写回 profile 负反馈）
- GET /api/daily-reports?date=：日报列表

保守模式（默认）：只出清单，主人点"收藏"才入馆；自动模式（score≥85）直接入馆。
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["purchase"])

# 内置热门源种子（无搜索 API key 时的公开热榜；可被 settings.purchase.sources 覆盖）
DEFAULT_HOT_SOURCES = [
    {"source": "arxiv", "title": "arXiv 最新论文精选",
     "url": "https://arxiv.org/list/cs.AI/recent",
     "topics": ["人工智能", "机器学习", "LLM Agent"]},
    {"source": "hn", "title": "Hacker News 热榜",
     "url": "https://news.ycombinator.com/",
     "topics": ["技术", "编程", "效率工具"]},
    {"source": "zhihu", "title": "知乎热榜",
     "url": "https://www.zhihu.com/hot",
     "topics": ["知识库", "写作", "学习方法"]},
]


class FeedbackRequest(BaseModel):
    action: str              # ignore | not_interested
    note: str = ""


def _today() -> str:
    return datetime.date.today().isoformat()


def _load_purchase_cfg(state) -> dict:
    raw = state.cfg and {}
    try:
        from app.config import DEFAULT_CONFIG_PATH
        import json as _json

        if DEFAULT_CONFIG_PATH.exists():
            raw = _json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        raw = {}
    return raw.get("purchase", {})


def _topics_from_profile(state) -> list[str]:
    """藏书主题分布（卡片 tags 聚合）作为采购方向池。"""
    profile = state.repo.get_profile()
    themes = profile.get("themes", {}) or {}
    # themes 可能为空 → 从卡片 tags 聚合
    if not themes:
        rows = state.repo.conn.execute(
            "SELECT tags FROM catalog_cards WHERE tags IS NOT NULL AND tags != ''"
        ).fetchall()
        counter: dict[str, int] = {}
        for r in rows:
            try:
                tags = json.loads(r["tags"] or "[]")
            except json.JSONDecodeError:
                tags = []
            for t in tags:
                counter[t] = counter.get(t, 0) + 1
        themes = dict(sorted(counter.items(), key=lambda kv: -kv[1])[:10])
    return list(themes.keys())


@router.get("/purchase/today")
def purchase_today(req: Request) -> dict:
    """今日配额 + 推荐清单。无推荐时返回空清单（前端提示可手动生成）。"""
    state = req.app.state.library
    today = _today()
    recs = state.repo.list_recommendations(date=today, limit=100)
    stats = state.repo.recommendation_stats(today)

    # 配额理由：藏书主题分布 → 比例
    topics = _topics_from_profile(state)
    max_daily = _load_purchase_cfg(state).get("max_daily_purchase", 5)
    quota_reason = (
        f"今日配额 {max_daily} 条。藏书主题分布："
        + ("、".join(topics[:6]) if topics else "暂无主题标签（新馆开张，先按热门源推荐）")
        + f"。方向池共 {len(state.repo.get_profile().get('direction_pool', []))} 条。"
    )

    return {
        "date": today,
        "quota": {"max_daily": max_daily, "reason": quota_reason, "stats": stats},
        "recommendations": recs,
        "auto_mode": bool((state.repo.get_profile().get("prefs") or {}).get("default_mode") == "auto"),
    }


@router.post("/purchase/generate")
def purchase_generate(req: Request) -> dict:
    """手动生成今日推荐（方向池 + 内置热门源种子启发式，评分理由透明）。"""
    state = req.app.state.library
    today = _today()
    existing = state.repo.list_recommendations(date=today, limit=100)
    if existing:
        # 已有推荐：不重复生成，返回现有
        return {"generated": 0, "date": today,
                "recommendations": existing,
                "note": "今日已有推荐，未重复生成"}

    topics = _topics_from_profile(state)
    direction_pool = state.repo.get_profile().get("direction_pool", []) or []
    pool_topics = [d.get("topic") for d in direction_pool if d.get("weight", 0) > 0]

    sources = _load_purchase_cfg(state).get("sources") or DEFAULT_HOT_SOURCES
    recs: list[dict] = []
    n = 0
    for src in sources:
        if n >= 5:
            break
        # 为每个来源取最相关主题（方向池 > 藏书主题 > 来源默认）
        s_topics = src.get("topics", [])
        match = next((t for t in pool_topics if t in s_topics), None) or \
            next((t for t in topics if t in s_topics), None) or s_topics[0]
        score = 55 + (10 if match in pool_topics else 0) + (5 if match in topics else 0)
        reason = (
            f"来源 {src['source']}（公开热榜）；命中方向{'池' if match in pool_topics else '主题'}：{match}；"
            f"评分 = 基准55 + 方向池命中10 + 主题命中5 = {score}"
        )
        recs.append({
            "rec_id": f"rec_{src['source']}_{today.replace('-', '')}_{n}",
            "date": today,
            "title": src["title"],
            "url": src["url"],
            "source": src["source"],
            "score": score,
            "reason": reason,
            "status": "pending",
        })
        n += 1

    for r in recs:
        state.repo.insert_recommendation(r)

    state.repo.insert_action({
        "agent": "purchaser",
        "action_type": "purchase_generate",
        "target_type": "recommendation",
        "target_id": today,
        "params": {"date": today, "count": len(recs)},
        "undo_params": {},
        "reason": f"生成 {today} 采购推荐清单（{len(recs)} 条）",
    })
    state.repo.insert_report({
        "date": today, "rtype": "purchase",
        "content": {"title": "今日采购推荐",
                    "items": [{"title": r["title"], "source": r["source"],
                               "score": r["score"]} for r in recs],
                    "note": "保守模式：仅推荐，主人确认后入馆"},
    })

    # WS 通知
    state.ws.broadcast({"type": "notice", "event": "purchase_ready",
                        "date": today, "count": len(recs)})
    return {"generated": len(recs), "date": today, "recommendations": recs}


@router.post("/purchase/{rec_id}/collect")
def purchase_collect(req: Request, rec_id: str) -> dict:
    """收藏入馆：创建书（文本入馆）→ 补书室 → rec status=collected。"""
    state = req.app.state.library
    rec = state.repo.get_recommendation(rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="推荐不存在")
    if rec["status"] == "collected":
        raise HTTPException(status_code=400, detail="该推荐已收藏")

    # 用推荐标题 + 理由构造一个简短 markdown 入馆（真实采集内容 P4 接抓取服务）
    md = (
        f"# {rec['title']}\n\n"
        f"> 来源：{rec.get('source', '')}  {rec.get('url', '')}\n\n"
        f"**采购员推荐理由**：{rec.get('reason', '')}\n\n"
        f"（P3 仅登记条目与理由；完整内容采集接入后自动补齐）\n"
    )
    # 复用入馆登记逻辑
    from app.ingest.cleaner import ingest_file as do_ingest

    with __import__("tempfile").TemporaryDirectory() as td:
        tmp = Path(td) / "purchase.md"
        tmp.write_text(md, encoding="utf-8")
        ingested = do_ingest(tmp, state.cfg.paths.data_dir / "archive" / "raw")

    from app.api.ingest import _register_and_index

    result = _register_and_index(state, ingested)
    state.repo.update_recommendation(rec_id, {"status": "collected"})
    state.repo.insert_action({
        "agent": "owner",
        "action_type": "purchase_collect",
        "target_type": "recommendation",
        "target_id": rec_id,
        "params": {"title": rec["title"], "book_id": result["book"]["book_id"]},
        "undo_params": {},
        "reason": f"主人收藏采购推荐《{rec['title']}》",
    })
    return {"ok": True, "book": result["book"], "rec_id": rec_id}


@router.post("/purchase/{rec_id}/feedback")
def purchase_feedback(req: Request, rec_id: str, body: FeedbackRequest) -> dict:
    """忽略 / 不感兴趣反馈：写回 profile 负反馈（采集方向学习）。"""
    state = req.app.state.library
    rec = state.repo.get_recommendation(rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="推荐不存在")
    if body.action not in ("ignore", "not_interested"):
        raise HTTPException(status_code=422, detail="action 必须是 ignore 或 not_interested")

    state.repo.update_recommendation(rec_id, {
        "status": body.action,
        "feedback_note": body.note,
    })

    # 负反馈写回 profile：方向池里相同主题降权
    profile = state.repo.get_profile()
    pool = profile.get("direction_pool", []) or []
    changed = False
    for d in pool:
        if rec.get("title") and d.get("topic") in rec["title"]:
            d["weight"] = max(0, int(d.get("weight", 1)) - 1)
            changed = True
    if changed:
        state.repo.save_profile(direction_pool=pool)

    state.repo.insert_action({
        "agent": "owner",
        "action_type": "purchase_feedback",
        "target_type": "recommendation",
        "target_id": rec_id,
        "params": {"action": body.action, "note": body.note, "title": rec["title"]},
        "undo_params": {},
        "reason": f"主人对推荐《{rec['title']}》反馈：{body.action}",
    })
    return {"ok": True, "rec_id": rec_id, "status": body.action}


@router.get("/daily-reports")
def daily_reports(req: Request, date: str | None = None,
                  rtype: str | None = None) -> dict:
    """日报列表（默认最近 50 条；可按日期/类型过滤）。"""
    state = req.app.state.library
    reports = state.repo.list_reports(date=date, rtype=rtype, limit=50)
    return {"reports": reports, "count": len(reports)}
