"""cleaner 测试：解析、规范化、不可变副本。"""
from __future__ import annotations

import pytest

from app.ingest.cleaner import ingest_file, normalize


def test_normalize():
    text = "行一  \r\n行二\n\n\n\n行三\r"
    assert normalize(text) == "行一\n行二\n\n行三"


def test_ingest_markdown(tmp_path):
    f = tmp_path / "note.md"
    f.write_text("# 我的笔记\n\n内容内容", encoding="utf-8")
    book = ingest_file(f, tmp_path / "archive" / "raw")
    assert book.title == "我的笔记"
    assert book.content_hash
    # 不可变副本已生成
    assert book.raw_path.exists()
    assert book.raw_path.read_bytes() == f.read_bytes()


def test_ingest_pdf_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        ingest_file(tmp_path / "nope.pdf", tmp_path / "archive" / "raw")


def test_ingest_unknown_format(tmp_path):
    f = tmp_path / "a.docx"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        ingest_file(f, tmp_path / "archive" / "raw")
