@echo off
REM Wrapper called by NSSM. Loads env.cmd and runs the application.
REM Working directory is set by NSSM to the "current" junction.

setlocal
set INSTALL_DIR=%~dp0..\..
set DATA_DIR=%ALLUSERSPROFILE%\MiddlewareMonitor

if exist "%DATA_DIR%\env.cmd" call "%DATA_DIR%\env.cmd"

REM Ensure embedded Python is used and our packages are visible.
"%INSTALL_DIR%\python\python.exe" -m middleware_monitor
exit /b %ERRORLEVEL%
