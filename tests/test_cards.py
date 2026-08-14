"""T3/T4：图书卡片生成 + 分类建议。"""
from __future__ import annotations

import pytest

from app.core.card_generator import CardResult


def _seed_book(state, book_id="bk_test", title="测试书", private=0, media_type="pdf"):
    state.repo.upsert_book({
        "book_id": book_id, "title": title, "media_type": media_type,
        "status": "cataloging", "content_hash": f"hash_{book_id}",
        "raw_path": "", "vault_path": "", "card_path": "", "private": private,
    })
    for i in range(3):
        state.repo.insert_chunk({
            "chunk_id": f"ck_{book_id}_{i}", "book_id": book_id,
            "section": f"第{i + 1}章", "seq": i,
            "content": f"内容{i + 1}：人工智能与混合检索、知识库构建。",
            "fts_content": "人工智能 混合检索 知识库", "token_cnt": 12,
            "vector_id": f"ck_{book_id}_{i}",
        })
    state.repo.commit()
    return book_id


def test_generate_creates_card_and_suggest(make_library_p1):
    state = make_library_p1()
    book_id = _seed_book(state)

    result = state.cards.generate(book_id)
    assert isinstance(result, CardResult)
    assert result.card_path is not None and result.card_path.exists()
    assert result.suggest["floor"] == "1F"
    assert result.suggest["room"] == "人工智能"
    assert result.suggest["shelf"] == "LLM与Agent"

    card = state.repo.get_card(book_id)
    assert card["summary"]
    assert card["distill_value"] == 82
    assert card["category"] == "methodology"

    book = state.repo.get_book(book_id)
    assert book["suggest_floor"] == "1F"
    assert book["card_path"]

    actions = state.repo.list_actions(target_type="book", target_id=book_id)
    assert any(a["action_type"] == "classify" for a in actions)

    # 卡片文件 frontmatter 与正文
    text = result.card_path.read_text(encoding="utf-8")
    assert "type: book-card" in text
    assert "status: reviewing" in text
    assert "## 摘要" in text


def test_generate_idempotent_skips(make_library_p1):
    state = make_library_p1()
    book_id = _seed_book(state)
    state.cards.generate(book_id)
    n_actions = len(state.repo.list_actions())

    result = state.cards.generate(book_id)
    assert result.skipped is True
    # 未重复生成：动作数不变
    assert len(state.repo.list_actions()) == n_actions


def test_generate_force_regenerates(make_library_p1):
    state = make_library_p1()
    book_id = _seed_book(state)
    state.cards.generate(book_id)
    result = state.cards.generate(book_id, force=True)
    assert result.skipped is False
    assert state.repo.get_card(book_id) is not None


def test_generate_private_skips_llm(make_library_p1):
    state = make_library_p1()
    book_id = _seed_book(state, private=1)
    result = state.cards.generate(book_id)
    assert result.private_skip is True
    # FakeLLM 未被调用
    assert state.llm.calls == []
    card = state.repo.get_card(book_id)
    assert "模型不可用" in card["summary"]
    assert result.card_path.exists()


def test_generate_no_chunks_returns_error(make_library_p1):
    state = make_library_p1()
    state.repo.upsert_book({
        "book_id": "bk_empty", "title": "空书", "media_type": "pdf",
        "status": "cataloging", "content_hash": "h",
    })
    state.repo.commit()
    result = state.cards.generate("bk_empty")
    assert result.error is not None


def test_generate_unknown_book_raises(make_library_p1):
    state = make_library_p1()
    with pytest.raises(ValueError):
        state.cards.generate("bk_nope")
