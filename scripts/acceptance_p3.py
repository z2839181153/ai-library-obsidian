"""P3 验收脚本：端到端模拟"扔书→确认上架→提问→收藏采购"全鼠标流程。

全程 HTTP（TestClient 模拟浏览器点击），无命令行操作；验证 WS 通知。

用法：.venv\\Scripts\\python.exe scripts\\acceptance_p3.py
（离线验收：FakeEmbed + FakeLLM；不需要 API key）
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.state import build_state
from tests.conftest import FakeEmbed, FakeLLM


def main() -> int:
    t_start = time.time()
    td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    try:
        cfg = AppConfig.load()
        cfg.paths.data_dir = Path(td.name) / "data"
        cfg.paths.vault_dir = Path(td.name) / "vault"
        state = build_state(cfg, embed=FakeEmbed(), llm=FakeLLM())

        from app import __version__
        from app.api import (actions, ask, books, conversations, dashboard, distill,
                             floors, health, index, ingest, purchase, settings, skills, ws)

        app = FastAPI(title="AI Library P3 Acceptance", version=__version__)
        app.state.library = state
        app.include_router(health.router, prefix="/api")
        app.include_router(index.router, prefix="/api")
        app.include_router(books.router, prefix="/api")
        app.include_router(actions.router, prefix="/api")
        app.include_router(floors.router, prefix="/api")
        app.include_router(ask.router, prefix="/api")
        app.include_router(distill.router, prefix="/api")
        app.include_router(skills.router, prefix="/api")
        app.include_router(ingest.router, prefix="/api")
        app.include_router(settings.router, prefix="/api")
        app.include_router(dashboard.router, prefix="/api")
        app.include_router(purchase.router, prefix="/api")
        app.include_router(conversations.router, prefix="/api")
        app.include_router(ws.router)

        with TestClient(app) as c:
            # ---- 0) 健康检查 ----
            r = c.get("/api/health")
            assert r.status_code == 200, r.text
            print("[0] 健康检查 OK")

            # ---- 1) 扔书入馆（鼠标：拖文件到大厅）----
            md = "# 检索增强指南\n\n## 第一章 混合检索\n"
            md += "混合检索融合词法与向量检索，提升召回质量。\n\n"
            md += "## 第二章 知识库\n知识库是 RAG 的地基。\n"
            r = c.post("/api/ingest",
                       files={"file": ("rag-guide.md", md.encode(), "text/markdown")})
            assert r.status_code == 200, r.text
            book = r.json()["book"]
            book_id = book["book_id"]
            assert book["status"] == "incoming"
            print(f"[1] 扔书入馆 OK  书={book_id} 《{book['title']}》 status={book['status']}")

            # ---- 2) WS 通知：补书室新书 ----
            # 注意：TestClient 在 websocket 连接期间主线程不能发 HTTP 请求（会死锁），
            # 因此用后台线程触发入馆广播；receive_timeout 兜底防无限阻塞。
            import threading

            ws_received = []
            with c.websocket_connect("/ws/chat", timeout=10) as ws:
                ws.send_json({"type": "ping"})
                assert ws.receive_json()["type"] == "pong"

                def _do_ingest() -> None:
                    try:
                        c.post("/api/ingest", data={"text": "# 第二本书\n\n内容"})
                    except Exception:  # noqa: BLE001 后台线程异常不影响主流程
                        pass

                t = threading.Thread(target=_do_ingest, daemon=True)
                t.start()
                # receive_json 阻塞直到广播到达（或 10s 超时）；收到 notice 即停止
                while not any(m.get("type") == "notice"
                              and m.get("event") == "book_ingested"
                              for m in ws_received):
                    try:
                        ws_received.append(ws.receive_json())
                    except Exception:  # noqa: BLE001 连接关闭/超时
                        break
            assert any(m.get("type") == "notice" and m.get("event") == "book_ingested"
                       for m in ws_received), f"未收到 WS 通知: {ws_received}"
            print(f"[2] WS 通知 OK  收到 book_ingested: {[m.get('title') for m in ws_received if m.get('event') == 'book_ingested']}")

            # ---- 3) 补书室待定区：生成分类建议 ----
            r = c.post(f"/api/books/{book_id}/classify", json={})
            assert r.status_code == 200, r.text
            suggest = r.json()["suggest"]
            print(f"[3] 分类建议 OK  suggest={suggest}")

            # ---- 4) 确认上架（建议区 → 已上架）----
            r = c.post(f"/api/books/{book_id}/confirm", json={})
            assert r.status_code == 200, r.text
            assert "vault_path" in r.json()
            book = c.get(f"/api/books/{book_id}").json()["book"]
            assert book["status"] == "shelved", book["status"]
            print(f"[4] 确认上架 OK  vault_path={book['vault_path']}")

            # ---- 5) 提问（管理员）→ 对话落库 ----
            r = c.post("/api/ask", json={"query": "什么是混合检索？"})
            assert r.status_code == 200, r.text
            assert r.json()["answer"]
            cv_id = r.json()["cv_id"]
            convs = c.get("/api/conversations").json()["conversations"]
            assert any(x["cv_id"] == cv_id for x in convs)
            print(f"[5] 提问+对话落库 OK  cv_id={cv_id} answer={r.json()['answer'][:30]}…")

            # ---- 6) 采购员：生成今日推荐 ----
            r = c.post("/api/purchase/generate")
            assert r.status_code == 200, r.text
            recs = r.json()["recommendations"]
            assert recs, "应生成推荐"
            rec_id = recs[0]["rec_id"]
            print(f"[6] 生成推荐 OK  {len(recs)} 条，首条《{recs[0]['title']}》score={recs[0]['score']}")

            # ---- 7) 收藏采购 → 入补书室 ----
            r = c.post(f"/api/purchase/{rec_id}/collect")
            assert r.status_code == 200, r.text
            assert r.json()["book"]["status"] == "incoming"
            today = c.get("/api/purchase/today").json()
            rec = next(x for x in today["recommendations"] if x["rec_id"] == rec_id)
            assert rec["status"] == "collected"
            print(f"[7] 收藏采购 OK  《{rec['title']}》→ 补书室")

            # ---- 8) 反馈：忽略另一条 ----
            other = [x for x in recs if x["rec_id"] != rec_id][0]
            r = c.post(f"/api/purchase/{other['rec_id']}/feedback",
                       json={"action": "ignore", "note": "不相关"})
            assert r.status_code == 200, r.text
            print(f"[8] 反馈 OK  《{other['title']}》→ ignore")

            # ---- 9) 设置：改楼层 + 托管程度 ----
            r = c.post("/api/floors", json={"name": "验收测试层"})
            assert r.status_code == 200, r.text
            floor_id = r.json()["floor_id"]
            r = c.put("/api/settings", json={"prefs": {"default_mode": "manual"}})
            assert r.status_code == 200
            assert r.json()["prefs"]["default_mode"] == "manual"
            print(f"[9] 设置 OK  新建楼层={floor_id} 托管程度=manual")

            # ---- 10) 健康度仪表 ----
            r = c.get("/api/dashboard")
            assert r.status_code == 200
            h = r.json()["health"]
            assert h["pending_classify"] >= 2   # 收藏的书 + 第二本书
            assert h["quota"]["total"] >= 2
            print(f"[10] 健康度 OK  pending_classify={h['pending_classify']} "
                  f"quota.total={h['quota']['total']} 执行率={h['quota']['execution_rate']}%")

        print(f"\n[PASS] P3 验收全部通过（{time.time() - t_start:.1f}s）："
              f"扔书→WS通知→分类→上架→提问→生成推荐→收藏→反馈→设置→健康度，全程 HTTP 无命令行")
        return 0
    finally:
        td.cleanup()


if __name__ == "__main__":
    sys.exit(main())
