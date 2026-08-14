"""T6：技能 API（批准/拒绝/解除阻塞 + 审阅页 + distill start/status/confirm）。"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.config import AppConfig
from app.distill.executor_fake import FakeDistiller
from app.state import build_state
from tests.conftest import FakeEmbed, FakeLLM


def _make_client(tmp_path, fail_stage=None):
    cfg = AppConfig()
    cfg.paths.data_dir = tmp_path / "data"
    cfg.paths.vault_dir = tmp_path / "vault"
    state = build_state(cfg, embed=FakeEmbed(), llm=FakeLLM())
    state.distill_executor = FakeDistiller(fail_stage=fail_stage)

    from fastapi import FastAPI

    from app import __version__
    from app.api import actions, ask, books, distill, floors, health, index, skills

    app = FastAPI(title="AI Library P2 Test", version=__version__)
    app.state.library = state
    app.include_router(health.router, prefix="/api")
    app.include_router(index.router, prefix="/api")
    app.include_router(books.router, prefix="/api")
    app.include_router(actions.router, prefix="/api")
    app.include_router(floors.router, prefix="/api")
    app.include_router(ask.router, prefix="/api")
    app.include_router(distill.router, prefix="/api")
    app.include_router(skills.router, prefix="/api")
    return TestClient(app), state


def _seed_and_run(client, state, book_id="bk_a1"):
    state.repo.upsert_book({"book_id": book_id, "title": "蒸馏书", "author": "作者",
                            "status": "shelved", "slug": "demo"})
    state.repo.upsert_card({"book_id": book_id, "summary": "方法论书", "category": "methodology",
                            "distill_value": 85})
    r = client.post(f"/api/distill/{book_id}/start", json={"force": False, "auto_confirm": True})
    assert r.status_code == 200, r.text
    return r.json()


def _wait_done(client, book_id, timeout=15.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        st = client.get(f"/api/distill/{book_id}/status").json()
        if st["distill_status"] == "done":
            return st
        time.sleep(0.1)
    raise AssertionError(f"蒸馏未完成: {st}")


def test_distill_api_flow(tmp_path):
    client, state = _make_client(tmp_path)
    _seed_and_run(client, state)
    st = _wait_done(client, "bk_a1")
    assert st["artifacts"]["skill_count"] == 2
    assert st["stage"] == "stage5_delivering"


def test_distill_api_validation(tmp_path):
    client, state = _make_client(tmp_path)
    # 未上架
    state.repo.upsert_book({"book_id": "bk_z", "title": "x", "status": "reviewing"})
    r = client.post("/api/distill/bk_z/start", json={})
    assert r.status_code == 400
    # 书不存在
    r = client.post("/api/distill/nope/start", json={})
    assert r.status_code == 404
    # status：未开始 → idle
    state.repo.upsert_book({"book_id": "bk_y", "title": "y", "status": "shelved"})
    r = client.get("/api/distill/bk_y/status")
    assert r.json()["distill_status"] == "idle"


def test_skills_list_and_approve(tmp_path):
    client, state = _make_client(tmp_path)
    _seed_and_run(client, state)
    _wait_done(client, "bk_a1")

    # 待审阅列表
    r = client.get("/api/skills?status=reviewing")
    assert r.status_code == 200
    skills = r.json()["skills"]
    assert len(skills) == 2
    sid = skills[0]["skill_id"]
    assert skills[0]["book_title"] == "蒸馏书"

    # 详情：SKILL.md + test-prompts + test-results
    d = client.get(f"/api/skills/{sid}").json()
    assert "## E — 可执行步骤" in d["skill_md"]
    assert "should_trigger" in d["test_prompts_text"]
    assert "通过率" in d["test_results"]

    # 批准 → installed + 进技能库索引 + action
    r = client.post(f"/api/skills/{sid}/approve")
    assert r.status_code == 200
    assert r.json()["status"] == "installed"
    assert state.repo.get_skill(sid)["status"] == "installed"
    acts = state.repo.list_actions(target_type="skill", target_id=sid)
    assert acts[0]["action_type"] == "skill_approve"


def test_skill_approve_indexes_vector(tmp_path):
    """批准后 description 进技能库向量索引，可检索到。"""
    client, state = _make_client(tmp_path)
    _seed_and_run(client, state)
    _wait_done(client, "bk_a1")
    skills = client.get("/api/skills?status=reviewing").json()["skills"]
    sid = skills[0]["skill_id"]
    client.post(f"/api/skills/{sid}/approve")
    # 用该技能 description 本身查询（FakeEmbed 下 cos=1.0，验证写入/读取链路）
    desc = skills[0]["description"]
    vec = state.embed.embed_one(desc)
    hits = state.skill_index.search(vec, top_k=5)
    assert any(h["skill_id"] == sid for h in hits)
    # 无关查询余弦应低于阈值（阈值过滤由 Router.retrieve 负责）
    far = state.embed.embed_one("今天天气怎么样 完全无关的话题")
    hits2 = state.skill_index.search(far, top_k=5)
    assert all(h["_distance"] < state.cfg.distill.route_threshold for h in hits2)
    # Router 层：无关查询 → no_hit
    no = state.router.retrieve("今天天气怎么样 完全无关的话题")
    assert no["skills"] == []


def test_skill_reject_and_block(tmp_path):
    client, state = _make_client(tmp_path)
    _seed_and_run(client, state)
    _wait_done(client, "bk_a1")
    skills = client.get("/api/skills?status=reviewing").json()["skills"]
    sid = skills[0]["skill_id"]

    # 拒绝必须附原因
    r = client.post(f"/api/skills/{sid}/reject", json={"reason": ""})
    assert r.status_code == 400

    # 连续拒绝到 ≥5 → blocked + 书 distill_status=blocked
    for i in range(5):
        r = client.post(f"/api/skills/{sid}/reject", json={"reason": f"第{i+1}次不合格"})
        assert r.status_code == 200
    assert state.repo.get_skill(sid)["status"] == "blocked"
    book = state.repo.get_book("bk_a1")
    assert book["distill_status"] == "blocked"
    # 再次 start 被拦截
    r = client.post("/api/distill/bk_a1/start", json={})
    assert r.status_code == 409

    # 解除阻塞 → draft + 书 idle
    r = client.post(f"/api/skills/{sid}/unblock")
    assert r.status_code == 200
    assert state.repo.get_skill(sid)["status"] == "draft"
    assert state.repo.get_skill(sid)["reject_count"] == 0
    assert state.repo.get_book("bk_a1")["distill_status"] == "idle"


def test_review_page(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.get("/api/skills/review")
    assert r.status_code == 200
    assert "技能审阅" in r.text


def test_qa_uses_installed_skill(tmp_path):
    """已批准技能在问答时被路由命中并注入。"""
    client, state = _make_client(tmp_path)
    _seed_and_run(client, state)
    _wait_done(client, "bk_a1")
    skills = client.get("/api/skills?status=reviewing").json()["skills"]
    for s in skills:
        client.post(f"/api/skills/{s['skill_id']}/approve")
    # 验证路由层：query 用技能 description（FakeEmbed 下 cos 高）→ 命中已安装技能
    routed = state.router.retrieve(skills[0]["description"])
    assert routed["skills"], routed
    assert routed["skills"][0]["skill_id"] == skills[0]["skill_id"]
    hint = state.router.build_system_hint(routed)
    assert "技能《" in hint
    # 无关问题不命中
    no = state.router.retrieve("今天天气怎么样 完全无关的话题")
    assert no["skills"] == []
