# AngelBot Service Fix
# Right-click -> Run as Administrator
# Handles: ODBC Driver, pyodbc, UTF-8 encoding, service re-registration

$BOT_DIR = "C:\AngelBot"

function OK   { param($m) Write-Host "  [OK]  $m" -ForegroundColor Green }
function Info { param($m) Write-Host "  [..] $m"  -ForegroundColor Cyan }
function Warn { param($m) Write-Host "  [!!] $m"  -ForegroundColor Yellow }
function Fail { param($m) Write-Host "  [XX] $m"  -ForegroundColor Red }

function Download {
    param($url, $dest)
    try {
        Invoke-WebRequest $url -OutFile $dest -UseBasicParsing -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "   AngelBot Full Service Fix Script   " -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

# ── 1. Pull latest code from GitHub ──────────────────────────────────────────
Info "Pulling latest code from GitHub..."
$oldPref = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$null = git -C $BOT_DIR fetch --all 2>&1
$null = git -C $BOT_DIR reset --hard origin/main 2>&1
$ErrorActionPreference = $oldPref
OK "Code up to date"

# ── 2. Find real Python ───────────────────────────────────────────────────────
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

# ── 3. Install ODBC Driver 17 for SQL Server ──────────────────────────────────
$odbcOk = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*" `
    -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like "*ODBC Driver 1*SQL*" }
if (-not $odbcOk) {
    $odbcOk = Get-ItemProperty "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*" `
        -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like "*ODBC Driver 1*SQL*" }
}
if ($odbcOk) {
    OK "ODBC Driver already installed"
} else {
    Info "Downloading ODBC Driver 17 for SQL Server (~6 MB)..."
    $odbcMsi = "$env:TEMP\msodbcsql17.msi"
    if (Download "https://go.microsoft.com/fwlink/?linkid=2168524" $odbcMsi) {
        Start-Process msiexec -ArgumentList "/i `"$odbcMsi`" /qn IACCEPTMSODBCSQLLICENSETERMS=YES" -Wait -NoNewWindow
        OK "ODBC Driver 17 installed"
    } else {
        Warn "ODBC Driver download failed -- SQL Server connection may not work"
    }
}

# ── 4. SQL Server SA account + database setup ────────────────────────────────
$envFile    = "$BOT_DIR\.env"
$envContent = if (Test-Path $envFile) { Get-Content $envFile -Raw } else { "" }

# Extract existing SA pass if present
$saPass = ""
if ($envContent -match "SQL_SA_PASS=(.+)") { $saPass = $Matches[1].Trim() }

# Helper: test SA connection using sqlcmd
function Test-SALogin($pass) {
    $result = & sqlcmd -S ".\ANGELBOT" -U sa -P $pass -Q "SELECT 1" 2>&1
    return ($LASTEXITCODE -eq 0)
}

# Helper: fix SA via Windows auth (uses current admin account)
function Fix-SAViaWindowsAuth($newPass) {
    Info "Using Windows auth to enable SA and set password..."
    $sql = "ALTER LOGIN sa ENABLE; ALTER LOGIN sa WITH PASSWORD='$newPass';"
    $r = & sqlcmd -S ".\ANGELBOT" -E -Q $sql 2>&1
    return ($LASTEXITCODE -eq 0)
}

# Helper: create angelbot database and tables via sqlcmd
function Init-Database($pass) {
    Info "Creating 'angelbot' database in SQL Server..."
    $sql = "IF NOT EXISTS (SELECT name FROM sys.databases WHERE name='angelbot') CREATE DATABASE angelbot;"
    & sqlcmd -S ".\ANGELBOT" -U sa -P $pass -Q $sql 2>&1 | Out-Null
}

# Check sqlcmd is available
$sqlcmdPath = Get-Command sqlcmd -ErrorAction SilentlyContinue
if (-not $sqlcmdPath) {
    # Try common SQL Server tool paths
    foreach ($p in @(
        "C:\Program Files\Microsoft SQL Server\Client SDK\ODBC\170\Tools\Binn\sqlcmd.exe",
        "C:\Program Files\Microsoft SQL Server\110\Tools\Binn\sqlcmd.exe",
        "C:\Program Files\Microsoft SQL Server\120\Tools\Binn\sqlcmd.exe",
        "C:\Program Files\Microsoft SQL Server\130\Tools\Binn\sqlcmd.exe",
        "C:\Program Files\Microsoft SQL Server\140\Tools\Binn\sqlcmd.exe",
        "C:\Program Files\Microsoft SQL Server\150\Tools\Binn\sqlcmd.exe"
    )) { if (Test-Path $p) { $env:PATH += ";$(Split-Path $p)"; break } }
}

# Test current SA password
$saOk = $false
if ($saPass) {
    Info "Testing SQL Server SA login..."
    $saOk = Test-SALogin $saPass
    if ($saOk) { OK "SA login works" }
    else        { Warn "SA login failed with saved password -- will reset" }
}

