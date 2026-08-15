"""图书卡片生成 + 分类建议（设计文档 §5.5 / §6.2 / §6.3）。

流程：
1. 取书 + chunks（正文抽样 = 前 20% + 全部章节标题）
2. LLM 生成 JSON：书名/一句话简介/摘要/章节/关键概念/标签/蒸馏价值 + 房间/书架建议
3. 楼层 = 来源媒介固定映射（不经过 LLM）；房间/书架 = LLM 建议（优先匹配已有）
4. 落盘 catalog/bk_<id>.md（frontmatter status: reviewing）
5. 写 catalog_cards 表 + books.card_path + action ledger（classify）

主人主权：这里只产建议（写 suggest_*），不移动书、不改变 status 之外的状态。
私密书（private=1）→ 不调 LLM，卡片标记"模型不可用（私密）"。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import AppConfig
from app.db.repo import Repo
from app.db.schema import floor_for_media_type
from app.llm.chat import ChatClient, LLMUnavailable


@dataclass
class CardResult:
    book_id: str
    card_path: Path | None = None
    card: dict | None = None     # catalog_cards 表数据
    suggest: dict = field(default_factory=dict)   # {floor, room, shelf}
    skipped: bool = False        # 幂等跳过
    private_skip: bool = False   # 私密跳过（无卡片数据）
    error: str | None = None


_CARD_SYSTEM = (
    "你是图书馆管理员。根据提供的书内容，输出图书卡片 JSON。"
    "只输出 JSON，不要任何解释文字。"
)


class CardGenerator:
    def __init__(self, repo: Repo, llm: ChatClient, cfg: AppConfig):
        self.repo = repo
        self.llm = llm
        self.cfg = cfg
        self._last_llm_error: str | None = None
        self.last_prompt: str | None = None

    # ---------- 主入口 ----------

    def generate(self, book_id: str, force: bool = False) -> CardResult:
        book = self.repo.get_book(book_id)
        if not book:
            raise ValueError(f"书不存在: {book_id}")

        # 幂等：卡片已存在且未强制 → 跳过
        existing = self.repo.get_card(book_id)
        if existing and not force:
            return CardResult(
                book_id=book_id,
                card_path=Path(book["card_path"]) if book.get("card_path") else None,
                card=existing,
                skipped=True,
            )

        # 私密书：不调 LLM，写降级卡片
        if book.get("private"):
            return self._generate_private(book)

        chunks = self._load_chunks(book_id)
        if not chunks:
            return CardResult(book_id=book_id, error="书无可用 chunk（未索引或为空）")

        data = self._call_llm(book, chunks)
        if data is None:
            return CardResult(book_id=book_id,
                              error=self._last_llm_error or "LLM 不可用（未配置 API key）")

        # 楼层固定映射 + 房间/书架建议
        suggest_floor = self._assign_floor(book.get("media_type", ""))
        suggest_room = str(data.get("room") or "").strip() or None
        suggest_shelf = str(data.get("shelf") or "").strip() or None

        card = self._build_card(book, data)
        card_path = self._write_card_md(book, card, suggest_floor, suggest_room, suggest_shelf)

        self.repo.upsert_card(card)
        self.repo.conn.execute(
            "UPDATE books SET card_path=?, suggest_floor=?, suggest_room=?, suggest_shelf=?, "
            "status='reviewing', updated_at=? WHERE book_id=?",
            (str(card_path), suggest_floor or "", suggest_room or "", suggest_shelf or "",
             time.strftime("%Y-%m-%dT%H:%M:%S+08:00"), book_id),
        )
        self.repo.commit()

        # action ledger：classify（可撤销=清空分类建议）
        self.repo.insert_action({
            "agent": "admin",
            "action_type": "classify",
            "target_type": "book",
            "target_id": book_id,
            "params": {"suggest_floor": suggest_floor, "suggest_room": suggest_room,
                       "suggest_shelf": suggest_shelf},
            "undo_params": {"suggest_floor": None, "suggest_room": None, "suggest_shelf": None},
            "status": "done",
            "reason": f"为《{book.get('title', book_id)}》生成图书卡片与分类建议",
        })

        return CardResult(
            book_id=book_id,
            card_path=card_path,
            card=card,
            suggest={"floor": suggest_floor, "room": suggest_room, "shelf": suggest_shelf},
        )

    # ---------- 内部 ----------

    def _load_chunks(self, book_id: str) -> list[dict]:
        rows = self.repo.conn.execute(
            "SELECT section, content, seq FROM chunks WHERE book_id=? ORDER BY seq",
            (book_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def _assign_floor(self, media_type: str) -> str | None:
        """楼层 = 来源媒介固定映射（设计文档 §6.2）。返回楼层 code 或 None。"""
        return floor_for_media_type(media_type)

    def _call_llm(self, book: dict, chunks: list[dict]) -> dict | None:
        prompt = self._build_prompt(book, chunks)
        self.last_prompt = prompt
        try:
            return self.llm.chat_json(prompt, system=_CARD_SYSTEM, max_tokens=8192)
        except LLMUnavailable as e:
            self._last_llm_error = f"LLM 不可用: {e} | prompt_len={len(prompt)} | head={prompt[:120]!r}"
            return None
        except Exception as e:  # noqa: BLE001  输出解析失败等
            self._last_llm_error = f"LLM 输出解析失败: {e} | prompt_len={len(prompt)}"
            return None

    def _build_prompt(self, book: dict, chunks: list[dict]) -> str:
        # 章节标题（section 去重，保留顺序）
        chapter_titles: list[str] = []
        for c in chunks:
            sec = (c.get("section") or "").strip()
            if sec and sec not in chapter_titles:
                chapter_titles.append(sec)

        # 正文抽样：前 20% 内容
        total_len = sum(len(c["content"]) for c in chunks)
        sample_limit = max(total_len // 5, 2000)
        sampled: list[str] = []
        used = 0
        for c in chunks:
            if used >= sample_limit:
                break
            piece = c["content"][: sample_limit - used]
            sampled.append(piece)
            used += len(piece)

        # 已有房间/书架（供 LLM 优先匹配）
        rooms = [r["name"] for r in self.repo.list_rooms()]
        shelves = [s["name"] for s in self.repo.list_shelves()]

        return f"""根据下面这本书的内容，生成图书卡片 JSON。

