"""P1 API 冒烟：补书室 → 分类 → 确认上架 → 原文 → 问答 → 撤销。"""
from __future__ import annotations

import json


def _seed_vault_book(client, name="机器学习笔记", content="神经网络与梯度下降是机器学习基础。"):
    """在 vault/books 写一个 md 并索引，返回 book_id。"""
    from pathlib import Path

    root = Path(client.app.state.library.cfg.paths.vault_dir) / "books"
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.md").write_text(f"# {name}\n\n{content}", encoding="utf-8")
    r = client.post("/api/index/run", json={"source_dir": str(root)})
    assert r.status_code == 200, r.text
    stats = r.json()["stats"]
    books = client.get("/api/books").json()["books"]
    assert books, "应至少有一本书"
    return books[0]["book_id"]


def test_api_floors(client):
    r = client.get("/api/floors")
    assert r.status_code == 200
    floors = r.json()["floors"]
    assert [f["code"] for f in floors] == ["1F", "2F", "3F", "4F"]
    assert floors[0]["rooms"] == []


def test_api_full_flow(client):
    book_id = _seed_vault_book(client)

    # 分类建议 + 卡片
    r = client.post(f"/api/books/{book_id}/classify", json={})
    assert r.status_code == 200, r.text
    assert r.json()["suggest"]["floor"] == "1F"
    assert r.json()["card_path"]

    # 补书室列表（reviewing）
    r = client.get("/api/books", params={"status": "reviewing"})
    assert r.status_code == 200
    pending = r.json()["books"]
    assert any(b["book_id"] == book_id and b["has_card"] for b in pending)

    # 详情 + 卡片
    r = client.get(f"/api/books/{book_id}")
    assert r.status_code == 200
    assert r.json()["card"]["summary"]
    assert r.json()["card"]["distill_value"] == 82

    # 确认上架
    r = client.post(f"/api/books/{book_id}/confirm", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "shelved"
    assert r.json()["vault_path"].startswith("books/1F")

    # 原文阅读（shelved 读 vault 文件）
    r = client.get(f"/api/books/{book_id}/content")
    assert r.status_code == 200
    assert len(r.json()["sections"]) >= 1

    # 问答
    r = client.post("/api/ask", json={"query": "神经网络"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"]
    assert body["refs"] and body["refs"][0]["link"].startswith("[[catalog/bk_")

    # 撤销上架 → 回补书室
    acts = client.get("/api/actions", params={"target_type": "book", "target_id": book_id}).json()["actions"]
    shelve_act = next(a for a in acts if a["action_type"] == "shelve")
    r = client.post(f"/api/actions/{shelve_act['act_id']}/undo")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "reviewing"

    book = client.get(f"/api/books/{book_id}").json()["book"]
    assert book["status"] == "reviewing"
    assert book["vault_path"] == ""


def test_api_classify_unknown_book_404(client):
    r = client.post("/api/books/bk_nope/classify", json={})
    assert r.status_code == 404


def test_api_confirm_invalid_floor_400(client):
    book_id = _seed_vault_book(client)
    # 无 body：FakeLLM 建议 1F → 默认上架成功；给不存在的楼层 → 400
    r = client.post(f"/api/books/{book_id}/confirm", json={"floor": "9F"})
    assert r.status_code == 400


def test_api_undo_actions_list(client):
    r = client.get("/api/actions")
    assert r.status_code == 200
    assert "actions" in r.json()
