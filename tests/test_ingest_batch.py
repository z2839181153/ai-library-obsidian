"""P5-2 批量入馆测试：/api/ingest/batch 校验 / 去重 / 批量索引 / 异步 WS 进度 / 429 词法兜底 + 向量补。"""
from __future__ import annotations

import time

from app.llm.embeddings import EmbeddingUnavailable
from app.retrieval.indexer import Indexer
from tests.conftest import FakeEmbed


def _wait_for(predicate, timeout: float = 10.0, interval: float = 0.05) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _chunk_count(state, book_id: str) -> int:
    row = state.repo.conn.execute(
        "SELECT COUNT(*) c FROM chunks WHERE book_id=?", (book_id,)
    ).fetchone()
    return row["c"]


def _set_embed(state, embed):
    """替换 embedding 客户端 + 重建依赖它的 indexer（与 build_state 一致）。"""
    state.embed = embed
    state.indexer = Indexer(state.repo, state.vec, state.embed)


def _md_files(n: int, prefix: str = "批量书"):
    return [
        ("files", (f"{prefix}{i}.md",
                   f"# {prefix}{i}\n\n## 第一章\n内容 {i}。".encode(),
                   "text/markdown"))
        for i in range(n)
    ]


def _wait_indexed(state, book_ids, timeout: float = 10.0) -> bool:
    return _wait_for(lambda: all(_chunk_count(state, b) > 0 for b in book_ids), timeout)


# ---------- 校验 ----------


def test_batch_ingest_registers_all(client):
    r = client.post("/api/ingest/batch", data={"format": "markdown"},
                    files=_md_files(3))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["accepted"] == 3
    assert data["total"] == 3
    assert data["duplicates"] == 0
    assert data["errors"] == 0
    assert len(data["results"]) == 3
    assert all(x["status"] == "registered" for x in data["results"])
    assert all(x["error"] is None for x in data["results"])

    st = client.get("/api/books").json()
    assert st["count"] == 3

    state = client.app.state.library
    ids = [x["book_id"] for x in data["results"]]
    # 登记即 incoming（补书室），索引在后台完成
    for i in ids:
        assert state.repo.get_book(i)["status"] == "incoming"
    assert _wait_indexed(state, ids)
    # 向量与 chunks 齐（FakeEmbed 可用）
    assert all(state.repo.get_book(i)["vector_pending"] == 0 for i in ids)
    assert all(_chunk_count(state, i) > 0 for i in ids)


def test_batch_rejects_over_10(client):
    r = client.post("/api/ingest/batch", data={"format": "markdown"},
                    files=_md_files(11))
    assert r.status_code == 400, r.text
    assert "最多" in r.json()["detail"]


def test_batch_rejects_mixed_format(client):
    files = [
        ("files", ("a.md", "# A\n\n内容".encode(), "text/markdown")),
        ("files", ("b.txt", "# B\n\n内容".encode(), "text/plain")),
    ]
    r = client.post("/api/ingest/batch", data={"format": "markdown"}, files=files)
    assert r.status_code == 400, r.text
    assert "混格式" in r.json()["detail"]
    # 整批拒绝：没有登记任何书
    st = client.get("/api/books").json()
    assert st["count"] == 0


def test_batch_missing_format(client):
    r = client.post("/api/ingest/batch", files=_md_files(2))
    assert r.status_code == 400, r.text
    assert "必须选择格式" in r.json()["detail"]


def test_batch_unsupported_format(client):
    r = client.post("/api/ingest/batch", data={"format": "epub"},
                    files=_md_files(1))
    assert r.status_code == 400, r.text
    assert "必须选择格式" in r.json()["detail"]


def test_batch_no_files(client):
    r = client.post("/api/ingest/batch", data={"format": "markdown"})
    assert r.status_code == 400, r.text
    assert "需要上传文件" in r.json()["detail"]


# ---------- 去重 ----------


