"""基础问答（设计文档 §6.5 前四步，P1 + P2 技能路由）。

流程：
1. 定位：混合检索（searcher）→ 命中 1-3 本书
2. 概览：组装书级上下文（卡片摘要 + 命中 chunk 片段）
3. 技能路由（P2）：问题 embedding → 技能库检索 → 命中注入 SKILL.md 到 system prompt
4. 作答：LLM 生成回答，要求带 [[catalog/bk_<id>]] 引用
5. 返回：{answer, refs, used_skills}

无 API key → answer=None + 降级说明，refs 仍返回（可读原文定位）。
方向池沉淀（⑥）留后续。
"""
from __future__ import annotations

import json

from app.db.repo import Repo
from app.llm.chat import ChatClient, LLMUnavailable
from app.retrieval.searcher import Searcher
from app.router.skill_router import SkillRouter

_QA_SYSTEM = (
    "你是 AI 图书馆的管理员。根据提供的馆藏资料回答问题。"
    "回答必须基于资料内容，在引用处标注 [[catalog/bk_<id>]] 格式的 wikilink；"
    "资料不足以回答时明确说'馆内暂无相关内容'。不要编造。"
)

_MAX_BOOKS = 3
_MAX_CHUNKS_PER_BOOK = 3
_CHUNK_CHARS = 400


class QAService:
    def __init__(self, repo: Repo, searcher: Searcher, llm: ChatClient,
                 router: SkillRouter | None = None):
        self.repo = repo
        self.searcher = searcher
        self.llm = llm
        self.router = router

    def ask(self, query: str, top_k: int = 20) -> dict:
        result = self.searcher.search(query, top_k=top_k)
        books = result.get("books", [])[:_MAX_BOOKS]
        refs = []
        for b in books:
            book = self.repo.get_book(b["book_id"]) or {}
            card = self.repo.get_card(b["book_id"])
            snippet = ""
            if b.get("hit_chunks"):
                snippet = b["hit_chunks"][0]["content"][:120]
            elif card and card.get("summary"):
                snippet = card["summary"][:120]
            refs.append({
                "book_id": b["book_id"],
                "title": book.get("title", b["book_id"]),
                "link": f"[[catalog/{b['book_id']}]]",
                "snippet": snippet,
                "status": book.get("status", ""),
            })

        if not books:
            return {
                "query": query,
                "answer": "馆内暂无相关内容。",
                "refs": [],
                "books": [],
                "used_skills": [],
                "model_unavailable": False,
            }

        # ③ 技能路由（P2）：命中注入 SKILL.md
        used_skills = []
        system = _QA_SYSTEM
        if self.router is not None:
            routed = self.router.retrieve(query)
            hint = self.router.build_system_hint(routed)
            if hint:
                system = _QA_SYSTEM + "\n\n" + hint
                used_skills = [
                    {"skill_id": s["skill_id"], "name": s["name"]}
                    for s in routed.get("skills", [])
                ]

        context = self._build_context(books)
        answer = None
        model_unavailable = False
        try:
            answer = self.llm.chat([
                {"role": "system", "content": system},
                {"role": "user", "content": f"问题：{query}\n\n馆藏资料：\n{context}"},
            ])
        except LLMUnavailable:
            model_unavailable = True
            answer = "（模型不可用：未配置 API key）以下是检索到的原文片段：\n\n" + self._snippet_only(books)

        return {
            "query": query,
            "answer": answer,
            "refs": refs,
            "books": [{"book_id": r["book_id"], "title": r["title"], "status": r["status"]} for r in refs],
            "used_skills": used_skills,
            "model_unavailable": model_unavailable,
        }

    # ---------- 内部 ----------

    def _build_context(self, books: list[dict]) -> str:
        parts: list[str] = []
        for b in books:
            book = self.repo.get_book(b["book_id"]) or {}
            card = self.repo.get_card(b["book_id"])
            parts.append(f"[书] 《{book.get('title', b['book_id'])}》 ([[catalog/{b['book_id']}]])")
            if card and card.get("summary"):
                parts.append(f"摘要：{card['summary']}")
            for i, ch in enumerate(b.get("hit_chunks", [])[:_MAX_CHUNKS_PER_BOOK]):
                content = ch["content"].replace("\n", " ")[:_CHUNK_CHARS]
                parts.append(f"- 〔{ch.get('section') or f'片段{i+1}'}〕{content}")
            parts.append("")
        return "\n".join(parts)

    def _snippet_only(self, books: list[dict]) -> str:
        out: list[str] = []
        for b in books:
            book = self.repo.get_book(b["book_id"]) or {}
            out.append(f"《{book.get('title', b['book_id'])}》：")
            for ch in b.get("hit_chunks", [])[:_MAX_CHUNKS_PER_BOOK]:
                out.append(f"- {ch['content'][:_CHUNK_CHARS]}")
        return "\n".join(out)
