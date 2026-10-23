$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $projectRoot "web\frontend"
$pnpm = Get-Command pnpm -ErrorAction SilentlyContinue
if ($null -eq $pnpm) { throw "pnpmが見つかりません。Node.jsの公式配布またはpnpmを準備してください。" }
Set-Location $frontend
& $pnpm.Source dev
