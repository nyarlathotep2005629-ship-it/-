$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot "web\backend\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Web用仮想環境がありません。web\backend\requirements-web.txtをインストールしてください。"
}
Set-Location (Join-Path $projectRoot "web\backend")
& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
