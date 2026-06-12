@echo off
title AngelBot — Status Check

echo Checking if AngelBot is running...
echo.

tasklist /fi "imagename eq python.exe" /fo list | findstr /i "python" >nul
if %errorlevel% == 0 (
    echo STATUS: RUNNING
    echo.
    tasklist /fi "imagename eq python.exe" /fo list
) else (
    echo STATUS: NOT RUNNING
    echo.
    echo Start it with start_bot.bat
)

echo.
echo === Last 30 lines of log ===
if exist logs\angelbot.log (
    powershell -command "Get-Content logs\angelbot.log -Tail 30"
) else (
    echo No log file yet.
)

pause
