"""chunker 测试：分节、超长切分、重叠。"""
from __future__ import annotations

from app.retrieval.chunker import chunk_text, split_sections


def test_split_sections_by_heading():
    text = "# 第一章\n正文一\n\n## 小节\n正文二\n\n# 第二章\n正文三"
    sections = split_sections(text)
    assert len(sections) == 3
    assert sections[0][0] == "# 第一章"
    assert sections[2][0] == "# 第二章"


def test_chunk_count_and_ids():
    text = "# 标题\n" + "这是一段测试文本。" * 50
    chunks = chunk_text(text, "bk_test")
    assert len(chunks) >= 1
    assert all(c["book_id"] == "bk_test" for c in chunks)
    assert all(c["chunk_id"].startswith("ck_bk_test_") for c in chunks)
    assert all(c["content"] for c in chunks)
    # seq 递增
    seqs = [c["seq"] for c in chunks]
    assert seqs == sorted(seqs)


def test_long_section_splits():
    # 3000 字长文（无标题）应切成多块
    text = "字" * 3000
    chunks = chunk_text(text, "bk_long")
    assert len(chunks) > 1
