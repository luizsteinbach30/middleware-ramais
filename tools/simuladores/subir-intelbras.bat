@echo off
rem Sobe o simulador de gravador Intelbras/Dahua na porta 18081 (so laboratorio).
rem Uso: subir-intelbras.bat [porta] [usuario] [senha]
rem Precisa de Python 3 (so a biblioteca padrao). Ctrl+C para parar.
setlocal
set "PORTA=%~1"
if "%PORTA%"=="" set "PORTA=18081"
set "USUARIO=%~2"
if "%USUARIO%"=="" set "USUARIO=admin"
set "SENHA=%~3"
if "%SENHA%"=="" set "SENHA=simulador123"
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%~dp0intelbras.py" --porta %PORTA% --usuario %USUARIO% --senha %SENHA%
) else (
  python "%~dp0intelbras.py" --porta %PORTA% --usuario %USUARIO% --senha %SENHA%
)
endlocal
