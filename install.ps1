#Requires -RunAsAdministrator
<#
.SYNOPSIS
    AngelBot One-Click Windows Installer
.DESCRIPTION
    5-screen wizard that sets up everything needed to run AngelBot on Windows:
    Python, SQL Server, IIS, NSSM Windows Services, Cloudflare Tunnel.
    Run: Right-click install.ps1 → Run with PowerShell (as Administrator)
#>

$ErrorActionPreference = "Stop"
$BOT_DIR   = Split-Path -Parent $MyInvocation.MyCommand.Path
$SETUP_DIR = Join-Path $BOT_DIR "prerequisite\setup"
$LOG_FILE  = Join-Path $BOT_DIR "logs\install_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

# Create logs dir
New-Item -ItemType Directory -Force -Path (Join-Path $BOT_DIR "logs") | Out-Null

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts  $msg"
    Write-Host $line
    Add-Content -Path $LOG_FILE -Value $line
}

function Show-Header($title) {
    Clear-Host
    Write-Host ""
    Write-Host "  ╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "  ║         AngelBot  —  Windows Installer                  ║" -ForegroundColor Cyan
    Write-Host "  ║         $title" -NoNewline -ForegroundColor Cyan
    Write-Host (" " * (57 - $title.Length)) -NoNewline
    Write-Host "║" -ForegroundColor Cyan
    Write-Host "  ╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
    Write-Host ""
}

function Prompt-Continue($msg = "Press Enter to continue...") {
    Write-Host ""
    Write-Host "  $msg" -ForegroundColor Yellow
    Read-Host | Out-Null
}

function Check-Mark($label, $ok) {
    $icon = if ($ok) { "  ✅" } else { "  ❌" }
    $col  = if ($ok) { "Green" } else { "Red" }
    Write-Host "$icon  $label" -ForegroundColor $col
}

# ─────────────────────────────────────────────────────────────────────────────
# SCREEN 1 — Prerequisites Check
# ─────────────────────────────────────────────────────────────────────────────
Show-Header "Screen 1 of 5 — Prerequisites Check"
Write-Host "  Checking what's already installed..." -ForegroundColor DarkGray
Write-Host ""

$checks = @{}

# Python
try {
    $pyVer = python --version 2>&1
    $checks["Python"] = $pyVer -match "Python 3"
} catch { $checks["Python"] = $false }

# pip
try {
    $pipOut = pip --version 2>&1
    $checks["pip"] = $pipOut -match "pip"
} catch { $checks["pip"] = $false }

# Git
try {
    $gitOut = git --version 2>&1
    $checks["Git"] = $gitOut -match "git version"
} catch { $checks["Git"] = $false }

# SQL Server instance ANGELBOT
$sqlSvc = Get-Service -Name "MSSQL`$ANGELBOT" -ErrorAction SilentlyContinue
$checks["SQL Server (ANGELBOT)"] = $sqlSvc -ne $null

# IIS
$iisSvc = Get-Service -Name "W3SVC" -ErrorAction SilentlyContinue
$checks["IIS (W3SVC)"] = $iisSvc -ne $null

# NSSM
$nssmPath = Join-Path $SETUP_DIR "nssm\nssm.exe"
$nssmSystem = "C:\Windows\nssm.exe"
$checks["NSSM"] = (Test-Path $nssmPath) -or (Test-Path $nssmSystem)

# Cloudflared
$cfPath = Join-Path $SETUP_DIR "cloudflared\cloudflared.exe"
$checks["Cloudflared"] = Test-Path $cfPath

foreach ($k in $checks.Keys) {
    Check-Mark $k $checks[$k]
}

$missingCritical = (-not $checks["Python"]) -or (-not $checks["pip"])

if ($missingCritical) {
    Write-Host ""
    Write-Host "  ⚠️  Python is required. Download from https://python.org" -ForegroundColor Yellow
    Write-Host "     Make sure to check 'Add Python to PATH' during install." -ForegroundColor Yellow
    Prompt-Continue "Install Python then press Enter to re-check..."
    & $MyInvocation.MyCommand.Path  # re-run
    exit 0
}

Prompt-Continue "Prerequisites OK. Press Enter to install components..."

# ─────────────────────────────────────────────────────────────────────────────
# SCREEN 2 — Auto-Install Components
# ─────────────────────────────────────────────────────────────────────────────
Show-Header "Screen 2 of 5 — Installing Components"

