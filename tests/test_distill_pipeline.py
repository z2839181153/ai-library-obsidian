"""T3：蒸馏状态机全流程（FakeDistiller，离线）。"""
from __future__ import annotations

import time

import pytest

from app.config import AppConfig
from app.db.repo import Repo
from app.distill.executor_fake import FakeDistiller
from app.distill.pipeline import DistillPipeline
from app.state import build_state
from tests.conftest import FakeEmbed, FakeLLM


def _wait_status(state, book_id, target, timeout=15.0):
    """轮询 book.distill_status 直到等于 target（后台线程跑完）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = state.repo.get_book(book_id).get("distill_status")
        if st == target:
            return st
        time.sleep(0.1)
    raise AssertionError(f"等待 {target} 超时，当前 {state.repo.get_book(book_id).get('distill_status')}")


def _seed_book(state, book_id="bk_d1", title="蒸馏测试书", status="shelved",
               category="methodology", distill_value=82, slug="distill-demo"):
    state.repo.upsert_book({
        "book_id": book_id, "title": title, "author": "作者",
        "status": status, "slug": slug,
    })
    state.repo.upsert_card({
        "book_id": book_id, "summary": "方法论书", "category": category,
        "distill_value": distill_value, "distill_reason": "测试",
    })
    return book_id


def _state(tmp_path, fail_stage=None):
    cfg = AppConfig()
    cfg.paths.data_dir = tmp_path / "data"
    cfg.paths.vault_dir = tmp_path / "vault"
    state = build_state(cfg, embed=FakeEmbed(), llm=FakeLLM())
    state.distill_executor = FakeDistiller(fail_stage=fail_stage)
    return state


def test_start_validation(tmp_path):
    state = _state(tmp_path)
    # 未上架
    _seed_book(state, status="reviewing")
    r = state.distill.start("bk_d1", FakeDistiller())
    assert not r["ok"] and "仅已上架" in r["error"]
    # 非方法论
    _seed_book(state, book_id="bk_d2", status="shelved", category="narrative")
    r = state.distill.start("bk_d2", FakeDistiller())
    assert not r["ok"] and "非方法论" in r["error"]
    # 价值分不足
    _seed_book(state, book_id="bk_d3", status="shelved", distill_value=40)
    r = state.distill.start("bk_d3", FakeDistiller())
    assert not r["ok"] and "蒸馏价值分" in r["error"]


def test_full_pipeline_done_and_registers(tmp_path):
    state = _state(tmp_path)
    bid = _seed_book(state)
    r = state.distill.start(bid, FakeDistiller(), auto_confirm=True)
    assert r["ok"]
    _wait_status(state, bid, "done")
    book = state.repo.get_book(bid)
    assert book["distill_status"] == "done"
    assert book["distill_slug"].startswith("distill-demo-")
    # 技能注册为 reviewing
    skills = state.repo.list_skills(book_id=bid)
    assert len(skills) == 2
    assert all(s["status"] == "reviewing" for s in skills)
    assert "test_prompts" in skills[0] and skills[0]["test_prompts"]
    # 卡片回填
    card = state.repo.get_card(bid)
    assert card["skills"] and "sk_" in card["skills"]
    # PIPELINE_STATE 全 done
    state_file = state.cfg.paths.vault_dir / "skills" / book["distill_slug"] / "PIPELINE_STATE.md"
    assert state_file.exists()
    text = state_file.read_text(encoding="utf-8")
    assert text.count("- [x]") == 9      # 7 阶段 + 2 确认
    # 审计复制
    audit = state.cfg.paths.vault_dir / "archive" / "distill-logs" / book["distill_slug"]
    assert (audit / "SKILL.md").exists() or any(audit.iterdir())
    # action ledger
    acts = state.repo.list_actions(target_type="book", target_id=bid)
    types = {a["action_type"] for a in acts}
    assert "distill_start" in types and "distill_done" in types and "distill_register" in types


def _wait_awaiting(state, book_id, stage, timeout=15.0):
    """轮询直到蒸馏处于指定等待阶段（区分旧/新 awaiting）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = state.distill.status(book_id)
        if st["distill_status"] == "awaiting" and st.get("awaiting_stage") == stage:
            return st
        time.sleep(0.1)
    raise AssertionError(f"等待 {stage} 超时，当前 {state.distill.status(book_id)}")


