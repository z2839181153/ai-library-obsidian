"""桌面壳 · 系统托盘（P5-1，基于 pystray）。

托盘常驻：关窗 → 隐藏到托盘（不退出），托盘菜单提供：
- 显示窗口（恢复）
- 打开浏览器（降级场景/习惯浏览器）
- 退出（关闭后端 + 退出程序）
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

import pystray
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("ai_library.desktop.tray")


def _default_icon() -> Image.Image:
    """生成一个简单书本图标（无外部资源依赖）。"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 深色圆角底
    d.rounded_rectangle([2, 2, 62, 62], radius=12, fill=(46, 68, 96, 255))
    # 书本：两页
    d.rounded_rectangle([12, 14, 34, 50], radius=4, fill=(245, 230, 205, 255))
    d.rounded_rectangle([30, 14, 52, 50], radius=4, fill=(222, 198, 156, 255))
    d.rectangle([30, 14, 34, 50], fill=(46, 68, 96, 255))  # 书脊
    # 文字行
    for y in (22, 30, 38):
        d.line([16, y, 29, y], fill=(120, 100, 70, 255), width=2)
        d.line([36, y, 48, y], fill=(120, 100, 70, 255), width=2)
    return img


class TrayApp:
    """pystray 托盘封装（独立线程运行）。"""

    def __init__(
        self,
        on_show: Callable[[], None] | None = None,
        on_open_browser: Callable[[], None] | None = None,
        on_quit: Callable[[], None] | None = None,
        icon_image: Image.Image | None = None,
        title: str = "AI 图书馆",
    ) -> None:
        self._on_show = on_show
        self._on_open_browser = on_open_browser
        self._on_quit = on_quit
        self._title = title
        self._image = icon_image or _default_icon()
        self._icon: pystray.Icon | None = None
        self._thread: threading.Thread | None = None

    def _menu(self):
        return pystray.Menu(
            pystray.MenuItem("📖 显示窗口", self._show, default=True),
            pystray.MenuItem("🌐 浏览器打开", self._open_browser),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._quit),
        )

    def _show(self, icon=None, item=None) -> None:
        if self._on_show:
            self._on_show()

    def _open_browser(self, icon=None, item=None) -> None:
        if self._on_open_browser:
            self._on_open_browser()

    def _quit(self, icon=None, item=None) -> None:
        if self._on_quit:
            self._on_quit()

    def start(self) -> None:
        """在独立线程启动托盘（不阻塞主线程）。"""
        if self._icon is not None:
            return
        self._icon = pystray.Icon(self._title, self._image, self._title, menu=self._menu())
        self._thread = threading.Thread(target=self._icon.run, daemon=True, name="tray")
        self._thread.start()
        logger.info("系统托盘已启动")

    def stop(self) -> None:
        """停止托盘。"""
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:  # noqa: BLE001
                pass
            self._icon = None
        logger.info("系统托盘已停止")
