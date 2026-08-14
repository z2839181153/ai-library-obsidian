"""WebSocket 连接管理（设计文档 §9.3）。

- ConnectionManager：维护活跃连接；同步/异步均可投递消息。
- 模式：每个连接一个 asyncio.Queue，后台 task 消费并推给客户端。
  业务代码（含线程池中的同步 def）调用 `broadcast()` 只做 put_nowait，
  不阻塞、不持有锁，适合从 FastAPI 同步端点或后台任务发通知。
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from typing import Any

from fastapi import WebSocket


def _loop_thread(loop: asyncio.AbstractEventLoop) -> int | None:
    """返回事件循环所在线程 id（CPython asyncio 内部属性，缺省返回 None）。"""
    return getattr(loop, "_thread_id", None)


class ConnectionManager:
    def __init__(self) -> None:
        self._clients: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._clients)

    def is_empty(self) -> bool:
        return self.count == 0

    async def connect(self, ws: WebSocket) -> str:
        """接受连接，返回 client_id；启动后台推送 task。"""
        await ws.accept()
        client_id = f"ws_{uuid.uuid4().hex[:8]}"
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        # 记录当前事件循环：broadcast() 可能从线程池中的同步端点调用，
        # 必须 call_soon_threadsafe 投递，否则跨线程操作 asyncio.Queue 不可靠。
        loop = asyncio.get_running_loop()
        with self._lock:
            self._clients[client_id] = {"ws": ws, "queue": queue, "loop": loop}
        asyncio.create_task(self._pump(client_id, ws, queue))
        return client_id

    async def _pump(self, client_id: str, ws: WebSocket,
                    queue: asyncio.Queue) -> None:
        try:
            while True:
                msg = await queue.get()
                await ws.send_json(msg)
        except Exception:  # noqa: BLE001 连接断开/客户端关闭
            pass
        finally:
            await self.disconnect(client_id)

    async def disconnect(self, client_id: str) -> None:
        with self._lock:
            self._clients.pop(client_id, None)

    def send(self, client_id: str, message: dict) -> None:
        """向单个连接投递（线程安全，非阻塞）。

        可从任意线程调用：若调用线程不是连接所属的事件循环，
        用 call_soon_threadsafe 调度，避免跨线程操作 asyncio.Queue。
        """
        with self._lock:
            client = self._clients.get(client_id)
        if not client:
            return
        loop: asyncio.AbstractEventLoop = client["loop"]
        if loop.is_running() and threading.get_ident() != _loop_thread(loop):
            loop.call_soon_threadsafe(client["queue"].put_nowait, message)
        else:
            try:
                client["queue"].put_nowait(message)
            except asyncio.QueueFull:
                pass  # 客户端太慢则丢弃通知（本地服务可接受）

    def broadcast(self, message: dict) -> None:
        """广播给所有活跃连接（线程安全，非阻塞）。"""
        with self._lock:
            clients = list(self._clients.values())
        for c in clients:
            self._safe_put(c, message)

    @staticmethod
    def _safe_put(client: dict, message: dict) -> None:
        loop: asyncio.AbstractEventLoop = client["loop"]
        if loop.is_running() and threading.get_ident() != _loop_thread(loop):
            try:
                loop.call_soon_threadsafe(client["queue"].put_nowait, message)
            except (RuntimeError, asyncio.QueueFull):
                pass  # 事件循环已关闭/队列满则丢弃
        else:
            try:
                client["queue"].put_nowait(message)
            except asyncio.QueueFull:
                pass