def test_awaiting_confirmation_twice(tmp_path):
    """阶段 0 与 1.5 各暂停一次，主人 continue/skip 后继续到 done。"""
    state = _state(tmp_path)
    bid = _seed_book(state)
    r = state.distill.start(bid, FakeDistiller())
    assert r["ok"]
    # 阶段 0 → awaiting
    _wait_awaiting(state, bid, "await_confirm_stage0")
    assert state.distill.status(bid)["awaiting"] is True
    r = state.distill.confirm_stage(bid, "continue")
    assert r["ok"] and r["decision"] == "continue"
    # 阶段 1.5 → awaiting
    _wait_awaiting(state, bid, "await_confirm_stage1_5")
    r = state.distill.confirm_stage(bid, "skip")
    assert r["ok"]
    _wait_status(state, bid, "done")
    # 两个确认都进 PIPELINE_STATE
    slug = state.repo.get_book(bid)["distill_slug"]
    text = (state.cfg.paths.vault_dir / "skills" / slug / "PIPELINE_STATE.md").read_text(encoding="utf-8")
    assert "确认 stage0_reading" in text and "确认 stage1_5_verifying" in text


def test_confirm_cancel(tmp_path):
    state = _state(tmp_path)
    bid = _seed_book(state)
    state.distill.start(bid, FakeDistiller())
    _wait_awaiting(state, bid, "await_confirm_stage0")
    r = state.distill.confirm_stage(bid, "cancel")
    assert r["ok"] and r["decision"] == "cancel"
    assert state.repo.get_book(bid)["distill_status"] == "cancelled"


def test_confirm_rejects_bad_decision(tmp_path):
    state = _state(tmp_path)
    bid = _seed_book(state)
    r = state.distill.confirm_stage(bid, "banana")
    assert not r["ok"]


def test_confirm_when_nothing_waiting(tmp_path):
    state = _state(tmp_path)
    bid = _seed_book(state)
    # 未开始蒸馏就 confirm → 拒绝
    r = state.distill.confirm_stage(bid, "continue")
    assert not r["ok"]


def test_failure_marks_failed(tmp_path):
    state = _state(tmp_path, fail_stage="stage2_building")
    bid = _seed_book(state)
    state.distill.start(bid, state.distill_executor, auto_confirm=True)
    _wait_status(state, bid, "failed")
    acts = state.repo.list_actions(target_type="book", target_id=bid)
    assert any(a["action_type"] == "distill_failed" for a in acts)


def test_resume_from_checkpoint(tmp_path):
    """PIPELINE_STATE 已有前 3 阶段 → 从 stage2 续跑（断点续跑）。"""
    state = _state(tmp_path)
    bid = _seed_book(state)
    slug = "distill-demo-" + bid[-6:]
    out = state.cfg.paths.vault_dir / "skills" / slug
    out.mkdir(parents=True)
    # 模拟已完成的阶段 0/1/1.5（写产物 + PIPELINE_STATE）
    fake = FakeDistiller()
    ctx = state.distill._build_context(state.repo.get_book(bid), slug)
    fake.stage0(ctx)
    fake.stage1(ctx)
    fake.stage1_5(ctx)
    state.distill._save_state(out, {"done": ["stage0_reading", "stage1_extracting", "stage1_5_verifying"],
                                    "confirmed": ["stage0_reading", "stage1_5_verifying"],
                                    "awaiting": [], "stage": "stage1_5_verifying"})
    state.repo.update_book_fields(bid, {"distill_slug": slug, "distill_status": "running"})
    # 续跑（executor 缺失场景通过 start 直接给 executor）
    r = state.distill.start(bid, FakeDistiller(), force=True, auto_confirm=True)
    assert r["ok"]
    _wait_status(state, bid, "done")
    skills = state.repo.list_skills(book_id=bid)
    assert len(skills) == 2


def test_blocked_blocks_restart(tmp_path):
    state = _state(tmp_path)
    bid = _seed_book(state)
    state.repo.upsert_skill({"book_id": bid, "name": "坏技能", "status": "blocked", "reject_count": 5})
    r = state.distill.start(bid, FakeDistiller())
    assert not r["ok"] and r.get("blocked")


def test_force_bypasses_trigger_checks(tmp_path):
    state = _state(tmp_path)
    bid = _seed_book(state, category="narrative", distill_value=10)
    r = state.distill.start(bid, FakeDistiller(), force=True, auto_confirm=True)
    assert r["ok"]
    _wait_status(state, bid, "done")
