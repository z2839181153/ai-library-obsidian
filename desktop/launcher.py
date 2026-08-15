"""AI 图书馆 · 桌面壳启动器（P5-1）。

双击启动：检测/拉起后端 → 检测 WebView2 → pywebview 窗口 + 托盘常驻。
- 后端未运行 → 自动拉起（uvicorn app.main:app，127.0.0.1:8800），退出时自动关闭
- WebView2 缺失 / pywebview 不可用 → 降级：系统浏览器打开 + 提示下载 WebView2
- 关窗 → 隐藏到托盘（不退出）；托盘菜单退出 → 关闭后端 + 退出程序

用法：
    python desktop/launcher.py            # 正常启动
    python desktop/launcher.py --port 8899
    python desktop/launcher.py --no-tray  # 调试：不启用托盘（关窗即退出）
    python desktop/launcher.py --fallback-browser  # 强制浏览器模式（调试降级路径）
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import webbrowser
from pathlib import Path

# 项目根：
# - 源码运行（python desktop/launcher.py）→ desktop/ 上级
# - PyInstaller 打包（frozen）→ exe 所在目录；若该目录不含 web/dist（如 exe 在
#   dist/ 子目录），向上查找含 web/dist 的目录（项目根）
def _resolve_root() -> Path:
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent.parent
    here = Path(sys.executable).resolve().parent
    if (here / "web" / "dist" / "index.html").exists():
        return here
    # 向上最多 3 层
    for parent in (here, here.parent, here.parent.parent, here.parent.parent.parent):
        if (parent / "web" / "dist" / "index.html").exists():
            return parent
    return here

ROOT = _resolve_root()
sys.path.insert(0, str(ROOT))

from desktop.backend import BackendManager  # noqa: E402
from desktop.tray import TrayApp, _default_icon  # noqa: E402
from desktop.webview2 import webview2_available, webview2_download_url  # noqa: E402

logger = logging.getLogger("ai_library.desktop.launcher")

APP_TITLE = "AI 图书馆"
WINDOW_W, WINDOW_H = 1280, 860
MIN_W, MIN_H = 960, 640


def setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    log_file = ROOT / "desktop" / "launcher.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ]
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AI 图书馆桌面壳")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8800)
    p.add_argument("--no-tray", action="store_true", help="不启用系统托盘（关窗即退出）")
    p.add_argument("--fallback-browser", action="store_true", help="强制用系统浏览器打开（调试降级路径）")
    p.add_argument("--debug", action="store_true", help="调试日志")
    return p.parse_args(argv)


def _check_frontend() -> bool:
    """检查前端是否已构建（web/dist/index.html）。"""
    return (ROOT / "web" / "dist" / "index.html").exists()


def _ensure_icon_file() -> str | None:
    """生成窗口图标 .ico（desktop/icon.ico，不入 git）。"""
    try:
        ico_path = ROOT / "desktop" / "icon.ico"
        if not ico_path.exists():
            img = _default_icon()
            img.save(ico_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
        return str(ico_path)
    except Exception as e:  # noqa: BLE001
        logger.warning("生成图标失败，跳过：%s", e)
        return None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.debug)
    logger.info("AI 图书馆桌面壳启动（root=%s）", ROOT)

    if not _check_frontend():
        logger.warning("未找到 web/dist/index.html —— 前端未构建。请先在 web/ 执行 `npm install && npm run build`。")

    # 1) 后端
    backend = BackendManager(root=ROOT, host=args.host, port=args.port)
    if not backend.start(wait=True, wait_timeout=30.0):
        logger.error("后端启动失败（请查看 desktop/backend.log）")
        return 1
    url = f"http://{args.host}:{args.port}"

    # 2) WebView2 检测
    use_webview = not args.fallback_browser
    if use_webview:
        try:
            import webview  # noqa: PLC0415
        except Exception as e:  # noqa: BLE001
            logger.warning("pywebview 不可用（%s）→ 降级浏览器", e)
            use_webview = False
        else:
            if not webview2_available():
                logger.warning("未检测到 WebView2 Runtime → 降级浏览器打开")
                use_webview = False

    # 3) 托盘（常驻；--no-tray 时仅调试模式）
    tray: TrayApp | None = None
    quit_event = threading.Event()
    window_ref: dict = {}

    def _open_browser() -> None:
        webbrowser.open(url)

    def _do_quit() -> None:
        logger.info("托盘退出")
        quit_event.set()
        w = window_ref.get("window")
        if w is not None:
            try:
                w.destroy()
            except Exception as e:  # noqa: BLE001
                logger.warning("窗口销毁异常：%s", e)
                os._exit(0)  # noqa: PLR1722 —— 兜底强制退出

    if not args.no_tray:
        tray = TrayApp(
            on_show=lambda: _restore_window(window_ref),
            on_open_browser=_open_browser,
            on_quit=_do_quit,
        )
        tray.start()

    # 4) 打开界面
    if use_webview:
        rc = _run_webview(args, url, quit_event, window_ref, tray, backend)
    else:
        rc = _run_browser_fallback(args, url, quit_event, window_ref, tray, backend)

    # 5) 清理
    if tray is not None:
        tray.stop()
    backend.stop()
    logger.info("桌面壳退出（rc=%s）", rc)
    return rc


def _restore_window(window_ref: dict) -> None:
    """托盘'显示窗口'：有窗口则恢复，否则浏览器打开。"""
    w = window_ref.get("window")
    if w is not None:
        try:
            w.restore()
            w.show()
        except Exception as e:  # noqa: BLE001
            logger.warning("恢复窗口失败：%s", e)
            webbrowser.open(window_ref.get("url", ""))
    else:
        webbrowser.open(window_ref.get("url", ""))


def _run_webview(args, url: str, quit_event, window_ref: dict, tray, backend) -> int:
    """pywebview 窗口模式：关窗隐藏到托盘，托盘退出才真正退出。"""
    import webview  # noqa: PLC0415

    icon_path = _ensure_icon_file()

    window = webview.create_window(
        APP_TITLE,
        url,
        width=WINDOW_W,
        height=WINDOW_H,
        min_size=(MIN_W, MIN_H),
        background_color="#f7f3ea",
    )
    window_ref["window"] = window
    window_ref["url"] = url

    def on_closing():
        """关窗 → 隐藏到托盘（除非正在退出）。"""
        if quit_event.is_set() or args.no_tray:
            return True  # 允许关闭
        try:
            window.hide()
        except Exception as e:  # noqa: BLE001
            logger.warning("隐藏窗口失败：%s", e)
            return True
        return False  # 阻止关闭

    window.events.closing += on_closing

    logger.info("启动 pywebview 窗口：%s", url)
    webview.start(icon=icon_path, debug=args.debug)

    logger.info("pywebview 窗口已关闭")
    return 0


def _run_browser_fallback(args, url: str, quit_event, window_ref: dict, tray, backend) -> int:
    """降级：系统浏览器打开 + 提示（控制台/日志）+ 等待托盘退出。"""
    logger.info("降级浏览器打开：%s", url)
    window_ref["url"] = url
    webbrowser.open(url)

    if args.no_tray:
        # 无托盘：浏览器模式等待用户回车（调试用）
        try:
            input("AI 图书馆已用浏览器打开（Enter 退出）...")
        except EOFError:
            pass
        return 0

    # 有托盘：等托盘退出事件
    print(f"⚠️  未检测到 WebView2 Runtime，已用系统浏览器打开 {url}")
    print(f"    如需桌面窗口体验，请安装 WebView2：{webview2_download_url()}")
    logger.info("等待托盘退出事件...")
    quit_event.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