# Python packages
Write-Host "  📦  Installing Python packages..." -ForegroundColor Cyan
$reqFile = Join-Path $BOT_DIR "requirements.txt"
if (Test-Path $reqFile) {
    pip install -r $reqFile --quiet
    Log "Python packages installed from requirements.txt"
    Write-Host "  ✅  Python packages installed" -ForegroundColor Green
} else {
    Write-Host "  ⚠️  requirements.txt not found — skipping pip install" -ForegroundColor Yellow
}

# SQL Server (if not installed)
if (-not $checks["SQL Server (ANGELBOT)"]) {
    Write-Host ""
    Write-Host "  🗄️  SQL Server ANGELBOT not found." -ForegroundColor Yellow
    $saPass = Read-Host "  Enter SA password to use for SQL Server (min 8 chars, uppercase + number)" -AsSecureString
    $saPassPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($saPass)
    )
    $sqlBat = Join-Path $SETUP_DIR "sql\sql_install.bat"
    if (Test-Path $sqlBat) {
        Write-Host "  Installing SQL Server (this takes 3-5 minutes)..." -ForegroundColor Cyan
        Start-Process -FilePath "cmd.exe" -ArgumentList "/c `"$sqlBat`" `"$saPassPlain`"" -Wait -NoNewWindow
        Log "SQL Server installation attempted"
    } else {
        Write-Host "  ⚠️  sql_install.bat not found. Place SQLEXPR_x64_ENU.exe in prerequisite\setup\sql\" -ForegroundColor Yellow
    }
} else {
    Write-Host "  ✅  SQL Server ANGELBOT already installed — skipping" -ForegroundColor Green
}

# IIS
if (-not $checks["IIS (W3SVC)"]) {
    Write-Host ""
    Write-Host "  🌐  Installing IIS..." -ForegroundColor Cyan
    $iisBat = Join-Path $SETUP_DIR "iis\iis_install.bat"
    if (Test-Path $iisBat) {
        Start-Process -FilePath "cmd.exe" -ArgumentList "/c `"$iisBat`"" -Wait -NoNewWindow
        Log "IIS installation attempted"
    }
    # URL Rewrite
    $urMsi = Get-ChildItem -Path (Join-Path $SETUP_DIR "urlrewrite") -Filter "*.msi" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($urMsi) {
        Write-Host "  Installing URL Rewrite..." -ForegroundColor Cyan
        Start-Process -FilePath "msiexec.exe" -ArgumentList "/i `"$($urMsi.FullName)`" /quiet" -Wait
        Log "URL Rewrite installed"
    }
    # ARR
    $arrExe = Get-ChildItem -Path (Join-Path $SETUP_DIR "arr") -Filter "*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($arrExe) {
        Write-Host "  Installing ARR..." -ForegroundColor Cyan
        Start-Process -FilePath $arrExe.FullName -ArgumentList "/quiet" -Wait
        Log "ARR installed"
    }
} else {
    Write-Host "  ✅  IIS already installed — skipping" -ForegroundColor Green
}

