"""入馆清洗（设计文档 §6.1）：解析 → 规范化 → 不可变副本。

P0 能力：单文件入馆（md/txt/html/pdf）。
- 规范化：去多余空行、统一换行、去行尾空格。
- 不可变副本：sha256 内容寻址，复制原始文件到 archive/raw/<hash>。
"""
from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.ingest.parsers import ParsedDoc, parse


@dataclass
class IngestedBook:
    book_id: str
    title: str
    author: str
    media_type: str
    content_hash: str
    raw_path: Path
    clean_text: str
    meta: dict


def normalize(text: str) -> str:
    """规范化：统一换行、去行尾空格、压缩多余空行。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def ingest_file(path: Path, archive_dir: Path) -> IngestedBook:
    """入馆一个文件：解析 + 规范化 + 复制不可变副本，返回入库数据。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")

    parsed: ParsedDoc = parse(path)
    clean = normalize(parsed.text)
    if not clean:
        raise ValueError(f"解析后无文本内容: {path}")

    content_hash = sha256_file(path)
    raw_path = archive_dir / content_hash[:2] / content_hash
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    if not raw_path.exists():
        shutil.copy2(path, raw_path)

    # 简单 slug
    import re as _re

    slug = _re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", parsed.title.lower()).strip("-")[:60] or "untitled"

    return IngestedBook(
        book_id=f"bk_{content_hash[:12]}",
        title=parsed.title or path.stem,
        author=parsed.author,
        media_type=parsed.meta.get("format", "other"),
        content_hash=content_hash,
        raw_path=raw_path,
        clean_text=clean,
        meta=parsed.meta,
    )
