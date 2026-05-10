[CmdletBinding()]
param([switch]$Purge)

$ErrorActionPreference = "Stop"
$Prefix = "$env:ProgramFiles\MiddlewareMonitor"
$Data = "$env:ProgramData\MiddlewareMonitor"

Write-Host "==> Stopping service"
& nssm stop MiddlewareMonitor 2>$null
& nssm remove MiddlewareMonitor confirm 2>$null

Write-Host "==> Removing $Prefix"
if (Test-Path $Prefix) { Remove-Item $Prefix -Recurse -Force }

if ($Purge) {
  Write-Host "==> Purging $Data"
  if (Test-Path $Data) { Remove-Item $Data -Recurse -Force }
}

Write-Host "Removed."
