"""FastAPI 应用入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8800
或直接运行 `python -m app.main`（开发便捷）。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import actions, ask, books, floors, health, index
from app.config import AppConfig
from app.state import build_state

config = AppConfig.load()


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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=config.server.host,
        port=config.server.port,
        reload=True,
    )
