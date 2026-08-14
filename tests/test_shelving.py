"""T6：确认上架 + 撤销（主人主权）。"""
from __future__ import annotations

import pytest

from app.core.shelving import Shelver


def _seed_book(state, book_id="bk_s1", title="测试书", floor="1F", room="人工智能", shelf="LLM与Agent"):
    state.repo.upsert_book({
        "book_id": book_id, "title": title, "media_type": "pdf",
        "status": "reviewing", "content_hash": f"h_{book_id}",
        "raw_path": "", "vault_path": "", "card_path": "",
        "suggest_floor": floor, "suggest_room": room, "suggest_shelf": shelf,
    })
    for i in range(2):
        state.repo.insert_chunk({
            "chunk_id": f"ck_{book_id}_{i}", "book_id": book_id,
            "section": f"第{i + 1}章", "seq": i,
            "content": f"正文{i + 1}：人工智能概念。", "fts_content": "人工智能",
            "token_cnt": 8, "vector_id": f"ck_{book_id}_{i}",
        })
    state.repo.commit()
    return book_id


def test_confirm_shelve_creates_vault(make_library_p1):
    state = make_library_p1()
    book_id = _seed_book(state)

    result = state.shelver.confirm_shelve(book_id)
    assert result["status"] == "shelved"
    assert result["floor"] == "1F"
    assert result["room"] == "人工智能"

    book = state.repo.get_book(book_id)
    assert book["status"] == "shelved"
    assert book["confirm_by"] == "owner"
    vp = state.cfg.paths.vault_dir / book["vault_path"]
    assert (vp / "book.md").exists()
    body = (vp / "book.md").read_text(encoding="utf-8")
    assert "正文1" in body and "## 第1章" in body

    # .floor.json / .room.json / .shelf.json / README.md
    floor_dir = state.cfg.paths.vault_dir / "books" / "1F-电子书"
    assert (floor_dir / ".floor.json").exists()
    room_dir = floor_dir / "人工智能"
    assert (room_dir / ".room.json").exists()
    assert (room_dir / "LLM与Agent" / ".shelf.json").exists()

    # action ledger
    acts = state.repo.list_actions(target_type="book", target_id=book_id)
    shelve_acts = [a for a in acts if a["action_type"] == "shelve"]
    assert shelve_acts and shelve_acts[0]["status"] == "done"
    assert shelve_acts[0]["undo_params"]["vault_path"]


def test_confirm_overrides_suggest(make_library_p1):
    state = make_library_p1()
    book_id = _seed_book(state, floor="1F", room="人工智能", shelf="LLM与Agent")
    result = state.shelver.confirm_shelve(book_id, floor="2F", room="产品", shelf="读书笔记")
    assert result["floor"] == "2F"
    assert result["room"] == "产品"
    # 新房间/书架自动创建
    rm = state.repo.room_by_name("fl_2f_web", "产品")
    assert rm is not None
    sh = state.repo.shelf_by_name(rm["room_id"], "读书笔记")
    assert sh is not None
    assert (state.cfg.paths.vault_dir / "books" / "2F-网页公众号" / "产品" / "读书笔记").exists()


def test_confirm_unknown_floor_raises(make_library_p1):
    state = make_library_p1()
    book_id = _seed_book(state, floor="9F")
    with pytest.raises(ValueError):
        state.shelver.confirm_shelve(book_id)


def test_confirm_already_shelved_raises(make_library_p1):
    state = make_library_p1()
    book_id = _seed_book(state)
    state.shelver.confirm_shelve(book_id)
    with pytest.raises(ValueError):
        state.shelver.confirm_shelve(book_id)


def test_undo_shelve_returns_to_reviewing(make_library_p1):
    state = make_library_p1()
    book_id = _seed_book(state)
    act_id = state.shelver.confirm_shelve(book_id)["act_id"]

    result = state.shelver.undo_shelve(state.repo.get_action(act_id))
    assert result["undone"] is True
    assert result["status"] == "reviewing"

    book = state.repo.get_book(book_id)
    assert book["status"] == "reviewing"
    assert book["vault_path"] == ""
    # vault 副本移到 data/tmp/unshelved/
    assert (state.cfg.paths.data_dir / "tmp" / "unshelved" / "测试书").exists()
    assert state.repo.get_action(act_id)["status"] == "undone"


def test_undo_classify_clears_suggest(make_library_p1):
    state = make_library_p1()
    book_id = _seed_book(state)
    state.repo.insert_action({
        "agent": "admin", "action_type": "classify", "target_type": "book",
        "target_id": book_id, "params": {}, "undo_params": {},
    })
    act = state.repo.list_actions(target_type="book", target_id=book_id)[0]

    result = state.shelver.undo_classify(act)
    assert result["undone"] is True
    book = state.repo.get_book(book_id)
    assert book["suggest_floor"] == ""
    assert book["suggest_room"] == ""
    assert book["suggest_shelf"] == ""
