"""桌面壳 · 后端进程管理（P5-1）。

负责检测 / 拉起 / 关闭 FastAPI 后端（127.0.0.1:8800）：
- 已运行（手动 uvicorn / 上次遗留）→ 复用，退出时不关闭（避免误杀主人手动启动的服务）
- 未运行 → 用项目 .venv python 拉起 `uvicorn app.main:app`，退出时终止自己拉起的进程

设计说明：
- 本模块不 import app 包（PyInstaller 打包壳时避免收集整个后端）
- 日志重定向到 desktop/backend.log（*.log 已被 .gitignore 忽略）
"""
from __future__ import annotations

import logging
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger("ai_library.desktop.backend")


class BackendManager:
    """FastAPI 后端生命周期管理。"""

    def __init__(
        self,
        root: Path,
        host: str = "127.0.0.1",
        port: int = 8800,
        python: str | None = None,
        log_file: Path | None = None,
        health_path: str = "/api/health",
        timeout: float = 0.6,
    ) -> None:
        self.root = Path(root)
        self.host = host
        self.port = port
        self.health_url = f"http://{host}:{port}{health_path}"
        self.timeout = timeout
        # python 解析顺序：显式传入 → sys.executable → 项目 .venv → PATH python
        self.python = python or self._resolve_python()
        self.log_file = Path(log_file) if log_file else (self.root / "desktop" / "backend.log")
        self.proc: subprocess.Popen | None = None
        self._owned = False  # True = 本实例拉起的进程，退出时应关闭

    @staticmethod
    def _resolve_python() -> str:
        exe = sys.executable
        # PyInstaller frozen 时 sys.executable 是壳 exe，找 .venv python：
        # 顺序：exe 同级 → exe 上级（dist/ → 项目根）→ exe 上级的上级 → PATH
        if getattr(sys, "frozen", False):
            candidates = [
                Path(exe).parent / ".venv" / "Scripts" / "python.exe",
                Path(exe).parent / "Scripts" / "python.exe",
                Path(exe).parent / "python.exe",
                Path(exe).parent.parent / ".venv" / "Scripts" / "python.exe",
                Path(exe).parent.parent.parent / ".venv" / "Scripts" / "python.exe",
            ]
            for cand in candidates:
                if cand.exists():
                    return str(cand)
            return "python"
        return exe

    # ---------- 探测 ----------
    def is_running(self) -> bool:
        """探测后端是否已就绪（GET /api/health，短超时）。"""
        try:
            with urllib.request.urlopen(self.health_url, timeout=self.timeout) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError, TimeoutError, ValueError):
            return False

    def port_in_use(self) -> bool:
        """端口是否被占用（可能只是占端口但非本后端）。"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(self.timeout)
            return s.connect_ex((self.host, self.port)) == 0

    # ---------- 启动 ----------
    def start(self, wait: bool = True, wait_timeout: float = 30.0) -> bool:
        """确保后端运行。返回是否就绪。

        - 已运行 → 复用（_owned=False），直接返回
        - 未运行 → 拉起子进程（_owned=True），可选等待就绪
        """
        if self.is_running():
            logger.info("后端已在运行：%s", self.health_url)
            return True

        cmd = [
            self.python,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            self.host,
            "--port",
            str(self.port),
        ]
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(self.log_file, "a", encoding="utf-8")
        kwargs: dict = {
            "cwd": str(self.root),
            "stdout": log_fh,
            "stderr": subprocess.STDOUT,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # 不弹黑窗
        try:
            self.proc = subprocess.Popen(cmd, **kwargs)
            self._owned = True
            logger.info("已拉起后端进程 pid=%s（%s）", self.proc.pid, " ".join(cmd))
        except FileNotFoundError as e:
            logger.error("找不到 python 解释器：%s（%s）", self.python, e)
            log_fh.close()
            return False

        if wait:
            return self.wait_ready(wait_timeout)
        return False

    def wait_ready(self, timeout: float = 30.0, interval: float = 0.4) -> bool:
        """轮询健康检查直到就绪或超时。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_running():
                logger.info("后端就绪：%s", self.health_url)
                return True
            if self.proc is not None and self.proc.poll() is not None:
                logger.error("后端进程提前退出，exit=%s（见 %s）", self.proc.returncode, self.log_file)
                return False
            time.sleep(interval)
        logger.error("等待后端就绪超时（%.1fs）", timeout)
        return False

    # ---------- 关闭 ----------
    def stop(self) -> None:
        """关闭自己拉起的后端进程（不动复用/主人手动启动的服务）。"""
        if not self._owned or self.proc is None:
            return
        if self.proc.poll() is None:
            logger.info("正在关闭后端进程 pid=%s", self.proc.pid)
            if sys.platform == "win32":
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                        capture_output=True,
                        timeout=10,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("taskkill 失败，回退 terminate：%s", e)
                    self.proc.terminate()
            else:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        self._owned = False

    def __del__(self) -> None:
        try:
            self.stop()
        except Exception:  # noqa: BLE001
            pass
