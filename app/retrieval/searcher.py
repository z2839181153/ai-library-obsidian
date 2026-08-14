"""混合检索（设计文档 §7.2）：FTS5 词法 + LanceDB 向量 → RRF 融合。

返回按书聚合：{books: [{book_id, title, score, hit_chunks: [...]}]}
"""
from __future__ import annotations

from dataclasses import dataclass

from app.db.repo import Repo
from app.llm.embeddings import EmbeddingClient
from app.retrieval.tokenizer import query_to_match
from app.vec.vector_store import VectorStore

RRF_K = 60  # RRF 常数


@dataclass
class Hit:
    chunk_id: str
    book_id: str
    section: str
    content: str
    score: float


def _rrf(rank: int) -> float:
    return 1.0 / (RRF_K + rank)


class Searcher:
    def __init__(self, repo: Repo, vec: VectorStore, embed: EmbeddingClient):
        self.repo = repo
        self.vec = vec
        self.embed = embed

    def search(self, query: str, top_k: int = 20, book_ids: list[str] | None = None) -> dict:
        """混合检索。book_ids 过滤（可选）。"""
        # 1) 词法路
        match_expr = query_to_match(query)
        lexical = self._lexical(match_expr, top_k, book_ids) if match_expr.strip() else []

        # 2) 向量路
        vector_hits: list[str] = []
        try:
            qvec = self.embed.embed_one(query)
            vec_results = self.vec.search(qvec, top_k=max(top_k * 3, 30))
            if book_ids:
                vec_results = [r for r in vec_results if r["book_id"] in book_ids]
            vector_hits = [r["chunk_id"] for r in vec_results]
        except Exception:  # noqa: BLE001  无 key 或向量表缺失时词法兜底
            pass

        return self._fuse(query, lexical, vector_hits, top_k)

    def search_lexical(self, query: str, top_k: int = 20,
                       book_ids: list[str] | None = None) -> dict:
        """仅词法路（FTS5）检索，结构与 search 相同。embedding 不可用时的降级路径。"""
        match_expr = query_to_match(query)
        lexical = self._lexical(match_expr, top_k, book_ids) if match_expr.strip() else []
        return self._fuse(query, lexical, [], top_k)

    def _lexical(self, match_expr: str, top_k: int, book_ids: list[str] | None) -> list[str]:
        sql = (
            "SELECT c.rowid, c.chunk_id, c.book_id, c.section, c.content "
            "FROM chunks_fts f JOIN chunks c ON c.rowid = f.rowid "
            "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?"
        )
        params: list = [match_expr, max(top_k * 3, 30)]
        if book_ids:
            ph = ",".join("?" for _ in book_ids)
            sql += f" AND c.book_id IN ({ph})"
            params.extend(book_ids)
        try:
            return [r["chunk_id"] for r in self.repo.conn.execute(sql, params)]
        except Exception:  # noqa: BLE001  FTS 语法错误时静默降级
            return []

    def _fuse(self, query: str, lexical: list[str], vector_hits: list[str],
              top_k: int) -> dict:
        """RRF 融合 + 按书聚合（search / search_lexical 共用）。"""
        # 3) RRF 融合
        scores: dict[str, float] = {}
        for rank, cid in enumerate(lexical):
            scores[cid] = scores.get(cid, 0.0) + _rrf(rank)
        for rank, cid in enumerate(vector_hits):
            scores[cid] = scores.get(cid, 0.0) + _rrf(rank)

        top_chunk_ids = sorted(scores, key=scores.get, reverse=True)[:top_k]
        if not top_chunk_ids:
            return {"query": query, "books": []}

        # 取 chunk 详情
        ph = ",".join("?" for _ in top_chunk_ids)
        rows = self.repo.conn.execute(
            f"SELECT chunk_id, book_id, section, content FROM chunks WHERE chunk_id IN ({ph})",
            top_chunk_ids,
        ).fetchall()
        by_id = {r["chunk_id"]: r for r in rows}

        # 按书聚合
        book_hits: dict[str, list[Hit]] = {}
        for cid in top_chunk_ids:
            r = by_id.get(cid)
            if not r:
                continue
            book_hits.setdefault(r["book_id"], []).append(
                Hit(chunk_id=cid, book_id=r["book_id"], section=r["section"],
                    content=r["content"], score=scores[cid])
            )

        books = []
        for bid, hits in book_hits.items():
            b = self.repo.get_book(bid) or {}
            books.append(
                {
                    "book_id": bid,
                    "title": b.get("title", bid),
                    "score": round(sum(h.score for h in hits), 4),
                    "hit_chunks": [
                        {"chunk_id": h.chunk_id, "section": h.section,
                         "content": h.content[:500], "score": round(h.score, 4)}
                        for h in sorted(hits, key=lambda x: x.score, reverse=True)
                    ],
                }
            )
        books.sort(key=lambda x: x["score"], reverse=True)
        return {"query": query, "books": books}
