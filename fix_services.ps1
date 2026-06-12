# AngelBot Service Fix
# Right-click -> Run as Administrator

$BOT_DIR = "C:\AngelBot"

function OK   { param($m) Write-Host "  [OK]  $m" -ForegroundColor Green }
function Info { param($m) Write-Host "  [..] $m"  -ForegroundColor Cyan }
function Warn { param($m) Write-Host "  [!!] $m"  -ForegroundColor Yellow }
function Fail { param($m) Write-Host "  [XX] $m"  -ForegroundColor Red }


Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "   AngelBot Full Service Fix Script   " -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

# ── 1. Find Python (needed early for patching) ────────────────────────────────
Info "Locating Python executable..."
$pyReal = $null
try { $pyReal = (& python -c "import sys; print(sys.executable)" 2>$null).Trim() } catch {}
if ($pyReal -and ($pyReal -match "WindowsApps")) { $pyReal = $null }
if (-not $pyReal) {
    foreach ($c in @(
        "C:\Program Files\Python313\python.exe","C:\Program Files\Python312\python.exe",
        "C:\Program Files\Python311\python.exe","C:\Program Files\Python310\python.exe",
        "C:\Python313\python.exe","C:\Python312\python.exe",
        "C:\Python311\python.exe","C:\Python310\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe"
    )) { if (Test-Path $c) { $pyReal = $c; break } }
}
if (-not $pyReal) { Fail "Python not found. Run the main installer first."; pause; exit 1 }
OK "Python: $pyReal"

# ── 2. Pull latest code from GitHub ──────────────────────────────────────────
Info "Pulling latest code from GitHub..."
$oldPref = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$null = git -C $BOT_DIR fetch --all 2>&1
$resetOut = git -C $BOT_DIR reset --hard origin/main 2>&1
$gitOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $oldPref
if ($gitOk) { OK "Code up to date" }
else { Warn "git had issues: $resetOut" }

# ── 3. Python patch (fix SQLite SQL that breaks SQL Server) ───────────────────
# Idempotent — already-fixed files are unchanged.
$patchPy = @'
import os, re
ROOT = r'C:\AngelBot'
FILES = [
    r'trading\paper_trader.py',
    r'trading\alpaca_trader.py',
    r'trading\crypto_trader.py',
    r'reporting\excel_report.py',
    r'reporting\telegram_listener.py',
]

def patch(path):
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        return
    with open(full, encoding='utf-8') as f:
        txt = f.read()
    orig = txt
    txt = txt.replace('date(exit_time)', 'TRY_CAST(TRY_CAST(exit_time AS DATETIME2) AS DATE)')
    txt = txt.replace('date(time)',      'TRY_CAST(TRY_CAST(time AS DATETIME2) AS DATE)')
    if 'c.lastrowid' in txt:
        txt = txt.replace('trade_id = c.lastrowid', 'trade_id = c.fetchone()[0]')
        # Add OUTPUT INSERTED.id before VALUES — handle Windows \r\n and multi-line INSERT
        txt = re.sub(
            r'(status(?:, source)?)\)\s*\r?\n(\s+)VALUES',
            lambda m: m.group(1) + ')\n' + m.group(2) + 'OUTPUT INSERTED.id\n' + m.group(2) + 'VALUES',
            txt
        )
    if txt != orig:
        with open(full, 'w', encoding='utf-8') as f:
            f.write(txt)
        print('Patched ' + path)

for p in FILES:
    try:
        patch(p)
    except Exception as e:
        print('Patch error ' + p + ': ' + str(e))
'@

$patchFile = "$env:TEMP\ab_patch.py"
$patchPy | Set-Content $patchFile -Encoding UTF8
$patchOut = & $pyReal $patchFile 2>&1
if ($patchOut) {
    foreach ($line in ($patchOut -split "`n")) {
        $line = $line.Trim()
        if ($line -match "^Patched")    { OK $line }
        elseif ($line -match "error")   { Warn $line }
    }
} else { OK "Python files already up to date" }
Remove-Item $patchFile -ErrorAction SilentlyContinue

# ── 4. ODBC Driver 17 check ───────────────────────────────────────────────────
$odbcRegKey = "HKLM:\SOFTWARE\ODBC\ODBCINST.INI\ODBC Driver 17 for SQL Server"
if (Test-Path $odbcRegKey) {
    OK "ODBC Driver 17 installed"
} else {
    Warn "ODBC Driver 17 not found. Install from https://aka.ms/odbc17 if workers fail to connect."
}

# ── 5. SQL Server SA + database ───────────────────────────────────────────────
$envFile    = "$BOT_DIR\.env"
$envContent = if (Test-Path $envFile) { Get-Content $envFile -Raw } else { "" }
$saPass = ""
if ($envContent -match "SQL_SA_PASS=(.+)") { $saPass = $Matches[1].Trim() }

