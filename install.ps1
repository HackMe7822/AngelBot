#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

$BOT_DIR  = "C:\AngelBot"
$LOG_FILE = "C:\angelbot_install.log"
$tmp      = "C:\angelbot_tmp"
$STEPS    = 10

# -- Helpers -------------------------------------------------------------------
function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LOG_FILE -Value "$ts  $msg" -ErrorAction SilentlyContinue
}
function Info($msg) { Write-Host "     $msg" -ForegroundColor Cyan;   Log $msg }
function OK($msg)   { Write-Host "  OK  $msg" -ForegroundColor Green; Log "OK: $msg" }
function Warn($msg) { Write-Host "  !!  $msg" -ForegroundColor Yellow; Log "WARN: $msg" }
function Err($msg)  { Write-Host "  XX  $msg" -ForegroundColor Red;   Log "ERR: $msg" }

function Step($n, $t) {
    Write-Host ""
    Write-Host "  --- Step $n/$STEPS : $t ---" -ForegroundColor White
}

function Download($url, $dest) {
    if (Test-Path $dest) { return }
    Info "Downloading $(Split-Path $dest -Leaf) ..."
    try {
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
    } catch {
        Warn "Download failed for $(Split-Path $dest -Leaf): $_"
    }
}

function Update-EnvPath {
    $machine = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
    $user    = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $env:PATH = "$machine;$user"
}

# Poll until a Windows service reaches Running state (or times out)
function Wait-ServiceRunning($name, $timeoutSec = 60) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
        if ($svc -and $svc.Status -eq 'Running') { return $true }
        Start-Sleep -Seconds 3
    }
    return $false
}

# Upsert a key=value line in a .env file
function Set-EnvKey($path, $key, $value) {
    if (Test-Path $path) {
        $content = Get-Content $path -Raw -ErrorAction SilentlyContinue
        if ($content -match "(?m)^$key=") {
            $content = $content -replace "(?m)^$key=.*", "$key=$value"
            Set-Content $path $content.TrimEnd() -Encoding ASCII
        } else {
            Add-Content $path "`n$key=$value" -Encoding ASCII
        }
    } else {
        Set-Content $path "$key=$value" -Encoding ASCII
    }
}

# Enable SQL Server TCP/IP via registry (installer flag is sometimes ignored)
function Enable-SqlTcp($instanceName = "ANGELBOT") {
    $regBase = "HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server"
    Get-ChildItem $regBase -ErrorAction SilentlyContinue |
        Where-Object { $_.PSChildName -match "^MSSQL\d+\.$instanceName$" } |
        ForEach-Object {
            $tcpPath = "$($_.PSPath)\MSSQLServer\SuperSocketNetLib\Tcp"
            if (Test-Path $tcpPath) {
                Set-ItemProperty $tcpPath -Name "Enabled" -Value 1 -ErrorAction SilentlyContinue
            }
        }
}

# -- Banner --------------------------------------------------------------------
Clear-Host
Write-Host ""
Write-Host "  ============================================================" -ForegroundColor Cyan
Write-Host "        AngelBot  --  One-Click Windows Installer             " -ForegroundColor Cyan
Write-Host "   Python / Git / SQL Server Express / NSSM / IIS             " -ForegroundColor Cyan
Write-Host "  ============================================================" -ForegroundColor Cyan
Write-Host ""

New-Item -ItemType Directory -Force -Path $tmp | Out-Null
Log "=== Installer started ==="

# -----------------------------------------------------------------------------
# STEP 1 -- Python 3.11
# -----------------------------------------------------------------------------
Step 1 "Python 3.11"
$pyOK = $false
try { $pyOK = ((python --version 2>&1) -match "Python 3\.(9|10|11|12)") } catch {}

if (-not $pyOK) {
    $exe = "$tmp\python-3.11.9-amd64.exe"
    Download "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" $exe
    if (-not (Test-Path $exe)) { Err "Python installer not downloaded -- check internet."; exit 1 }
    Info "Installing Python 3.11 ..."
    Start-Process $exe -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 Include_test=0" -Wait -NoNewWindow
    Update-EnvPath
    try { $pyOK = ((python --version 2>&1) -match "Python 3") } catch {}
    if (-not $pyOK) { Err "Python install failed -- re-run as Administrator."; exit 1 }
    OK "Python 3.11 installed"
} else {
    OK "Python already installed -- $((python --version 2>&1))"
}

# -----------------------------------------------------------------------------
# STEP 2 -- Git
# -----------------------------------------------------------------------------
Step 2 "Git"
$gitOK = $null
try { $gitOK = Get-Command git -ErrorAction SilentlyContinue } catch {}

