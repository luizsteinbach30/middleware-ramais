# Middleware USCall Monitor — Painel de Controle (Windows).
# Lightweight WinForms GUI that lets the operator start / stop / restart the
# service, open the browser-based panel and inspect logs without touching
# the command line.
#
# Invoked by the Start Menu / Desktop shortcuts created by the Inno Setup
# installer. Runs elevated when the user clicks Start/Stop/Restart (UAC
# prompt is triggered on demand by calling sc.exe through a shell verb).

[CmdletBinding()]
param(
  [string]$ServiceName = "MiddlewareMonitor",
  [string]$Url = "http://localhost:8080/",
  [string]$DataDir = "$env:ProgramData\MiddlewareMonitor"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# --- helpers ---------------------------------------------------------------

function Get-ServiceStatus {
  $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
  if (-not $svc) { return "Não instalado" }
  return $svc.Status.ToString()
}

function Invoke-ServiceAction([string]$Verb) {
  # Uses sc.exe via runas verb to elevate just this call (no need for the
  # whole GUI to be elevated).
  $arg = switch ($Verb) {
    "start"   { "start  $ServiceName" }
    "stop"    { "stop   $ServiceName" }
    "restart" { "/c sc stop $ServiceName & timeout /t 2 /nobreak & sc start $ServiceName" }
  }
  $shell = if ($Verb -eq "restart") { "cmd.exe" } else { "sc.exe" }
  try {
    Start-Process -FilePath $shell -ArgumentList $arg -Verb RunAs -Wait -WindowStyle Hidden
    return $true
  } catch {
    [System.Windows.Forms.MessageBox]::Show(
      "Falha ao $Verb`: $($_.Exception.Message)",
      "Erro", "OK", "Error") | Out-Null
    return $false
  }
}

# --- form ------------------------------------------------------------------

$form = New-Object System.Windows.Forms.Form
$form.Text = "Middleware USCall Monitor — Controle"
$form.Size = New-Object System.Drawing.Size(440, 320)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.BackColor = [System.Drawing.Color]::FromArgb(17, 24, 39)        # gray-900
$form.ForeColor = [System.Drawing.Color]::FromArgb(229, 231, 235)     # gray-200
$form.Font = New-Object System.Drawing.Font("Segoe UI", 10)

# Title bar
$title = New-Object System.Windows.Forms.Label
$title.Text = "Middleware USCall Monitor"
$title.Location = New-Object System.Drawing.Point(20, 16)
$title.Size = New-Object System.Drawing.Size(400, 24)
$title.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 13)
$title.ForeColor = [System.Drawing.Color]::FromArgb(243, 244, 246)
$form.Controls.Add($title)

# Status row
$statusLabel = New-Object System.Windows.Forms.Label
$statusLabel.Text = "Status:"
$statusLabel.Location = New-Object System.Drawing.Point(20, 56)
$statusLabel.Size = New-Object System.Drawing.Size(60, 22)
$statusLabel.ForeColor = [System.Drawing.Color]::FromArgb(156, 163, 175)
$form.Controls.Add($statusLabel)

$statusValue = New-Object System.Windows.Forms.Label
$statusValue.Text = "—"
$statusValue.Location = New-Object System.Drawing.Point(80, 56)
$statusValue.Size = New-Object System.Drawing.Size(340, 22)
$statusValue.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 10)
$form.Controls.Add($statusValue)

$urlLabel = New-Object System.Windows.Forms.Label
$urlLabel.Text = "URL:"
$urlLabel.Location = New-Object System.Drawing.Point(20, 82)
$urlLabel.Size = New-Object System.Drawing.Size(60, 22)
$urlLabel.ForeColor = [System.Drawing.Color]::FromArgb(156, 163, 175)
$form.Controls.Add($urlLabel)

$urlValue = New-Object System.Windows.Forms.LinkLabel
$urlValue.Text = $Url
$urlValue.Location = New-Object System.Drawing.Point(80, 82)
$urlValue.Size = New-Object System.Drawing.Size(340, 22)
$urlValue.LinkColor = [System.Drawing.Color]::FromArgb(96, 165, 250)
$urlValue.ActiveLinkColor = [System.Drawing.Color]::FromArgb(147, 197, 253)
$urlValue.Add_LinkClicked({ Start-Process $Url })
$form.Controls.Add($urlValue)

# Separator
$sep = New-Object System.Windows.Forms.Panel
$sep.Location = New-Object System.Drawing.Point(20, 118)
$sep.Size = New-Object System.Drawing.Size(400, 1)
$sep.BackColor = [System.Drawing.Color]::FromArgb(55, 65, 81)
$form.Controls.Add($sep)

