# AI 图书馆启动脚本（Windows）
# 用法：双击或 .\start.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "[AI Library] 首次运行：创建虚拟环境并安装依赖..."
    python -m venv .venv
    & $py -m pip install --upgrade pip
    & $py -m pip install -r requirements.txt -r requirements-dev.txt
}

Write-Host "[AI Library] 启动后端 http://127.0.0.1:8800 ..."
& $py -m uvicorn app.main:app --host 127.0.0.1 --port 8800
