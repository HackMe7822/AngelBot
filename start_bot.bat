@echo off
title AngelBot — Paper Trading
cd /d "%~dp0"

echo ============================================
echo   AngelBot Starting
echo   %date% %time%
echo ============================================

:: Prevent Windows from sleeping while bot runs
powercfg /change standby-timeout-ac 0 >nul 2>&1
powercfg /change hibernate-timeout-ac 0 >nul 2>&1

:: Kill any stale instance first (port 47832 must be free)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :47832 ^| findstr LISTENING') do taskkill /f /pid %%a >nul 2>&1

:: Run bot — output goes to screen AND to log file
python -u main.py 2>&1 | tee logs\angelbot.log

echo.
echo ============================================
echo   Bot stopped at %date% %time%
echo   Check logs\angelbot.log for details
echo ============================================
pause