def test_batch_duplicate_of_existing(client):
    """与馆内已有书内容重复 → 标记 duplicate，不阻断其余。"""
    content = "# 已存在的书\n\n## 第一章\n正文。".encode()
    r1 = client.post("/api/ingest", files={"file": ("exist.md", content, "text/markdown")})
    assert r1.json()["created"] is True
    existing_id = r1.json()["book"]["book_id"]

    files = [
        ("files", ("dup.md", content, "text/markdown")),
        ("files", ("new.md", "# 新书\n\n## 第一章\n新内容。".encode(), "text/markdown")),
    ]
    r = client.post("/api/ingest/batch", data={"format": "markdown"}, files=files)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["accepted"] == 1
    assert data["duplicates"] == 1

    by_name = {x["filename"]: x for x in data["results"]}
    assert by_name["dup.md"]["status"] == "duplicate"
    assert by_name["dup.md"]["book_id"] == existing_id
    assert by_name["dup.md"]["duplicate"] is True
    assert by_name["new.md"]["status"] == "registered"

    st = client.get("/api/books").json()
    assert st["count"] == 2  # 已存在 + 新书，重复书不重复登记


def test_batch_in_batch_duplicate(client):
    """同一批内两份相同内容 → 第二份标 duplicate。"""
    content = "# 批内重复\n\n## 第一章\n内容。".encode()
    files = [
        ("files", ("a.md", content, "text/markdown")),
        ("files", ("b.md", content, "text/markdown")),
        ("files", ("c.md", "# 独立书\n\n## 第一章\n其他内容。".encode(), "text/markdown")),
    ]
    r = client.post("/api/ingest/batch", data={"format": "markdown"}, files=files)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["accepted"] == 2
    assert data["duplicates"] == 1
    by_name = {x["filename"]: x for x in data["results"]}
    assert by_name["a.md"]["status"] == "registered"
    assert by_name["b.md"]["status"] == "duplicate"
    assert "批内" in by_name["b.md"]["error"]
    assert by_name["c.md"]["status"] == "registered"
    st = client.get("/api/books").json()
    assert st["count"] == 2


# ---------- 批量 embedding 提速 ----------


class CountingEmbed(FakeEmbed):
    """记录 embed_many 调用次数与文本量（验证 N 次往返降为 1 次批量）。"""

    def __init__(self):
        super().__init__()
        self.calls: list[int] = []

    def embed_many(self, texts):
        self.calls.append(len(texts))
        return super().embed_many(texts)


def test_batch_embedding_single_call(client):
    """3 本书所有 chunk 合并成一次 embed_many（而非逐本 3 次）。"""
    state = client.app.state.library
    counting = CountingEmbed()
    _set_embed(state, counting)

    r = client.post("/api/ingest/batch", data={"format": "markdown"},
                    files=_md_files(3, "提速书"))
    assert r.status_code == 200, r.text
    ids = [x["book_id"] for x in r.json()["results"]]
    assert _wait_indexed(state, ids)
    assert len(counting.calls) == 1, f"期望 1 次批量调用，实际 {len(counting.calls)}"
    total_chunks = sum(_chunk_count(state, i) for i in ids)
    assert counting.calls[0] == total_chunks


# ---------- 429/额度兜底：词法先行 + 向量后台补 ----------


class FailingEmbed(FakeEmbed):
    def embed_many(self, texts):
        raise EmbeddingUnavailable("429 insufficient balance")

    def embed_one(self, text):
        raise EmbeddingUnavailable("429 insufficient balance")


