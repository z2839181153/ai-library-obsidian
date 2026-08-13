"""LanceDB 向量存储封装（设计文档 §7.1）。

- 表名：chunks；主键：chunk_id。
- 按 book_id 删除/覆盖（copy-on-write 的简化：增量只动变更书）。
- 维度在首写时固定（Qwen3-Embedding-0.6B = 1024）。
"""
from __future__ import annotations

from pathlib import Path

import lancedb
import pyarrow as pa


class VectorStore:
    def __init__(self, db_dir: Path, table_name: str = "chunks"):
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.table_name = table_name
        self._db = lancedb.connect(str(self.db_dir))
        self._table = None
        self._dim: int | None = None

    @property
    def table(self):
        if self._table is None:
            if self.table_name in self._db.table_names():
                self._table = self._db.open_table(self.table_name)
            else:
                raise FileNotFoundError(
                    f"向量表 {self.table_name} 不存在（请先运行索引）"
                )
        return self._table

    def exists(self) -> bool:
        return self.table_name in self._db.table_names()

    def create_table(self, dim: int) -> None:
        self._dim = dim
        schema = pa.schema(
            [
                pa.field("chunk_id", pa.string()),
                pa.field("book_id", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), dim)),
            ]
        )
        self._table = self._db.create_table(
            self.table_name, schema=schema, mode="overwrite"
        )

    def upsert(self, rows: list[dict]) -> None:
        """rows: [{chunk_id, book_id, vector}]。按 chunk_id 去重写。"""
        if not rows:
            return
        dim = len(rows[0]["vector"])
        if not self.exists():
            self.create_table(dim)
        elif self._dim is None:
            self._dim = dim
        data = pa.Table.from_pylist(rows)
        self.table.merge_insert("chunk_id").when_matched_update_all().when_not_matched_insert_all().execute(data)

    def delete_by_book(self, book_id: str) -> None:
        if self.exists():
            self.table.delete(f"book_id = '{book_id}'")

    def search(self, vector: list[float], top_k: int = 20) -> list[dict]:
        """返回 [{chunk_id, book_id, _distance}]。"""
        try:
            rs = self.table.search(vector).limit(top_k).to_list()
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"向量检索失败: {e}") from e
        return [
            {"chunk_id": r["chunk_id"], "book_id": r["book_id"], "_distance": float(r["_distance"])}
            for r in rs
        ]