function Test-SALogin($pass) {
    $null = & sqlcmd -S ".\ANGELBOT" -U sa -P $pass -Q "SELECT 1" 2>&1
    return ($LASTEXITCODE -eq 0)
}
function Fix-SALogin($newPass) {
    Info "Using Windows auth to enable SA..."
    $null = & sqlcmd -S ".\ANGELBOT" -E -Q "ALTER LOGIN sa ENABLE; ALTER LOGIN sa WITH PASSWORD='$newPass';" 2>&1
    return ($LASTEXITCODE -eq 0)
}

$sqlcmdPath = Get-Command sqlcmd -ErrorAction SilentlyContinue
if (-not $sqlcmdPath) {
    foreach ($p in @(
        "C:\Program Files\Microsoft SQL Server\Client SDK\ODBC\170\Tools\Binn\sqlcmd.exe",
        "C:\Program Files\Microsoft SQL Server\150\Tools\Binn\sqlcmd.exe",
        "C:\Program Files\Microsoft SQL Server\140\Tools\Binn\sqlcmd.exe",
        "C:\Program Files\Microsoft SQL Server\130\Tools\Binn\sqlcmd.exe"
    )) { if (Test-Path $p) { $env:PATH += ";$(Split-Path $p)"; break } }
}

$saOk = $false
if ($saPass) {
    Info "Testing SQL Server SA login..."
    $saOk = Test-SALogin $saPass
    if ($saOk) { OK "SA login works" }
    else        { Warn "SA login failed with saved password — will reset" }
}

if (-not $saOk) {
    Write-Host ""
    Write-Host "  SQL Server SA login is not working." -ForegroundColor Yellow
    Write-Host "  Set a new SA password (min 8 chars, uppercase + number):" -ForegroundColor White
    $saSecure = Read-Host "  New SA Password" -AsSecureString
    $saPass   = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($saSecure))
    $null = Fix-SALogin $saPass
    Start-Sleep 2
    $saOk = Test-SALogin $saPass
    if ($saOk) { OK "SA login works" }
    else        { Warn "SA login still failing — workers may not connect" }
    if ($envContent -match "SQL_SA_PASS=") {
        $envContent = $envContent -replace "SQL_SA_PASS=.*(\r?\n|$)", "SQL_SA_PASS=$saPass`n"
        Set-Content $envFile $envContent.TrimEnd() -Encoding ASCII
    } else {
        Add-Content $envFile "`nSQL_SA_PASS=$saPass" -Encoding ASCII
    }
    OK "SQL_SA_PASS saved to .env"
}

Info "Ensuring 'angelbot' database exists..."
$createDb = "IF NOT EXISTS (SELECT name FROM sys.databases WHERE name='angelbot') CREATE DATABASE angelbot;"
$null = & sqlcmd -S ".\ANGELBOT" -E -Q $createDb 2>&1
if ($LASTEXITCODE -eq 0) { OK "Database 'angelbot' ready" }
elseif ($saOk) {
    $null = & sqlcmd -S ".\ANGELBOT" -U sa -P $saPass -Q $createDb 2>&1
    OK "Database 'angelbot' created via SA"
} else { Warn "Could not create database" }

# ── 6. Install Python packages ────────────────────────────────────────────────
Info "Installing Python packages..."
$null = & $pyReal -m pip install pyodbc --quiet 2>&1
$null = & $pyReal -m pip install -r "$BOT_DIR\requirements.txt" --quiet 2>&1
OK "Packages ready"

# ── 7. Create DB tables via sqlcmd ────────────────────────────────────────────
Info "Creating database tables..."
$fixSql = @"
USE angelbot;
IF EXISTS (SELECT * FROM sys.columns WHERE object_id=OBJECT_ID('signal_performance') AND name='signal_name' AND max_length=-1) DROP TABLE signal_performance;
IF EXISTS (SELECT * FROM sys.columns WHERE object_id=OBJECT_ID('monitor_state') AND name='market' AND max_length=-1) DROP TABLE monitor_state;
IF EXISTS (SELECT * FROM sys.columns WHERE object_id=OBJECT_ID('monitor_state') AND name='key') DROP TABLE monitor_state;
"@
$null = & sqlcmd -S ".\ANGELBOT" -U sa -P $saPass -Q $fixSql 2>&1

