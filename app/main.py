"""FastAPI 应用入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8800
或直接运行 `python -m app.main`（开发便捷）。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import (actions, ask, books, conversations, dashboard, distill,
                     floors, health, index, ingest, purchase, settings, skills, starmap, ws)
from app.config import AppConfig
from app.state import build_state

config = AppConfig.load()

# P3：前端构建产物目录（web/dist）。存在才挂载静态托管。
from app.config import ROOT_DIR

WEB_DIST = ROOT_DIR / "web" / "dist"
FRONTEND_BUILT = WEB_DIST.exists() and (WEB_DIST / "index.html").exists()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    app.state.library = build_state(config)
    yield


app = FastAPI(
    title="AI Library",
    description="AI 图书馆：AI + Obsidian 个人知识库后端",
    version=__version__,
    lifespan=lifespan,
)

# 开发期 CORS（P3 Web UI 与后端同源，此配置主要供调试）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8800", "http://localhost:8800"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
app.include_router(starmap.router, prefix="/api")
app.include_router(ws.router)


# P3：静态托管 + SPA fallback（web/dist 存在时）
if FRONTEND_BUILT:
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        """未匹配 API 的路由 → index.html（SPA 刷新不 404）。"""
        # 静态资源直接返回文件；其余交给前端路由
        if full_path and (WEB_DIST / full_path).is_file():
            return FileResponse(WEB_DIST / full_path)
        return FileResponse(WEB_DIST / "index.html")
else:
    @app.get("/", include_in_schema=False)
    def frontend_not_built():
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "message": "AI Library 后端运行中。前端未构建：请进入 web/ 目录执行 "
                           "`npm install && npm run build`，或使用开发服务器 `npm run dev`（5173）。",
                "api": "http://127.0.0.1:8800/api",
            },
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=config.server.host,
        port=config.server.port,
        reload=True,
    )
