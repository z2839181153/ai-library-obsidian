"""确认上架 + 撤销（设计文档 §6.2 / §6.9）。

主人确认后：
- 楼层/房间/书架不存在 → 自动创建（目录 + .json + README.md）
- 创建 books/<楼层>/<房间>/<书架>/<书名>/book.md（正文来自 chunks 拼接）
- books 状态 → shelved，vault_path 落位，action ledger 记录 undo_params
撤销（undo shelve）：
- vault 副本移到 data/tmp/unshelved/（不删除，可恢复）
- 状态回退 reviewing（补书室），vault_path 清空
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from app.config import AppConfig
from app.db.repo import Repo


def safe_dir_name(name: str) -> str:
    """目录名清理：去除 Windows/文件系统非法字符，保留中文。"""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name.strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name or "未命名"


class Shelver:
    def __init__(self, repo: Repo, cfg: AppConfig):
        self.repo = repo
        self.cfg = cfg
        self.vault = cfg.paths.vault_dir

    # ---------- 主操作 ----------

    def confirm_shelve(self, book_id: str, floor: str | None = None,
                       room: str | None = None, shelf: str | None = None,
                       confirm_by: str = "owner") -> dict:
        book = self.repo.get_book(book_id)
        if not book:
            raise ValueError(f"书不存在: {book_id}")
        if book.get("vault_path"):
            raise ValueError(f"书已上架: {book.get('vault_path')}")

        floor_code = floor or book.get("suggest_floor") or ""
        room_name = (room or book.get("suggest_room") or "").strip()
        shelf_name = (shelf or book.get("suggest_shelf") or "").strip()
        if not floor_code:
            raise ValueError("缺少楼层（floor）")
        if not room_name:
            raise ValueError("缺少房间（room）")

        # 楼层匹配：code → floor_id → name（UI 自由输入名称也能命中）
        floor_row = (
            self.repo.floor_by_code(floor_code)
            or self.repo.get_floor(floor_code)
            or self.repo.floor_by_name(floor_code)
        )
        if not floor_row:
            raise ValueError(f"楼层不存在: {floor_code}")

        # 房间/书架：DB 登记（不存在则创建）
        room_id = self.repo.insert_room({"floor_id": floor_row["floor_id"], "name": room_name})
        shelf_id = None
        if shelf_name:
            shelf_id = self.repo.insert_shelf({"room_id": room_id, "name": shelf_name})

        # 构建目录：books/<code>-<name>/<room>/<shelf>/<书名>/
        floor_dir = self.vault / "books" / f"{floor_row['code']}-{safe_dir_name(floor_row['name'])}"
        room_dir = floor_dir / safe_dir_name(room_name)
        shelf_dir = room_dir / safe_dir_name(shelf_name) if shelf_name else room_dir
        book_dir = shelf_dir / safe_dir_name(book.get("title") or book_id)
        book_dir.mkdir(parents=True, exist_ok=True)

        self._write_node_files(floor_dir, floor_row, "floor")
        self._write_node_files(room_dir, self.repo.get_room(room_id), "room")
        if shelf_id:
            self._write_node_files(shelf_dir, self.repo.get_shelf(shelf_id), "shelf")

        # 正文：chunks 按 seq 拼接 → book.md
        body = self._book_body(book_id)
        (book_dir / "book.md").write_text(body, encoding="utf-8")

        vault_rel = str(book_dir.relative_to(self.vault)).replace("\\", "/")
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        self.repo.conn.execute(
            "UPDATE books SET vault_path=?, status='shelved', confirm_by=?, updated_at=? WHERE book_id=?",
            (vault_rel, confirm_by, now, book_id),
        )
        self.repo.commit()

        act_id = self.repo.insert_action({
            "agent": "owner",
            "action_type": "shelve",
            "target_type": "book",
            "target_id": book_id,
            "params": {"floor": floor_row["code"], "room": room_name, "shelf": shelf_name,
                       "vault_path": vault_rel},
            "undo_params": {"vault_path": vault_rel, "prev_status": book.get("status") or "reviewing"},
            "status": "done",
            "reason": f"主人确认上架《{book.get('title', book_id)}》到 {floor_row['code']}/{room_name}/{shelf_name or '-'}",
        })
        return {
            "book_id": book_id,
            "status": "shelved",
            "vault_path": vault_rel,
            "act_id": act_id,
            "floor": floor_row["code"],
            "room": room_name,
            "shelf": shelf_name,
        }

    # ---------- 撤销 ----------

    def undo_shelve(self, act: dict) -> dict:
        """撤销上架：vault 副本移到 data/tmp/unshelved/，状态回退补书室。"""
        book_id = act.get("target_id")
        book = self.repo.get_book(book_id)
        if not book:
            raise ValueError(f"书不存在: {book_id}")
        if book.get("status") != "shelved":
            raise ValueError(f"书当前状态 {book.get('status')}，无法撤销上架")

        undo = act.get("undo_params") or {}
        vault_rel = undo.get("vault_path") or book.get("vault_path")
        if vault_rel:
            src = self.vault / vault_rel
            dst_root = self.cfg.paths.data_dir / "tmp" / "unshelved"
            dst = dst_root / Path(vault_rel).name
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.exists():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.move(str(src), str(dst))

        prev_status = undo.get("prev_status") or "reviewing"
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        self.repo.conn.execute(
            "UPDATE books SET vault_path='', status=?, updated_at=? WHERE book_id=?",
            (prev_status, now, book_id),
        )
        self.repo.commit()
        self.repo.set_action_status(act["act_id"], "undone")
        return {"book_id": book_id, "status": prev_status, "undone": True}

    def undo_classify(self, act: dict) -> dict:
        """撤销分类建议：清空 suggest_*（不删卡片文件，卡片留作参考）。"""
        book_id = act.get("target_id")
        if not self.repo.get_book(book_id):
            raise ValueError(f"书不存在: {book_id}")
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        self.repo.conn.execute(
            "UPDATE books SET suggest_floor='', suggest_room='', suggest_shelf='', updated_at=? "
            "WHERE book_id=?",
            (now, book_id),
        )
        self.repo.commit()
        self.repo.set_action_status(act["act_id"], "undone")
        return {"book_id": book_id, "undone": True}

    # ---------- 内部 ----------

    def _book_body(self, book_id: str) -> str:
        rows = self.repo.conn.execute(
            "SELECT section, content FROM chunks WHERE book_id=? ORDER BY seq", (book_id,)
        ).fetchall()
        parts: list[str] = []
        last_sec: str | None = None
        for r in rows:
            sec = r["section"] or ""
            if sec and sec != last_sec:
                parts.append(f"## {sec}")
                last_sec = sec
            parts.append(r["content"])
        text = "\n\n".join(parts)
        book = self.repo.get_book(book_id) or {}
        header = (
            "---\n"
            f"title: {book.get('title', '')}\n"
            f"book_id: {book_id}\n"
            f"media_type: {book.get('media_type', '')}\n"
            "---\n\n"
        )
        return header + text

    def _write_node_files(self, node_dir: Path, row: dict | None, kind: str) -> None:
        if row is None:
            return
        node_dir.mkdir(parents=True, exist_ok=True)
        json_name = {  # noqa: F841
            "floor": ".floor.json", "room": ".room.json", "shelf": ".shelf.json",
        }[kind]
        data = {k: row.get(k) for k in
                (("floor_id", "name", "code", "media_type", "description", "ord")
                 if kind == "floor" else
                 (("room_id", "floor_id", "name", "description") if kind == "room" else
                  ("shelf_id", "room_id", "name", "description")))}
        (node_dir / json_name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        readme = node_dir / "README.md"
        if not readme.exists():
            readme.write_text(
                f"# {row.get('name', kind)}\n\n{row.get('description') or ''}\n", encoding="utf-8"
            )