if (-not $gitOK) {
    $exe = "$tmp\git-installer.exe"
    Download "https://github.com/git-for-windows/git/releases/download/v2.45.2.windows.1/Git-2.45.2-64-bit.exe" $exe
    if (-not (Test-Path $exe)) { Err "Git installer not downloaded."; exit 1 }
    Info "Installing Git ..."
    Start-Process $exe -ArgumentList "/VERYSILENT /NORESTART /NOCANCEL /SP- /CLOSEAPPLICATIONS" -Wait -NoNewWindow
    Update-EnvPath
    try { $gitOK = Get-Command git -ErrorAction SilentlyContinue } catch {}
    if (-not $gitOK) { Err "Git install failed."; exit 1 }
    OK "Git installed"
} else {
    OK "Git already installed"
}

# -----------------------------------------------------------------------------
# STEP 3 -- Clone / update AngelBot code
# -----------------------------------------------------------------------------
Step 3 "AngelBot code"

# Back up .env before any git operation so it survives reset/re-clone
$envPath   = "$BOT_DIR\.env"
$envBackup = "$tmp\angelbot_env.bak"
if (Test-Path $envPath) {
    Copy-Item $envPath $envBackup -Force
    Info "Backed up existing .env"
}

$oldPref = $ErrorActionPreference
$ErrorActionPreference = "Continue"

if (Test-Path "$BOT_DIR\.git") {
    Info "Pulling latest code from GitHub ..."
    git -C $BOT_DIR fetch --all 2>&1 | Out-Null
    git -C $BOT_DIR reset --hard origin/main 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Warn "git reset had errors -- code may be partially updated." }
    else { OK "Code updated to latest" }
} elseif (Test-Path $BOT_DIR) {
    Info "Folder exists but no git repo -- removing and cloning fresh ..."
    Remove-Item $BOT_DIR -Recurse -Force -ErrorAction SilentlyContinue
    git clone https://github.com/HackMe7822/AngelBot.git $BOT_DIR 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Err "git clone failed -- check internet."; exit 1 }
    OK "Cloned to $BOT_DIR"
} else {
    Info "Cloning from GitHub ..."
    git clone https://github.com/HackMe7822/AngelBot.git $BOT_DIR 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Err "git clone failed -- check internet."; exit 1 }
    OK "Downloaded to $BOT_DIR"
}

$ErrorActionPreference = $oldPref

# Restore .env -- git never touches .gitignore'd files on reset,
# but a fresh re-clone wipes the folder first, so always restore from backup
if ((Test-Path $envBackup) -and -not (Test-Path $envPath)) {
    Copy-Item $envBackup $envPath -Force
    Info ".env restored from backup"
}
if (Test-Path $envBackup) { Remove-Item $envBackup -Force -ErrorAction SilentlyContinue }

New-Item -ItemType Directory -Force -Path "$BOT_DIR\learning" | Out-Null
New-Item -ItemType Directory -Force -Path "$BOT_DIR\logs"     | Out-Null

# -----------------------------------------------------------------------------
# STEP 4 -- SQL Server 2019 Express (instance: ANGELBOT)
# -----------------------------------------------------------------------------
Step 4 "SQL Server 2019 Express"
$sqlSvc = Get-Service -Name "MSSQL`$ANGELBOT" -ErrorAction SilentlyContinue

