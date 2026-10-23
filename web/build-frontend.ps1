$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $projectRoot "web\frontend"
$node = Get-Command node -ErrorAction SilentlyContinue
if ($null -eq $node) { throw "Node.js runtimeが見つかりません。" }
Set-Location $frontend
& $node.Source ".\node_modules\typescript\bin\tsc" -b --pretty false
& $node.Source ".\node_modules\vite\bin\vite.js" build