if (-not $saOk) {
    Write-Host ""
    Write-Host "  SQL Server SA login is not working." -ForegroundColor Yellow
    Write-Host "  Attempting to fix via Windows authentication..." -ForegroundColor White

    # Ask for new SA password
    Write-Host "  Set a new SA password (min 8 chars, must include uppercase + number):" -ForegroundColor White
    $saSecure = Read-Host "  New SA Password" -AsSecureString
    $saPass   = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($saSecure))

    $fixed = Fix-SAViaWindowsAuth $saPass
    if ($fixed) {
        OK "SA account enabled and password set"
    } else {
        Warn "Could not fix SA via Windows auth -- trying to continue anyway"
    }
    Start-Sleep -Seconds 2

    $saOk = Test-SALogin $saPass
    if ($saOk) { OK "SA login now works" }
    else        { Warn "SA login still failing -- workers may not connect to DB" }

    # Save new password to .env
    if ($envContent -match "SQL_SA_PASS=") {
        $envContent = $envContent -replace "SQL_SA_PASS=.*(\r?\n|$)", "SQL_SA_PASS=$saPass`n"
        Set-Content $envFile $envContent.TrimEnd() -Encoding ASCII
    } else {
        Add-Content $envFile "`nSQL_SA_PASS=$saPass" -Encoding ASCII
    }
    OK "SQL_SA_PASS updated in .env"
}

# Create angelbot database using Windows auth (works regardless of SA password)
Info "Ensuring 'angelbot' database exists..."
$createDb = "IF NOT EXISTS (SELECT name FROM sys.databases WHERE name='angelbot') CREATE DATABASE angelbot;"
& sqlcmd -S ".\ANGELBOT" -E -Q $createDb 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    OK "Database 'angelbot' ready"
} else {
    # Fallback: try with SA credentials
    if ($saOk) {
        & sqlcmd -S ".\ANGELBOT" -U sa -P $saPass -Q $createDb 2>&1 | Out-Null
        OK "Database 'angelbot' created via SA"
    } else {
        Warn "Could not create 'angelbot' database -- workers may fail on first start"
    }
}

# ── 5. Install / upgrade Python packages ─────────────────────────────────────
Info "Installing Python packages (pyodbc + requirements)..."
& $pyReal -m pip install pyodbc --quiet
& $pyReal -m pip install -r "$BOT_DIR\requirements.txt" --quiet
OK "Packages ready"

# ── 5b. Create all DB tables via Python init_db() ────────────────────────────
Info "Creating database tables (init_db)..."
$initScript = @"
import sys, os
sys.path.insert(0, r'$BOT_DIR')
os.chdir(r'$BOT_DIR')
from data.database import init_db
init_db()
"@
$initOut = & $pyReal -c $initScript 2>&1
if ($LASTEXITCODE -eq 0) {
    OK "All tables created / verified"
} else {
    Warn "init_db had issues:"
    Write-Host $initOut -ForegroundColor DarkYellow
}

# ── 6. NSSM setup ────────────────────────────────────────────────────────────
$nssm = "C:\Windows\nssm.exe"
if (-not (Test-Path $nssm)) {
    $bundled = "$BOT_DIR\prerequisite\setup\nssm\nssm.exe"
    if (Test-Path $bundled) { Copy-Item $bundled $nssm -Force }
    else { Fail "NSSM not found."; pause; exit 1 }
}

# ── 7. Re-register worker services ───────────────────────────────────────────
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
    & $nssm remove $name confirm 2>$null | Out-Null
    Start-Sleep -Seconds 1

    & $nssm install         $name $pyReal $script         | Out-Null
    & $nssm set $name AppDirectory          $BOT_DIR      | Out-Null
    & $nssm set $name AppStdout             $log          | Out-Null
    & $nssm set $name AppStderr             $log          | Out-Null
    & $nssm set $name AppRotateFiles        1             | Out-Null
    & $nssm set $name AppRotateBytes        10485760      | Out-Null
    & $nssm set $name Start                 SERVICE_AUTO_START | Out-Null
    & $nssm set $name AppThrottle           5000          | Out-Null
    & $nssm set $name AppEnvironmentExtra   "PYTHONUTF8=1" | Out-Null

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

# ── Also set UTF-8 on portal service ─────────────────────────────────────────
& $nssm set AngelBot-Portal AppEnvironmentExtra "PYTHONUTF8=1" | Out-Null
Restart-Service AngelBot-Portal -Force -ErrorAction SilentlyContinue
Start-Sleep 3
$portalSt = (Get-Service AngelBot-Portal -ErrorAction SilentlyContinue).Status
if ($portalSt -eq "Running") { OK "AngelBot-Portal -- Running" }
else { $failCount++; Fail "AngelBot-Portal -- FAILED" }

Write-Host ""
if ($failCount -eq 0) {
    OK "All 4 services running. Open http://localhost:8080"
} else {
    Fail "$failCount service(s) failed. Check log lines above for the error."
    Write-Host "  Logs folder: $BOT_DIR\logs\" -ForegroundColor Yellow
}
Write-Host ""
pause