if ($sqlSvc) {
    OK "SQL Server instance ANGELBOT already installed"
    if ($sqlSvc.Status -ne "Running") {
        Info "Starting SQL Server ..."
        Start-Service "MSSQL`$ANGELBOT" -ErrorAction SilentlyContinue
        if (-not (Wait-ServiceRunning "MSSQL`$ANGELBOT" 60)) {
            Warn "SQL Server did not reach Running state -- DB init may fail"
        }
    }
    $browser = Get-Service -Name "SQLBrowser" -ErrorAction SilentlyContinue
    if ($browser) {
        Set-Service -Name "SQLBrowser" -StartupType Automatic -ErrorAction SilentlyContinue
        Start-Service "SQLBrowser" -ErrorAction SilentlyContinue
        OK "SQL Server Browser running"
    }
} else {
    Write-Host ""
    Write-Host "  SQL Server Express will be installed as instance: ANGELBOT" -ForegroundColor White
    Write-Host "  Choose an SA password (min 8 chars, uppercase + digit)." -ForegroundColor DarkGray
    Write-Host ""
    $saSecure = Read-Host "  SA Password" -AsSecureString
    $script:saPass = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($saSecure))

    $ssei = "$tmp\sql_ssei.exe"
    Download "https://go.microsoft.com/fwlink/p/?linkid=866658" $ssei

    $sqlMedia = "$tmp\sql_media"
    New-Item -ItemType Directory -Force -Path $sqlMedia | Out-Null
    $fullExe = Get-ChildItem $sqlMedia -Filter "SQLEXPR*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $fullExe) {
        Info "Downloading SQL Server 2019 Express (approx 280 MB) ..."
        Start-Process $ssei -ArgumentList "/Action=Download /MediaPath=`"$sqlMedia`" /MediaType=Advanced /Quiet" -Wait -NoNewWindow
        $fullExe = Get-ChildItem $sqlMedia -Filter "SQLEXPR*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    }

    if (-not $fullExe) {
        Warn "SQL Server download failed -- install manually then re-run."
    } else {
        Info "Installing SQL Server 2019 Express ..."
        $sqlArgs = "/Q /IACCEPTSQLSERVERLICENSETERMS /ACTION=Install /FEATURES=SQLEngine" +
                   " /INSTANCENAME=ANGELBOT" +
                   " /SQLSYSADMINACCOUNTS=`"$env:USERDOMAIN\$env:USERNAME`"" +
                   " /SECURITYMODE=SQL /SAPWD=`"$($script:saPass)`"" +
                   " /TCPENABLED=1 /BROWSERSVCSTARTUPTYPE=Automatic"
        Start-Process $fullExe.FullName -ArgumentList $sqlArgs -Wait -NoNewWindow
        Log "SQL Server install completed"

        Enable-SqlTcp "ANGELBOT"

        $browser = Get-Service -Name "SQLBrowser" -ErrorAction SilentlyContinue
        if ($browser) {
            Set-Service -Name "SQLBrowser" -StartupType Automatic -ErrorAction SilentlyContinue
            Start-Service "SQLBrowser" -ErrorAction SilentlyContinue
            OK "SQL Server Browser started"
        }

        Info "Waiting for SQL Server to reach Running state ..."
        $sqlSvc = Get-Service -Name "MSSQL`$ANGELBOT" -ErrorAction SilentlyContinue
        if ($sqlSvc) {
            Start-Service "MSSQL`$ANGELBOT" -ErrorAction SilentlyContinue
            if (Wait-ServiceRunning "MSSQL`$ANGELBOT" 90) { OK "SQL Server ANGELBOT running" }
            else { Warn "SQL Server not yet running -- may need a reboot" }
        } else {
            Warn "SQL Server service not found -- may need a reboot to finish install"
        }
    }

    New-NetFirewallRule -DisplayName "SQL Server 1433" -Direction Inbound -Protocol TCP `
        -LocalPort 1433 -Action Allow -ErrorAction SilentlyContinue | Out-Null
    OK "Firewall: SQL Server port 1433 opened"
}

# -----------------------------------------------------------------------------
# STEP 5 -- ODBC Driver 17 + Python packages
# -----------------------------------------------------------------------------
Step 5 "ODBC Driver 17 + Python packages"

$odbcKey = "HKLM:\SOFTWARE\ODBC\ODBCINST.INI\ODBC Driver 17 for SQL Server"
if (Test-Path $odbcKey) {
    OK "ODBC Driver 17 already installed"
} else {
    Info "Downloading ODBC Driver 17 ..."
    $odbcMsi = "$tmp\msodbcsql17.msi"
    Download "https://go.microsoft.com/fwlink/?linkid=2168524" $odbcMsi
    if (Test-Path $odbcMsi) {
        Start-Process msiexec -ArgumentList "/i `"$odbcMsi`" /qn IACCEPTMSODBCSQLLICENSETERMS=YES" -Wait -NoNewWindow
        OK "ODBC Driver 17 installed"
    } else {
        Warn "ODBC Driver 17 download failed -- pyodbc may not connect"
    }
}

Info "Installing Python packages ..."
$oldPref = $ErrorActionPreference
$ErrorActionPreference = "Continue"
python -m pip install --upgrade pip --quiet 2>&1 | Out-Null
$pipOut = python -m pip install -r "$BOT_DIR\requirements.txt" --quiet 2>&1
if ($LASTEXITCODE -ne 0) {
    Warn "pip install had warnings/errors:`n$($pipOut | Select-Object -Last 8 | Out-String)"
} else {
    OK "All Python packages installed"
}
$ErrorActionPreference = $oldPref

# -----------------------------------------------------------------------------
# STEP 6 -- API Keys (.env)
# -----------------------------------------------------------------------------
Step 6 "API Keys (.env)"

if ($script:saPass) { $saForEnv = $script:saPass } else { $saForEnv = "" }

if (Test-Path $envPath) {
    if ($saForEnv) {
        Set-EnvKey $envPath "SQL_SA_PASS" $saForEnv
        OK "SQL_SA_PASS updated in .env"
    }
    OK ".env exists -- configure API keys via the portal (API Keys tab)"
} else {
    $envLines = @(
        "# Angel One (India NSE)",
        "ANGEL_API_KEY=",
        "ANGEL_SECRET=",
        "ANGEL_CLIENT_ID=",
        "ANGEL_PIN=",
        "ANGEL_TOTP_SECRET=",
        "",
        "# Alpaca (US Paper Trading)",
        "ALPACA_KEY=",
        "ALPACA_SECRET=",
        "ALPACA_PAPER=true",
        "",
        "# Binance (Crypto -- paper only)",
        "BINANCE_KEY=",
        "BINANCE_SECRET=",
        "BINANCE_PAPER=true",
        "",
        "# Telegram (optional)",
        "TELEGRAM_BOT_TOKEN=",
        "TELEGRAM_CHAT_ID=",
        "",
        "# Capital (INR / USD)",
        "INDIA_CAPITAL=10000",
        "US_CAPITAL=10000",
        "CRYPTO_CAPITAL=1000",
        "",
        "# Safety flags -- never set false via portal",
        "PAPER_MODE=true",
        "",
        "# Portal admin password",
        "PORTAL_PASS=AngelBot@1234",
        "",
        "# SQL Server",
        "SQL_SA_PASS=$saForEnv"
    )
    $envLines -join "`r`n" | Out-File $envPath -Encoding ASCII -Force
    OK ".env template created -- enter API keys via the portal after startup"
}

