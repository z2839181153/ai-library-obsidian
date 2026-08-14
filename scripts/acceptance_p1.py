"""P1 验收脚本：端到端走一遍核心流程（不依赖真实 API key）。

流程：入馆 → 索引 → 编目(分类建议) → 补书室 → 确认上架 → 原文阅读 → 问答 → 撤销上架。
用法：.venv\\Scripts\\python.exe scripts\\acceptance_p1.py [--real]
  --real 时使用真实 embedding/LLM（需配置 API key）；默认用 Fake（离线验收）。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import AppConfig
from app.state import build_state


def main() -> int:
    use_real = "--real" in sys.argv
    td = tempfile.TemporaryDirectory()
    state = None
    try:
        root = Path(td.name)
        cfg = AppConfig()
        cfg.paths.data_dir = root / "data"
        cfg.paths.vault_dir = root / "vault"

        # 1) 准备一本书
        src = root / "inbox"
        src.mkdir(parents=True)
        (src / "机器学习笔记.md").write_text(
            "# 机器学习笔记\n\n"
            "## 第一章 神经网络\n"
            "神经网络通过多层非线性变换学习特征表示。梯度下降是最常用的优化算法。\n\n"
            "## 第二章 检索增强\n"
            "检索增强生成（RAG）把外部知识库接入大模型，减少幻觉。\n",
            encoding="utf-8",
        )

        # 2) 构建状态（离线用 Fake；--real 用真实 API）
        if use_real:
            state = build_state(cfg)
        else:
            from tests.conftest import FakeEmbed, FakeLLM

            state = build_state(cfg, embed=FakeEmbed(), llm=FakeLLM())

        # 3) 入馆 + 索引
        stats = state.indexer.run(root=src)
        assert stats.get("new_or_changed", 0) >= 1, f"索引失败: {stats}"
        books = state.repo.all_books()
        book_id = books[0]["book_id"]
        print(f"[1] 入馆+索引 OK  书={book_id} title={books[0]['title']} status={books[0]['status']}")

        # 4) 编目：卡片 + 分类建议
        result = state.cards.generate(book_id)
        assert result.card_path and result.card_path.exists(), f"卡片生成失败: {result.error}"
        print(f"[2] 编目 OK  suggest={result.suggest} distill={state.repo.get_card(book_id)['distill_value']}")
        print(f"    卡片文件: {result.card_path.name}")

        # 5) 补书室
        book = state.repo.get_book(book_id)
        assert book["status"] == "reviewing", f"应回补书室 reviewing，实际 {book['status']}"
        print(f"[3] 补书室 OK  status={book['status']}")

        # 6) 确认上架（用建议）
        shelved = state.shelver.confirm_shelve(book_id)
        print(f"[4] 上架 OK  vault_path={shelved['vault_path']}")
        vp = cfg.paths.vault_dir / shelved["vault_path"]
        assert (vp / "book.md").exists()
        print(f"    目录文件: {sorted(p.name for p in vp.parent.parent.rglob('*.md'))}")

        # 7) 原文阅读
        chunks = [r["content"] for r in state.repo.conn.execute(
            "SELECT content FROM chunks WHERE book_id=? ORDER BY seq", (book_id,)
        )]
        assert chunks, "无原文 chunks"
        print(f"[5] 原文 OK  {len(chunks)} 个 chunk，首片段: {chunks[0][:24]}…")

        # 8) 问答
        qa = state.qa.ask("神经网络")
        print(f"[6] 问答 OK  answer={qa['answer'][:40]}… refs={len(qa['refs'])}")

        # 9) 撤销上架
        act = state.repo.list_actions(target_type="book", target_id=book_id)[0]
        undo = state.shelver.undo_shelve(act)
        assert undo["status"] == "reviewing"
        print(f"[7] 撤销 OK  status={undo['status']} 动作已回滚")

        print("\n[PASS] P1 验收通过（全部 7 步）")
        return 0
    finally:
        if state is not None:
            state.repo.close()
        td.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
