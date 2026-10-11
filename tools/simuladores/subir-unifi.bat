@echo off
rem Sobe o simulador de gravador UniFi Protect na porta 18083 (so laboratorio).
rem Uso: subir-unifi.bat [porta] [usuario] [senha]
rem Precisa de Python 3 (so a biblioteca padrao). Ctrl+C para parar.
setlocal
set "PORTA=%~1"
if "%PORTA%"=="" set "PORTA=18083"
set "USUARIO=%~2"
if "%USUARIO%"=="" set "USUARIO=admin"
set "SENHA=%~3"
if "%SENHA%"=="" set "SENHA=simulador123"
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%~dp0unifi.py" --porta %PORTA% --usuario %USUARIO% --senha %SENHA%
) else (
  python "%~dp0unifi.py" --porta %PORTA% --usuario %USUARIO% --senha %SENHA%
)
endlocal
