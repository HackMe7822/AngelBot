@echo off
:: AngelBot Installer Launcher
:: Double-click this file to run the installer (handles PowerShell execution policy)
echo Starting AngelBot Installer...
powershell -ExecutionPolicy Bypass -File "%~dp0install.ps1"
pause
