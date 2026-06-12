@echo off
echo Killing any running AngelBot / Python processes...
taskkill /F /IM python.exe /T >nul 2>&1
taskkill /F /IM python3.exe /T >nul 2>&1
echo Done.
echo.
echo You can now start run.bat
pause
