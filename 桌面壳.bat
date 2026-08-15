@echo off
rem AI 图书馆 · 桌面壳启动器（P5-1）
rem 双击本文件：自动拉起/复用后端 → pywebview 桌面窗口 + 托盘常驻
rem 首次运行会安装桌面壳依赖（pywebview / pystray / Pillow）
chcp 65001 >nul
cd /d "%~dp0"

set PY=.venv\Scripts\python.exe
if not exist "%PY%" (
    echo [AI Library] 未找到虚拟环境，请先运行 start.ps1 初始化。
    pause
    exit /b 1
)

rem 桌面壳依赖（pywebview 等）不在基础 requirements.txt，这里按需安装
"%PY%" -c "import pywebview, pystray, PIL" >nul 2>&1
if errorlevel 1 (
    echo [AI Library] 安装桌面壳依赖 ...
    "%PY%" -m pip install -r desktop\requirements.txt
)

echo [AI Library] 启动桌面壳 ...
"%PY%" desktop\launcher.py
