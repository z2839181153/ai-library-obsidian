"""P5-1 桌面壳测试：后端进程管理 / WebView2 检测 / 启动器参数与降级路径。

原则：
- 不真正启动 uvicorn（慢且依赖环境）——用假 python 进程 / 极简 HTTP 服务器验证
- WebView2 注册表检测用 mock winreg（不依赖本机实际安装）
- 启动器降级路径用 mock webbrowser / BackendManager
"""
from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from desktop.backend import BackendManager  # noqa: E402
from desktop.launcher import (  # noqa: E402
    _check_frontend,
    _run_browser_fallback,
    parse_args,
)
from desktop.webview2 import webview2_available  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _HealthHandler(BaseHTTPRequestHandler):
    """模拟后端 /api/health。"""

    def do_GET(self):  # noqa: N802
        if self.path == "/api/health":
            body = b'{"status": "ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # noqa: D102
        pass


class _FakeHTTPServer:
    """上下文管理器：在随机端口起一个健康检查服务器。"""

    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "_FakeHTTPServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()


# ---------- BackendManager ----------

def test_is_running_false_when_down():
    mgr = BackendManager(root=ROOT, host="127.0.0.1", port=_free_port())
    assert mgr.is_running() is False
    assert mgr.port_in_use() is False


def test_is_running_true_with_health_server():
    with _FakeHTTPServer() as srv:
        mgr = BackendManager(root=ROOT, host="127.0.0.1", port=srv.port)
        assert mgr.is_running() is True
        assert mgr.port_in_use() is True


def test_wait_ready_with_server_and_timeout():
    with _FakeHTTPServer() as srv:
        mgr = BackendManager(root=ROOT, host="127.0.0.1", port=srv.port)
        assert mgr.wait_ready(timeout=3) is True

    mgr2 = BackendManager(root=ROOT, host="127.0.0.1", port=_free_port())
    assert mgr2.wait_ready(timeout=0.5) is False


def test_start_launches_and_stop_terminates_owned():
    """拉起假后端进程（python sleep），stop 必须终止自己拉起的进程。"""
    mgr = BackendManager(root=ROOT, host="127.0.0.1", port=_free_port())
    fake_cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
    # 直接模拟 Popen 生命周期：start(wait=False) 不会等健康检查
    import os

    mgr.proc = subprocess.Popen(fake_cmd, cwd=str(ROOT), stdout=subprocess.DEVNULL)
    mgr._owned = True
    pid = mgr.proc.pid
    assert mgr.proc.poll() is None

    mgr.stop()
    assert mgr.proc is None
    # Windows taskkill /T /F 后进程退出
    if sys.platform == "win32":
        time.sleep(0.3)
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True
        )
        assert f"{pid}" not in result.stdout or "没有运行的任务" in result.stdout


def test_stop_does_not_kill_reused_backend():
    """复用已有后端（_owned=False）时 stop 不应终止它。"""
    with _FakeHTTPServer() as srv:
        mgr = BackendManager(root=ROOT, host="127.0.0.1", port=srv.port)
        assert mgr.is_running() is True
        # 模拟复用：未拉起进程
        mgr.stop()  # 不应崩溃，不应关闭 server
        assert mgr.is_running() is True


def test_start_when_already_running_reuses():
    with _FakeHTTPServer() as srv:
        mgr = BackendManager(root=ROOT, host="127.0.0.1", port=srv.port)
        assert mgr.start(wait=False) is True
        assert mgr._owned is False  # 复用，不属本实例


def test_resolve_python_dev_mode():
    """开发模式（非 frozen）下 python = sys.executable。"""
    mgr = BackendManager(root=ROOT)
    assert mgr.python == sys.executable


# ---------- WebView2 ----------

def test_webview2_available_non_windows_true(monkeypatch):
    monkeypatch.setattr("desktop.webview2.sys.platform", "linux")
    assert webview2_available() is True


def test_webview2_registry_found(monkeypatch):
    """mock winreg：找到 WebView2 Runtime GUID → True。"""
    import desktop.webview2 as wv2

    class FakeKey:
        pass

    calls = {"n": 0}

    def fake_enum(*args):
        calls["n"] += 1
        if calls["n"] <= 2:
            return "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"  # WebView2 GUID
        raise OSError

    def fake_open(hive, path):
        return FakeKey()

    monkeypatch.setattr(wv2, "_check_registry", lambda: True)
    assert wv2.webview2_available() is True


def test_webview2_registry_not_found(monkeypatch):
    """mock 检测返回 False → 不可用。"""
    import desktop.webview2 as wv2

    monkeypatch.setattr(wv2, "_check_registry", lambda: False)
    monkeypatch.setattr("desktop.webview2.sys.platform", "win32")
    assert wv2.webview2_available() is False


# ---------- launcher ----------

def test_parse_args_defaults():
    args = parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == 8800
    assert args.no_tray is False
    assert args.fallback_browser is False


def test_check_frontend():
    assert _check_frontend() is (ROOT / "web" / "dist" / "index.html").exists()


def test_browser_fallback_with_tray(monkeypatch):
    """降级路径：打开浏览器并等待退出事件。"""
    import threading

    import desktop.launcher as launcher

    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url))

    class FakeTray:
        pass

    quit_event = threading.Event()
    window_ref = {}

    def _set_quit():
        quit_event.set()

    t = threading.Thread(target=_set_quit, daemon=True)
    # 模拟托盘稍后退出
    t.start()

    args = parse_args(["--fallback-browser"])
    rc = launcher._run_browser_fallback(args, "http://127.0.0.1:8800", quit_event, window_ref, FakeTray(), None)
    assert rc == 0
    assert opened == ["http://127.0.0.1:8800"]
    assert window_ref.get("url") == "http://127.0.0.1:8800"


def test_browser_fallback_no_tray(monkeypatch):
    """无托盘 + 降级：input 提示后退出。"""
    import desktop.launcher as launcher

    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr("builtins.input", lambda prompt="": "x")

    import threading

    quit_event = threading.Event()
    args = parse_args(["--fallback-browser", "--no-tray"])
    rc = launcher._run_browser_fallback(args, "http://127.0.0.1:8800", quit_event, {}, None, None)
    assert rc == 0
    assert opened == ["http://127.0.0.1:8800"]


def test_main_fallback_with_mock_backend(monkeypatch):
    """main 走降级路径：mock BackendManager 与 webbrowser，验证后端被 stop。"""
    import desktop.launcher as launcher

    class FakeBackend:
        def __init__(self, **kwargs):
            self.stopped = False

        def start(self, wait=True, wait_timeout=30.0):
            return True

        def stop(self):
            self.stopped = True

    fake = FakeBackend()
    monkeypatch.setattr(launcher, "BackendManager", lambda **kw: fake)
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": "x")

    rc = launcher.main(["--fallback-browser", "--no-tray"])
    assert rc == 0
    assert fake.stopped is True