# -----------------------------------------------------------------------------
# STEP 7 -- Database initialisation
# -----------------------------------------------------------------------------
Step 7 "Database initialisation"
$sqlSvcCheck = Get-Service -Name "MSSQL`$ANGELBOT" -ErrorAction SilentlyContinue
if ($sqlSvcCheck -and $sqlSvcCheck.Status -eq "Running") {
    Info "Creating database and tables ..."

    # Write a temp Python script -- avoids PowerShell -c argument quoting issues
    $initPy = "$tmp\angelbot_init_db.py"
    @"
import sys, os

sys.path.insert(0, r'$BOT_DIR')

env_path = r'$envPath'
if os.path.exists(env_path):
    for line in open(env_path, encoding='utf-8').read().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition('=')
            os.environ.setdefault(k.strip(), v.strip())

from data.database import init_db
init_db()
"@ | Out-File $initPy -Encoding ASCII -Force

    $oldPref = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $result = python $initPy 2>&1
    $initOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $oldPref

    Remove-Item $initPy -Force -ErrorAction SilentlyContinue

    if ($initOk) {
        OK "Database tables ready"
    } else {
        Warn "DB init had errors (workers will retry on start):`n$($result | Out-String)"
    }
} else {
    Warn "SQL Server not running -- database will be created when services first start"
}

# -----------------------------------------------------------------------------
# STEP 8 -- IIS + URL Rewrite + ARR (reverse proxy 80 -> 8080)
# -----------------------------------------------------------------------------
Step 8 "IIS + URL Rewrite + ARR"

$iisSvc = Get-Service -Name "W3SVC" -ErrorAction SilentlyContinue
if (-not $iisSvc) {
    Info "Enabling IIS via DISM ..."
    $features = @(
        "IIS-WebServerRole","IIS-WebServer","IIS-CommonHttpFeatures",
        "IIS-HttpErrors","IIS-HttpRedirect","IIS-ApplicationDevelopment",
        "IIS-CGI","IIS-ISAPIExtensions","IIS-ISAPIFilter",
        "IIS-WebServerManagementTools","IIS-ManagementConsole"
    )
    foreach ($f in $features) {
        dism /online /enable-feature /featurename:$f /All /NoRestart /quiet 2>&1 | Out-Null
    }
    Start-Service W3SVC -ErrorAction SilentlyContinue
    OK "IIS installed"
} else {
    OK "IIS already installed"
}

$urKey = "HKLM:\SOFTWARE\Microsoft\IIS Extensions\URL Rewrite"
if (-not (Test-Path $urKey)) {
    $urMsi = "$tmp\rewrite_amd64_en-US.msi"
    Download "https://download.microsoft.com/download/1/2/8/128E2E22-C1B9-44A4-BE2A-5859ED1D4592/rewrite_amd64_en-US.msi" $urMsi
    if (Test-Path $urMsi) {
        Info "Installing URL Rewrite ..."
        Start-Process msiexec -ArgumentList "/i `"$urMsi`" /quiet /norestart" -Wait
        OK "URL Rewrite installed"
    } else { Warn "URL Rewrite download failed" }
} else { OK "URL Rewrite already installed" }

$arrKey = "HKLM:\SOFTWARE\Microsoft\IIS Extensions\Application Request Routing"
if (-not (Test-Path $arrKey)) {
    $arrExe = "$tmp\ARRv3_setup_amd64_en-us.exe"
    if (-not (Test-Path $arrExe)) {
        try {
            Invoke-WebRequest -Uri "https://download.microsoft.com/download/E/9/8/E9849D6A-020E-47E4-9FD0-A023E99B54EB/ARRv3_setup_amd64_en-us.exe" `
                -OutFile $arrExe -UseBasicParsing -ErrorAction Stop
        } catch { Warn "ARR download failed -- portal still reachable at http://<ip>:8080" }
    }
    if ((Test-Path $arrExe) -and (Get-Item $arrExe).Length -gt 100000) {
        Info "Installing ARR ..."
        Start-Process $arrExe -ArgumentList "/quiet /norestart" -Wait
        OK "ARR installed"
    } else {
        Remove-Item $arrExe -ErrorAction SilentlyContinue
        Warn "ARR not installed -- portal available on port 8080 directly"
    }
} else { OK "ARR already installed" }