# Copy NSSM to system32
$nssmDest = "C:\Windows\nssm.exe"
if (-not (Test-Path $nssmDest)) {
    $nssmSrc = Get-ChildItem -Path (Join-Path $SETUP_DIR "nssm") -Filter "nssm.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($nssmSrc) {
        Copy-Item $nssmSrc.FullName $nssmDest -Force
        Log "NSSM copied to $nssmDest"
        Write-Host "  ✅  NSSM installed" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "  ✅  Component installation complete" -ForegroundColor Green
Prompt-Continue

# ─────────────────────────────────────────────────────────────────────────────
# SCREEN 3 — Database Setup
# ─────────────────────────────────────────────────────────────────────────────
Show-Header "Screen 3 of 5 — Database Setup"
Write-Host "  AngelBot uses SQLite (built-in — no config needed)." -ForegroundColor Cyan
Write-Host "  Database file: $BOT_DIR\angelbot.db" -ForegroundColor DarkGray
Write-Host ""

$dbPath = Join-Path $BOT_DIR "angelbot.db"
if (Test-Path $dbPath) {
    Write-Host "  ✅  Existing database found — keeping all trade history and ML learnings" -ForegroundColor Green
    Write-Host "  ⚠️  NEVER delete angelbot.db or learning\ files!" -ForegroundColor Yellow
} else {
    Write-Host "  Creating fresh database..." -ForegroundColor Cyan
    $pyScript = Join-Path $BOT_DIR "data\database.py"
    python $pyScript
    Log "Database initialized"
    Write-Host "  ✅  Database created" -ForegroundColor Green
}

Prompt-Continue

# ─────────────────────────────────────────────────────────────────────────────
# SCREEN 4 — API Keys & Configuration
# ─────────────────────────────────────────────────────────────────────────────
Show-Header "Screen 4 of 5 — API Keys & Configuration"

$envPath = Join-Path $BOT_DIR ".env"

if (Test-Path $envPath) {
    Write-Host "  ✅  .env file found — API keys already configured" -ForegroundColor Green
    $overwrite = Read-Host "  Re-enter API keys? (y/N)"
    if ($overwrite -ne "y") {
        Prompt-Continue
    }
}

if (-not (Test-Path $envPath) -or $overwrite -eq "y") {
    Write-Host "  Enter your API keys below (leave blank to skip a market):" -ForegroundColor Cyan
    Write-Host ""

    Write-Host "  🇮🇳  ANGEL ONE (India NSE)" -ForegroundColor Yellow
    $angelKey    = Read-Host "     API Key"
    $angelSecret = Read-Host "     Client Secret"
    $angelClient = Read-Host "     Client ID"
    $angelPin    = Read-Host "     PIN (4-digit MPIN)"
    $angelTotp   = Read-Host "     TOTP Secret (from authenticator app)"
    Write-Host ""

    Write-Host "  🇺🇸  ALPACA (US Market — Paper)" -ForegroundColor Yellow
    $alpacaKey    = Read-Host "     API Key (from paper.alpaca.markets)"
    $alpacaSecret = Read-Host "     Secret Key"
    Write-Host ""

    Write-Host "  🪙  BINANCE (Crypto — prices only)" -ForegroundColor Yellow
    $binanceKey    = Read-Host "     API Key"
    $binanceSecret = Read-Host "     Secret Key"
    Write-Host ""

    Write-Host "  📱  TELEGRAM" -ForegroundColor Yellow
    $tgToken  = Read-Host "     Bot Token (from @BotFather)"
    $tgChatId = Read-Host "     Chat ID"
    Write-Host ""

    Write-Host "  🌐  PORTAL LOGIN" -ForegroundColor Yellow
    $portalUser = Read-Host "     Username (default: admin)"
    $portalPass = Read-Host "     Password (default: AngelBot@1234)"
    if (-not $portalUser) { $portalUser = "admin" }
    if (-not $portalPass) { $portalPass = "AngelBot@1234" }

    $envContent = @"
# AngelBot Configuration
# IMPORTANT: Keep this file private — never share or commit

# India — Angel One
ANGEL_API_KEY=$angelKey
ANGEL_SECRET=$angelSecret
ANGEL_CLIENT_ID=$angelClient
ANGEL_PIN=$angelPin
ANGEL_TOTP_SECRET=$angelTotp

# US — Alpaca (paper trading)
ALPACA_KEY=$alpacaKey
ALPACA_SECRET=$alpacaSecret
ALPACA_PAPER=true

# Crypto — Binance (price data only)
BINANCE_KEY=$binanceKey
BINANCE_SECRET=$binanceSecret
BINANCE_PAPER=true

# Telegram
TELEGRAM_BOT_TOKEN=$tgToken
TELEGRAM_CHAT_ID=$tgChatId

# Capital
INDIA_CAPITAL=10000
US_CAPITAL=10000
CRYPTO_CAPITAL=1000

# Safety — ALWAYS true until you manually verify
PAPER_MODE=true

# Portal login
PORTAL_USER=$portalUser
PORTAL_PASS=$portalPass
"@

    $envContent | Out-File -FilePath $envPath -Encoding UTF8 -Force
    Log ".env file written"
    Write-Host "  ✅  .env saved" -ForegroundColor Green
}

Prompt-Continue

# ─────────────────────────────────────────────────────────────────────────────
# SCREEN 5 — Windows Services + Final Setup
# ─────────────────────────────────────────────────────────────────────────────
Show-Header "Screen 5 of 5 — Windows Services"
Write-Host "  Installing AngelBot as Windows Services (auto-start on reboot)..." -ForegroundColor Cyan
Write-Host ""

$pyExe = (Get-Command python -ErrorAction SilentlyContinue)?.Source
if (-not $pyExe) {
    Write-Host "  ❌  Python not found in PATH. Cannot create services." -ForegroundColor Red
    Prompt-Continue "Fix Python PATH then re-run install.ps1"
    exit 1
}

$nssm = if (Test-Path "C:\Windows\nssm.exe") { "C:\Windows\nssm.exe" } else { $null }

if (-not $nssm) {
    Write-Host "  ⚠️  NSSM not found — skipping service installation." -ForegroundColor Yellow
    Write-Host "     Workers can still be started manually with run.bat" -ForegroundColor DarkGray
} else {
    $services = @(
        @{ Name = "AngelBot-India";  Script = "india_worker.py";  Log = "india" },
        @{ Name = "AngelBot-US";     Script = "us_worker.py";     Log = "us" },
        @{ Name = "AngelBot-Crypto"; Script = "crypto_worker.py"; Log = "crypto" },
        @{ Name = "AngelBot-Portal"; Script = "portal_worker.py"; Log = "portal" }
    )

    foreach ($svc in $services) {
        $svcName   = $svc.Name
        $scriptPath = Join-Path $BOT_DIR $svc.Script
        $logPath    = Join-Path $BOT_DIR "logs\$($svc.Log)_service.log"

        # Remove existing service first
        $existing = Get-Service -Name $svcName -ErrorAction SilentlyContinue
        if ($existing) {
            Stop-Service -Name $svcName -Force -ErrorAction SilentlyContinue
            & $nssm remove $svcName confirm 2>$null
        }

        & $nssm install $svcName $pyExe $scriptPath
        & $nssm set $svcName AppDirectory $BOT_DIR
        & $nssm set $svcName AppStdout $logPath
        & $nssm set $svcName AppStderr $logPath
        & $nssm set $svcName AppRotateFiles 1
        & $nssm set $svcName AppRotateBytes 10485760  # 10 MB rotation
        & $nssm set $svcName Start SERVICE_AUTO_START
        Log "Service installed: $svcName"
        Write-Host "  ✅  $svcName installed" -ForegroundColor Green
    }

    Write-Host ""
    Write-Host "  Starting services..." -ForegroundColor Cyan
    foreach ($svc in $services) {
        try {
            Start-Service -Name $svc.Name -ErrorAction SilentlyContinue
            Write-Host "  ✅  $($svc.Name) started" -ForegroundColor Green
        } catch {
            Write-Host "  ⚠️  $($svc.Name) — start manually if needed" -ForegroundColor Yellow
        }
    }
}

# IIS reverse proxy config (route / to :8080)
Write-Host ""
Write-Host "  Configuring IIS reverse proxy to portal (port 8080)..." -ForegroundColor Cyan
$webConfigPath = "C:\inetpub\wwwroot\web.config"
$webConfig = @'
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <system.webServer>
    <rewrite>
      <rules>
        <rule name="AngelBot Portal" stopProcessing="true">
          <match url="(.*)" />
          <action type="Rewrite" url="http://localhost:8080/{R:1}" />
        </rule>
      </rules>
    </rewrite>
  </system.webServer>
</configuration>
'@
$webConfig | Out-File -FilePath $webConfigPath -Encoding UTF8 -Force
Log "IIS web.config written"
Write-Host "  ✅  IIS configured as reverse proxy → localhost:8080" -ForegroundColor Green

# Cloudflared
$cfExe = Join-Path $SETUP_DIR "cloudflared\cloudflared.exe"
if (Test-Path $cfExe) {
    Copy-Item $cfExe "C:\Windows\cloudflared.exe" -Force
    Write-Host ""
    Write-Host "  ☁️  Cloudflare Tunnel (cloudflared) copied to system." -ForegroundColor Cyan
    Write-Host "     To create a permanent tunnel:" -ForegroundColor DarkGray
    Write-Host "       cloudflared tunnel login" -ForegroundColor DarkGray
    Write-Host "       cloudflared tunnel create angelbot" -ForegroundColor DarkGray
    Write-Host "       cloudflared tunnel route dns angelbot yourdomain.com" -ForegroundColor DarkGray
    Write-Host "       cloudflared service install" -ForegroundColor DarkGray
}

# Final summary
Write-Host ""
Write-Host "  ╔══════════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║   ✅  AngelBot Installation Complete!                   ║" -ForegroundColor Green
Write-Host "  ╠══════════════════════════════════════════════════════════╣" -ForegroundColor Green
Write-Host "  ║  Portal  : http://localhost:8080                        ║" -ForegroundColor Green
Write-Host "  ║  via IIS : http://localhost                             ║" -ForegroundColor Green
Write-Host "  ║  Log dir : $BOT_DIR\logs" -ForegroundColor Green
Write-Host "  ║                                                          ║" -ForegroundColor Green
Write-Host "  ║  Manage with:                                            ║" -ForegroundColor Green
Write-Host "  ║    services.msc  → start/stop individual workers        ║" -ForegroundColor Green
Write-Host "  ║    Portal UI     → pause bot, view trades, edit config   ║" -ForegroundColor Green
Write-Host "  ║    Telegram      → /help  /pnl  /positions  /exit SYM   ║" -ForegroundColor Green
Write-Host "  ╚══════════════════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Log "Installation complete."

Start-Process "http://localhost:8080"  # Open portal in browser

Prompt-Continue "Installation complete! Press Enter to exit."