$schemaSql = @"
USE angelbot;
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='trades')
CREATE TABLE trades (
    id INT IDENTITY(1,1) PRIMARY KEY, symbol NVARCHAR(500),
    entry_time NVARCHAR(50), exit_time NVARCHAR(50),
    entry_price FLOAT, exit_price FLOAT, quantity FLOAT, capital_used FLOAT,
    pnl FLOAT, pnl_pct FLOAT, stop_loss FLOAT, target FLOAT,
    exit_reason NVARCHAR(500), signals NVARCHAR(MAX),
    status NVARCHAR(50) DEFAULT 'open', source NVARCHAR(50) DEFAULT 'paper'
);
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='top_ups')
CREATE TABLE top_ups (
    id INT IDENTITY(1,1) PRIMARY KEY, amount FLOAT,
    reason NVARCHAR(MAX), balance_before FLOAT, time NVARCHAR(50)
);
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='signal_performance')
CREATE TABLE signal_performance (
    id INT IDENTITY(1,1) PRIMARY KEY, signal_name NVARCHAR(200),
    correct INT DEFAULT 0, total INT DEFAULT 0,
    weight FLOAT DEFAULT 1.0, updated_at NVARCHAR(50)
);
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='idx_signal_name' AND object_id=OBJECT_ID('signal_performance'))
CREATE UNIQUE INDEX idx_signal_name ON signal_performance(signal_name);
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='monitor_state')
CREATE TABLE monitor_state (
    market NVARCHAR(100) NOT NULL, pos_id INT, symbol NVARCHAR(50),
    state_key NVARCHAR(500) NOT NULL, value NVARCHAR(MAX) NOT NULL,
    updated_at NVARCHAR(50) NOT NULL,
    CONSTRAINT pk_monitor_state PRIMARY KEY (market, state_key)
);
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='portal_users')
CREATE TABLE portal_users (
    id INT IDENTITY(1,1) PRIMARY KEY, username NVARCHAR(100) NOT NULL,
    password_hash NVARCHAR(256) NOT NULL, role NVARCHAR(20) DEFAULT 'viewer',
    created_at NVARCHAR(50), last_login NVARCHAR(50)
);
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='idx_portal_users_username' AND object_id=OBJECT_ID('portal_users'))
CREATE UNIQUE INDEX idx_portal_users_username ON portal_users(username);
"@
$null = & sqlcmd -S ".\ANGELBOT" -U sa -P $saPass -Q $schemaSql 2>&1
if ($LASTEXITCODE -eq 0) { OK "All tables ready" }
else { Warn "sqlcmd schema step had warnings (may be harmless)" }

# Seed admin user
$seedPy = @"
import sys, os
sys.path.insert(0, r'$BOT_DIR')
os.chdir(r'$BOT_DIR')
try:
    from data.database import init_db
    init_db()
    print('Admin user seeded')
except Exception as e:
    print('Seed warning: ' + str(e))
"@
$seedOut = & $pyReal -c $seedPy 2>&1
if ($seedOut) { OK "$seedOut" }

# ── 8. NSSM setup ─────────────────────────────────────────────────────────────
$nssm = "C:\Windows\nssm.exe"
if (-not (Test-Path $nssm)) {
    $bundled = "$BOT_DIR\prerequisite\setup\nssm\nssm.exe"
    if (Test-Path $bundled) { Copy-Item $bundled $nssm -Force }
    else { Fail "NSSM not found."; pause; exit 1 }
}

# ── 9. Re-register and start worker services ──────────────────────────────────
Write-Host ""
Info "Re-registering worker services..."

$workers = @(
    @{ Name="AngelBot-India";  Script="india_worker.py"  },
    @{ Name="AngelBot-US";     Script="us_worker.py"     },
    @{ Name="AngelBot-Crypto"; Script="crypto_worker.py" }
)
$failCount = 0

foreach ($w in $workers) {
    $name   = $w.Name
    $script = "$BOT_DIR\$($w.Script)"
    $log    = "$BOT_DIR\logs\$name.log"

    Stop-Service -Name $name -Force -ErrorAction SilentlyContinue
    $null = & $nssm remove $name confirm 2>&1
    Start-Sleep -Seconds 1

    $null = & $nssm install $name $pyReal $script
    $null = & $nssm set $name AppDirectory        $BOT_DIR
    $null = & $nssm set $name AppStdout           $log
    $null = & $nssm set $name AppStderr           $log
    $null = & $nssm set $name AppRotateFiles      1
    $null = & $nssm set $name AppRotateBytes      10485760
    $null = & $nssm set $name Start               SERVICE_AUTO_START
    $null = & $nssm set $name AppThrottle         5000
    $null = & $nssm set $name AppEnvironmentExtra "PYTHONUTF8=1"

    Start-Service -Name $name -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 4

    $st = (Get-Service -Name $name -ErrorAction SilentlyContinue).Status
    if ($st -eq "Running") {
        OK "$name -- Running"
    } else {
        $failCount++
        Fail "$name -- FAILED. Last 10 log lines:"
        if (Test-Path $log) { Get-Content $log -Tail 10 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
    }
}

# ── Portal service ────────────────────────────────────────────────────────────
$null = & $nssm set AngelBot-Portal AppEnvironmentExtra "PYTHONUTF8=1" 2>&1
Restart-Service AngelBot-Portal -Force -ErrorAction SilentlyContinue
Start-Sleep 4
$portalSt = (Get-Service AngelBot-Portal -ErrorAction SilentlyContinue).Status
if ($portalSt -eq "Running") { OK "AngelBot-Portal -- Running" }
else { $failCount++; Fail "AngelBot-Portal -- FAILED" }

Write-Host ""
if ($failCount -eq 0) {
    OK "All 4 services running. Open http://localhost:8080"
} else {
    Fail "$failCount service(s) failed. Check log lines above."
    Write-Host "  Logs: $BOT_DIR\logs\" -ForegroundColor Yellow
}
Write-Host ""
pause
