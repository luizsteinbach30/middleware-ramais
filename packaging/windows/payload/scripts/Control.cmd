@echo off
REM Launches the WinForms control panel via PowerShell hidden window.
REM Used by the Start Menu / Desktop shortcuts.
start "" /MIN powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0Control.ps1"
