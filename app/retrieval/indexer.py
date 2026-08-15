"""索引管线（设计文档 §7.1）：扫描 → 清洗 → chunk → embedding → LanceDB+FTS5。

copy-on-write 简化实现：
- 书 content_hash 未变且已有 chunks → 跳过（增量）。
- 变更/新增书 → 重新 chunk + embedding + 写入（先删该书旧 chunks）。
- 文件消失 → 删除该书 chunks（及向量）。
- 每轮结束更新 index_state.revision（增量轮次）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from app.db.repo import Repo
from app.ingest.cleaner import ingest_file
from app.llm.embeddings import EmbeddingClient, EmbeddingUnavailable
from app.retrieval.chunker import chunk_text
from app.retrieval.tokenizer import tokenize
from app.vec.vector_store import VectorStore

SUPPORTED_EXTS = {".md", ".markdown", ".txt", ".html", ".htm", ".pdf"}


class Indexer:
    def __init__(self, repo: Repo, vec: VectorStore, embed: EmbeddingClient):
        self.repo = repo
        self.vec = vec
        self.embed = embed

    def scan_files(self, root: Path) -> list[Path]:
        files = []
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
                files.append(p)
        return files

    def run(self, root: Path, rebuild: bool = False) -> dict:
        """索引目录下所有支持文件。返回统计信息。

        性能要点：所有待索引 chunk 的 embedding 跨书统一批量调用（避免逐书
        单条请求，免费 API 单条 ~1s 而 64 条批量 <1s）。
        """
        t0 = time.time()
        stats = {"scanned": 0, "new_or_changed": 0, "skipped": 0, "removed": 0, "failed": []}

        files = self.scan_files(root)
        stats["scanned"] = len(files)

        if rebuild:
            self._reset()

        existing: dict[str, dict] = {}
        for b in self.repo.all_books():
            if b["status"] != "deleted":
                existing[b["book_id"]] = b

        seen_hashes: set[str] = set()
        changed_book_ids: list[str] = []
        # 待索引计划：(book_id, ingested, chunks)
        pending: list[tuple[str, object, list[dict]]] = []

        for path in files:
            stats["skipped"] += 1  # 默认视为跳过，实际未跳过会覆盖
            try:
                ingested = ingest_file(path, self.repo.db_path.parent / "archive" / "raw")
            except Exception as e:  # noqa: BLE001
                stats["failed"].append({"path": str(path), "error": str(e)})
                stats["skipped"] -= 1
                continue

            seen_hashes.add(ingested.content_hash)
            prev = self.repo.book_by_hash(ingested.content_hash)
            if prev and prev["book_id"] in existing:
                if self._has_chunks(prev["book_id"]):
                    continue
                # 内容没变但缺 chunks（索引被清过）→ 补索引
                chunks = chunk_text(ingested.clean_text, prev["book_id"])
                if chunks:
                    pending.append((prev["book_id"], ingested, chunks))
                    changed_book_ids.append(prev["book_id"])
                stats["new_or_changed"] += 1
                stats["skipped"] -= 1
                continue

            # 新增书
            book_id = ingested.book_id
            chunks = chunk_text(ingested.clean_text, book_id)
            if chunks:
                pending.append((book_id, ingested, chunks))
                changed_book_ids.append(book_id)
            stats["new_or_changed"] += 1
            stats["skipped"] -= 1

        # 统一批量 embedding + 写入
        if pending:
            all_texts: list[str] = []
            for _bid, _ing, chunks in pending:
                all_texts.extend(c["content"] for c in chunks)
            vecs: list[list[float]] | None = None
            try:
                vecs = self.embed.embed_many(all_texts)
            except EmbeddingUnavailable:
                vecs = None

            idx = 0
            for book_id, ingested, chunks in pending:
                self.repo.delete_chunks_by_book(book_id)
                self.vec.delete_by_book(book_id)
                book = {
                    "book_id": book_id,
                    "title": ingested.title,
                    "author": ingested.author,
                    "slug": Path(ingested.raw_path).stem,
                    "media_type": ingested.media_type,
                    "source_uri": str(ingested.raw_path),
                    "content_hash": ingested.content_hash,
                    "raw_path": str(ingested.raw_path),
                    "vault_path": "",
                    "card_path": "",
                    "status": "indexed",
                    "meta": json.dumps(ingested.meta, ensure_ascii=False),
                }
                self.repo.upsert_book(book)
                rows: list[dict] = []
                for chunk, vec in zip(chunks, vecs or [None] * len(chunks)):
                    chunk["token_cnt"] = len(chunk["content"])
                    chunk["fts_content"] = tokenize(chunk["content"])
                    chunk["vector_id"] = chunk["chunk_id"]
                    self.repo.insert_chunk(chunk)
                    if vec is not None:
                        rows.append(
                            {"chunk_id": chunk["chunk_id"], "book_id": book_id, "vector": vec}
                        )
                    idx += 1
                self.repo.commit()
                if rows:
                    self.vec.upsert(rows)
                if vecs is None:
                    self.repo.set_vector_pending(book_id, True)

        # 文件消失 → 删除
        for book_id, book in existing.items():
            if book["content_hash"] not in seen_hashes:
                self.repo.delete_chunks_by_book(book_id)
                self.vec.delete_by_book(book_id)
                self.repo.delete_book(book_id)
                stats["removed"] += 1
                changed_book_ids.append(book_id)

        self.repo.commit()
        revision = self.repo.latest_revision() + 1
        self.repo.set_state(revision, "active", changed_book_ids)
        stats["revision"] = revision
        stats["elapsed_sec"] = round(time.time() - t0, 2)
        return stats

    def _reset(self) -> None:
        for b in self.repo.all_books():
            self.repo.delete_chunks_by_book(b["book_id"])
            self.repo.delete_book(b["book_id"])
        if self.vec.exists():
            self.vec._db.drop_table(self.vec.table_name)  # noqa: SLF001
            self.vec._table = None  # noqa: SLF001

    def _has_chunks(self, book_id: str) -> bool:
        row = self.repo.conn.execute(
            "SELECT COUNT(*) AS c FROM chunks WHERE book_id=?", (book_id,)
        ).fetchone()
        return bool(row["c"])

    def index_book(self, book_id: str, ingested) -> dict:
        """对单本书执行 chunk + embedding + 写入（P3 入馆用，不依赖目录扫描）。

        区别于 run()：书已登记在 books 表（status=incoming），这里只补 chunks
        与向量，不碰 books 行、不触发"文件消失删除"逻辑。

        embedding 失败（无 key / 429 / 断网）→ 词法先行（FTS5 chunks 照写），
        书标 vector_pending=1，向量可稍后 backfill_vectors 补（与 index_books
        批量路径一致）。保证 classify / 阅览室等只依赖 chunks 的功能不卡死。
        """
        t0 = time.time()
        self.repo.delete_chunks_by_book(book_id)
        self.vec.delete_by_book(book_id)
        chunks = chunk_text(ingested.clean_text, book_id)
        if not chunks:
            return {"chunks": 0, "elapsed_sec": round(time.time() - t0, 2)}
        vecs: list[list[float]] | None = None
        try:
            vecs = self.embed.embed_many([c["content"] for c in chunks])
        except EmbeddingUnavailable:
            vecs = None
        rows: list[dict] = []
        for chunk, vec in zip(chunks, vecs or [None] * len(chunks)):
            chunk["token_cnt"] = len(chunk["content"])
            chunk["fts_content"] = tokenize(chunk["content"])
            chunk["vector_id"] = chunk["chunk_id"]
            self.repo.insert_chunk(chunk)
            if vec is not None:
                rows.append({"chunk_id": chunk["chunk_id"], "book_id": book_id, "vector": vec})
        self.repo.commit()
        if rows:
            self.vec.upsert(rows)
        if vecs is None:
            self.repo.set_vector_pending(book_id, True)
        return {"chunks": len(chunks), "vectors": len(rows),
                "fallback": vecs is None,
                "elapsed_sec": round(time.time() - t0, 2)}

    def index_books(self, infos: list[tuple[str, object]]) -> dict:
        """对多本书统一 chunk + 批量 embedding + 写入（P5-2 批量入馆提速）。

        infos: [(book_id, ingested), ...]
        所有书 chunk 合并后一次 embed_many（内部按 64 条/批）——把 N 次 API
        往返降为 N/64。
        embedding 失败（无 key / 429 / 断网）→ 全部落词法索引（FTS5），
        书标记 vector_pending=1（向量后台补），不阻断入馆。
        """
        t0 = time.time()
        stats = {"books": 0, "chunks": 0, "vectors": 0, "fallback": False,
                 "per_book": [], "elapsed_sec": 0.0}
        if not infos:
            return stats

        plan: list[tuple[str, list[dict]]] = []
        all_texts: list[str] = []
        for book_id, ingested in infos:
            self.repo.delete_chunks_by_book(book_id)
            self.vec.delete_by_book(book_id)
            chunks = chunk_text(ingested.clean_text, book_id)
            if chunks:
                plan.append((book_id, chunks))
                all_texts.extend(c["content"] for c in chunks)

        # 统一批量 embedding（一次 API 批量调用；失败则词法兜底）
        vecs: list[list[float]] | None = None
        try:
            vecs = self.embed.embed_many(all_texts)
        except EmbeddingUnavailable:
            vecs = None

        idx = 0
        for book_id, chunks in plan:
            rows: list[dict] = []
            for chunk in chunks:
                chunk["token_cnt"] = len(chunk["content"])
                chunk["fts_content"] = tokenize(chunk["content"])
                chunk["vector_id"] = chunk["chunk_id"]
                self.repo.insert_chunk(chunk)
                if vecs is not None:
                    rows.append({"chunk_id": chunk["chunk_id"], "book_id": book_id,
                                 "vector": vecs[idx]})
                idx += 1
            self.repo.commit()
            if rows:
                self.vec.upsert(rows)
            if vecs is None:
                self.repo.set_vector_pending(book_id, True)
            stats["books"] += 1
            stats["chunks"] += len(chunks)
            stats["vectors"] += len(rows)
            stats["per_book"].append(
                {"book_id": book_id, "chunks": len(chunks), "vectors": len(rows)}
            )

        stats["fallback"] = vecs is None
        stats["elapsed_sec"] = round(time.time() - t0, 2)
        return stats

    def backfill_vectors(self, book_ids: list[str] | None = None) -> dict:
        """给向量待补的书补向量（embedding 恢复后调用，P5-2 兜底）。

        从 chunks 表读回内容 → embed_many → LanceDB upsert → 清 vector_pending。
        """
        t0 = time.time()
        if book_ids is None:
            book_ids = self.repo.list_vector_pending()
        stats = {"books": 0, "chunks": 0, "errors": [], "elapsed_sec": 0.0}
        for book_id in book_ids:
            rows = self.repo.conn.execute(
                "SELECT chunk_id, content FROM chunks WHERE book_id=? ORDER BY seq",
                (book_id,),
            ).fetchall()
            if not rows:
                self.repo.set_vector_pending(book_id, False)
                continue
            try:
                vecs = self.embed.embed_many([r["content"] for r in rows])
            except EmbeddingUnavailable:
                stats["errors"].append({"book_id": book_id, "error": "embedding 不可用"})
                continue
            self.vec.upsert(
                [{"chunk_id": r["chunk_id"], "book_id": book_id, "vector": v}
                 for r, v in zip(rows, vecs)]
            )
            self.repo.set_vector_pending(book_id, False)
            stats["books"] += 1
            stats["chunks"] += len(rows)
        stats["elapsed_sec"] = round(time.time() - t0, 2)
        return stats

    def _index_book(self, book_id: str, ingested) -> None:
        """对一本书执行 chunk + embedding + 写入（先清旧）。"""
        chunks = chunk_text(ingested.clean_text, book_id)
        if not chunks:
            return
        # 清旧
        self.repo.delete_chunks_by_book(book_id)
        self.vec.delete_by_book(book_id)

        # embedding（失败则词法先行，向量后台补，与 index_book/index_books 一致）
        vecs: list[list[float]] | None = None
        try:
            vecs = self.embed.embed_many([c["content"] for c in chunks])
        except EmbeddingUnavailable:
            vecs = None

        rows: list[dict] = []
        for chunk, vec in zip(chunks, vecs or [None] * len(chunks)):
            chunk["token_cnt"] = len(chunk["content"])
            chunk["fts_content"] = tokenize(chunk["content"])
            chunk["vector_id"] = chunk["chunk_id"]
            self.repo.insert_chunk(chunk)
            if vec is not None:
                rows.append({"chunk_id": chunk["chunk_id"], "book_id": book_id, "vector": vec})
        self.repo.commit()
        if rows:
            self.vec.upsert(rows)
        if vecs is None:
            self.repo.set_vector_pending(book_id, True)

    # ---------- 完整性检测（P0 验收：索引损坏可检测） ----------

    def check(self) -> dict:
        """校验索引一致性，返回 {ok, rebuild_required, counts, issues}。

        检查项：
        - 已索引书是否有 chunk
        - FTS 索引完整性：抽样 chunk 的 token 做 MATCH，验证 shadow 索引可命中
        - chunks 行数 vs LanceDB 行数
        - archive 原始文件是否存在（content_hash -> raw_path）
        """
        issues: list[str] = []
        counts: dict = {"books": 0, "chunks": 0, "fts_ok": True, "vectors": 0}

        counts["books"] = len(self.repo.all_books())
        counts["chunks"] = self.repo.conn.execute(
            "SELECT COUNT(*) c FROM chunks"
        ).fetchone()["c"]

        if self.vec.exists():
            try:
                counts["vectors"] = self.vec.table.count_rows()
            except Exception as e:  # noqa: BLE001
                issues.append(f"向量表不可读: {e}")
                counts["vectors"] = -1

        # 1) 每本 status=indexed 的书应有 chunk
        for b in self.repo.all_books():
            n = self.repo.conn.execute(
                "SELECT COUNT(*) c FROM chunks WHERE book_id=?", (b["book_id"],)
            ).fetchone()["c"]
            if b["status"] == "indexed" and n == 0:
                issues.append(f"书 {b['book_id']} ({b['title']}) 状态为 indexed 但无 chunk")

        # 2) FTS 抽样 MATCH 验证（external content 表的行数恒等于 content 表，
        #    只能通过实际查询验证 shadow 索引是否完好）
        if counts["chunks"] > 0:
            samples = self.repo.conn.execute(
                "SELECT rowid, fts_content FROM chunks ORDER BY random() LIMIT 5"
            ).fetchall()
            misses = 0
            for r in samples:
                tokens = (r["fts_content"] or "").split()
                if not tokens:
                    continue
                try:
                    hit = self.repo.conn.execute(
                        "SELECT COUNT(*) c FROM chunks_fts WHERE chunks_fts MATCH ?",
                        (f'"{tokens[0]}"',),
                    ).fetchone()["c"]
                except Exception:  # noqa: BLE001
                    hit = 0
                if not hit:
                    misses += 1
            counts["fts_ok"] = misses == 0
            if misses:
                issues.append(f"FTS 索引异常：{misses}/5 抽样 token 无法命中（索引可能损坏）")

        # 3) 向量与 chunks 对齐（vector_pending 的书允许词法先行，向量后台补）
        if counts["vectors"] >= 0:
            pending_ids = set(self.repo.list_vector_pending())
            if pending_ids:
                ph = ",".join("?" for _ in pending_ids)
                pending_chunks = self.repo.conn.execute(
                    f"SELECT COUNT(*) c FROM chunks WHERE book_id IN ({ph})",
                    list(pending_ids),
                ).fetchone()["c"]
                counts["pending_vectors"] = pending_chunks
                if (counts["chunks"] - pending_chunks) != counts["vectors"]:
                    issues.append(
                        f"向量行数不一致: chunks={counts['chunks']} "
                        f"(含向量待补 {pending_chunks}) vectors={counts['vectors']}"
                    )
            elif counts["chunks"] != counts["vectors"]:
                issues.append(
                    f"向量行数不一致: chunks={counts['chunks']} vectors={counts['vectors']}"
                )

        # 4) archive 原始文件存在
        missing = 0
        for b in self.repo.all_books():
            if b.get("raw_path"):
                raw = Path(b["raw_path"])
                if not raw.exists():
                    missing += 1
        if missing:
            issues.append(f"{missing} 个 archive 原始文件缺失")

        # 向量待补的书（词法先行）不计入"chunks≠vectors"的重建判断
        pending_ids = set(self.repo.list_vector_pending())
        pending_chunks = 0
        if pending_ids:
            ph = ",".join("?" for _ in pending_ids)
            pending_chunks = self.repo.conn.execute(
                f"SELECT COUNT(*) c FROM chunks WHERE book_id IN ({ph})",
                list(pending_ids),
            ).fetchone()["c"]
        rebuild_required = ((not counts["fts_ok"])
                            or (counts["vectors"] >= 0
                                and (counts["chunks"] - pending_chunks) != counts["vectors"]))
        return {
            "ok": not issues and not rebuild_required,
            "rebuild_required": rebuild_required,
            "counts": counts,
            "issues": issues,
        }
