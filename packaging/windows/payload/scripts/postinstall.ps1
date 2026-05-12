# Runs once at the end of the Inno Setup installer (as Admin).
# Wires up Python embeddable, installs offline wheels, runs migrations,
# bootstraps the admin user and registers the Windows service via NSSM.

[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$InstallDir,
  [Parameter(Mandatory=$true)][string]$DataDir,
  [Parameter(Mandatory=$true)][string]$AppVersion
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$LogFile = Join-Path $DataDir "logs\install.log"
function Log($msg) {
  $line = "[{0}] {1}" -f (Get-Date -F "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $LogFile -Value $line
  Write-Host $line
}

Log "postinstall start (version=$AppVersion)"
Log "install_dir=$InstallDir data_dir=$DataDir"

$Python   = Join-Path $InstallDir "python\python.exe"
$Wheels   = Join-Path $InstallDir "wheels"
$AppDir   = Join-Path $InstallDir "app\$AppVersion"
$Current  = Join-Path $InstallDir "current"
$Bin      = Join-Path $InstallDir "bin"
$Nssm     = Join-Path $Bin "nssm.exe"
$EnvFile  = Join-Path $DataDir "env.cmd"
$Logs     = Join-Path $DataDir "logs"

# 1. Update "current" junction to point at the new app/<version>
if (Test-Path $Current) {
  Log "removing existing current junction"
  cmd /c rmdir $Current | Out-Null
}
cmd /c mklink /J "$Current" "$AppDir" | Out-Null
Log "current -> $AppDir"

# 2. Enable site-packages on the embeddable Python (required to import installed wheels)
$PthFiles = Get-ChildItem -Path (Join-Path $InstallDir "python") -Filter "python*._pth" -ErrorAction SilentlyContinue
foreach ($pth in $PthFiles) {
  $content = Get-Content $pth.FullName
  if ($content -notmatch "^import\s+site") {
    $newContent = $content + "import site"
    Set-Content -Path $pth.FullName -Value $newContent -Encoding ASCII
    Log "patched $($pth.Name) (enabled import site)"
  }
}

# 3. Install offline wheels (already inside the installer payload)
Log "installing wheels from $Wheels"
& $Python -m pip install --no-index --find-links "$Wheels" --no-warn-script-location pip 2>&1 | Out-Null
& $Python -m pip install --no-index --find-links "$Wheels" --no-warn-script-location middleware-monitor 2>&1 | Tee-Object -FilePath $LogFile -Append | Out-Null
if ($LASTEXITCODE -ne 0) {
  Log "pip install failed with code $LASTEXITCODE"
  exit 1
}

# 4. Generate env.cmd with a strong secret (first install only)
if (-not (Test-Path $EnvFile)) {
  Log "generating env.cmd"
  $secret = & $Python -c "import secrets;print(secrets.token_urlsafe(64))"
  $envContent = @"
@echo off
set APP_HOST=0.0.0.0
set APP_PORT=8080
set APP_DATA_DIR=$DataDir
set APP_SECRET_KEY=$secret
set APP_LOG_LEVEL=INFO
set APP_LOG_JSON=true
set APP_COOKIE_SECURE=false
set APP_UPDATE_REPO=luizsteinbach30/middleware-ramais
set APP_UPDATE_CHANNEL=stable
"@
  $envContent | Out-File -FilePath $EnvFile -Encoding ASCII
}

# 5. Run alembic upgrade head against the DB in ProgramData
Log "running alembic upgrade head"
$env:APP_DATA_DIR = $DataDir
$env:APP_SECRET_KEY = (Get-Content $EnvFile | Where-Object { $_ -match "APP_SECRET_KEY" } | ForEach-Object { ($_ -split "=", 2)[1] })
$env:PYTHONPATH = ""
$alembicIni = Join-Path $Current "alembic.ini"
if (Test-Path $alembicIni) {
  & $Python -m alembic -c $alembicIni upgrade head 2>&1 | Tee-Object -FilePath $LogFile -Append | Out-Null
} else {
  Log "alembic.ini not found at $alembicIni"
}

# 6. Bootstrap admin (prints temporary password into install.log too)
$bootstrap = Join-Path $Current "scripts\bootstrap_admin.py"
if (Test-Path $bootstrap) {
  Log "bootstrapping admin"
  & $Python $bootstrap 2>&1 | Tee-Object -FilePath $LogFile -Append
}

# 7. Register the Windows service via bundled NSSM
$serviceName = "MiddlewareMonitor"
$wrapper = Join-Path $InstallDir "scripts\service-wrapper.cmd"

Log "uninstalling existing service (if any)"
& $Nssm stop $serviceName 2>&1 | Out-Null
& $Nssm remove $serviceName confirm 2>&1 | Out-Null

Log "installing service"
& $Nssm install $serviceName $wrapper | Out-Null
& $Nssm set $serviceName AppDirectory $Current | Out-Null
& $Nssm set $serviceName Start SERVICE_AUTO_START | Out-Null
& $Nssm set $serviceName AppStdout (Join-Path $Logs "app.log") | Out-Null
& $Nssm set $serviceName AppStderr (Join-Path $Logs "app.err") | Out-Null
& $Nssm set $serviceName AppRotateFiles 1 | Out-Null
& $Nssm set $serviceName AppRotateBytes 10485760 | Out-Null
& $Nssm set $serviceName AppEnvironmentExtra `
  "APP_DATA_DIR=$DataDir" `
  "APP_SECRET_KEY=$($env:APP_SECRET_KEY)" `
  "PYTHONPATH=" | Out-Null
& $Nssm set $serviceName Description "Middleware USCall Monitor v$AppVersion" | Out-Null

Log "starting service"
& $Nssm start $serviceName | Out-Null

# 8. Wait for healthcheck (up to 30s)
$started = Get-Date
$ok = $false
while (((Get-Date) - $started).TotalSeconds -lt 30) {
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8080/api/system/healthz" -UseBasicParsing -TimeoutSec 2
    if ($r.StatusCode -eq 200) { $ok = $true; break }
  } catch { }
  Start-Sleep -Milliseconds 700
}
if ($ok) {
  Log "service is up at http://127.0.0.1:8080/"
} else {
  Log "service did not respond in 30s — check $Logs\\app.err"
  exit 1
}

Log "postinstall done"
