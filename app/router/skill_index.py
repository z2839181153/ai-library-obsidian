"""技能库向量索引（设计文档 §6.5 ③ 技能路由的索引侧）。

独立 LanceDB 表 `skills`，doc 为技能 trigger 描述（description），
路由时用 query embedding 检索命中技能，把 SKILL.md 注入问答 system prompt。

相似度用**余弦**（embedding 未归一化时 L2 距离语义失真）；技能量小，
search 全表扫描 numpy 计算，返回 _distance = cosine（越大越近）。

表结构：
- skill_id  TEXT PRIMARY KEY
- status    TEXT        -- approved/installed 才参与路由
- doc       TEXT        -- description + 书名 + SKILL.md 摘要（检索用文本）
- vector    float32[]
"""
from __future__ import annotations

from pathlib import Path

import lancedb
import numpy as np
import pyarrow as pa


class SkillIndex:
    def __init__(self, db_dir: Path, table_name: str = "skills"):
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.table_name = table_name
        self._db = lancedb.connect(str(self.db_dir))
        self._table = None
        self._dim: int | None = None

    @property
    def table(self):
        if self._table is None:
            if self.exists():
                self._table = self._db.open_table(self.table_name)
            else:
                raise FileNotFoundError(
                    f"技能索引表 {self.table_name} 不存在（尚未注册任何已批准技能）"
                )
        return self._table

    def exists(self) -> bool:
        try:
            tables = self._db.list_tables()
        except AttributeError:  # 旧版
            tables = self._db.table_names()
        if isinstance(tables, (list, tuple)):
            return self.table_name in tables
        # lancedb 0.37: list_tables() 返回分页对象（.tables 属性）
        return self.table_name in list(getattr(tables, "tables", []))

    def _ensure_table(self, dim: int) -> None:
        if not self.exists():
            schema = pa.schema(
                [
                    pa.field("skill_id", pa.string()),
                    pa.field("status", pa.string()),
                    pa.field("doc", pa.string()),
                    pa.field("vector", pa.list_(pa.float32(), dim)),
                ]
            )
            self._dim = dim
            self._table = self._db.create_table(
                self.table_name, schema=schema, mode="overwrite"
            )
        elif self._dim is None:
            self._dim = dim

    def upsert(self, skill_id: str, status: str, doc: str, vector: list[float]) -> None:
        """注册/更新一个技能到索引。"""
        self._ensure_table(len(vector))
        row = {
            "skill_id": skill_id,
            "status": status,
            "doc": doc,
            "vector": vector,
        }
        data = pa.Table.from_pylist([row])
        self.table.merge_insert("skill_id").when_matched_update_all().when_not_matched_insert_all().execute(data)

    def remove(self, skill_id: str) -> None:
        """从索引移除（拒绝/阻塞的技能不再参与路由）。"""
        if self.exists():
            self.table.delete(f"skill_id = '{skill_id}'")

    def set_status(self, skill_id: str, status: str) -> None:
        if self.exists():
            try:
                self.table.update(where=f"skill_id = '{skill_id}'", values={"status": status})
            except Exception:  # noqa: BLE001  -- 不存在该行时忽略
                pass

    def search(self, vector: list[float], top_k: int = 5,
               statuses: tuple[str, ...] = ("approved", "installed")) -> list[dict]:
        """余弦检索技能，返回 [{skill_id, doc, status, _distance(cosine)}]，cos 越大越近。"""
        if not self.exists():
            return []
        try:
            tbl = self.table.to_arrow()
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"技能检索失败: {e}") from e
        if tbl.num_rows == 0:
            return []
        skill_ids = tbl.column("skill_id").to_pylist()
        status_col = tbl.column("status").to_pylist()
        docs = tbl.column("doc").to_pylist()
        vectors = tbl.column("vector").to_pylist()

        q = np.asarray(vector, dtype=np.float32)
        qn = float(np.linalg.norm(q)) or 1e-9
        out = []
        for sid, st, doc, vec in zip(skill_ids, status_col, docs, vectors):
            if st not in statuses:
                continue
            v = np.asarray(vec, dtype=np.float32)
            vn = float(np.linalg.norm(v)) or 1e-9
            cos = float(np.dot(q, v) / (qn * vn))
            out.append({
                "skill_id": sid,
                "doc": doc,
                "status": st,
                "_distance": cos,
            })
        out.sort(key=lambda x: x["_distance"], reverse=True)
        return out[:top_k]
