#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

$BOT_DIR  = "C:\AngelBot"
$LOG_FILE = "$BOT_DIR\logs\install.log"

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    New-Item -ItemType Directory -Force -Path "$BOT_DIR\logs" | Out-Null
    Add-Content -Path $LOG_FILE -Value "$ts  $msg" -ErrorAction SilentlyContinue
}
function Info($msg) { Write-Host "  $msg" -ForegroundColor Cyan    ; Log $msg }
function OK($msg)   { Write-Host "  ✅  $msg" -ForegroundColor Green ; Log "OK: $msg" }
function Warn($msg) { Write-Host "  ⚠️   $msg" -ForegroundColor Yellow; Log "WARN: $msg" }
function Step($n, $title) {
    Write-Host ""
    Write-Host "  ── Step $n/5  $title " -ForegroundColor White -NoNewline
    Write-Host ("─" * [Math]::Max(0, 46 - $title.Length)) -ForegroundColor DarkGray
}
function Download($url, $dest) {
    if (Test-Path $dest) { return }
    Info "Downloading $(Split-Path $dest -Leaf)..."
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
}
function Update-EnvPath {
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH","User")
}

# ── Banner ───────────────────────────────────────────────────────────────────
Clear-Host
Write-Host ""
Write-Host "  ╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║         AngelBot  —  One-Click Installer                ║" -ForegroundColor Cyan
Write-Host "  ║         Installs everything automatically               ║" -ForegroundColor Cyan
Write-Host "  ╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

$tmp = "$env:TEMP\angelbot_setup"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
Log "Installer started"

# ─────────────────────────────────────────────────────────────────────────────
# 1 — Python
# ─────────────────────────────────────────────────────────────────────────────
Step 1 "Installing Python 3.11"
$pyOK = $false
try { $pyOK = ((python --version 2>&1) -match "Python 3\.(9|10|11|12)") } catch {}

if (-not $pyOK) {
    $exe = "$tmp\python-3.11.9-amd64.exe"
    Download "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" $exe
    Info "Installing Python 3.11 (~1 min)..."
    Start-Process $exe -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 Include_test=0" -Wait -NoNewWindow
    Update-EnvPath
    try { $pyOK = ((python --version 2>&1) -match "Python 3") } catch {}
    if (-not $pyOK) { Write-Host "  ❌  Python install failed. See $LOG_FILE" -ForegroundColor Red; Read-Host; exit 1 }
    OK "Python 3.11 installed"
} else {
    OK "Python already installed — $((python --version 2>&1))"
}

# ─────────────────────────────────────────────────────────────────────────────
# 2 — Git
# ─────────────────────────────────────────────────────────────────────────────
Step 2 "Installing Git"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    $exe = "$tmp\git-installer.exe"
    Download "https://github.com/git-for-windows/git/releases/download/v2.45.2.windows.1/Git-2.45.2-64-bit.exe" $exe
    Info "Installing Git silently..."
    Start-Process $exe -ArgumentList "/VERYSILENT /NORESTART /NOCANCEL /SP- /CLOSEAPPLICATIONS" -Wait -NoNewWindow
    Update-EnvPath
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Write-Host "  ❌  Git install failed." -ForegroundColor Red; Read-Host; exit 1 }
    OK "Git installed"
} else { OK "Git already installed" }

# ─────────────────────────────────────────────────────────────────────────────
# 3 — Clone / update bot (includes db + learning files)
# ─────────────────────────────────────────────────────────────────────────────
Step 3 "Downloading AngelBot"
if (Test-Path "$BOT_DIR\.git") {
    Info "Updating to latest version..."
    git -C $BOT_DIR pull
    OK "Updated to latest"
} else {
    Info "Cloning from GitHub (includes DB and ML model)..."
    git clone https://github.com/HackMe7822/AngelBot.git $BOT_DIR
    OK "Downloaded to $BOT_DIR"
}
New-Item -ItemType Directory -Force -Path "$BOT_DIR\logs" | Out-Null

