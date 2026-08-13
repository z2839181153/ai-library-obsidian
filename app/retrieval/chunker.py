"""文本分块：按 markdown 标题分节，超长再按段落/字数切分（设计文档 §7.1）。

策略：
- 一级/二级标题（# / ##）作为节边界，节内标题行保留。
- 目标块 500-800 token（中文约 800-1200 字），块间重叠 10%（跨节不重叠）。
- 每块输出 section 名（最后遇到的标题），供引用溯源。
"""
from __future__ import annotations

import re

TARGET_CHARS = 900          # 中文约 900 字 ≈ 500-600 token
MAX_CHARS = 1200
OVERLAP_CHARS = 100         # 约 10%


def _heading_level(line: str) -> int:
    m = re.match(r"^(#{1,6})\s+", line)
    return len(m.group(1)) if m else 0


def split_sections(text: str) -> list[tuple[str, str]]:
    """按标题分节，返回 [(section_title, body)]。无标题则整篇为 ('', body)。"""
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in lines:
        # 仅 0 < level <= 2 视为节标题；level 0 是普通正文
        if line.strip() and 0 < _heading_level(line) <= 2:
            sections.append((line.strip(), []))
        else:
            sections[-1][1].append(line)
    return [(title, "\n".join(body).strip()) for title, body in sections if body]


def _split_long(text: str, section: str) -> list[str]:
    """超长节按段落切，段超长按字数切（带重叠）。"""
    if len(text) <= MAX_CHARS:
        return [text]
    chunks: list[str] = []
    # 先按空行分段落
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    buf = ""
    for p in paras:
        while len(p) > MAX_CHARS:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(p[:MAX_CHARS])
            p = p[MAX_CHARS - OVERLAP_CHARS:]
        if len(buf) + len(p) + 1 <= TARGET_CHARS:
            buf = f"{buf}\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            buf = p
    if buf:
        chunks.append(buf)
    return chunks


def chunk_text(text: str, book_id: str) -> list[dict]:
    """整篇文本 -> chunk 列表 [{chunk_id, book_id, section, seq, content}]。"""
    chunks: list[dict] = []
    seq = 0
    for section, body in split_sections(text):
        for piece in _split_long(body, section):
            if not piece.strip():
                continue
            seq += 1
            chunks.append(
                {
                    "chunk_id": f"ck_{book_id}_{seq:04d}",
                    "book_id": book_id,
                    "section": section,
                    "seq": seq,
                    "content": piece,
                }
            )
    return chunks
