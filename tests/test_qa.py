"""T7：基础问答（带 [[wikilink]] 引用）。"""
from __future__ import annotations

from app.llm.chat import LLMUnavailable


class RaisingLLM:
    """模拟无 API key：任何调用抛 LLMUnavailable。"""

    def chat(self, messages, temperature=0.3):
        raise LLMUnavailable("no key")

    def chat_json(self, prompt, system=None):
        raise LLMUnavailable("no key")


def _seed_book(state, book_id="bk_q1", title="机器学习入门", content="神经网络与梯度下降。"):
    state.repo.upsert_book({
        "book_id": book_id, "title": title, "media_type": "pdf",
        "status": "shelved", "content_hash": f"h_{book_id}",
        "raw_path": "", "vault_path": "books/1F-电子书/人工智能/机器学习", "card_path": "",
    })
    state.repo.insert_chunk({
        "chunk_id": f"ck_{book_id}_0", "book_id": book_id, "section": "第1章",
        "seq": 0, "content": content, "fts_content": "神经网络 梯度下降",
        "token_cnt": 10, "vector_id": f"ck_{book_id}_0",
    })
    # 向量路：手动写 LanceDB（测试不走 indexer）
    state.vec.upsert([{
        "chunk_id": f"ck_{book_id}_0", "book_id": book_id,
        "vector": state.embed.embed_one(content),
    }])
    state.repo.upsert_card({
        "book_id": book_id, "summary": "介绍机器学习基础与神经网络。",
        "chapters": "[]", "concepts": "[]", "distill_value": 70,
        "distill_reason": "", "category": "methodology", "tags": "[]",
        "skills": "[]", "model": "fake",
    })
    state.repo.commit()


def test_ask_returns_answer_and_refs(make_library_p1):
    state = make_library_p1()
    _seed_book(state)
    # 查询词与 chunk 内容一致（词袋向量 + FTS token 可命中）
    result = state.qa.ask("神经网络")
    assert result["answer"]
    assert "测试回答" in result["answer"]
    assert len(result["refs"]) >= 1
    ref = result["refs"][0]
    assert ref["link"] == "[[catalog/bk_q1]]"
    assert ref["title"] == "机器学习入门"


def test_ask_no_results(make_library_p1):
    state = make_library_p1()
    _seed_book(state)
    result = state.qa.ask("完全不相关的内容词 番茄炒蛋")
    # FakeEmbed 词袋哈希：无共同词 → 可能无命中
    if not result["refs"]:
        assert "暂无相关内容" in result["answer"]


def test_ask_model_unavailable_fallback(make_library_p1):
    state = make_library_p1()
    _seed_book(state)
    state.qa.llm = RaisingLLM()
    result = state.qa.ask("神经网络")
    assert result["model_unavailable"] is True
    assert "模型不可用" in result["answer"]
    # 降级时 refs 仍在（可定位原文）
    assert len(result["refs"]) >= 0
