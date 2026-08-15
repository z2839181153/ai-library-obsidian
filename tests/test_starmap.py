"""P4-1 占星室测试：/api/starmap 图数据（书/技能/主题/档案/对话 + 关联）。
P5-5 扩展：书↔书语义边（卡片向量相似 / 同房间 / 同标签 / 引用 + top-k + 词法兜底）。
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.config import AppConfig
from app.state import build_state


def _make_state(tmp_path, embed=None, llm=None):
    cfg = AppConfig()
    cfg.paths.data_dir = tmp_path / "data"
    cfg.paths.vault_dir = tmp_path / "vault"
    from tests.conftest import FakeEmbed, FakeLLM

    return build_state(cfg, embed=embed or FakeEmbed(), llm=llm or FakeLLM())


def _make_app(state):
    from fastapi import FastAPI

    from app import __version__
    from app.api import (actions, ask, books, conversations, dashboard, distill,
                         floors, health, index, ingest, purchase, settings, skills, starmap, ws)

    app = FastAPI(title="AI Library Test", version=__version__)
    app.state.library = state
    app.include_router(health.router, prefix="/api")
    app.include_router(index.router, prefix="/api")
    app.include_router(books.router, prefix="/api")
    app.include_router(actions.router, prefix="/api")
    app.include_router(floors.router, prefix="/api")
    app.include_router(ask.router, prefix="/api")
    app.include_router(distill.router, prefix="/api")
    app.include_router(skills.router, prefix="/api")
    app.include_router(ingest.router, prefix="/api")
    app.include_router(settings.router, prefix="/api")
    app.include_router(dashboard.router, prefix="/api")
    app.include_router(purchase.router, prefix="/api")
    app.include_router(conversations.router, prefix="/api")
    app.include_router(starmap.router, prefix="/api")
    app.include_router(ws.router)
    return app


def _add_book(state, title, *, status="incoming", vault_path="", suggest_room="",
              summary=None, tags=None, content="", bid=None):
    """直接造书（+卡片 +chunks），免走 ingest 管线。返回 book_id。"""
    from app.db.repo import new_id

    bid = bid or new_id("bk")
    state.repo.upsert_book({
        "book_id": bid, "title": title, "status": status,
        "vault_path": vault_path, "suggest_room": suggest_room,
        "media_type": "markdown",
    })
    if summary is not None or tags is not None:
        state.repo.upsert_card({
            "book_id": bid, "summary": summary or "",
            "tags": json.dumps(tags or []),
            "category": "reference",
        })
    if content:
        state.repo.conn.execute(
            "INSERT INTO chunks (chunk_id, book_id, section, seq, content,"
            " fts_content, token_cnt) VALUES (?,?,?,?,?,?,?)",
            (new_id("ch"), bid, "正文", 0, content, " ".join(content.split()), 1),
        )
        state.repo.conn.commit()
    return bid


def test_starmap_empty(tmp_path):
    """空库：返回空 nodes/links 与 0 计数。"""
    app = _make_app(_make_state(tmp_path))
    with TestClient(app) as c:
        r = c.get("/api/starmap")
        assert r.status_code == 200
        d = r.json()
        assert d["nodes"] == []
        assert d["links"] == []
        assert d["counts"]["book"] == 0


def test_starmap_books_and_links(tmp_path):
    """入馆 1 本书（incoming）→ 有书节点 + archive 原始副本节点 + 关联。"""
    state = _make_state(tmp_path)
    app = _make_app(state)

    # 手工入馆：写文件 → ingest 管线
    from app.ingest.cleaner import ingest_file

    from app.api.ingest import _register_and_index

    src = tmp_path / "book.md"
    src.write_text("# 测试书\n\n## 第一章\n\n神经网络介绍。\n", encoding="utf-8")
    ingested = ingest_file(src, state.cfg.paths.data_dir / "archive" / "raw")
    result = _register_and_index(state, ingested)
    assert result["created"] is True
    bid = result["book"]["book_id"]

    with TestClient(app) as c:
        d = c.get("/api/starmap").json()
        types = {n["type"] for n in d["nodes"]}
        assert "book" in types
        assert "archive" in types
        book_node = next(n for n in d["nodes"] if n["type"] == "book")
        assert book_node["id"] == bid
        assert book_node["status"] == "incoming"
        # 书 ↔ 原始副本
        arc_links = [l for l in d["links"] if l["relation"] == "raw_copy"]
        assert any(l["source"] == bid for l in arc_links)


def test_starmap_theme_and_skill_links(tmp_path):
    """上架后 → book↔theme；注册技能后 → book↔skill。"""
    state = _make_state(tmp_path)
    app = _make_app(state)

    # 入馆 + 确认上架
    from app.ingest.cleaner import ingest_file

    from app.api.ingest import _register_and_index

    src = tmp_path / "ml.md"
    src.write_text("# 机器学习\n\n## 基础\n\n监督学习与无监督学习。\n", encoding="utf-8")
    ingested = ingest_file(src, state.cfg.paths.data_dir / "archive" / "raw")
    result = _register_and_index(state, ingested)
    bid = result["book"]["book_id"]

    state.shelver.confirm_shelve(bid, floor="1F", room="人工智能", shelf="入门")

    # 注册一个技能（关联本书）
    from app.db.repo import new_id

    sk_id = new_id("sk")
    state.repo.upsert_skill({
        "skill_id": sk_id,
        "book_id": bid,
        "name": "机器学习技能",
        "slug": "ml-skill",
        "path": f"vault/skills/{bid}/ml-skill/SKILL.md",
        "description": "当用户询问机器学习时使用",
        "status": "approved",
    })

    with TestClient(app) as c:
        d = c.get("/api/starmap").json()
        themes = [n for n in d["nodes"] if n["type"] == "theme"]
        skills = [n for n in d["nodes"] if n["type"] == "skill"]
        assert any(n["name"] == "人工智能" for n in themes)
        assert any(n["id"] == sk_id for n in skills)

        relations = {l["relation"] for l in d["links"]}
        assert "shelved_in" in relations
        assert "distilled" in relations
        # book↔theme 与 book↔skill 都连到这本书
        assert any(l["source"] == bid and l["relation"] == "shelved_in" for l in d["links"])
        assert any(l["source"] == bid and l["relation"] == "distilled" for l in d["links"])


def test_starmap_conversation_links(tmp_path):
    """对话消息引用书 → conversation↔book 关联；归档 → archived。"""
    state = _make_state(tmp_path)
    app = _make_app(state)

    # 造一本书
    from app.ingest.cleaner import ingest_file

    from app.api.ingest import _register_and_index
    from app.api.conversations import append_message, ensure_conversation

    src = tmp_path / "qa.md"
    src.write_text("# 问答书\n\n## 内容\n\n可引用的资料。\n", encoding="utf-8")
    ingested = ingest_file(src, state.cfg.paths.data_dir / "archive" / "raw")
    result = _register_and_index(state, ingested)
    bid = result["book"]["book_id"]

    # 对话引用这本书
    cv_id = ensure_conversation(state, "测试对话")
    append_message(state, cv_id, "assistant", "参考 [[catalog/%s]]" % bid,
                   refs=[{"book_id": bid, "link": f"[[catalog/{bid}]]"}])

    with TestClient(app) as c:
        d = c.get("/api/starmap").json()
        convs = [n for n in d["nodes"] if n["type"] == "conversation"]
        assert any(n["id"] == cv_id for n in convs)
        assert any(l["source"] == cv_id and l["target"] == bid
                   and l["relation"] == "referenced" for l in d["links"])


# ---------- P5-5 书↔书语义边 ----------

def _book_links(d):
    """只取书↔书边（source 和 target 都是书节点）。"""
    book_ids = {n["id"] for n in d["nodes"] if n["type"] == "book"}
    return [l for l in d["links"]
            if l["source"] in book_ids and l["target"] in book_ids]


def test_starmap_book_book_semantic_edge(tmp_path):
    """两张卡片文本相同的书 → semantic 边（相似度 ≈ 1.0），semantic_source=embedding。"""
    state = _make_state(tmp_path)
    app = _make_app(state)

    summary = "机器学习与人工智能基础概念，介绍监督学习与神经网络。"
    a = _add_book(state, "机器学习入门", summary=summary, tags=["AI"])
    b = _add_book(state, "人工智能导论", summary=summary, tags=["AI"])

    with TestClient(app) as c:
        d = c.get("/api/starmap").json()
        sem = [l for l in _book_links(d) if l["relation"] == "semantic"]
        assert len(sem) == 1
        assert {sem[0]["source"], sem[0]["target"]} == {a, b}
        assert sem[0]["similarity"] > 0.9
        assert d["book_edges"]["semantic"] == 1
        assert d["book_edges"]["semantic_source"] == "embedding"


def test_starmap_book_book_same_room(tmp_path):
    """同房间（vault_path 房间段相同）→ same_room 边。"""
    state = _make_state(tmp_path)
    app = _make_app(state)

    a = _add_book(state, "书A", status="shelved", vault_path="books/1F-电子书/机器学习/入门/书A")
    b = _add_book(state, "书B", status="shelved", vault_path="books/1F-电子书/机器学习/入门/书B")

    with TestClient(app) as c:
        d = c.get("/api/starmap").json()
        room = [l for l in _book_links(d) if l["relation"] == "same_room"]
        assert len(room) == 1
        assert {room[0]["source"], room[0]["target"]} == {a, b}


def test_starmap_book_book_same_tag(tmp_path):
    """共享标签 → same_tag 边（不同房间，避免 same_room 抢先占对）。"""
    state = _make_state(tmp_path)
    app = _make_app(state)

    a = _add_book(state, "书A", suggest_room="房间甲", summary="内容甲", tags=["密码学"])
    b = _add_book(state, "书B", suggest_room="房间乙", summary="内容乙", tags=["密码学"])
    c = _add_book(state, "书C", suggest_room="房间丙", summary="内容丙", tags=["哲学"])

    with TestClient(app) as c:
        d = c.get("/api/starmap").json()
        tag = [l for l in _book_links(d) if l["relation"] == "same_tag"]
        assert len(tag) == 1
        assert {tag[0]["source"], tag[0]["target"]} == {a, b}


def test_starmap_book_book_references(tmp_path):
    """书 A 正文引用 [[catalog/bk_B]] → references 边。"""
    state = _make_state(tmp_path)
    app = _make_app(state)

    b = _add_book(state, "被引书", summary="独立内容", tags=["哲学"])
    a = _add_book(state, "引文", summary="独立内容二", tags=["历史"],
                  content=f"参见 [[catalog/{b}]] 的详细说明。")

    with TestClient(app) as c:
        d = c.get("/api/starmap").json()
        refs = [l for l in _book_links(d) if l["relation"] == "references"]
        assert len(refs) == 1
        assert refs[0]["source"] == a and refs[0]["target"] == b


def test_starmap_book_book_topk_truncation(tmp_path):
    """8 本卡片文本相同的书：语义边每本书度数 ≤ top-k(5)，总数 < 完全图 28。"""
    state = _make_state(tmp_path)
    app = _make_app(state)

    summary = "完全相同的内容，用于验证 top-k 截断防毛线球机制。"
    b_ids = [_add_book(state, f"书{i}", summary=summary, tags=["同题"]) for i in range(8)]

    with TestClient(app) as c:
        d = c.get("/api/starmap").json()
        sem = [l for l in _book_links(d) if l["relation"] == "semantic"]
        degree = {bid: 0 for bid in b_ids}
        for l in sem:
            degree[l["source"]] += 1
            degree[l["target"]] += 1
        assert len(sem) < 28          # 完全图 28 条，截断后更少
        assert max(degree.values()) <= 5
        assert d["book_edges"]["semantic"] == len(sem)


def test_starmap_book_book_lexical_fallback(tmp_path):
    """embedding 不可用（抛异常）→ 词法兜底仍产出语义边，semantic_source=lexical。"""
    from app.llm.embeddings import EmbeddingUnavailable

    class FailEmbed:
        """embed_many 直接失败（模拟无 key / 429）。"""

        def embed_many(self, texts):
            raise EmbeddingUnavailable("no key")

        def embed_one(self, text):
            raise EmbeddingUnavailable("no key")

    state = _make_state(tmp_path, embed=FailEmbed())
    app = _make_app(state)

    summary = "分布式系统与一致性算法，包括 Raft 与 Paxos。"
    a = _add_book(state, "分布式系统", summary=summary)
    b = _add_book(state, "一致性算法", summary=summary)

    with TestClient(app) as c:
        d = c.get("/api/starmap").json()
        assert d["book_edges"]["semantic_source"] == "lexical"
        sem = [l for l in _book_links(d) if l["relation"] == "semantic"]
        assert len(sem) == 1
        assert {sem[0]["source"], sem[0]["target"]} == {a, b}
        assert sem[0]["similarity"] > 0.9


def test_starmap_book_book_exclude_deleted(tmp_path):
    """已删除书不参与书↔书边。"""
    state = _make_state(tmp_path)
    app = _make_app(state)

    a = _add_book(state, "书A", status="shelved", vault_path="books/1F-电子书/机器学习/入门/书A")
    b = _add_book(state, "书B", status="shelved", vault_path="books/1F-电子书/机器学习/入门/书B")
    c = _add_book(state, "书C", status="deleted", vault_path="books/1F-电子书/机器学习/入门/书C")

    with TestClient(app) as c:
        d = c.get("/api/starmap").json()
        bb = _book_links(d)
        # 没有边连到已删除书 C
        assert all(l["source"] != c and l["target"] != c for l in bb)
        # A↔B 仍有 same_room 边
        assert any(l["relation"] == "same_room" and {l["source"], l["target"]} == {a, b}
                   for l in bb)