# ─────────────────────────────────────────────────────────────────────────────
# 4 — API Keys
# ─────────────────────────────────────────────────────────────────────────────
Step 4 "API Keys"
if (Test-Path "$BOT_DIR\.env") {
    OK ".env already exists — skipping"
} else {
    Write-Host ""
    Write-Host "  Enter your API keys. Bot runs in PAPER mode until you change it." -ForegroundColor White
    Write-Host ""
    Write-Host "  🇮🇳  Angel One (India NSE)" -ForegroundColor Yellow
    $aKey    = Read-Host "     API Key"
    $aSecret = Read-Host "     Client Secret"
    $aClient = Read-Host "     Client ID"
    $aPin    = Read-Host "     MPIN (4-digit)"
    $aTotp   = Read-Host "     TOTP Secret"
    Write-Host "  🇺🇸  Alpaca (US — paper trading)" -ForegroundColor Yellow
    $alKey    = Read-Host "     API Key"
    $alSecret = Read-Host "     Secret Key"
    Write-Host "  🪙  Binance (Crypto — price data only)" -ForegroundColor Yellow
    $bKey    = Read-Host "     API Key"
    $bSecret = Read-Host "     Secret Key"
    Write-Host "  📱  Telegram" -ForegroundColor Yellow
    $tgToken  = Read-Host "     Bot Token"
    $tgChatId = Read-Host "     Chat ID"
    Write-Host "  🌐  Portal Login" -ForegroundColor Yellow
    $pUser = Read-Host "     Username  (Enter = admin)"
    $pPass = Read-Host "     Password  (Enter = AngelBot@1234)"
    if (-not $pUser) { $pUser = "admin" }
    if (-not $pPass) { $pPass = "AngelBot@1234" }

    @"
ANGEL_API_KEY=$aKey
ANGEL_SECRET=$aSecret
ANGEL_CLIENT_ID=$aClient
ANGEL_PIN=$aPin
ANGEL_TOTP_SECRET=$aTotp
ALPACA_KEY=$alKey
ALPACA_SECRET=$alSecret
ALPACA_PAPER=true
BINANCE_KEY=$bKey
BINANCE_SECRET=$bSecret
BINANCE_PAPER=true
TELEGRAM_BOT_TOKEN=$tgToken
TELEGRAM_CHAT_ID=$tgChatId
INDIA_CAPITAL=10000
US_CAPITAL=10000
CRYPTO_CAPITAL=1000
PAPER_MODE=true
PORTAL_USER=$pUser
PORTAL_PASS=$pPass
"@ | Out-File "$BOT_DIR\.env" -Encoding UTF8 -Force
    OK ".env saved"
}

# ─────────────────────────────────────────────────────────────────────────────
# 5 — Python packages + NSSM + Services
# ─────────────────────────────────────────────────────────────────────────────
Step 5 "Installing packages and Windows Services"

Info "Installing Python packages (2-4 min)..."
python -m pip install --upgrade pip --quiet
python -m pip install -r "$BOT_DIR\requirements.txt" --quiet
OK "Python packages installed"

# NSSM
$nssmDest = "C:\Windows\nssm.exe"
if (-not (Test-Path $nssmDest)) {
    Info "Downloading NSSM..."
    $zip = "$tmp\nssm.zip"
    Download "https://nssm.cc/ci/nssm-2.24-103-gdee49fc.zip" $zip
    Expand-Archive $zip "$tmp\nssm_ext" -Force
    $nssmExe = Get-ChildItem "$tmp\nssm_ext" -Filter "nssm.exe" -Recurse |
               Where-Object { $_.FullName -match "win64" } | Select-Object -First 1
    if (-not $nssmExe) {
        $nssmExe = Get-ChildItem "$tmp\nssm_ext" -Filter "nssm.exe" -Recurse | Select-Object -First 1
    }
    Copy-Item $nssmExe.FullName $nssmDest -Force
    OK "NSSM installed"
}

Update-EnvPath
$pyExe = (Get-Command python).Source

$services = @(
    @{ Name="AngelBot-India";  Script="india_worker.py"  },
    @{ Name="AngelBot-US";     Script="us_worker.py"     },
    @{ Name="AngelBot-Crypto"; Script="crypto_worker.py" },
    @{ Name="AngelBot-Portal"; Script="portal_worker.py" }
)

foreach ($svc in $services) {
    $name   = $svc.Name
    $script = "$BOT_DIR\$($svc.Script)"
    $log    = "$BOT_DIR\logs\$name.log"

    $existing = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($existing) {
        Stop-Service -Name $name -Force -ErrorAction SilentlyContinue
        & $nssmDest remove $name confirm 2>$null
        Start-Sleep -Seconds 1
    }

    & $nssmDest install         $name $pyExe $script
    & $nssmDest set $name AppDirectory   $BOT_DIR
    & $nssmDest set $name AppStdout      $log
    & $nssmDest set $name AppStderr      $log
    & $nssmDest set $name AppRotateFiles 1
    & $nssmDest set $name AppRotateBytes 10485760
    & $nssmDest set $name Start          SERVICE_AUTO_START
    & $nssmDest set $name AppThrottle    5000

    Start-Service -Name $name -ErrorAction SilentlyContinue
    $st = (Get-Service -Name $name -ErrorAction SilentlyContinue)?.Status
    if ($st -eq "Running") { OK "$name — running" } else { Warn "$name — installed (starting...)" }
}

New-NetFirewallRule -DisplayName "AngelBot Portal" -Direction Inbound `
    -Protocol TCP -LocalPort 8080 -Action Allow -ErrorAction SilentlyContinue | Out-Null

# ── Done ─────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ╔══════════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║   ✅  AngelBot is installed and running!                ║" -ForegroundColor Green
Write-Host "  ╠══════════════════════════════════════════════════════════╣" -ForegroundColor Green
Write-Host "  ║  Open portal →  http://localhost:8080                   ║" -ForegroundColor Green
Write-Host "  ║  View logs   →  C:\AngelBot\logs\                       ║" -ForegroundColor Green
Write-Host "  ║  Services    →  services.msc  (search AngelBot)         ║" -ForegroundColor Green
Write-Host "  ║                                                          ║" -ForegroundColor Green
Write-Host "  ║  Bot starts automatically on every reboot.              ║" -ForegroundColor Green
Write-Host "  ╚══════════════════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Log "Installation complete"

Start-Sleep -Seconds 3
Start-Process "http://localhost:8080"
Write-Host "  Press Enter to close..." -ForegroundColor DarkGray
Read-Host | Out-Null
