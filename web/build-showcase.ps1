$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $PSScriptRoot "frontend"
$backend = Join-Path $PSScriptRoot "backend"
$buildVenv = Join-Path $root ".build-venv"
$python = Join-Path $buildVenv "Scripts\python.exe"
$spec = Join-Path $backend "showcase.spec"
$dist = Join-Path $PSScriptRoot "dist"
$work = Join-Path $root "build\pyinstaller"

if (-not (Test-Path -LiteralPath $python)) {
    py -3.12 -m venv $buildVenv
    if ($LASTEXITCODE -ne 0) { throw "Python 3.12 build environment creation failed" }
}
& $python -m pip install --disable-pip-version-check -r (Join-Path $backend "requirements-packaging.txt")
if ($LASTEXITCODE -ne 0) { throw "Packaging dependency installation failed" }

Push-Location $frontend
try {
    pnpm.cmd install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed" }
    pnpm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend production build failed" }
} finally { Pop-Location }

Push-Location $root
try {
    & $python -m PyInstaller --clean --noconfirm --distpath $dist --workpath $work $spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }
} finally { Pop-Location }

$package = Join-Path $dist "RFIDShowcase"
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "Start-Showcase.cmd") -Destination (Join-Path $package "Start-Showcase.cmd") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "Pair-Device.cmd") -Destination (Join-Path $package "Pair-Device.cmd") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "SHOWCASE-README.txt") -Destination (Join-Path $package "README.txt") -Force
Copy-Item -LiteralPath (Join-Path $root "docs\v2\学校貸出PC_導入手順.md") -Destination (Join-Path $package "学校PC導入手順.md") -Force
Write-Host "Showcase package: $package"
