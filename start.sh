#!/usr/bin/env bash
# AI 图书馆启动脚本（WSL/Linux）
set -e
cd "$(dirname "$0")"

PY=".venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "[AI Library] 首次运行：创建虚拟环境并安装依赖..."
  python3 -m venv .venv
  "$PY" -m pip install --upgrade pip
  "$PY" -m pip install -r requirements.txt -r requirements-dev.txt
fi

echo "[AI Library] 启动后端 http://127.0.0.1:8800 ..."
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8800