# Button factory
function New-FlatButton($text, $x, $y, $color, $fg = $null) {
  $b = New-Object System.Windows.Forms.Button
  $b.Text = $text
  $b.Location = New-Object System.Drawing.Point($x, $y)
  $b.Size = New-Object System.Drawing.Size(190, 40)
  $b.FlatStyle = "Flat"
  $b.FlatAppearance.BorderSize = 0
  $b.BackColor = $color
  if ($fg) { $b.ForeColor = $fg } else { $b.ForeColor = [System.Drawing.Color]::White }
  $b.Cursor = "Hand"
  $b.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 10)
  return $b
}

# Row 1: Open Panel | Restart
$openBtn = New-FlatButton "Abrir Painel" 20 140 ([System.Drawing.Color]::FromArgb(59, 130, 246))
$openBtn.Add_Click({ Start-Process $Url })
$form.Controls.Add($openBtn)

$restartBtn = New-FlatButton "Reiniciar Serviço" 230 140 ([System.Drawing.Color]::FromArgb(75, 85, 99))
$restartBtn.Add_Click({
  $restartBtn.Enabled = $false
  if (Invoke-ServiceAction "restart") {
    Start-Sleep -Seconds 1
    Update-Status
  }
  $restartBtn.Enabled = $true
})
$form.Controls.Add($restartBtn)

# Row 2: Start | Stop
$startBtn = New-FlatButton "Iniciar" 20 190 ([System.Drawing.Color]::FromArgb(34, 197, 94))
$startBtn.Add_Click({
  $startBtn.Enabled = $false
  Invoke-ServiceAction "start" | Out-Null
  Start-Sleep -Seconds 1
  Update-Status
  $startBtn.Enabled = $true
})
$form.Controls.Add($startBtn)

$stopBtn = New-FlatButton "Parar (Finalizar)" 230 190 ([System.Drawing.Color]::FromArgb(239, 68, 68))
$stopBtn.Add_Click({
  $stopBtn.Enabled = $false
  Invoke-ServiceAction "stop" | Out-Null
  Start-Sleep -Seconds 1
  Update-Status
  $stopBtn.Enabled = $true
})
$form.Controls.Add($stopBtn)

# Bottom links
$logsLink = New-Object System.Windows.Forms.LinkLabel
$logsLink.Text = "Abrir pasta de logs"
$logsLink.Location = New-Object System.Drawing.Point(20, 248)
$logsLink.Size = New-Object System.Drawing.Size(180, 22)
$logsLink.LinkColor = [System.Drawing.Color]::FromArgb(156, 163, 175)
$logsLink.ActiveLinkColor = [System.Drawing.Color]::FromArgb(229, 231, 235)
$logsLink.Add_LinkClicked({ Start-Process explorer.exe (Join-Path $DataDir "logs") })
$form.Controls.Add($logsLink)

$installLogLink = New-Object System.Windows.Forms.LinkLabel
$installLogLink.Text = "Ver log da instalação"
$installLogLink.Location = New-Object System.Drawing.Point(220, 248)
$installLogLink.Size = New-Object System.Drawing.Size(200, 22)
$installLogLink.LinkColor = [System.Drawing.Color]::FromArgb(156, 163, 175)
$installLogLink.ActiveLinkColor = [System.Drawing.Color]::FromArgb(229, 231, 235)
$installLogLink.Add_LinkClicked({
  $p = Join-Path $DataDir "logs\install.log"
  if (Test-Path $p) { Start-Process notepad.exe $p }
  else { [System.Windows.Forms.MessageBox]::Show("Sem log de instalação em $p", "Aviso") | Out-Null }
})
$form.Controls.Add($installLogLink)

# --- status updater --------------------------------------------------------

function Update-Status {
  $s = Get-ServiceStatus
  $statusValue.Text = $s
  switch ($s) {
    "Running"      { $statusValue.ForeColor = [System.Drawing.Color]::FromArgb(74, 222, 128) }
    "Stopped"      { $statusValue.ForeColor = [System.Drawing.Color]::FromArgb(248, 113, 113) }
    "Paused"       { $statusValue.ForeColor = [System.Drawing.Color]::FromArgb(250, 204, 21) }
    "Não instalado"{ $statusValue.ForeColor = [System.Drawing.Color]::FromArgb(156, 163, 175) }
    default        { $statusValue.ForeColor = [System.Drawing.Color]::FromArgb(250, 204, 21) }
  }
}

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 2000
$timer.Add_Tick({ Update-Status })
$timer.Start()
Update-Status

[void]$form.ShowDialog()
$timer.Stop()