New-Item -ItemType Directory -Force -Path "C:\inetpub\wwwroot" | Out-Null
@'
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <system.webServer>
    <rewrite>
      <rules>
        <rule name="AngelBot" stopProcessing="true">
          <match url="(.*)" />
          <action type="Rewrite" url="http://localhost:8080/{R:1}" />
        </rule>
      </rules>
    </rewrite>
  </system.webServer>
</configuration>
'@ | Out-File "C:\inetpub\wwwroot\web.config" -Encoding ASCII -Force
Start-Service W3SVC -ErrorAction SilentlyContinue
OK "IIS reverse proxy configured (port 80 -> 8080)"

# -----------------------------------------------------------------------------
# STEP 9 -- NSSM + Windows Services
# -----------------------------------------------------------------------------
Step 9 "NSSM + Windows Services"

$nssmDest = "C:\Windows\nssm.exe"
if (-not (Test-Path $nssmDest)) {
    $nssmBundled = "$BOT_DIR\prerequisite\setup\nssm\nssm.exe"
    if (Test-Path $nssmBundled) {
        Copy-Item $nssmBundled $nssmDest -Force
        OK "NSSM copied from repo bundle"
    } else {
        $zip = "$tmp\nssm.zip"
        Download "https://nssm.cc/release/nssm-2.24.zip" $zip
        if ((Test-Path $zip) -and (Get-Item $zip).Length -gt 100000) {
            Expand-Archive $zip "$tmp\nssm_ext" -Force
            $found = Get-ChildItem "$tmp\nssm_ext" -Filter "nssm.exe" -Recurse |
                     Where-Object { $_.FullName -match "win64" } | Select-Object -First 1
            if (-not $found) {
                $found = Get-ChildItem "$tmp\nssm_ext" -Filter "nssm.exe" -Recurse | Select-Object -First 1
            }
            if ($found) { Copy-Item $found.FullName $nssmDest -Force; OK "NSSM installed from download" }
            else { Err "nssm.exe not found in archive"; exit 1 }
        } else { Err "NSSM download failed -- cannot register services"; exit 1 }
    }
} else { OK "NSSM already present" }

Update-EnvPath
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pyCmd) { $pyExe = $pyCmd.Source } else { $pyExe = $null }
if (-not $pyExe) { Err "python.exe not found in PATH"; exit 1 }

# Force UTF-8 I/O for all Python processes on this machine (fixes UnicodeEncodeError
# when workers write box-drawing chars to the NSSM-captured stdout pipe)
[System.Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "Machine")
OK "PYTHONUTF8=1 set machine-wide"

$services = @(
    @{ Name="AngelBot-India";  Script="india_worker.py"  },
    @{ Name="AngelBot-US";     Script="us_worker.py"     },
    @{ Name="AngelBot-Crypto"; Script="crypto_worker.py" },
    @{ Name="AngelBot-Portal"; Script="portal_worker.py" }
)

$oldPref = $ErrorActionPreference
$ErrorActionPreference = "Continue"

foreach ($svc in $services) {
    $name   = $svc.Name
    $script = "$BOT_DIR\$($svc.Script)"
    $log    = "$BOT_DIR\logs\$name.log"

    $existing = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($existing) {
        # Service already registered -- stop it, update settings in-place (never remove first)
        Info "Updating existing $name ..."
        Stop-Service -Name $name -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        & $nssmDest set $name Application          $pyExe           2>&1 | Out-Null
        & $nssmDest set $name AppParameters        $script          2>&1 | Out-Null
    } else {
        # Fresh registration
        Info "Installing $name ..."
        & $nssmDest install $name $pyExe $script                    2>&1 | Out-Null
    }

    # Apply / refresh all settings (safe to run on both new and existing services)
    & $nssmDest set $name AppDirectory          $BOT_DIR            2>&1 | Out-Null
    & $nssmDest set $name AppStdout             $log                2>&1 | Out-Null
    & $nssmDest set $name AppStderr             $log                2>&1 | Out-Null
    & $nssmDest set $name AppRotateFiles        1                   2>&1 | Out-Null
    & $nssmDest set $name AppRotateBytes        10485760            2>&1 | Out-Null
    & $nssmDest set $name Start                 SERVICE_AUTO_START  2>&1 | Out-Null
    & $nssmDest set $name AppThrottle           5000                2>&1 | Out-Null
    & $nssmDest set $name AppEnvironmentExtra "PYTHONPATH=$BOT_DIR" "PYTHONUTF8=1" 2>&1 | Out-Null

    Start-Service -Name $name -ErrorAction SilentlyContinue
    if (Wait-ServiceRunning $name 60) {
        OK "$name -- running"
    } else {
        Warn "$name -- installed but not yet running (check logs\$name.log)"
    }
}

$ErrorActionPreference = $oldPref

