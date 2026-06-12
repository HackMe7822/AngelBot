@echo off
title AngelBot AutoStart Setup
color 0E
cd /d "%~dp0"

echo ============================================================
echo   Setting up AngelBot to start automatically on Windows boot
echo   (Requires Administrator rights)
echo ============================================================
echo.

:: Get full path to run.bat and python
set BOT_DIR=%~dp0
set RUN_BAT=%BOT_DIR%run.bat

echo Bot folder: %BOT_DIR%
echo.

:: Create a scheduled task that runs run.bat at login (any user)
schtasks /create ^
  /tn "AngelBot" ^
  /tr "%RUN_BAT%" ^
  /sc ONLOGON ^
  /rl HIGHEST ^
  /f

if %errorlevel% equ 0 (
    echo.
    echo SUCCESS: AngelBot will now start automatically when Windows starts.
    echo.
    echo To remove auto-start later, run:
    echo   schtasks /delete /tn "AngelBot" /f
) else (
    echo.
    echo FAILED: Could not create task. Right-click autostart.bat and
    echo         choose "Run as administrator" then try again.
)

echo.
pause
