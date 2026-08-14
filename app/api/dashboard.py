"""健康度仪表 API（设计文档 §9.2 GET /api/dashboard；大厅页面数据）。

统计：待分类书数、待审阅技能数、索引状态、采购配额执行率、本周入馆趋势、
楼层鸟瞰（每层藏书数）、今日日报。
"""
from __future__ import annotations

import datetime

from fastapi import APIRouter, Request

router = APIRouter(tags=["dashboard"])


def _today() -> str:
    return datetime.date.today().isoformat()


@router.get("/dashboard")
def dashboard(req: Request) -> dict:
    state = req.app.state.library
    conn = state.repo.conn

    # 待分类书数（补书室）：reviewing（有建议）+ incoming（无建议）
    reviewing = conn.execute(
        "SELECT COUNT(*) c FROM books WHERE status='reviewing'"
    ).fetchone()["c"]
    incoming = conn.execute(
        "SELECT COUNT(*) c FROM books WHERE status='incoming'"
    ).fetchone()["c"]

    # 待审阅技能数
    skills_reviewing = conn.execute(
        "SELECT COUNT(*) c FROM skills WHERE status='reviewing'"
    ).fetchone()["c"]
    skills_blocked = conn.execute(
        "SELECT COUNT(*) c FROM skills WHERE status='blocked'"
    ).fetchone()["c"]

    # 索引状态
    st = state.repo.get_state()
    total_chunks = conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
    index = {
        "revision": st["revision"] if st else 0,
        "status": st["status"] if st else "missing",
        "built_at": st["built_at"] if st else None,
        "total_chunks": total_chunks,
    }

    # 本周入馆趋势（近 7 天，按 created_at 日期）
    week: list[dict] = []
    for i in range(6, -1, -1):
        day = (datetime.date.today() - datetime.timedelta(days=i)).isoformat()
        n = conn.execute(
            "SELECT COUNT(*) c FROM books WHERE substr(created_at,1,10)=?",
            (day,),
        ).fetchone()["c"]
        week.append({"date": day, "count": n})

    # 楼层鸟瞰：每层藏书数（vault_path 含楼层 code 前缀，shelved 的书）
    floors = []
    for f in state.repo.list_floors():
        code = f["code"]
        n = conn.execute(
            "SELECT COUNT(*) c FROM books WHERE vault_path LIKE ? OR vault_path LIKE ?",
            (f"%/{code}-%", f"%/{code}/%"),
        ).fetchone()["c"]
        floors.append({
            "floor_id": f["floor_id"],
            "code": code,
            "name": f["name"],
            "media_type": f["media_type"],
            "book_count": n,
        })

    # 今日采购配额执行率
    today = _today()
    rec_stats = state.repo.recommendation_stats(today)
    quota = {
        "date": today,
        **rec_stats,
        "execution_rate": round(
            (rec_stats["collected"] + rec_stats["ignored"] + rec_stats["not_interested"])
            / rec_stats["total"] * 100, 1,
        ) if rec_stats["total"] else 0.0,
    }

    # 今日日报（最新 purchase/ingest/system）
    reports = state.repo.list_reports(date=today, limit=5)

    return {
        "health": {
            "reviewing_books": reviewing,
            "incoming_books": incoming,
            "pending_classify": reviewing + incoming,
            "skills_reviewing": skills_reviewing,
            "skills_blocked": skills_blocked,
            "index": index,
            "quota": quota,
            "week": week,
        },
        "floors": floors,
        "reports": reports,
        "today": today,
    }
