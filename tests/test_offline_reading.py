"""P5-3 无额度读原文测试：离线优先读 vault / chunks / archive/raw 兜底 + 原文件查看。

核心断言：content 端点纯本地（不调 LLM），未上架/未编目书从原始副本读回正文；
raw-file 端点按 media_type 返回正确 MIME 的原始文件。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.state import build_state


def _make_state(tmp_path):
    cfg = AppConfig()
    cfg.paths.data_dir = tmp_path / "data"
    cfg.paths.vault_dir = tmp_path / "vault"
    from tests.conftest import FakeEmbed, FakeLLM

    return build_state(cfg, embed=FakeEmbed(), llm=FakeLLM())


def _make_app(state):
    from fastapi import FastAPI

    from app import __version__
    from app.api import (actions, archive, ask, books, conversations, dashboard,
                         distill, floors, health, index, ingest, profile, purchase,
                         settings, skills, starmap, ws)

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
    app.include_router(archive.router, prefix="/api")
    app.include_router(profile.router, prefix="/api")
    app.include_router(ws.router)
    return app


def _ingest_only(state, tmp_path, name="测试书.md", text=None, raw_bytes=None):
    """登记书但不索引（无 chunks）→ 验证 archive/raw 兜底路径。"""
    from app.ingest.cleaner import ingest_file

    src = tmp_path / name
    if raw_bytes is not None:
        src.write_bytes(raw_bytes)
    else:
        src.write_text(text or "# 测试书\n\n## 章节\n\n内容。\n", encoding="utf-8")
    ingested = ingest_file(src, state.cfg.paths.data_dir / "archive" / "raw")
    book = {
        "book_id": ingested.book_id,
        "title": ingested.title,
        "author": ingested.author,
        "slug": src.stem,
        "media_type": ingested.media_type,
        "source_uri": str(ingested.raw_path),
        "content_hash": ingested.content_hash,
        "raw_path": str(ingested.raw_path),
        "vault_path": "",
        "card_path": "",
        "status": "incoming",
        "private": 0,
        "meta": '{"format": "%s"}' % ingested.media_type,
    }
    state.repo.upsert_book(book)
    return ingested.book_id


def _ingest_and_index(state, tmp_path, name="测试书.md", text="# 测试书\n\n## 章节\n\n内容。\n"):
    """登记 + 索引（有 chunks）。"""
    from app.ingest.cleaner import ingest_file

    from app.api.ingest import _register_and_index

    src = tmp_path / name
    src.write_text(text, encoding="utf-8")
    ingested = ingest_file(src, state.cfg.paths.data_dir / "archive" / "raw")
    return _register_and_index(state, ingested)["book"]["book_id"]


def _shelve(state, book_id, room="测试房", shelf="测试架"):
    """手工构造已上架状态：写 vault book.md + 更新 vault_path。"""
    vp = state.cfg.paths.vault_dir / "books" / "1F-电子书" / room / shelf / book_id[:8]
    vp.mkdir(parents=True, exist_ok=True)
    (vp / "book.md").write_text(
        f"# {book_id}\n\n## 章节甲\n\nvault 甲内容。\n\n## 章节乙\n\nvault 乙内容。\n",
        encoding="utf-8",
    )
    state.repo.update_book_fields(book_id, {"vault_path": f"books/1F-电子书/{room}/{shelf}/{book_id[:8]}"})


def _make_pdf_bytes() -> bytes:
    """生成合法极简 PDF（单页，含文本 'Hello PDF Offline'），pypdf 可解析。"""
    stream_content = b"BT /F1 24 Tf 100 700 Td (Hello PDF Offline) Tj ET\n"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"),
        (b"<< /Length " + str(len(stream_content)).encode("ascii") + b" >>\nstream\n"
         + stream_content + b"endstream"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("ascii")
        out += o + b"\n"
        out += b"endobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode("ascii")
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n").encode("ascii")
    return bytes(out)


# ---------- content：三级来源 ----------

def test_content_shelved_reads_vault(tmp_path):
    """已上架书：content 读 vault book.md（source=vault）。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    c = TestClient(app)
    bid = _ingest_and_index(state, tmp_path)
    _shelve(state, bid)

    d = c.get(f"/api/books/{bid}/content").json()
    assert d["source"] == "vault"
    joined = "\n".join(s["content"] for s in d["sections"])
    assert "vault 甲内容" in joined


