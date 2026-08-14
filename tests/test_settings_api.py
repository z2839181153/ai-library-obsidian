"""P3 设置 API + 楼层 CRUD 测试。"""
from __future__ import annotations


def test_get_settings(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    d = r.json()
    assert d["server"]["port"] == 8800
    assert "api_key_masked" in d["modelscope"]
    assert "api_key_set" in d["modelscope"]
    assert "max_daily_purchase" in d["purchase"]
    assert "default_mode" in d["prefs"] or "prefs" in d


def test_put_settings_updates_file(client, tmp_path):
    # 默认配置路径在项目根，不能污染真实库 → 直接验证响应回读
    r = client.put("/api/settings", json={"purchase": {"max_daily_purchase": 7}})
    assert r.status_code == 200, r.text
    assert r.json()["purchase"]["max_daily_purchase"] == 7

    # 托管程度写 profile
    r2 = client.put("/api/settings", json={"prefs": {"default_mode": "manual"}})
    assert r2.status_code == 200
    assert r2.json()["prefs"]["default_mode"] == "manual"


def test_put_settings_ollama(client):
    r = client.put("/api/settings", json={"ollama": {"enabled": True, "model": "qwen2.5:7b"}})
    assert r.status_code == 200
    assert r.json()["ollama"]["enabled"] is True


def test_floors_crud(client):
    # 建楼层
    r = client.post("/api/floors", json={"name": "测试层", "media_type": "other"})
    assert r.status_code == 200, r.text
    floor_id = r.json()["floor_id"]
    floors = client.get("/api/floors").json()["floors"]
    assert any(f["floor_id"] == floor_id for f in floors)

    # 改名
    r = client.put(f"/api/floors/{floor_id}", json={"name": "测试层2"})
    assert r.status_code == 200
    floors = client.get("/api/floors").json()["floors"]
    f = next(x for x in floors if x["floor_id"] == floor_id)
    assert f["name"] == "测试层2"

    # 建房间 + 书架
    r = client.post("/api/rooms", json={"floor_id": floor_id, "name": "房间A"})
    assert r.status_code == 200, r.text
    room_id = r.json()["room_id"]
    r = client.post("/api/shelves", json={"room_id": room_id, "name": "书架B"})
    assert r.status_code == 200, r.text
    shelf_id = r.json()["shelf_id"]

    # 删书架 → 删房间 → 删楼层
    assert client.delete(f"/api/shelves/{shelf_id}").json()["deleted"] is True
    assert client.delete(f"/api/rooms/{room_id}").json()["deleted"] is True
    assert client.delete(f"/api/floors/{floor_id}").json()["deleted"] is True
    floors = client.get("/api/floors").json()["floors"]
    assert not any(f["floor_id"] == floor_id for f in floors)


def test_delete_floor_with_books_rejected(client):
    # 入馆 + 分类 + 上架到默认楼层 → 尝试删除该楼层被拒
    r = client.post("/api/ingest", data={"text": "# 上架书\n\n正文"})
    book_id = r.json()["book"]["book_id"]
    client.post(f"/api/books/{book_id}/classify", json={})
    c = client.post(f"/api/books/{book_id}/confirm", json={})
    assert c.status_code == 200, c.text

    floor = client.get("/api/floors").json()["floors"][0]  # 1F
    r = client.delete(f"/api/floors/{floor['floor_id']}")
    assert r.status_code == 409
    assert "还有" in r.json()["detail"]


def test_floor_actions_recorded(client):
    r = client.post("/api/floors", json={"name": "台账层"})
    floor_id = r.json()["floor_id"]
    acts = client.get("/api/actions").json()["actions"]
    assert any(a["action_type"] == "floor_create" and a["target_id"] == floor_id for a in acts)