def test_batch_fallback_lexical_on_embed_failure(client):
    """embedding 不可用 → 书照常入馆，落词法索引，vector_pending=1。"""
    state = client.app.state.library
    _set_embed(state, FailingEmbed())

    r = client.post("/api/ingest/batch", data={"format": "markdown"},
                    files=_md_files(2, "兜底书"))
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] == 2
    ids = [x["book_id"] for x in r.json()["results"]]
    assert _wait_indexed(state, ids)
    for i in ids:
        assert state.repo.get_book(i)["vector_pending"] == 1
        assert _chunk_count(state, i) > 0  # FTS5 词法索引在

    # 词法检索仍可命中（Searcher.search 对 embedding 异常自动词法兜底）
    hit = state.searcher.search("内容 0")
    assert any(b["book_id"] in ids for b in hit["books"])

    # 完整性检测：向量待补的书不判为损坏
    chk = state.indexer.check()
    assert chk["ok"] is True


def test_backfill_vectors_after_fallback(client):
    """embedding 恢复后 backfill_vectors() 补齐向量并清除标记。"""
    state = client.app.state.library
    _set_embed(state, FailingEmbed())

    r = client.post("/api/ingest/batch", data={"format": "markdown"},
                    files=_md_files(1, "补齐书"))
    ids = [x["book_id"] for x in r.json()["results"]]
    assert _wait_indexed(state, ids)
    assert state.repo.get_book(ids[0])["vector_pending"] == 1

    # 恢复 embed → 补齐
    _set_embed(state, FakeEmbed())
    back = state.indexer.backfill_vectors()
    assert back["books"] == 1
    assert back["errors"] == []
    assert state.repo.get_book(ids[0])["vector_pending"] == 0
    # 向量表行数与 chunks 对齐
    n_chunks = _chunk_count(state, ids[0])
    assert state.vec.exists()
    vec_rows = state.vec.table.count_rows()
    assert vec_rows == n_chunks


# ---------- WS 进度广播 ----------


def test_batch_ws_events(client):
    """批量入馆广播 batch_ingested + 后台索引进度 batch_index_progress/done。"""
    state = client.app.state.library
    received = []

    import asyncio

    def drain_until_done(q, out):
        """把队列里现有消息取出；出现 batch_index_done 才算后台索引跑完。"""
        while not q.empty():
            out.append(q.get_nowait())
        return any(m.get("type") == "notice" and m.get("event") == "batch_index_done"
                   for m in out)

    manager = state.ws
    q = asyncio.Queue()
    fake_id = "ws_batch_test"
    with manager._lock:
        manager._clients[fake_id] = {"ws": object(), "queue": q,
                                     "loop": asyncio.new_event_loop()}

    try:
        r = client.post("/api/ingest/batch", data={"format": "markdown"},
                        files=_md_files(2, "WS书"))
        assert r.json()["accepted"] == 2
        ids = [x["book_id"] for x in r.json()["results"]]

        assert _wait_for(lambda: drain_until_done(q, received), 10.0)
        events = [m.get("event") for m in received if m.get("type") == "notice"]
        assert "batch_ingested" in events
        assert "batch_index_progress" in events
        assert "batch_index_done" in events
        done = next(m for m in received if m.get("event") == "batch_index_done")
        assert done["books"] == 2
        # 逐本 progress 至少覆盖每本书
        prog_ids = {m["book_id"] for m in received if m.get("event") == "batch_index_progress"}
        assert prog_ids == set(ids)
    finally:
        with manager._lock:
            manager._clients.pop(fake_id, None)


# ---------- private / 账本 ----------


def test_batch_private_flag(client):
    r = client.post("/api/ingest/batch", data={"format": "markdown", "private": "true"},
                    files=_md_files(1, "私密书"))
    assert r.status_code == 200, r.text
    book_id = r.json()["results"][0]["book_id"]
    assert client.app.state.library.repo.get_book(book_id)["private"] == 1


def test_batch_records_actions(client):
    r = client.post("/api/ingest/batch", data={"format": "markdown"},
                    files=_md_files(2, "账本书"))
    ids = [x["book_id"] for x in r.json()["results"]]
    acts = client.get("/api/actions").json()["actions"]
    ingest_acts = [a for a in acts if a["action_type"] == "ingest"]
    assert len(ingest_acts) == 2
    assert {a["target_id"] for a in ingest_acts} == set(ids)
