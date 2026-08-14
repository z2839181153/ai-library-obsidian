"""P3 采购员 API 测试：今日推荐 / 生成 / 收藏 / 反馈 / 日报。"""
from __future__ import annotations


def test_purchase_today_empty(client):
    r = client.get("/api/purchase/today")
    assert r.status_code == 200
    d = r.json()
    assert d["recommendations"] == []
    assert d["quota"]["max_daily"] >= 1
    assert d["quota"]["reason"]


def test_purchase_generate(client):
    r = client.post("/api/purchase/generate")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["generated"] >= 1
    assert all(x["status"] == "pending" for x in d["recommendations"])
    assert all(x["score"] and x["reason"] for x in d["recommendations"])


def test_purchase_generate_idempotent(client):
    client.post("/api/purchase/generate")
    r2 = client.post("/api/purchase/generate")
    assert r2.json()["generated"] == 0
    assert r2.json()["note"]  # "已有推荐，未重复生成"


def test_purchase_collect_creates_book(client):
    g = client.post("/api/purchase/generate").json()
    rec_id = g["recommendations"][0]["rec_id"]
    r = client.post(f"/api/purchase/{rec_id}/collect")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["book"]["status"] == "incoming"  # 入补书室
    # rec 状态变 collected
    today = client.get("/api/purchase/today").json()
    rec = next(x for x in today["recommendations"] if x["rec_id"] == rec_id)
    assert rec["status"] == "collected"


def test_purchase_collect_twice_rejected(client):
    g = client.post("/api/purchase/generate").json()
    rec_id = g["recommendations"][0]["rec_id"]
    client.post(f"/api/purchase/{rec_id}/collect")
    r2 = client.post(f"/api/purchase/{rec_id}/collect")
    assert r2.status_code == 400


def test_purchase_feedback(client):
    g = client.post("/api/purchase/generate").json()
    rec_id = g["recommendations"][0]["rec_id"]
    r = client.post(f"/api/purchase/{rec_id}/feedback",
                    json={"action": "not_interested", "note": "不感兴趣"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "not_interested"
    today = client.get("/api/purchase/today").json()
    rec = next(x for x in today["recommendations"] if x["rec_id"] == rec_id)
    assert rec["status"] == "not_interested"
    assert rec["feedback_note"] == "不感兴趣"


def test_purchase_feedback_bad_action(client):
    g = client.post("/api/purchase/generate").json()
    rec_id = g["recommendations"][0]["rec_id"]
    r = client.post(f"/api/purchase/{rec_id}/feedback", json={"action": "whatever"})
    assert r.status_code == 422


def test_daily_reports(client):
    client.post("/api/purchase/generate")
    r = client.get("/api/daily-reports")
    assert r.status_code == 200
    reps = r.json()["reports"]
    assert any(x["rtype"] == "purchase" for x in reps)