# Firewall
New-NetFirewallRule -DisplayName "AngelBot Portal 8080" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "AngelBot IIS 80"      -Direction Inbound -Protocol TCP -LocalPort 80   -Action Allow -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "SQL Server 1433"      -Direction Inbound -Protocol TCP -LocalPort 1433 -Action Allow -ErrorAction SilentlyContinue | Out-Null
OK "Firewall rules added (8080, 80, 1433)"

# -----------------------------------------------------------------------------
# STEP 10 -- Cloudflare Tunnel
# -----------------------------------------------------------------------------
Step 10 "Cloudflare Tunnel (trading.creationsit.com)"

$cfDest     = "C:\Windows\cloudflared.exe"
$cfBundled  = "$BOT_DIR\prerequisite\setup\cloudflared\cloudflared.exe"
$cfTmpExe   = "$tmp\cloudflared.exe"
$CF_HOSTNAME = "trading.creationsit.com"

# Install binary ---------------------------------------------------------------
if (-not (Test-Path $cfDest)) {
    if (Test-Path $cfBundled) {
        try { Copy-Item $cfBundled $cfDest -Force; OK "cloudflared installed from bundle" }
        catch { Warn "Could not copy to C:\Windows -- will use full path from repo" }
    }
    if (-not (Test-Path $cfDest)) {
        Download "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" $cfTmpExe
        if (Test-Path $cfTmpExe) {
            try { Copy-Item $cfTmpExe $cfDest -Force; OK "cloudflared downloaded and installed" }
            catch { $cfDest = $cfTmpExe; OK "cloudflared at $cfDest" }
        } else { Warn "cloudflared download failed -- skipping tunnel"; $cfDest = $null }
    }
} else { OK "cloudflared already present" }

$script:cfPublicUrl = $null

# ─────────────────────────────────────────────────────────────────────────────
# CASE A: Existing 'cloudflared' Windows service (e.g. installed by MeshCentral)
#   Add trading.creationsit.com to the existing config and restart — do NOT
#   create a second tunnel service or touch any other ingress rules.
# ─────────────────────────────────────────────────────────────────────────────
$existingCfSvc = Get-Service -Name "cloudflared" -ErrorAction SilentlyContinue

