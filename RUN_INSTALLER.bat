@echo off
:: AngelBot One-Click Installer
:: Just double-click this file as Administrator — downloads everything automatically

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Please right-click this file and choose "Run as administrator"
    pause
    exit /b 1
)

echo Launching AngelBot Installer...

:: If install.ps1 is in same folder, use it. Otherwise download from GitHub.
if exist "%~dp0install.ps1" (
    powershell -ExecutionPolicy Bypass -File "%~dp0install.ps1"
) else (
    echo install.ps1 not found locally - downloading from GitHub...
    powershell -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/HackMe7822/AngelBot/main/install.ps1' -OutFile '%TEMP%\angelbot_install.ps1' -UseBasicParsing; & '%TEMP%\angelbot_install.ps1'"
)

pause
