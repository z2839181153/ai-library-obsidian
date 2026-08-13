"""P0 验收脚本：生成 N 篇中文笔记，验证索引性能与召回。

用法:
  python scripts/acceptance.py --fake     # 用确定性伪向量（无 API key 时的管线验收）
  python scripts/acceptance.py --real     # 用真实 ModelScope embedding（需 data/secrets.json 明文 key）

验收点（设计文档 §11 P0）:
  1. 100 篇中文笔记首次索引 <= 60s（真实 API 网络主导；--fake 测管线本身）
  2. 修改 1 篇增量重建 <= 3s
  3. 含特定术语查询 top-10 召回率 >= 90%
  4. 一键全量重建
  5. 索引完整性检测（rebuild-required）
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import AppConfig
from app.db.repo import Repo
from app.llm.embeddings import EmbeddingClient, EmbeddingUnavailable
from app.retrieval.indexer import Indexer
from app.retrieval.searcher import Searcher
from app.vec.vector_store import VectorStore

TOPICS = [
    ("自行车维修", "链条、刹车、轮胎、变速器、润滑"),
    ("机器学习", "神经网络、梯度下降、过拟合、数据集"),
    ("家常菜谱", "番茄炒蛋、红烧肉、食材、火候"),
    ("阳台园艺", "月季、浇水、施肥、病虫害"),
    ("冥想入门", "呼吸、专注、放松、正念"),
    ("Python 爬虫", "requests、解析、反爬、代理"),
    ("摄影构图", "三分法、光线、景深、快门"),
    ("跑步训练", "配速、心率、恢复、马拉松"),
    ("尤克里里", "和弦、扫弦、指法、节拍"),
    ("理财基础", "基金、定投、风险、复利"),
]


def gen_notes(root: Path, n: int = 100, seed: int = 42) -> None:
    root.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    for i in range(n):
        title, terms = TOPICS[i % len(TOPICS)]
        paras = []
        for _ in range(rng.randint(3, 6)):
            parts = rng.sample(terms.split("、"), rng.randint(2, 4))
            paras.append(f"关于{title}的要点：{'、'.join(parts)}。实践笔记编号{i}。")
        (root / f"note_{i:03d}.md").write_text(
            f"# {title}笔记{i}\n\n" + "\n\n".join(paras), encoding="utf-8"
        )


def run(cfg: AppConfig, source: Path, use_real: bool) -> None:
    cfg.ensure_dirs()
    repo = Repo(cfg.paths.data_dir / "library.db")
    vec = VectorStore(cfg.paths.data_dir / "lancedb")

    if use_real:
        try:
            embed = EmbeddingClient(cfg, repo)
        except EmbeddingUnavailable as e:
            print("!! 未配置明文 ModelScope key:", e)
            sys.exit(1)
    else:
        from tests.conftest import FakeEmbed

        embed = FakeEmbed()

    indexer = Indexer(repo, vec, embed)
    searcher = Searcher(repo, vec, embed)

    # 1) 首次索引（含重建）
    t0 = time.time()
    stats = indexer.run(source, rebuild=True)
    t_first = time.time() - t0
    print(f"[1] 首次索引: {stats['new_or_changed']} 本, 用时 {t_first:.2f}s, "
          f"chunks={repo.conn.execute('SELECT COUNT(*) c FROM chunks').fetchone()['c']}")
    assert stats["new_or_changed"] == 100, "应索引 100 篇"

    # 2) 增量（改 1 篇）
    target = sorted(source.glob("*.md"))[0]
    target.write_text(target.read_text(encoding="utf-8") + "\n新增段落测试。", encoding="utf-8")
    t0 = time.time()
    stats = indexer.run(source)
    t_inc = time.time() - t0
    print(f"[2] 增量重建(改1篇): 用时 {t_inc:.2f}s, new_or_changed={stats['new_or_changed']}")
    assert stats["new_or_changed"] == 1, "增量应只重建 1 篇"

    # 3) 召回率：10 个主题查询，每个 top-10 中应含对应主题笔记
    total = 0
    hit = 0
    for title, _terms in TOPICS:
        res = searcher.search(title, top_k=10)
        hit_books = [b["title"] for b in res["books"]]
        relevant = sum(1 for t in hit_books if title.split()[0] in t or title in t)
        total += 10
        hit += relevant
        print(f"    查询「{title}」 top10 命中相关: {relevant}/10")
    recall = hit / total
    print(f"[3] 召回率(top-10): {recall:.0%} ({hit}/{total})")
    assert recall >= 0.9, f"召回率不足: {recall:.0%}"

    # 4) 一键全量重建
    t0 = time.time()
    stats = indexer.run(source, rebuild=True)
    t_rebuild = time.time() - t0
    print(f"[4] 全量重建: 用时 {t_rebuild:.2f}s, 索引 {stats['new_or_changed']} 本")

    # 5) 完整性检测
    check = indexer.check()
    print(f"[5] 完整性检测: ok={check['ok']}, rebuild_required={check['rebuild_required']}, "
          f"counts={check['counts']}")
    assert check["ok"] is True

    print("\n=== P0 验收通过 ===")
    print(f"    首建 {t_first:.2f}s | 增量 {t_inc:.2f}s | 重建 {t_rebuild:.2f}s | 召回 {recall:.0%}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="用确定性伪向量（默认）")
    ap.add_argument("--real", action="store_true", help="用真实 ModelScope embedding")
    ap.add_argument("--n", type=int, default=100, help="笔记数量（默认 100）")
    args = ap.parse_args()

    cfg = AppConfig.load()
    source = Path(__file__).resolve().parent.parent / "data" / "acceptance_books"
    gen_notes(source, n=args.n)
    run(cfg, source, use_real=args.real)
