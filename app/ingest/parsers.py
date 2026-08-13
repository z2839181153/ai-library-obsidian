"""多格式解析（设计文档 §3.4）：md/txt/html/pdf。

输出统一为规范化文本 + 元信息（标题/作者/来源）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParsedDoc:
    text: str
    title: str = ""
    author: str = ""
    meta: dict = field(default_factory=dict)


def parse_markdown(path: Path) -> ParsedDoc:
    text = path.read_text(encoding="utf-8", errors="replace")
    title = ""
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return ParsedDoc(text=text, title=title, meta={"format": "markdown"})


def parse_text(path: Path) -> ParsedDoc:
    text = path.read_text(encoding="utf-8", errors="replace")
    return ParsedDoc(text=text, title=path.stem, meta={"format": "text"})


def parse_html(path: Path) -> ParsedDoc:
    """trafilatura 抽取正文（兼容微信文章等）。"""
    import trafilatura

    html = path.read_text(encoding="utf-8", errors="replace")
    text = trafilatura.extract(html, include_comments=False, include_tables=True) or ""
    return ParsedDoc(text=text.strip(), title=path.stem, meta={"format": "html"})


def parse_pdf(path: Path) -> ParsedDoc:
    """pypdf 抽取文本层（扫描版 PDF 无法抽取，P2 起考虑 OCR）。"""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts: list[str] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        if t.strip():
            parts.append(f"[第{i}页]\n{t}")
    text = "\n\n".join(parts)
    title = reader.metadata.title if reader.metadata and reader.metadata.title else path.stem
    author = reader.metadata.author if reader.metadata and reader.metadata.author else ""
    return ParsedDoc(text=text, title=title or path.stem, author=author or "",
                     meta={"format": "pdf", "pages": len(reader.pages)})


_PARSERS = {
    ".md": parse_markdown,
    ".markdown": parse_markdown,
    ".txt": parse_text,
    ".html": parse_html,
    ".htm": parse_html,
    ".pdf": parse_pdf,
}


def parse(path: Path) -> ParsedDoc:
    """按扩展名路由解析；未知格式抛 ValueError。"""
    ext = path.suffix.lower()
    if ext not in _PARSERS:
        raise ValueError(f"不支持的格式: {ext}（支持: {', '.join(sorted(_PARSERS))}）")
    return _PARSERS[ext](path)
