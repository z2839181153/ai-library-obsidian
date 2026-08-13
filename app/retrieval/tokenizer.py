"""FTS5 中文分词：jieba 预分词（设计文档 §7.3）。

MVP 方案：chunk 写入 FTS5 前用 jieba 分词（空格连接）；查询同样预分词。
"""
from __future__ import annotations

import jieba

# 全局初始化一次（jieba 首次加载字典较慢）
_loaded = False


def ensure_loaded() -> None:
    global _loaded
    if not _loaded:
        jieba.initialize()
        _loaded = True


def tokenize(text: str) -> str:
    """jieba 分词，空格连接，供 FTS5 MATCH。"""
    ensure_loaded()
    return " ".join(jieba.cut(text))


def query_to_match(query: str) -> str:
    """把用户查询转成 FTS5 MATCH 表达式（词组精确匹配）。"""
    ensure_loaded()
    return " ".join(f'"{t}"' for t in jieba.cut(query.strip()) if t.strip())
