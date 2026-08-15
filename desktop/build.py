"""AI 图书馆 · 桌面壳打包脚本（P5-1）。

用法：python desktop/build.py
产物：dist/AI图书馆.exe（单文件，无控制台窗口）

说明：
- exe = 桌面壳（launcher + pywebview + 托盘），双击即启动窗口
- 后端（FastAPI/LanceDB 等）不打包进 exe —— exe 自动用项目 .venv 的 python
  拉起后端（desktop/backend.py 的 _resolve_python 在 frozen 模式下查找
  exe 同级 .venv/Scripts/python.exe）。因此把 exe 放在项目根目录运行即可，
  或把项目 .venv 与 web/dist 放到 exe 同级目录。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
if not PY.exists():
    PY = Path(sys.executable)


def _ensure_pyinstaller() -> None:
    subprocess.run([str(PY), "-m", "pip", "install", "pyinstaller"],
                   check=True, capture_output=True)


def _ensure_icon() -> Path:
    ico = ROOT / "desktop" / "icon.ico"
    if not ico.exists():
        sys.path.insert(0, str(ROOT))
        from desktop.tray import _default_icon

        _default_icon().save(ico, format="ICO",
                             sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    return ico


def main() -> int:
    _ensure_pyinstaller()
    icon = _ensure_icon()
    for stale in (ROOT / "build", ROOT / "AI图书馆.spec"):
        if stale.exists():
            if stale.is_dir():
                import shutil

                shutil.rmtree(stale, ignore_errors=True)
            else:
                stale.unlink()

    cmd = [
        str(PY), "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", "AI图书馆",
        "--icon", str(icon),
        "--hidden-import", "desktop.backend",
        "--hidden-import", "desktop.tray",
        "--hidden-import", "desktop.webview2",
        "--hidden-import", "PIL",
        "desktop/launcher.py",
    ]
    print("[AI Library] 运行:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print("[AI Library] 打包失败")
        return result.returncode
    print("\n[AI Library] 打包完成: dist/AI图书馆.exe")
    print("提示：把 exe 放在项目根目录运行（自动用 .venv 拉起后端），")
    print("      或把项目 .venv 与 web/dist 放到 exe 同级目录。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
