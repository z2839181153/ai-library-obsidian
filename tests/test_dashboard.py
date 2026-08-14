"""P3 健康度仪表 API 测试。"""
from __future__ import annotations


def test_dashboard_empty(client):
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    d = r.json()
    h = d["health"]
    assert h["pending_classify"] == 0
    assert h["skills_reviewing"] == 0
    assert h["index"]["revision"] == 0
    assert h["quota"]["total"] == 0
    assert len(d["floors"]) == 4          # 默认 4 楼层种子
    assert len(h["week"]) == 7            # 近 7 天


def test_dashboard_counts_after_ingest(client):
    client.post("/api/ingest", data={"text": "# 待分类书\n\n正文"})
    r = client.get("/api/dashboard")
    h = r.json()["health"]
    assert h["incoming_books"] == 1
    assert h["pending_classify"] == 1


def test_dashboard_counts_reviewing_and_skills(client):
    # 入馆 → 分类 → reviewing
    r = client.post("/api/ingest", data={"text": "# 方法论书\n\n正文"})
    book_id = r.json()["book"]["book_id"]
    client.post(f"/api/books/{book_id}/classify", json={})

    d = client.get("/api/dashboard").json()
    assert d["health"]["reviewing_books"] == 1
    assert d["health"]["pending_classify"] == 1


def test_dashboard_quota_execution_rate(client):
    # 生成推荐 → 收藏 1 条 → 执行率应 > 0
    g = client.post("/api/purchase/generate")
    assert g.status_code == 200, g.text
    recs = g.json()["recommendations"]
    assert recs, "应生成推荐"

    first = recs[0]["rec_id"]
    client.post(f"/api/purchase/{first}/collect")

    d = client.get("/api/dashboard").json()
    quota = d["health"]["quota"]
    assert quota["total"] >= 1
    assert quota["collected"] >= 1
    assert quota["execution_rate"] > 0
