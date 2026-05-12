# Called by Inno Setup during uninstall (as Admin).
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$InstallDir,
  [Parameter(Mandatory=$true)][string]$DataDir
)

$ErrorActionPreference = "Continue"
$Nssm = Join-Path $InstallDir "bin\nssm.exe"
$serviceName = "MiddlewareMonitor"

if (Test-Path $Nssm) {
  & $Nssm stop $serviceName 2>&1 | Out-Null
  & $Nssm remove $serviceName confirm 2>&1 | Out-Null
}

# Keep $DataDir (ProgramData) so the operator can choose what to do with
# the database and backups. To wipe it, the operator can use:
#   Remove-Item -Recurse -Force $DataDir