字段要求（只输出 JSON）：
- title: 书名（2-20 字）
- one_liner: 一句话简介（30 字内）
- summary: 200 字左右摘要
- chapters: [{{"title": 章节标题, "summary": 一句摘要, "ref": 章节序号}}]（3-8 项）
- concepts: [{{"term": 术语, "definition": 定义, "ref": 章节序号}}]（3-6 项）
- tags: 标签数组（3-6 个）
- distill_value: 0-100 蒸馏价值分（方法论/框架类高分）
- category: "methodology" 或 "reference" 或 "narrative" 或 "data"
- distill_reason: 蒸馏价值理由（40 字内）
- room: 建议房间名（语义主题）。优先从已有房间中选择；没有合适的可建议新房间名
- shelf: 建议书架名（可留空字符串）

已有房间列表：{json.dumps(rooms, ensure_ascii=False)}
已有书架列表：{json.dumps(shelves, ensure_ascii=False)}

书名：《{book.get('title', '')}》 来源类型：{book.get('media_type', '')}

章节标题：
{chr(10).join(f"{i+1}. {t}" for i, t in enumerate(chapter_titles[:30]))}

正文抽样：
{chr(10).join(sampled[:12])}"""

    def _build_card(self, book: dict, data: dict) -> dict:
        def _clean_list(key: str) -> list:
            v = data.get(key) or []
            return v if isinstance(v, list) else []

        chapters = _clean_list("chapters")
        concepts = _clean_list("concepts")
        tags = _clean_list("tags")
        return {
            "book_id": book["book_id"],
            "summary": str(data.get("summary") or "").strip(),
            "chapters": json.dumps(chapters, ensure_ascii=False),
            "concepts": json.dumps(concepts, ensure_ascii=False),
            "distill_value": int(data.get("distill_value") or 0),
            "distill_reason": str(data.get("distill_reason") or "").strip(),
            "category": str(data.get("category") or "").strip(),
            "tags": json.dumps(tags, ensure_ascii=False),
            "skills": "[]",
            "model": self.cfg.modelscope.chat_model,
        }

    def _write_card_md(self, book: dict, card: dict, suggest_floor: str | None,
                       suggest_room: str | None, suggest_shelf: str | None) -> Path:
        """落盘 catalog/bk_<id>.md（frontmatter + 正文）。"""
        catalog_dir = self.cfg.paths.vault_dir / "catalog"
        catalog_dir.mkdir(parents=True, exist_ok=True)
        card_path = catalog_dir / f"{book['book_id']}.md"

        chapters = json.loads(card["chapters"] or "[]")
        concepts = json.loads(card["concepts"] or "[]")
        tags = json.loads(card["tags"] or "[]")
        now = time.strftime("%Y-%m-%d")

        frontmatter = [
            "---",
            "type: book-card",
            f"book_id: {book['book_id']}",
            f"title: 《{book.get('title', '')}》",
            f"author: {book.get('author') or ''}",
            f"media_type: {book.get('media_type', '')}",
            f"floor: {suggest_floor or '待定'}（建议）",
            f"room: {suggest_room or '待定'}（建议）",
            f"shelf: {suggest_shelf or '待定'}（建议）",
            "status: reviewing",
            f"private: {str(bool(book.get('private'))).lower()}",
            f"distill_value: {card.get('distill_value') or 0}",
            f"ingested: {now}",
            "---",
            "",
            f"# 《{book.get('title', '')}》图书卡片",
            "",
            "## 一句话简介",
            "",
            (card.get("summary") or "（无）").split("。")[0] + "。",
            "",
            "## 摘要",
            "",
            card.get("summary") or "（无）",
            "",
            "## 章节结构",
            "",
        ]
        for i, ch in enumerate(chapters, 1):
            frontmatter.append(
                f"{i}. **{ch.get('title', '')}**：{ch.get('summary', '')}（ref: {ch.get('ref', '')}）"
            )
        frontmatter.append("")
        frontmatter.append("## 关键概念")
        frontmatter.append("")
        for c in concepts:
            frontmatter.append(f"- **{c.get('term', '')}**：{c.get('definition', '')}（ref: {c.get('ref', '')}）")
        frontmatter.append("")
        frontmatter.append("## 蒸馏价值评估")
        frontmatter.append("")
        frontmatter.append(f"- 分数：{card.get('distill_value') or 0}/100")
        frontmatter.append(f"- 类别：{card.get('category') or 'unknown'}")
        frontmatter.append(f"- 理由：{card.get('distill_reason') or '（无）'}")
        frontmatter.append("")
        frontmatter.append("## 标签")
        frontmatter.append("")
        frontmatter.append("、".join(tags) if tags else "（无）")
        frontmatter.append("")

        card_path.write_text("\n".join(frontmatter), encoding="utf-8")
        return card_path

    def _generate_private(self, book: dict) -> CardResult:
        """私密书：不调 LLM，写降级卡片。"""
        catalog_dir = self.cfg.paths.vault_dir / "catalog"
        catalog_dir.mkdir(parents=True, exist_ok=True)
        card_path = catalog_dir / f"{book['book_id']}.md"
        now = time.strftime("%Y-%m-%d")
        card_path.write_text(
            "---\n"
            "type: book-card\n"
            f"book_id: {book['book_id']}\n"
            f"title: 《{book.get('title', '')}》\n"
            "status: reviewing\n"
            "private: true\n"
            "---\n\n"
            "# 《" + str(book.get("title", "")) + "》图书卡片\n\n"
            "## 摘要\n\n模型不可用（私密内容，永不发送 API）。仅本地索引与检索。\n",
            encoding="utf-8",
        )
        card = {
            "book_id": book["book_id"],
            "summary": "（私密内容，模型不可用）",
            "chapters": "[]",
            "concepts": "[]",
            "distill_value": 0,
            "distill_reason": "私密内容不评估",
            "category": "",
            "tags": "[]",
            "skills": "[]",
            "model": "",
        }
        self.repo.upsert_card(card)
        self.repo.conn.execute(
            "UPDATE books SET card_path=?, status='reviewing', updated_at=? WHERE book_id=?",
            (str(card_path), time.strftime("%Y-%m-%dT%H:%M:%S+08:00"), book["book_id"]),
        )
        self.repo.commit()
        self.repo.insert_action({
            "agent": "admin",
            "action_type": "classify",
            "target_type": "book",
            "target_id": book["book_id"],
            "params": {"private": True},
            "undo_params": {},
            "status": "done",
            "reason": f"《{book.get('title', book['book_id'])}》为私密书，跳过 LLM 卡片生成",
        })
        return CardResult(
            book_id=book["book_id"],
            card_path=card_path,
            card=card,
            private_skip=True,
        )
