@echo off
:: Run this ONCE as Administrator to register AngelBot as a scheduled task.
:: After this, the bot starts automatically on login and survives screen lock.

echo Setting up AngelBot auto-start...

:: Delete old task if it exists
schtasks /delete /tn "AngelBot" /f >nul 2>&1

:: Create logs folder if missing
if not exist "%~dp0logs" mkdir "%~dp0logs"

:: Register task: runs at login, stays running, no time limit
schtasks /create ^
  /tn "AngelBot" ^
  /tr "wscript.exe \"%~dp0start_bot_hidden.vbs\"" ^
  /sc ONLOGON ^
  /ru "%USERNAME%" ^
  /rl HIGHEST ^
  /f

if %errorlevel% == 0 (
    echo.
    echo SUCCESS — AngelBot will now auto-start every time you log in.
    echo The bot runs hidden in the background. Logs saved to logs\angelbot.log
    echo.
    echo To start it right now without rebooting, run start_bot.bat
) else (
    echo.
    echo FAILED — try right-clicking setup_autostart.bat and choosing "Run as administrator"
)

:: Disable sleep and hibernate on AC power permanently
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
echo Power sleep disabled on AC power.

pause
