"""T1/T6：操作账本 repo 层。"""
from __future__ import annotations

from app.db.repo import new_id


def test_new_id_prefix(tmp_path, make_library):
    _, _, repo, _ = make_library()
    assert new_id("act").startswith("act_")
    assert len(new_id("act")) > 4


def test_insert_and_get_action(make_library):
    _, _, repo, _ = make_library()
    act_id = repo.insert_action({
        "agent": "admin", "action_type": "classify", "target_type": "book",
        "target_id": "bk_x", "params": {"room": "AI"}, "undo_params": {}, "reason": "r",
    })
    act = repo.get_action(act_id)
    assert act["status"] == "done"
    assert act["params"] == {"room": "AI"}
    assert act["action_type"] == "classify"


def test_list_actions_filter_and_status(make_library):
    _, _, repo, _ = make_library()
    repo.insert_action({"agent": "admin", "action_type": "classify",
                        "target_type": "book", "target_id": "bk_x"})
    repo.insert_action({"agent": "owner", "action_type": "shelve",
                        "target_type": "book", "target_id": "bk_y"})
    acts = repo.list_actions(target_type="book", target_id="bk_x")
    assert len(acts) == 1
    assert acts[0]["action_type"] == "classify"

    repo.set_action_status(acts[0]["act_id"], "undone")
    assert repo.get_action(acts[0]["act_id"])["status"] == "undone"
