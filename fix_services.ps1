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

# ── 4. Install / upgrade Python packages ─────────────────────────────────────
Info "Installing Python packages (pyodbc + requirements)..."
& $pyReal -m pip install pyodbc --quiet
& $pyReal -m pip install -r "$BOT_DIR\requirements.txt" --quiet
OK "Packages ready"

# ── 5. NSSM setup ────────────────────────────────────────────────────────────
$nssm = "C:\Windows\nssm.exe"
if (-not (Test-Path $nssm)) {
    $bundled = "$BOT_DIR\prerequisite\setup\nssm\nssm.exe"
    if (Test-Path $bundled) { Copy-Item $bundled $nssm -Force }
    else { Fail "NSSM not found."; pause; exit 1 }
}

# ── 6. Re-register worker services ───────────────────────────────────────────
Write-Host ""
Info "Re-registering worker services..."

$workers = @(
    @{ Name="AngelBot-India";  Script="india_worker.py"  },
    @{ Name="AngelBot-US";     Script="us_worker.py"     },
    @{ Name="AngelBot-Crypto"; Script="crypto_worker.py" }
)

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
    Start-Sleep -Seconds 3

    $st = (Get-Service -Name $name -ErrorAction SilentlyContinue).Status
    if ($st -eq "Running") { OK "$name -- Running" }
    else {
        Fail "$name -- failed. Last log:"
        if (Test-Path $log) { Get-Content $log -Tail 15 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
    }
}

# ── Also set UTF-8 on portal service ─────────────────────────────────────────
& $nssm set AngelBot-Portal AppEnvironmentExtra "PYTHONUTF8=1" | Out-Null
Restart-Service AngelBot-Portal -Force -ErrorAction SilentlyContinue
Start-Sleep 3
$st = (Get-Service AngelBot-Portal -ErrorAction SilentlyContinue).Status
if ($st -eq "Running") { OK "AngelBot-Portal -- Running" }

Write-Host ""
OK "All done. Open http://localhost:8080 to verify."
Write-Host ""
pause