if ($existingCfSvc) {
    Info "Existing 'cloudflared' service detected — patching config for $CF_HOSTNAME ..."

    # Locate the config.yml used by the running service
    $cfConfigPath = $null
    try {
        $svcPath = (Get-WmiObject Win32_Service -Filter "Name='cloudflared'").PathName
        if ($svcPath -match '--config[= ]"?([^"]+\.yml)"?') { $cfConfigPath = $Matches[1] }
    } catch {}
    if (-not $cfConfigPath -or -not (Test-Path $cfConfigPath)) {
        foreach ($candidate in @("C:\cloudflared\config.yml",
                                  "$env:USERPROFILE\.cloudflared\config.yml",
                                  "C:\ProgramData\cloudflared\config.yml")) {
            if (Test-Path $candidate) { $cfConfigPath = $candidate; break }
        }
    }

    if ($cfConfigPath -and (Test-Path $cfConfigPath)) {
        OK "Config found: $cfConfigPath"
        $cfContent = Get-Content $cfConfigPath -Raw

        if ($cfContent -match [regex]::Escape($CF_HOSTNAME)) {
            OK "$CF_HOSTNAME already in config — no changes needed"
            $script:cfPublicUrl = "https://$CF_HOSTNAME"
        } else {
            # Insert before the final catch-all line, preserving all existing rules
            $newEntry  = "  - hostname: $CF_HOSTNAME`r`n    service: http://localhost:8080"
            $cfContent = $cfContent -replace '(\r?\n\s*-\s+service:\s+http_status:404)', "`r`n$newEntry`$1"
            Set-Content $cfConfigPath $cfContent.TrimEnd() -Encoding ASCII -Force
            OK "Added $CF_HOSTNAME -> http://localhost:8080 to config"

            # Get tunnel name/UUID for DNS route
            $tunnelId = $null
            if ($cfContent -match '(?m)^tunnel:\s+(\S+)') { $tunnelId = $Matches[1] }

            if ($tunnelId -and (Test-Path $cfDest)) {
                Info "Creating DNS route: $CF_HOSTNAME -> tunnel $tunnelId ..."
                $oldPref2 = $ErrorActionPreference; $ErrorActionPreference = "Continue"
                & $cfDest tunnel route dns $tunnelId $CF_HOSTNAME 2>&1
                $ErrorActionPreference = $oldPref2
                OK "DNS CNAME created"
            } else {
                Warn "Could not auto-create DNS route — run manually:"
                Write-Host "      cloudflared tunnel route dns <TUNNEL-NAME> $CF_HOSTNAME" -ForegroundColor White
            }

            # Restart the existing service (Restart-Service works for both native and NSSM installs)
            Info "Restarting cloudflared service ..."
            try {
                Restart-Service -Name "cloudflared" -Force -ErrorAction Stop
                if (Wait-ServiceRunning "cloudflared" 30) {
                    OK "cloudflared restarted — $CF_HOSTNAME is live"
                    $script:cfPublicUrl = "https://$CF_HOSTNAME"
                } else {
                    Warn "cloudflared restart may have stalled — check: sc query cloudflared"
                }
            } catch {
                Warn "Restart-Service failed ($_) — try manually: net stop cloudflared && net start cloudflared"
            }
        }
    } else {
        Warn "Could not find cloudflared config.yml — add $CF_HOSTNAME manually:"
        Write-Host ""
        Write-Host "  1. Edit config.yml and add:" -ForegroundColor Yellow
        Write-Host "       - hostname: $CF_HOSTNAME" -ForegroundColor White
        Write-Host "         service: http://localhost:8080" -ForegroundColor White
        Write-Host "  2. cloudflared tunnel route dns <TUNNEL-NAME> $CF_HOSTNAME" -ForegroundColor White
        Write-Host "  3. net stop cloudflared && net start cloudflared" -ForegroundColor White
    }

# ─────────────────────────────────────────────────────────────────────────────
# CASE B: No existing cloudflared service — set up a fresh tunnel
# ─────────────────────────────────────────────────────────────────────────────
} elseif ($cfDest -and (Test-Path $cfDest)) {

    Write-Host ""
    Write-Host "  +----------------------------------------------------------+" -ForegroundColor Cyan
    Write-Host "  |  Cloudflare Tunnel - choose setup mode                   |" -ForegroundColor Cyan
    Write-Host "  |                                                          |" -ForegroundColor Cyan
    Write-Host "  |  [1] Quick tunnel  - instant random URL, no account      |" -ForegroundColor Cyan
    Write-Host "  |      Example: https://abc-xyz.trycloudflare.com          |" -ForegroundColor Cyan
    Write-Host "  |      Note: URL changes every restart                     |" -ForegroundColor Cyan
    Write-Host "  |                                                          |" -ForegroundColor Cyan
    Write-Host "  |  [2] Named tunnel  - trading.creationsit.com             |" -ForegroundColor Cyan
    Write-Host "  |      Stable URL, requires Cloudflare login               |" -ForegroundColor Cyan
    Write-Host "  |      Domain creationsit.com must be on Cloudflare        |" -ForegroundColor Cyan
    Write-Host "  |                                                          |" -ForegroundColor Cyan
    Write-Host "  |  [N] Skip tunnel setup                                   |" -ForegroundColor Cyan
    Write-Host "  +----------------------------------------------------------+" -ForegroundColor Cyan
    Write-Host ""
    $cfChoice = Read-Host "  Choice (1/2/N)"

    # -- OPTION 1: Quick tunnel -----------------------------------------------
    if ($cfChoice -eq "1") {
        $cfSvcName = "AngelBot-Tunnel"
        $cfLog     = "$BOT_DIR\logs\AngelBot-Tunnel.log"
        $existing  = Get-Service -Name $cfSvcName -ErrorAction SilentlyContinue
        if ($existing) {
            Stop-Service $cfSvcName -Force -ErrorAction SilentlyContinue
            & $nssmDest remove $cfSvcName confirm 2>&1 | Out-Null
            Start-Sleep -Seconds 2
        }
        & $nssmDest install $cfSvcName $cfDest "tunnel --no-autoupdate --url http://127.0.0.1:8080" 2>&1 | Out-Null
        & $nssmDest set $cfSvcName AppDirectory    $BOT_DIR             2>&1 | Out-Null
        & $nssmDest set $cfSvcName AppStdout       $cfLog               2>&1 | Out-Null
        & $nssmDest set $cfSvcName AppStderr       $cfLog               2>&1 | Out-Null
        & $nssmDest set $cfSvcName AppRotateFiles  1                    2>&1 | Out-Null
        & $nssmDest set $cfSvcName AppRotateBytes  5242880              2>&1 | Out-Null
        & $nssmDest set $cfSvcName Start           SERVICE_AUTO_START   2>&1 | Out-Null
        Start-Service $cfSvcName -ErrorAction SilentlyContinue
        if (Wait-ServiceRunning $cfSvcName 20) {
            OK "AngelBot-Tunnel (quick) running -- URL printed in logs\AngelBot-Tunnel.log"
            Write-Host ""
            Write-Host "  Run this to see your public URL:" -ForegroundColor Yellow
            Write-Host ("      Get-Content " + $cfLog + " | Select-String trycloudflare") -ForegroundColor White
        } else {
            Warn "Tunnel service installed -- check logs\AngelBot-Tunnel.log for URL"
        }

    # -- OPTION 2: Named tunnel (trading.creationsit.com) ---------------------
    } elseif ($cfChoice -eq "2") {

        Write-Host ""
        Write-Host "  Opening Cloudflare login page..." -ForegroundColor Yellow
        Write-Host "  (If no browser opens, copy the URL from below into any browser)" -ForegroundColor DarkGray
        Write-Host ""
        $oldPref2 = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        & $cfDest tunnel login 2>&1
        $loginOk = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $oldPref2

        if (-not $loginOk) {
            Warn "Cloudflare login failed or cancelled -- skipping named tunnel"
        } else {
            OK "Logged in to Cloudflare"

            # Create tunnel named after this VM
            $tunnelName = "creationsit-vm"
            Info "Creating tunnel: $tunnelName ..."
            $oldPref2 = $ErrorActionPreference; $ErrorActionPreference = "Continue"
            $createOut = & $cfDest tunnel create $tunnelName 2>&1
            $ErrorActionPreference = $oldPref2
            Write-Host ($createOut | Out-String).Trim() -ForegroundColor DarkGray

            # Find credentials file
            $cfCredDir  = "$env:USERPROFILE\.cloudflared"
            $credFile   = Get-ChildItem $cfCredDir -Filter "*.json" -ErrorAction SilentlyContinue |
                          Sort-Object LastWriteTime -Descending | Select-Object -First 1
            $tunnelUUID = if ($credFile) { $credFile.BaseName } else { $null }

            if (-not $tunnelUUID) { Warn "Could not find tunnel credentials -- check .cloudflared folder" }
            else {
                OK "Tunnel UUID: $tunnelUUID"

                # Write config — trading.creationsit.com hardcoded as the hostname
                $cfConfigPath = "C:\cloudflared\config.yml"
                New-Item -ItemType Directory -Force -Path "C:\cloudflared" | Out-Null
                @"
tunnel: $tunnelUUID
credentials-file: $($credFile.FullName)

ingress:
  - hostname: $CF_HOSTNAME
    service: http://localhost:8080
  - service: http_status:404
"@ | Out-File $cfConfigPath -Encoding ASCII -Force
                OK "Config written: $cfConfigPath"

                # Create DNS CNAME
                $oldPref2 = $ErrorActionPreference; $ErrorActionPreference = "Continue"
                & $cfDest tunnel route dns $tunnelName $CF_HOSTNAME 2>&1 | Out-Null
                $ErrorActionPreference = $oldPref2
                OK "DNS route created: $CF_HOSTNAME -> tunnel"
                $script:cfPublicUrl = "https://$CF_HOSTNAME"

                # Register as native Windows service (matches how MeshCentral installs it)
                $oldPref2 = $ErrorActionPreference; $ErrorActionPreference = "Continue"
                & $cfDest --config $cfConfigPath service install 2>&1 | Out-Null
                $ErrorActionPreference = $oldPref2
                Start-Service cloudflared -ErrorAction SilentlyContinue
                if (Wait-ServiceRunning "cloudflared" 30) {
                    OK "cloudflared service running — $CF_HOSTNAME is live"
                } else {
                    Warn "cloudflared service may not have started -- check: sc query cloudflared"
                }
            }
        }
    } else {
        Info "Skipping tunnel -- portal available at http://localhost:8080"
    }
}

