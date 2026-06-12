# AngelBot Service Diagnostic & Fix
# Run as Administrator when workers fail to start
#
# What this does:
#   1. Finds the real Python.exe (not the Windows Store stub)
#   2. Shows the last 30 lines of each worker's NSSM log
#   3. Re-registers the 3 worker services with the correct Python path
#   4. Starts them and reports status

$BOT_DIR = "C:\AngelBot"

function OK   { param($m) Write-Host "  [OK]  $m" -ForegroundColor Green }
function Info { param($m) Write-Host "  [..] $m"  -ForegroundColor Cyan }
function Warn { param($m) Write-Host "  [!!] $m"  -ForegroundColor Yellow }
function Fail { param($m) Write-Host "  [XX] $m"  -ForegroundColor Red }

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  AngelBot Service Diagnostics & Fix  " -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

# ---- Find real Python -------------------------------------------------------
Info "Locating Python executable..."
$pyReal = $null

# Try python -c sys.executable first
try {
    $pyReal = (& python -c "import sys; print(sys.executable)" 2>$null).Trim()
} catch {}

# Reject Windows Store stub (doesn't work for services)
if ($pyReal -and ($pyReal -match "WindowsApps" -or $pyReal -match "Microsoft\\WindowsApps")) {
    Warn "Found Windows Store stub: $pyReal  (will not work as service)"
    $pyReal = $null
}

# Search common install locations
if (-not $pyReal) {
    $candidates = @(
        "C:\Python313\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "C:\Program Files\Python313\python.exe",
        "C:\Program Files\Python312\python.exe",
        "C:\Program Files\Python311\python.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $pyReal = $c; break }
    }
}

if (-not $pyReal) {
    Fail "Cannot locate Python.exe. Make sure Python 3.10+ is installed."
    exit 1
}
OK "Python: $pyReal"

# ---- Show last NSSM log lines for each service ------------------------------
Write-Host ""
Write-Host "--- Service Status & Recent Logs ---" -ForegroundColor White

$allServices = @("AngelBot-India","AngelBot-US","AngelBot-Crypto","AngelBot-Portal")
foreach ($svc in $allServices) {
    $svcObj = Get-Service -Name $svc -ErrorAction SilentlyContinue
    $status  = if ($svcObj) { $svcObj.Status } else { "Not Installed" }
    $color   = if ($status -eq "Running") { "Green" } else { "Red" }
    Write-Host ""
    Write-Host "[$svc]  Status: $status" -ForegroundColor $color

    $log = "$BOT_DIR\logs\$svc.log"
    if (Test-Path $log) {
        Write-Host "  Last 30 lines of log:" -ForegroundColor Gray
        Get-Content $log -Tail 30 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    } else {
        Write-Host "  No log file yet at: $log" -ForegroundColor DarkYellow
    }
}

# ---- Re-register the 3 worker services with correct Python ------------------
Write-Host ""
Write-Host "--- Re-registering Worker Services ---" -ForegroundColor White

$nssm = "C:\Windows\nssm.exe"
if (-not (Test-Path $nssm)) {
    $nssm = "$BOT_DIR\prerequisite\setup\nssm\nssm.exe"
    if (-not (Test-Path $nssm)) {
        Fail "NSSM not found. Run the main installer first."
        exit 1
    }
    Copy-Item $nssm "C:\Windows\nssm.exe" -Force
}

$workers = @(
    @{ Name="AngelBot-India";  Script="india_worker.py"  },
    @{ Name="AngelBot-US";     Script="us_worker.py"     },
    @{ Name="AngelBot-Crypto"; Script="crypto_worker.py" }
)

foreach ($w in $workers) {
    $name   = $w.Name
    $script = "$BOT_DIR\$($w.Script)"
    $log    = "$BOT_DIR\logs\$name.log"

    Info "Registering $name..."
    Stop-Service -Name $name -Force -ErrorAction SilentlyContinue
    & $nssm remove $name confirm 2>$null | Out-Null
    Start-Sleep -Seconds 1

    & $nssm install         $name $pyReal $script
    & $nssm set $name AppDirectory   $BOT_DIR
    & $nssm set $name AppStdout      $log
    & $nssm set $name AppStderr      $log
    & $nssm set $name AppRotateFiles 1
    & $nssm set $name AppRotateBytes 10485760
    & $nssm set $name Start          SERVICE_AUTO_START
    & $nssm set $name AppThrottle    5000

    Start-Service -Name $name -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3

    $st = (Get-Service -Name $name -ErrorAction SilentlyContinue).Status
    if ($st -eq "Running") {
        OK "$name is Running"
    } else {
        Fail "$name failed to start -- check log: $log"
    }
}

Write-Host ""
Write-Host "Done. If services still fail, check the log files listed above for the Python error." -ForegroundColor Cyan
Write-Host "Common causes: missing .env file, wrong API keys, missing Python package." -ForegroundColor DarkGray
Write-Host ""
pause
