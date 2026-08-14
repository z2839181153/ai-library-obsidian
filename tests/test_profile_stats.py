"""P4-4 借书证画像 API 测试：stats 统计 / 方向池编辑 / 主题编辑 / 收藏拒绝历史。"""
from __future__ import annotations


def _shelve_book(client, title: str, floor: str, room: str, shelf: str) -> str:
    r = client.post("/api/ingest", data={"text": f"# {title}\n\n正文内容。", "title": title})
    assert r.status_code == 200, r.text
    book_id = r.json()["book"]["book_id"]
    cr = client.post(f"/api/books/{book_id}/confirm",
                     json={"floor": floor, "room": room, "shelf": shelf})
    assert cr.status_code == 200, cr.text
    return book_id


def test_profile_stats_empty(client):
    d = client.get("/api/profile/stats").json()
    # 默认 4 个楼层种子（count=0）
    assert len(d["floors_dist"]) >= 4
    assert all(x["count"] == 0 for x in d["floors_dist"])
    assert d["rooms_dist"] == []
    assert d["tags_dist"] == []
    assert d["direction_pool"] == []
    assert d["rec_history"] == []
    assert d["rec_totals"]["total"] == 0
    assert d["prefs"] == {}


def test_profile_stats_floor_room_dist(client):
    _shelve_book(client, "机器学习基础", "1F", "机器学习", "入门")
    _shelve_book(client, "密码学入门", "1F", "密码学", "入门")
    _shelve_book(client, "LLM 综述", "2F", "人工智能", "LLM")
    d = client.get("/api/profile/stats").json()
    floors = {x["code"]: x["count"] for x in d["floors_dist"]}
    assert floors.get("1F") == 2
    assert floors.get("2F") == 1
    rooms = {x["name"]: x["count"] for x in d["rooms_dist"]}
    assert rooms.get("机器学习") == 1
    assert rooms.get("密码学") == 1
    assert rooms.get("人工智能") == 1


def test_profile_stats_tags_dist(client):
    # 分类建议会生成卡片（FakeLLM DEFAULT_CARD_JSON tags=["人工智能","知识库"]）
    r = client.post("/api/ingest", data={"text": "# 带标签书\n\n正文。", "title": "带标签书"})
    book_id = r.json()["book"]["book_id"]
    cr = client.post(f"/api/books/{book_id}/classify", json={"force": False})
    assert cr.status_code == 200, cr.text
    d = client.get("/api/profile/stats").json()
    tags = {x["tag"]: x["count"] for x in d["tags_dist"]}
    assert tags.get("人工智能", 0) >= 1


def test_profile_rec_history(client):
    g = client.post("/api/purchase/generate").json()
    recs = g["recommendations"]
    client.post(f"/api/purchase/{recs[0]['rec_id']}/collect")
    client.post(f"/api/purchase/{recs[1]['rec_id']}/feedback",
                json={"action": "not_interested", "note": "不需要"})
    d = client.get("/api/profile/stats").json()
    assert d["rec_totals"]["total"] >= 2
    assert d["rec_totals"]["collected"] >= 1
    assert d["rec_totals"]["not_interested"] >= 1
    assert d["rec_history"] and d["rec_history"][-1]["collected"] >= 1
    assert any(x["status"] == "collected" for x in d["recent_recs"])


def test_profile_direction_pool_update(client):
    r = client.put("/api/profile/direction-pool", json={
        "direction_pool": [
            {"topic": "自行车维修", "weight": 3, "source": "question", "first_seen": "2026-08-10"},
            {"topic": "LLM Agent", "weight": 5, "source": "hot"},
            {"topic": "  ", "weight": 2},   # 空白 topic 应被过滤
        ]
    })
    assert r.status_code == 200, r.text
    pool = r.json()["direction_pool"]
    assert len(pool) == 2
    assert any(x["topic"] == "自行车维修" and x["weight"] == 3 for x in pool)
    # 读回
    d = client.get("/api/profile/stats").json()
    assert len(d["direction_pool"]) == 2
    # action ledger 记录
    acts = client.get("/api/actions").json()["actions"]
    assert any(a["action_type"] == "profile_update" for a in acts)


def test_profile_themes_update(client):
    r = client.put("/api/profile/themes", json={"themes": {"人工智能": 31, "哲学": 20}})
    assert r.status_code == 200, r.text
    assert r.json()["themes"]["人工智能"] == 31
    d = client.get("/api/profile/stats").json()
    themes = {x["topic"]: x["count"] for x in d["themes_dist"]}
    assert themes.get("人工智能") == 31