def test_content_unshelved_reads_chunks(tmp_path):
    """未上架但有 chunks：content 读 chunks 拼接（source=chunks）。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    c = TestClient(app)
    bid = _ingest_and_index(state, tmp_path)

    d = c.get(f"/api/books/{bid}/content").json()
    assert d["source"] == "chunks"
    assert d["sections"]


def test_content_unshelved_no_chunks_reads_raw(tmp_path):
    """未上架且无 chunks（未编目/未索引）：content 从 archive/raw 读回原文（source=raw）。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    c = TestClient(app)
    bid = _ingest_only(state, tmp_path, text="# 离线书\n\n## 离线章节\n\n原始副本内容。\n")

    d = c.get(f"/api/books/{bid}/content").json()
    assert d["source"] == "raw"
    assert d["raw_available"] is True
    joined = "\n".join(s["content"] for s in d["sections"])
    assert "原始副本内容" in joined
    assert any(s["title"] == "离线章节" for s in d["sections"])


def test_content_raw_pdf_fallback(tmp_path):
    """PDF 书无 chunks：content 用 PDF 解析器从原始副本读回文本。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    c = TestClient(app)
    bid = _ingest_only(state, tmp_path, name="离线.pdf", raw_bytes=_make_pdf_bytes())

    d = c.get(f"/api/books/{bid}/content").json()
    assert d["source"] == "raw"
    joined = "\n".join(s["content"] for s in d["sections"])
    assert "Hello PDF Offline" in joined


def test_content_never_calls_llm(tmp_path):
    """content 纯本地：LLM 调用记录为空（无额度/断网场景正文照常）。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    c = TestClient(app)
    bid = _ingest_only(state, tmp_path)

    c.get(f"/api/books/{bid}/content")
    assert state.llm.calls == []


def test_content_missing_book_404(tmp_path):
    state = _make_state(tmp_path)
    app = _make_app(state)
    c = TestClient(app)
    assert c.get("/api/books/bk_nonexistent/content").status_code == 404


# ---------- raw-file：原文件查看 ----------

def test_raw_file_pdf_mime_and_bytes(tmp_path):
    """PDF 原文件：Content-Type=application/pdf，字节与原始副本一致。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    c = TestClient(app)
    pdf = _make_pdf_bytes()
    bid = _ingest_only(state, tmp_path, name="离线.pdf", raw_bytes=pdf)

    r = c.get(f"/api/books/{bid}/raw-file")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content == pdf
    # 浏览器 viewer 友好的内联文件名
    assert ".pdf" in r.headers.get("content-disposition", "")


def test_raw_file_markdown_mime(tmp_path):
    """markdown 原文件：Content-Type=text/markdown，内容一致。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    c = TestClient(app)
    bid = _ingest_only(state, tmp_path, name="笔记.md", text="# 笔记\n\n## 节\n\nmd 内容。\n")

    r = c.get(f"/api/books/{bid}/raw-file")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "md 内容" in r.text


def test_raw_file_404_when_no_raw(tmp_path):
    """书无原始副本（无 raw_path/content_hash）：raw-file 404。"""
    state = _make_state(tmp_path)
    app = _make_app(state)
    c = TestClient(app)
    state.repo.upsert_book({
        "book_id": "bk_noraw",
        "title": "无副本",
        "media_type": "text",
        "raw_path": "",
        "content_hash": None,
        "vault_path": "",
        "card_path": "",
        "status": "incoming",
        "private": 0,
        "meta": "{}",
    })
    assert c.get("/api/books/bk_noraw/raw-file").status_code == 404
    # content 也降级为空正文而非 500
    d = c.get("/api/books/bk_noraw/content").json()
    assert d["sections"] == []
    assert d["raw_available"] is False
