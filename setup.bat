@echo off
title AngelBot Setup
color 0B
cd /d "%~dp0"

echo ============================================================
echo   AngelBot First-Time Setup
echo ============================================================
echo.

echo [1/3] Installing Python dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: pip install failed. Make sure Python is installed.
    echo Download Python from https://python.org/downloads/
    pause
    exit /b 1
)

echo.
echo [2/3] Creating logs folder...
if not exist "logs" mkdir logs
echo Done.

echo.
echo [3/3] Testing bot startup...
python -c "from config import CAPITAL; print('Config OK  Capital: INR', CAPITAL)"
python -c "from data.database import init_db; init_db(); print('Database OK')"
python -c "from data.binance_client import test_connection; ok,msg = test_connection(); print('Binance:', msg)"

echo.
echo ============================================================
echo   Setup complete!
echo.
echo   TO START THE BOT:  double-click run.bat
echo   TO START ON BOOT:  double-click autostart.bat (run as Admin)
echo ============================================================
echo.
pause
