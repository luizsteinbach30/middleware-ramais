# Builds the self-contained Windows installer (.exe).
#
# Output: packaging\windows\inno\MiddlewareMonitorSetup-<version>.exe
#
# Requirements on the build machine (NOT on the target server!):
#   * Inno Setup 6+ — https://jrsoftware.org/isinfo.php  (iscc.exe in PATH)
#   * Python 3.11+ to run pip wheel
#   * Internet (only at build time, to download Python embeddable + wheels + NSSM)
#
# After the build, the resulting .exe is fully offline-installable on any
# Windows 10/Server 2019+ machine — no Python, NSSM, internet or extra
# dependencies are required on the target.

[CmdletBinding()]
param(
  [string]$Version = "2.0.0",
  [string]$PythonVersion = "3.11.9",
  [string]$NssmVersion = "2.24"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
$Pkg = Join-Path $Root "packaging\windows"
$Payload = Join-Path $Pkg "payload"
$Build = Join-Path $Root "build\win"
$Inno = Join-Path $Pkg "inno"

Write-Host "==> Cleaning previous build" -ForegroundColor Cyan
if (Test-Path $Build) { Remove-Item -Recurse -Force $Build }
New-Item -ItemType Directory -Force -Path $Build | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Build "python") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Build "wheels") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Build "app\$Version") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Build "scripts") | Out-Null

Write-Host "==> Downloading Python $PythonVersion embeddable" -ForegroundColor Cyan
$pyZip = Join-Path $env:TEMP "python-embed-$PythonVersion.zip"
$pyUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
Invoke-WebRequest -Uri $pyUrl -OutFile $pyZip
Expand-Archive -Force -Path $pyZip -DestinationPath (Join-Path $Build "python")

Write-Host "==> Installing pip into the embeddable Python" -ForegroundColor Cyan
$Python = Join-Path $Build "python\python.exe"
# Embeddable distributions ship without pip; enable site-packages and bootstrap pip.
$pthFile = Get-ChildItem (Join-Path $Build "python") -Filter "python*._pth" | Select-Object -First 1
if ($pthFile) {
  $content = Get-Content $pthFile.FullName
  if ($content -notmatch "^import\s+site") {
    Set-Content -Path $pthFile.FullName -Value ($content + "import site") -Encoding ASCII
  }
}
$getPip = Join-Path $env:TEMP "get-pip.py"
Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip
& $Python $getPip --no-warn-script-location | Out-Null

Write-Host "==> Building wheel from current source" -ForegroundColor Cyan
Push-Location $Root
& $Python -m pip install --upgrade pip build wheel | Out-Null
& $Python -m build --wheel --outdir (Join-Path $Build "wheels") | Out-Null
Pop-Location

Write-Host "==> Downloading runtime dependencies as wheels" -ForegroundColor Cyan
& $Python -m pip wheel --wheel-dir (Join-Path $Build "wheels") `
  "fastapi>=0.110" "uvicorn[standard]>=0.27" "sqlalchemy>=2.0.25" "alembic>=1.13" `
  "pydantic>=2.5" "pydantic-settings>=2.1" "structlog>=24.1" "httpx>=0.27" `
  "jinja2>=3.1" "bcrypt>=4.1,<5" "itsdangerous>=2.1" "cryptography>=42.0" `
  "apscheduler>=3.10" "python-multipart>=0.0.7" "packaging>=23.0"

Write-Host "==> Copying application source" -ForegroundColor Cyan
$AppOut = Join-Path $Build "app\$Version"
robocopy (Join-Path $Root "src") (Join-Path $AppOut "src") /E /NFL /NDL /NJH /NJS /NP | Out-Null
Copy-Item (Join-Path $Root "alembic.ini") $AppOut
Copy-Item (Join-Path $Root "pyproject.toml") $AppOut
robocopy (Join-Path $Root "scripts") (Join-Path $AppOut "scripts") /E /NFL /NDL /NJH /NJS /NP | Out-Null
robocopy (Join-Path $Root "docs") (Join-Path $AppOut "docs") /E /NFL /NDL /NJH /NJS /NP | Out-Null
Copy-Item (Join-Path $Root "README.md") $AppOut
Copy-Item (Join-Path $Root "CHANGELOG.md") $AppOut

Write-Host "==> Downloading NSSM $NssmVersion" -ForegroundColor Cyan
$nssmZip = Join-Path $env:TEMP "nssm-$NssmVersion.zip"
Invoke-WebRequest "https://nssm.cc/release/nssm-$NssmVersion.zip" -OutFile $nssmZip
$nssmExtract = Join-Path $env:TEMP "nssm-$NssmVersion"
if (Test-Path $nssmExtract) { Remove-Item -Recurse -Force $nssmExtract }
Expand-Archive -Force -Path $nssmZip -DestinationPath (Split-Path $nssmExtract)
Copy-Item (Join-Path $nssmExtract "win64\nssm.exe") (Join-Path $Build "nssm.exe")

Write-Host "==> Copying install scripts" -ForegroundColor Cyan
Copy-Item (Join-Path $Payload "scripts\postinstall.ps1")      (Join-Path $Build "scripts\postinstall.ps1")
Copy-Item (Join-Path $Payload "scripts\uninstall.ps1")        (Join-Path $Build "scripts\uninstall.ps1")
Copy-Item (Join-Path $Payload "scripts\service-wrapper.cmd")  (Join-Path $Build "scripts\service-wrapper.cmd")
Copy-Item (Join-Path $Payload "scripts\Control.ps1")          (Join-Path $Build "scripts\Control.ps1")
Copy-Item (Join-Path $Payload "scripts\Control.cmd")          (Join-Path $Build "scripts\Control.cmd")

Write-Host "==> Staging Inno Setup payload" -ForegroundColor Cyan
$IssPayload = Join-Path $Inno "payload"
if (Test-Path $IssPayload) { Remove-Item -Recurse -Force $IssPayload }
robocopy $Build $IssPayload /E /NFL /NDL /NJH /NJS /NP | Out-Null

Write-Host "==> Compiling installer with Inno Setup" -ForegroundColor Cyan
Push-Location $Inno
& iscc.exe /DAppVersion=$Version "MiddlewareMonitor.iss"
Pop-Location

$Out = Get-ChildItem $Inno -Filter "MiddlewareMonitorSetup-*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $Out) {
  Write-Error "Installer not produced"
  exit 1
}
Write-Host ""
Write-Host "Installer ready: $($Out.FullName)" -ForegroundColor Green
Write-Host "Size: $([math]::Round($Out.Length / 1MB, 1)) MB"