# -- Done ----------------------------------------------------------------------
Write-Host ""
Write-Host "  ============================================================" -ForegroundColor Green
Write-Host "    AngelBot installed and running!" -ForegroundColor Green
Write-Host "  ============================================================" -ForegroundColor Green
Write-Host "    Portal (local)    : http://localhost:8080" -ForegroundColor Green
Write-Host "    Portal (via IIS)  : http://localhost" -ForegroundColor Green
if ($script:cfPublicUrl) {
Write-Host "    Portal (public)   : $($script:cfPublicUrl)" -ForegroundColor Green
} else {
Write-Host "    Portal (public)   : https://trading.creationsit.com  (after tunnel restart)" -ForegroundColor DarkGray
}
Write-Host "    Default login     : admin / AngelBot@1234" -ForegroundColor Green
Write-Host "    SQL Server        : .\ANGELBOT  (SA password in .env)" -ForegroundColor Green
Write-Host "    Logs              : $BOT_DIR\logs\" -ForegroundColor Green
Write-Host "    Config            : $BOT_DIR\.env" -ForegroundColor Green
Write-Host "    4 bot services + cloudflared auto-start on every reboot." -ForegroundColor Green
Write-Host "    (India, US, Crypto, Portal + Cloudflare tunnel)" -ForegroundColor Green
Write-Host "  ============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  NEXT: Open the portal and go to API Keys to enter your" -ForegroundColor Yellow
Write-Host "  Angel One / Alpaca / Binance / Telegram credentials." -ForegroundColor Yellow
Write-Host ""
Write-Host "  If a service shows yellow, check $BOT_DIR\logs\" -ForegroundColor DarkGray
Write-Host ""
Log "=== Installation complete ==="

Start-Sleep -Seconds 3
if ($script:cfPublicUrl) {
    Start-Process $script:cfPublicUrl
} else {
    Start-Process "http://localhost:8080"
}
Write-Host "  Press Enter to close ..." -ForegroundColor DarkGray
Read-Host | Out-Null
