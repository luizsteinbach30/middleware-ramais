@echo off
rem reset-admin.bat - Middleware USCall Monitor: devolve o login admin / admin.
rem
rem Uso:  reset-admin.bat [caminho\do\app.db]     (ou dois cliques no arquivo)
rem
rem O painel nao tem "esqueci a senha": o usuario e unico e a troca exige a senha
rem atual. Este script grava no banco um hash bcrypt novo (o de "admin"), marca a
rem troca obrigatoria no proximo login e zera o bloqueio de tentativas. Nao precisa
rem de Python nem de nada instalado: usa o winsqlite3.dll do proprio Windows, via
rem PowerShell. O aplicativo ou o servico podem ficar no ar: a senha vale no
rem proximo login.
rem
rem Sem argumento, procura o banco em %LOCALAPPDATA%\MiddlewareMonitor\db\app.db
rem (aplicativo .exe - do usuario do Windows que executa este .bat) e em
rem %ProgramData%\MiddlewareMonitor\db\app.db (servico). Se os dois existirem,
rem passe o caminho do app.db como argumento. Para o servico, execute como
rem administrador.
setlocal
set "MM_DB=%~1"
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$t = Get-Content -LiteralPath '%~f0' -Raw -Encoding UTF8; $m = [char]10 + '#' + '#### PowerShell'; Invoke-Expression $t.Substring($t.IndexOf($m))"
set "RC=%ERRORLEVEL%"
rem Aberto com dois cliques (cmd /c ...)? Segura a janela para dar tempo de ler.
rem Expansao atrasada: o valor pode conter aspas e "&&" sem quebrar o parser.
setlocal EnableDelayedExpansion
set "CMDLINE=!CMDCMDLINE!"
if not "!CMDLINE:/c=!"=="!CMDLINE!" pause
endlocal
exit /b %RC%

##### PowerShell - tudo abaixo desta linha e executado pelo PowerShell, nunca pelo cmd.
$ErrorActionPreference = 'Stop'
# bcrypt de "admin" (custo 12): o mesmo formato que o painel grava.
$Hash = '$2b$12$mondvXbpzATNV2q.BLFFBuyh2G65fg8kL8vooEihB6MWiJq5G8y0i'

function Fail([string]$msg) { Write-Host "ERRO: $msg" -ForegroundColor Red; exit 2 }

$Db = $env:MM_DB
if (-not $Db) {
  $candidatos = @(
    (Join-Path $env:LOCALAPPDATA 'MiddlewareMonitor\db\app.db'),
    (Join-Path $env:ProgramData  'MiddlewareMonitor\db\app.db')
  ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
  if (@($candidatos).Count -eq 0) {
    Fail 'banco não encontrado em %LOCALAPPDATA%\MiddlewareMonitor nem em %ProgramData%\MiddlewareMonitor. Passe o caminho do app.db como argumento.'
  }
  if (@($candidatos).Count -gt 1) {
    Fail ("dois bancos encontrados:`n  " + ($candidatos -join "`n  ") + "`nPasse o caminho do app.db certo como argumento.")
  }
  $Db = @($candidatos)[0]
}
if (-not (Test-Path -LiteralPath $Db -PathType Leaf)) { Fail "banco não encontrado: $Db" }
$Db = (Resolve-Path -LiteralPath $Db).ProviderPath

# SQLite que já vem com o Windows 10/11 (System32\winsqlite3.dll).
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class WinSqlite3 {
    [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
    public static extern int sqlite3_open16([MarshalAs(UnmanagedType.LPWStr)] string filename, out IntPtr db);
    [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
    public static extern int sqlite3_busy_timeout(IntPtr db, int ms);
    [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
    public static extern int sqlite3_exec(IntPtr db, [MarshalAs(UnmanagedType.LPStr)] string sql, IntPtr callback, IntPtr arg, out IntPtr errmsg);
    [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
    public static extern int sqlite3_changes(IntPtr db);
    [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
    public static extern IntPtr sqlite3_errmsg16(IntPtr db);
    [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
    public static extern int sqlite3_close_v2(IntPtr db);
    [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
    public static extern void sqlite3_free(IntPtr p);
}
'@

$h = [IntPtr]::Zero
$rc = [WinSqlite3]::sqlite3_open16($Db, [ref]$h)
if ($rc -ne 0) { Fail "não abriu o banco (SQLite $rc): $Db" }
[void][WinSqlite3]::sqlite3_busy_timeout($h, 5000)

function Sql([string]$sql) {
  $err = [IntPtr]::Zero
  $rc = [WinSqlite3]::sqlite3_exec($h, $sql, [IntPtr]::Zero, [IntPtr]::Zero, [ref]$err)
  if ($err -ne [IntPtr]::Zero) { [WinSqlite3]::sqlite3_free($err) }
  if ($rc -ne 0) {
    $msg = [Runtime.InteropServices.Marshal]::PtrToStringUni([WinSqlite3]::sqlite3_errmsg16($h))
    [void][WinSqlite3]::sqlite3_close_v2($h)
    if ($rc -eq 8) { $msg += ' (banco somente leitura: execute como administrador, no caso do serviço, ou com o usuário do Windows dono do aplicativo)' }
    Fail "SQLite ($rc): $msg"
  }
}

Write-Host "Banco: $Db"
Sql 'BEGIN IMMEDIATE'
Sql "UPDATE users SET password_hash='$Hash', must_change_password=1, failed_login_count=0, locked_until=NULL WHERE username='admin'"
if ([WinSqlite3]::sqlite3_changes($h) -eq 0) {
  Sql "INSERT INTO users (username, password_hash, role, must_change_password, failed_login_count, created_at) VALUES ('admin', '$Hash', 'admin', 1, 0, strftime('%Y-%m-%d %H:%M:%f', 'now'))"
  $acao = 'criado'
} else {
  $acao = 'redefinido'
}
Sql 'DELETE FROM login_attempts WHERE success=0'
Sql 'COMMIT'
[void][WinSqlite3]::sqlite3_close_v2($h)

Write-Host "Usuário admin $acao." -ForegroundColor Green
Write-Host 'Login: admin / admin. A troca de senha é obrigatória no próximo acesso'
Write-Host '(mínimo 12 caracteres, com letras e números). Faça o login agora.'
exit 0
