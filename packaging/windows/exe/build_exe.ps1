# Builds the single-file MiddlewareMonitor.exe via PyInstaller.
#
# Output: dist/MiddlewareMonitor.exe
#
# Requirements (build machine only — NOT on the target machine!):
#   * Python 3.11+ with venv
#   * Internet (only at build time, to install PyInstaller + runtime deps)
#
# The resulting .exe runs on any Windows 10/11 / Server 2019+ machine
# without Python or any other prerequisite.

[CmdletBinding()]
param(
  [string]$Version
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$Spec = Join-Path $Root "packaging\windows\exe\MiddlewareMonitor.spec"

if (-not $Version) {
  $Version = & python -c "import sys, importlib.util; spec = importlib.util.spec_from_file_location('v', '$($Root -replace '\\','\\\\')\\src\\middleware_monitor\\version.py'); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print(m.__version__)"
}
Write-Host "==> Building MiddlewareMonitor v$Version" -ForegroundColor Cyan

$Venv = Join-Path $Root ".venv-build"
if (-not (Test-Path "$Venv\Scripts\python.exe")) {
  Write-Host "==> Creating build venv ($Venv)"
  python -m venv $Venv
}
$Py = Join-Path $Venv "Scripts\python.exe"

Write-Host "==> Installing build + runtime dependencies"
& $Py -m pip install --upgrade pip wheel setuptools | Out-Null
& $Py -m pip install --upgrade pyinstaller==6.10.0 | Out-Null
Push-Location $Root
& $Py -m pip install -e ".[metrics]" | Out-Null
Pop-Location

Write-Host "==> Running PyInstaller"
$Dist = Join-Path $Root "dist"
$Build = Join-Path $Root "build\pyinstaller"
if (Test-Path $Dist)  { Remove-Item -Recurse -Force $Dist }
if (Test-Path $Build) { Remove-Item -Recurse -Force $Build }

Push-Location $Root
& $Py -m PyInstaller --noconfirm --clean --distpath $Dist --workpath $Build $Spec
if ($LASTEXITCODE -ne 0) {
  Pop-Location
  throw "PyInstaller failed with exit code $LASTEXITCODE"
}
Pop-Location

# Rename to include the version
$Out = Join-Path $Dist "MiddlewareMonitor.exe"
$Final = Join-Path $Dist "MiddlewareMonitor-$Version.exe"
if (Test-Path $Final) { Remove-Item -Force $Final }
Move-Item $Out $Final

Write-Host ""
Write-Host "Built: $Final" -ForegroundColor Green
Write-Host "Size:  $([math]::Round((Get-Item $Final).Length / 1MB, 1)) MB"
Write-Host ""
Write-Host "Hash:" -ForegroundColor Cyan
(Get-FileHash $Final -Algorithm SHA256).Hash.ToLower()
