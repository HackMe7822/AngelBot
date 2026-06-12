@echo off
title AngelBot - Main Monitor
cd /d "%~dp0"

:: UTF-8 code page for Unicode output
chcp 65001 > nul 2>&1

:: Suppress third-party DeprecationWarnings (binance, etc.) across all threads/subprocesses
set PYTHONWARNINGS=ignore::DeprecationWarning,ignore::PendingDeprecationWarning

:: Enable ANSI colour codes in this console window (Windows 10+)
reg add HKCU\Console /v VirtualTerminalLevel /t REG_DWORD /d 1 /f > nul 2>&1

if not exist "logs" mkdir logs

:restart
echo.
echo ============================================================
echo   AngelBot Starting  [%date%  %time%]
echo ============================================================
echo.

python -W ignore::DeprecationWarning main.py

echo.
echo ============================================================
echo   Bot stopped [%date%  %time%]  Exit code: %errorlevel%
echo   Restarting in 15 seconds...  Press Ctrl+C to cancel.
echo ============================================================
timeout /t 15 /nobreak > nul
goto restart
