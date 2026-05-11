# Windows installer for Middleware USCall Monitor.
# Requires: PowerShell 5+, NSSM in PATH (https://nssm.cc/), admin rights.
[CmdletBinding()]
param(
  [string]$Version,
  [string]$Repo = $env:APP_UPDATE_REPO,
  [string]$Prefix = "$env:ProgramFiles\MiddlewareMonitor",
  [string]$Data = "$env:ProgramData\MiddlewareMonitor"
)

$ErrorActionPreference = "Stop"
if (-not $Repo) { $Repo = "luizsteinbach30/middleware-ramais" }

Write-Host "==> Ensuring directories"
foreach ($p in @($Prefix, "$Prefix\app", "$Prefix\venv", $Data, "$Data\db", "$Data\backups", "$Data\tmp", "$Data\logs")) {
  if (-not (Test-Path $p)) { New-Item -ItemType Directory -Force -Path $p | Out-Null }
}

if (-not $Version) {
  Write-Host "==> Resolving latest release from $Repo"
  $latest = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest"
  $Version = $latest.tag_name
}
$tag = $Version.TrimStart("v")
$tar = "app-v$tag.tar.gz"
$url = "https://github.com/$Repo/releases/download/$Version/$tar"
$shaUrl = "https://github.com/$Repo/releases/download/$Version/SHA256SUMS"

$tmp = New-Item -ItemType Directory -Force -Path (Join-Path $env:TEMP "mm-install-$tag")
Invoke-WebRequest $url -OutFile (Join-Path $tmp $tar)
Invoke-WebRequest $shaUrl -OutFile (Join-Path $tmp "SHA256SUMS")

Write-Host "==> Verifying checksum"
$expected = (Get-Content (Join-Path $tmp "SHA256SUMS") | Where-Object { $_ -match $tar }).Split(' ')[0]
$actual = (Get-FileHash -Algorithm SHA256 (Join-Path $tmp $tar)).Hash.ToLower()
if ($expected.ToLower() -ne $actual) { throw "Checksum mismatch ($expected != $actual)" }

Write-Host "==> Extracting (requires tar.exe — built into Windows 10+)"
$dest = Join-Path "$Prefix\app" $tag
if (-not (Test-Path $dest)) { New-Item -ItemType Directory -Force -Path $dest | Out-Null }
tar -xzf (Join-Path $tmp $tar) -C $dest --strip-components=1

Write-Host "==> Setting up venv"
if (-not (Test-Path "$Prefix\venv\Scripts\python.exe")) {
  python -m venv "$Prefix\venv"
}
& "$Prefix\venv\Scripts\python.exe" -m pip install --upgrade pip | Out-Null
$reqLock = Join-Path $dest "requirements.lock"
if (Test-Path $reqLock) {
  & "$Prefix\venv\Scripts\python.exe" -m pip install -r $reqLock
} else {
  & "$Prefix\venv\Scripts\python.exe" -m pip install -e $dest
}

Write-Host "==> Switching 'current' (junction)"
$current = Join-Path $Prefix "current"
if (Test-Path $current) { cmd /c rmdir $current }
cmd /c mklink /J $current $dest | Out-Null

# Generate env file
$envFile = Join-Path $Data "env.cmd"
if (-not (Test-Path $envFile)) {
  $secret = & "$Prefix\venv\Scripts\python.exe" -c "import secrets;print(secrets.token_urlsafe(64))"
  @"
@echo off
set APP_HOST=0.0.0.0
set APP_PORT=8080
set APP_DATA_DIR=$Data
set APP_SECRET_KEY=$secret
set APP_LOG_LEVEL=INFO
set APP_LOG_JSON=true
set APP_COOKIE_SECURE=false
set APP_UPDATE_REPO=$Repo
set APP_UPDATE_CHANNEL=stable
set APP_UPDATE_CHECK_MINUTES=60
"@ | Out-File -FilePath $envFile -Encoding ASCII
}

Write-Host "==> Running alembic upgrade head"
& "$Prefix\venv\Scripts\python.exe" -m alembic -c (Join-Path $current "alembic.ini") upgrade head

Write-Host "==> Bootstrapping admin"
$bootstrap = Join-Path $current "scripts\bootstrap_admin.py"
if (Test-Path $bootstrap) { & "$Prefix\venv\Scripts\python.exe" $bootstrap }

Write-Host "==> Configuring NSSM service"
nssm install MiddlewareMonitor "$Prefix\venv\Scripts\python.exe" "-m" "middleware_monitor"
nssm set MiddlewareMonitor AppDirectory $current
nssm set MiddlewareMonitor AppEnvironmentExtra "APP_DATA_DIR=$Data"
nssm set MiddlewareMonitor Start SERVICE_AUTO_START
nssm set MiddlewareMonitor AppStdout "$Data\logs\app.log"
nssm set MiddlewareMonitor AppStderr "$Data\logs\app.err"
nssm set MiddlewareMonitor AppRotateFiles 1
nssm set MiddlewareMonitor AppRotateBytes 10485760
nssm restart MiddlewareMonitor

Write-Host ""
Write-Host "Middleware USCall Monitor v$tag installed."
Write-Host "Open: http://localhost:8080/"
