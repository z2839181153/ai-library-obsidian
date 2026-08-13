"""API 测试：/api/index/run、/api/index/status、/api/search。"""
from __future__ import annotations


def test_index_run_and_status(client, tmp_path):
    src = tmp_path / "books"
    src.mkdir(parents=True, exist_ok=True)
    (src / "a.md").write_text("# A\n苹果香蕉", encoding="utf-8")

    r = client.post("/api/index/run", json={"source_dir": str(src)})
    assert r.status_code == 200
    assert r.json()["stats"]["new_or_changed"] == 1

    s = client.get("/api/index/status")
    assert s.status_code == 200
    body = s.json()
    assert body["status"] == "active"
    assert body["total_chunks"] >= 1


def test_search_api(client, tmp_path):
    src = tmp_path / "books"
    src.mkdir(parents=True, exist_ok=True)
    (src / "bike.md").write_text("# 自行车维修\n链条刹车", encoding="utf-8")
    client.post("/api/index/run", json={"source_dir": str(src)})

    r = client.post("/api/search", json={"query": "自行车", "top_k": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["books"]
    assert "title" in body["books"][0]


def test_search_empty_query_422(client):
    r = client.post("/api/search", json={"query": "   "})
    assert r.status_code == 422
