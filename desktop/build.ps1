# AI Library desktop shell packaging script (P5-1)
# Usage: powershell -ExecutionPolicy Bypass -File desktop\build.ps1
# Output: dist\AI图书馆.exe
# NOTE: kept ASCII-only to avoid PowerShell 5.1 UTF-8 parsing issues.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location (Join-Path $root "..")

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $py = Join-Path $PWD ".venv\Scripts\python.exe"
}

Write-Host "[AI Library] Using Python: $py"
& $py "desktop\build.py"
