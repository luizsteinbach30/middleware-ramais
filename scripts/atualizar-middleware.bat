@echo off
rem atualizar-middleware.bat - Middleware USCall Monitor: instala a ultima versao a mao.
rem
rem Uso:  atualizar-middleware.bat [versao]      (ou dois cliques no arquivo)
rem
rem Para quem esta na 2.14.1 ou antes: o atualizador dessas versoes abre janelas
rem do cmd em laco (corrigido na 2.14.2). Este script faz a troca uma vez, a mao;
rem dali em diante o middleware se atualiza sozinho pelo NOC.
rem
rem O que ele faz: acha o MiddlewareMonitor*.exe que esta rodando (ou pergunta o
rem caminho), baixa o .exe da ultima release publicada no GitHub (ou da versao
rem pedida), confere o SHA256 pelo SHA256SUMS da release, fecha o aplicativo,
rem guarda o atual como .exe.bak, troca e abre a versao nova. Os dados ficam onde
rem estao (%LOCALAPPDATA%\MiddlewareMonitor). Se a versao nova nao responder em
rem 150 s, volta o .bak.
rem
rem Execute com o mesmo usuario do Windows que usa o middleware.
setlocal
set "MM_VERSAO=%~1"
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$t = Get-Content -LiteralPath '%~f0' -Raw -Encoding UTF8; $m = [char]10 + '#' + '#### PowerShell'; Invoke-Expression $t.Substring($t.IndexOf($m))"
set "RC=%ERRORLEVEL%"
rem Aberto com dois cliques (cmd /c ...)? Segura a janela para dar tempo de ler.
setlocal EnableDelayedExpansion
set "CMDLINE=!CMDCMDLINE!"
if not "!CMDLINE:/c=!"=="!CMDLINE!" pause
endlocal
exit /b %RC%

##### PowerShell - tudo abaixo desta linha e executado pelo PowerShell, nunca pelo cmd.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$repo = 'luizsteinbach30/middleware-ramais'

function Sair([int]$codigo, [string]$texto) {
  if ($codigo -eq 0) { Write-Host $texto -ForegroundColor Green } else { Write-Host $texto -ForegroundColor Red }
  exit $codigo
}

try {
  $rodando = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and (Split-Path -Leaf $_.Path) -like 'MiddlewareMonitor*.exe' })
  $caminhos = @($rodando | ForEach-Object { $_.Path } | Sort-Object -Unique)
  if ($caminhos.Count -gt 1) { Sair 2 ("Ha mais de um middleware rodando: " + ($caminhos -join ', ') + '. Feche os que sobram e rode de novo.') }
  if ($caminhos.Count -eq 1) {
    $atual = $caminhos[0]
  } else {
    Write-Host 'O middleware nao esta aberto. Informe o caminho do MiddlewareMonitor .exe (ex.: C:\Middleware\MiddlewareMonitor-2.14.1.exe):'
    $atual = (Read-Host 'Caminho').Trim('"', ' ')
    if (-not (Test-Path -LiteralPath $atual)) { Sair 2 "Arquivo nao encontrado: $atual" }
  }
  Write-Host "Executavel atual: $atual"

  $api = "https://api.github.com/repos/$repo/releases/" + $(if ($env:MM_VERSAO) { 'tags/v' + $env:MM_VERSAO.TrimStart('v') } else { 'latest' })
  $release = Invoke-RestMethod -UseBasicParsing -Uri $api -Headers @{ 'User-Agent' = 'atualizar-middleware' }
  $versao = $release.tag_name.TrimStart('v')
  $exe = $release.assets | Where-Object { $_.name -like 'MiddlewareMonitor*.exe' } | Select-Object -First 1
  $somas = $release.assets | Where-Object { $_.name -eq 'SHA256SUMS' } | Select-Object -First 1
  if (-not $exe -or -not $somas) { Sair 3 "A release $versao ainda nao tem o .exe e o SHA256SUMS (o build pode estar subindo). Tente em alguns minutos." }
  Write-Host "Versao a instalar: $versao"

  $tmp = Join-Path $env:TEMP "mm-atualizar-$versao"
  New-Item -ItemType Directory -Force -Path $tmp | Out-Null
  $novo = Join-Path $tmp $exe.name
  Write-Host 'Baixando...'
  Invoke-WebRequest -UseBasicParsing -Uri $exe.browser_download_url -OutFile $novo
  Invoke-WebRequest -UseBasicParsing -Uri $somas.browser_download_url -OutFile (Join-Path $tmp 'SHA256SUMS')
  $linha = Get-Content (Join-Path $tmp 'SHA256SUMS') | Where-Object { $_ -match [regex]::Escape($exe.name) } | Select-Object -First 1
  if (-not $linha) { Sair 4 "O SHA256SUMS nao lista $($exe.name)." }
  $esperado = ($linha -split '\s+')[0].ToLower()
  $obtido = (Get-FileHash -Algorithm SHA256 -LiteralPath $novo).Hash.ToLower()
  if ($esperado -ne $obtido) { Sair 4 "SHA256 nao confere ($obtido, esperado $esperado). Nada foi trocado." }
  Write-Host 'SHA256 conferido.'

  Write-Host 'Fechando o middleware...'
  $prazo = (Get-Date).AddSeconds(30)
  Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $atual } | ForEach-Object { $_.CloseMainWindow() | Out-Null }
  while ((Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $atual }) -and (Get-Date) -lt $prazo) { Start-Sleep -Milliseconds 500 }
  Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $atual } | Stop-Process -Force
  Start-Sleep -Seconds 2

  $bak = "$atual.bak"
  Copy-Item -LiteralPath $atual -Destination $bak -Force
  $trocou = $false
  for ($i = 0; $i -lt 10 -and -not $trocou; $i++) {
    try { Copy-Item -LiteralPath $novo -Destination $atual -Force; $trocou = $true } catch { Start-Sleep -Seconds 1 }
  }
  if (-not $trocou) {
    Start-Process -FilePath $atual -WorkingDirectory (Split-Path -Parent $atual) | Out-Null
    Sair 5 'Nao foi possivel trocar o executavel (arquivo em uso). O middleware anterior foi reaberto.'
  }
  Start-Process -FilePath $atual -WorkingDirectory (Split-Path -Parent $atual) | Out-Null
  Write-Host 'Abrindo a versao nova e conferindo...'
  $prazo = (Get-Date).AddSeconds(150)
  while ((Get-Date) -lt $prazo) {
    Start-Sleep -Seconds 5
    try {
      $v = [string](Invoke-RestMethod -UseBasicParsing -TimeoutSec 4 -Uri 'http://127.0.0.1:8080/api/system/healthz').version
      if ($v -eq $versao) { Sair 0 "Pronto: middleware $versao no ar. Dali em diante ele se atualiza pelo NOC." }
    } catch { }
  }
  Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $atual } | Stop-Process -Force
  Start-Sleep -Seconds 3
  Copy-Item -LiteralPath $bak -Destination $atual -Force
  Start-Process -FilePath $atual -WorkingDirectory (Split-Path -Parent $atual) | Out-Null
  Sair 6 "A versao $versao nao respondeu em 150 s na porta 8080; a anterior foi reaberta. Mande este texto ao suporte."
} catch {
  Sair 1 ("Falhou: " + $_.Exception.Message)
}
