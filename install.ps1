#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

$BOT_DIR  = "C:\AngelBot"
$LOG_FILE = "C:\angelbot_install.log"
$tmp      = "C:\angelbot_tmp"

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LOG_FILE -Value "$ts  $msg" -ErrorAction SilentlyContinue
}
function Info($msg) { Write-Host "     $msg" -ForegroundColor Cyan   ; Log $msg }
function OK($msg)   { Write-Host "  OK  $msg" -ForegroundColor Green ; Log "OK: $msg" }
function Warn($msg) { Write-Host "  !!  $msg" -ForegroundColor Yellow; Log "WARN: $msg" }
function Err($msg)  { Write-Host "  XX  $msg" -ForegroundColor Red   ; Log "ERR: $msg" }

function Step($n, $t) {
    Write-Host ""
    Write-Host "  --- Step $n/9 : $t ---" -ForegroundColor White
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
    $env:PATH = $machine + ";" + $user
}

# ── Enable SQL Server TCP/IP via SMO registry ─────────────────────────────────
function Enable-SqlTcp {
    param($instanceName = "ANGELBOT")
    $regBase = "HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server"
    # Find the instance key
    $instances = Get-ChildItem $regBase -ErrorAction SilentlyContinue |
        Where-Object { $_.PSChildName -match "^MSSQL\d+\.$instanceName$" }
    foreach ($inst in $instances) {
        $tcpPath = "$($inst.PSPath)\MSSQLServer\SuperSocketNetLib\Tcp"
        if (Test-Path $tcpPath) {
            Set-ItemProperty -Path $tcpPath -Name "Enabled" -Value 1 -ErrorAction SilentlyContinue
        }
    }
}

# Banner
Clear-Host
Write-Host ""
Write-Host "  ============================================================" -ForegroundColor Cyan
Write-Host "        AngelBot  --  One-Click Windows Installer             " -ForegroundColor Cyan
Write-Host "   Python / Git / SQL Server Express / NSSM / IIS             " -ForegroundColor Cyan
Write-Host "  ============================================================" -ForegroundColor Cyan
Write-Host ""

New-Item -ItemType Directory -Force -Path $tmp            | Out-Null
New-Item -ItemType Directory -Force -Path "$BOT_DIR\logs" | Out-Null
Log "=== Installer started ==="

# ---------------------------------------------------------------------------
# STEP 1 -- Python 3.11
# ---------------------------------------------------------------------------
Step 1 "Python 3.11"
$pyOK = $false
try { $pyOK = ((python --version 2>&1) -match "Python 3\.(9|10|11|12)") } catch {}

if (-not $pyOK) {
    $exe = "$tmp\python-3.11.9-amd64.exe"
    Download "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" $exe
    Info "Installing Python 3.11 silently (1-2 min) ..."
    Start-Process $exe -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 Include_test=0" -Wait -NoNewWindow
    Update-EnvPath
    try { $pyOK = ((python --version 2>&1) -match "Python 3") } catch {}
    if (-not $pyOK) { Err "Python install failed. Check $LOG_FILE"; Read-Host; exit 1 }
    OK "Python 3.11 installed"
} else {
    OK "Python already installed -- $((python --version 2>&1))"
}

# ---------------------------------------------------------------------------
# STEP 2 -- Git
# ---------------------------------------------------------------------------
Step 2 "Git"
$gitFound = $null
try { $gitFound = Get-Command git -ErrorAction SilentlyContinue } catch {}

if (-not $gitFound) {
    $exe = "$tmp\git-installer.exe"
    Download "https://github.com/git-for-windows/git/releases/download/v2.45.2.windows.1/Git-2.45.2-64-bit.exe" $exe
    Info "Installing Git silently ..."
    Start-Process $exe -ArgumentList "/VERYSILENT /NORESTART /NOCANCEL /SP- /CLOSEAPPLICATIONS" -Wait -NoNewWindow
    Update-EnvPath
    try { $gitFound = Get-Command git -ErrorAction SilentlyContinue } catch {}
    if (-not $gitFound) { Err "Git install failed."; Read-Host; exit 1 }
    OK "Git installed"
} else {
    OK "Git already installed"
}

# ---------------------------------------------------------------------------
# STEP 3 -- Clone / update AngelBot
# ---------------------------------------------------------------------------
Step 3 "AngelBot code"
$oldPref = $ErrorActionPreference
$ErrorActionPreference = "Continue"
if (Test-Path "$BOT_DIR\.git") {
    Info "Updating to latest code ..."
    $null = git -C $BOT_DIR fetch --all 2>&1
    $null = git -C $BOT_DIR reset --hard origin/main 2>&1
    OK "Code updated"
} elseif (Test-Path $BOT_DIR) {
    Info "Folder exists but not a git repo -- cleaning and cloning ..."
    Remove-Item $BOT_DIR -Recurse -Force -ErrorAction SilentlyContinue
    $null = git clone https://github.com/HackMe7822/AngelBot.git $BOT_DIR 2>&1
    OK "Downloaded to $BOT_DIR"
} else {
    Info "Cloning from GitHub ..."
    $null = git clone https://github.com/HackMe7822/AngelBot.git $BOT_DIR 2>&1
    OK "Downloaded to $BOT_DIR"
}
$ErrorActionPreference = $oldPref
New-Item -ItemType Directory -Force -Path "$BOT_DIR\learning" | Out-Null
New-Item -ItemType Directory -Force -Path "$BOT_DIR\logs"     | Out-Null

# ---------------------------------------------------------------------------
# STEP 4 -- SQL Server 2019 Express (instance: ANGELBOT)
# ---------------------------------------------------------------------------
Step 4 "SQL Server 2019 Express"
$sqlSvc = Get-Service -Name "MSSQL`$ANGELBOT" -ErrorAction SilentlyContinue
if ($sqlSvc) {
    OK "SQL Server instance ANGELBOT already installed"
    # Make sure it is running
    if ($sqlSvc.Status -ne "Running") {
        Start-Service "MSSQL`$ANGELBOT" -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
    }
    # Ensure SQL Browser is running (required for named instances)
    $browser = Get-Service -Name "SQLBrowser" -ErrorAction SilentlyContinue
    if ($browser) {
        Set-Service -Name "SQLBrowser" -StartupType Automatic -ErrorAction SilentlyContinue
        Start-Service "SQLBrowser" -ErrorAction SilentlyContinue
        OK "SQL Server Browser running"
    }
} else {
    Write-Host ""
    Write-Host "  SQL Server Express will be installed as instance: ANGELBOT" -ForegroundColor White
    Write-Host "  Set the SA password (min 8 chars, must include uppercase + number)." -ForegroundColor DarkGray
    Write-Host "  IMPORTANT: write this down -- it goes into .env as SQL_SA_PASS" -ForegroundColor Yellow
    Write-Host ""
    $saSecure = Read-Host "  SA Password" -AsSecureString
    $script:saPass = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($saSecure))

    # Download bootstrapper then full media
    $ssei = "$tmp\sql_ssei.exe"
    Download "https://go.microsoft.com/fwlink/p/?linkid=866658" $ssei

    $sqlMedia = "$tmp\sql_media"
    New-Item -ItemType Directory -Force -Path $sqlMedia | Out-Null
    $fullExe = Get-ChildItem $sqlMedia -Filter "SQLEXPR*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $fullExe) {
        Info "Downloading SQL Server 2019 Express full package (~280 MB) ..."
        Start-Process $ssei -ArgumentList "/Action=Download /MediaPath=`"$sqlMedia`" /MediaType=Advanced /Quiet" -Wait -NoNewWindow
        $fullExe = Get-ChildItem $sqlMedia -Filter "SQLEXPR*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    }

    if (-not $fullExe) {
        Warn "SQL Server download failed -- install manually then re-run."
    } else {
        Info "Installing SQL Server 2019 Express (3-5 min) ..."
        $sqlArgs = "/Q /IACCEPTSQLSERVERLICENSETERMS /ACTION=Install /FEATURES=SQLEngine" +
                   " /INSTANCENAME=ANGELBOT" +
                   " /SQLSYSADMINACCOUNTS=`"$env:USERDOMAIN\$env:USERNAME`"" +
                   " /SECURITYMODE=SQL /SAPWD=`"$($script:saPass)`"" +
                   " /TCPENABLED=1 /BROWSERSVCSTARTUPTYPE=Automatic"
        Start-Process $fullExe.FullName -ArgumentList $sqlArgs -Wait -NoNewWindow
        Log "SQL Server install attempted"

        # Ensure TCP is enabled in registry (installer flag sometimes ignored)
        Enable-SqlTcp -instanceName "ANGELBOT"

        # Start SQL Server Browser (needed for .\ANGELBOT named instance)
        $browser = Get-Service -Name "SQLBrowser" -ErrorAction SilentlyContinue
        if ($browser) {
            Set-Service -Name "SQLBrowser" -StartupType Automatic -ErrorAction SilentlyContinue
            Start-Service "SQLBrowser" -ErrorAction SilentlyContinue
            OK "SQL Server Browser started"
        }

        # Start the SQL Server instance
        $sqlSvc = Get-Service -Name "MSSQL`$ANGELBOT" -ErrorAction SilentlyContinue
        if ($sqlSvc) {
            Start-Service "MSSQL`$ANGELBOT" -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 5
            OK "SQL Server ANGELBOT installed and running"
        } else {
            Warn "SQL Server may need a reboot to complete -- continuing"
        }
    }

    # Open SQL Server port in firewall
    New-NetFirewallRule -DisplayName "SQL Server 1433" -Direction Inbound -Protocol TCP -LocalPort 1433 -Action Allow -ErrorAction SilentlyContinue | Out-Null
    OK "SQL Server port 1433 opened in firewall"
}

# ---------------------------------------------------------------------------
# STEP 5 -- ODBC Driver 17 + Python packages
# ---------------------------------------------------------------------------
Step 5 "ODBC Driver 17 + Python packages"

# PS5.1-safe ODBC check: use Test-Path on registry key (no nested script blocks)
$odbcRegKey = "HKLM:\SOFTWARE\ODBC\ODBCINST.INI\ODBC Driver 17 for SQL Server"
if (Test-Path $odbcRegKey) {
    OK "ODBC Driver 17 for SQL Server already installed"
} else {
    Info "Downloading ODBC Driver 17 for SQL Server (~6 MB)..."
    $odbcMsi = "$tmp\msodbcsql17.msi"
    Download "https://go.microsoft.com/fwlink/?linkid=2168524" $odbcMsi
    if (Test-Path $odbcMsi) {
        Start-Process msiexec -ArgumentList "/i `"$odbcMsi`" /qn IACCEPTMSODBCSQLLICENSETERMS=YES" -Wait -NoNewWindow
        OK "ODBC Driver 17 installed"
    } else {
        Warn "ODBC Driver 17 download failed -- pyodbc may not connect to SQL Server"
    }
}

Info "Running pip install (2-4 min) ..."
python -m pip install --upgrade pip --quiet
python -m pip install -r "$BOT_DIR\requirements.txt" --quiet
OK "All Python packages installed"

# ---------------------------------------------------------------------------
# STEP 6 -- API Keys (.env)
# ---------------------------------------------------------------------------
Step 6 "API Keys (.env)"
$envPath = "$BOT_DIR\.env"

# Always ensure SQL_SA_PASS is in .env
function Set-EnvKey($path, $key, $value) {
    if (Test-Path $path) {
        $content = Get-Content $path -Raw -ErrorAction SilentlyContinue
        if ($content -match "(?m)^$key=") {
            # Update existing
            $content = $content -replace "(?m)^$key=.*", "$key=$value"
            Set-Content $path $content.TrimEnd() -Encoding ASCII
        } else {
            Add-Content $path "`n$key=$value" -Encoding ASCII
        }
    } else {
        Set-Content $path "$key=$value" -Encoding ASCII
    }
}

# Check if .env has all required keys
$envHasKeys = $false
if (Test-Path $envPath) {
    $envTxt = Get-Content $envPath -Raw -ErrorAction SilentlyContinue
    if ($envTxt -match "ALPACA_KEY=" -and $envTxt -match "ANGEL_API_KEY=") {
        $envHasKeys = $true
    }
}

if ($envHasKeys) {
    OK ".env already complete -- skipping API key entry"
    # Still ensure SQL_SA_PASS is there
    if ($script:saPass) {
        Set-EnvKey $envPath "SQL_SA_PASS" $script:saPass
        OK "SQL_SA_PASS added/updated in .env"
    } elseif ((Get-Content $envPath -Raw) -notmatch "SQL_SA_PASS=") {
        $saSecure2 = Read-Host "  .env exists but SQL_SA_PASS is missing. Enter SA Password"
        Set-EnvKey $envPath "SQL_SA_PASS" $saSecure2
        OK "SQL_SA_PASS added to .env"
    }
} else {
    Write-Host ""
    Write-Host "  Enter your API keys. Bot runs in PAPER mode by default." -ForegroundColor White
    Write-Host ""
    Write-Host "  Angel One (India NSE)" -ForegroundColor Yellow
    $aKey     = Read-Host "     API Key"
    $aSecret  = Read-Host "     Client Secret"
    $aClient  = Read-Host "     Client ID"
    $aPin     = Read-Host "     MPIN (4-digit)"
    $aTotp    = Read-Host "     TOTP Secret"
    Write-Host "  Alpaca (US paper trading)" -ForegroundColor Yellow
    $alKey    = Read-Host "     API Key"
    $alSecret = Read-Host "     Secret Key"
    Write-Host "  Binance (Crypto)" -ForegroundColor Yellow
    $bKey     = Read-Host "     API Key"
    $bSecret  = Read-Host "     Secret Key"
    Write-Host "  Telegram (optional -- press Enter to skip)" -ForegroundColor Yellow
    $tgToken  = Read-Host "     Bot Token"
    $tgChatId = Read-Host "     Chat ID"
    Write-Host "  Portal login" -ForegroundColor Yellow
    $pUser    = Read-Host "     Username  (Enter = admin)"
    $pPass    = Read-Host "     Password  (Enter = AngelBot@1234)"
    Write-Host "  Starting capital" -ForegroundColor Yellow
    $indCap   = Read-Host "     India capital INR  (Enter = 100000)"
    $usCap    = Read-Host "     US capital USD     (Enter = 10000)"
    $cryCap   = Read-Host "     Crypto capital USD (Enter = 1000)"
    if (-not $pUser)   { $pUser   = "admin" }
    if (-not $pPass)   { $pPass   = "AngelBot@1234" }
    if (-not $indCap)  { $indCap  = "100000" }
    if (-not $usCap)   { $usCap   = "10000" }
    if (-not $cryCap)  { $cryCap  = "1000" }

    # Collect SA password if not already entered in Step 4
    $saForEnv = if ($script:saPass) { $script:saPass } else {
        Read-Host "  SQL Server SA Password (same as entered in Step 4)"
    }

    $envLines = @(
        "# Angel One (India)",
        "ANGEL_API_KEY=$aKey",
        "ANGEL_SECRET=$aSecret",
        "ANGEL_CLIENT_ID=$aClient",
        "ANGEL_PIN=$aPin",
        "ANGEL_TOTP_SECRET=$aTotp",
        "",
        "# Alpaca (US)",
        "ALPACA_KEY=$alKey",
        "ALPACA_SECRET=$alSecret",
        "ALPACA_PAPER=true",
        "",
        "# Binance (Crypto)",
        "BINANCE_KEY=$bKey",
        "BINANCE_SECRET=$bSecret",
        "BINANCE_PAPER=true",
        "",
        "# Telegram",
        "TELEGRAM_BOT_TOKEN=$tgToken",
        "TELEGRAM_CHAT_ID=$tgChatId",
        "",
        "# Capital",
        "INDIA_CAPITAL=$indCap",
        "US_CAPITAL=$usCap",
        "CRYPTO_CAPITAL=$cryCap",
        "",
        "# Safety flags -- NEVER set these to false via portal",
        "PAPER_MODE=true",
        "",
        "# Portal",
        "PORTAL_PASS=$pPass",
        "",
        "# SQL Server",
        "SQL_SA_PASS=$saForEnv"
    )
    $envLines -join "`r`n" | Out-File $envPath -Encoding ASCII -Force
    OK ".env saved with all keys"
}

# ---------------------------------------------------------------------------
# STEP 7 -- Initialize database (create tables)
# ---------------------------------------------------------------------------
Step 7 "Database initialization"
$sqlSvcCheck = Get-Service -Name "MSSQL`$ANGELBOT" -ErrorAction SilentlyContinue
if ($sqlSvcCheck -and $sqlSvcCheck.Status -eq "Running") {
    Info "Creating database and tables ..."
    $initScript = "import sys; sys.path.insert(0,'$($BOT_DIR -replace '\\','/')'); from data.database import init_db; init_db()"
    $result = python -c $initScript 2>&1
    if ($LASTEXITCODE -eq 0) {
        OK "Database tables ready"
    } else {
        Warn "Database init returned errors -- workers will retry on start: $result"
    }
} else {
    Warn "SQL Server not running yet -- database will be created when services first start"
}

# ---------------------------------------------------------------------------
# STEP 8 -- IIS + URL Rewrite + ARR (reverse proxy port 80 -> 8080)
# ---------------------------------------------------------------------------
Step 8 "IIS + URL Rewrite + ARR"

$iisSvc = Get-Service -Name "W3SVC" -ErrorAction SilentlyContinue
if (-not $iisSvc) {
    Info "Enabling IIS via DISM ..."
    $features = @(
        "IIS-WebServerRole", "IIS-WebServer", "IIS-CommonHttpFeatures",
        "IIS-HttpErrors", "IIS-HttpRedirect", "IIS-ApplicationDevelopment",
        "IIS-CGI", "IIS-ISAPIExtensions", "IIS-ISAPIFilter",
        "IIS-WebServerManagementTools", "IIS-ManagementConsole"
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
        Start-Process "msiexec.exe" -ArgumentList "/i `"$urMsi`" /quiet /norestart" -Wait
        OK "URL Rewrite installed"
    }
} else {
    OK "URL Rewrite already installed"
}

$arrKey = "HKLM:\SOFTWARE\Microsoft\IIS Extensions\Application Request Routing"
if (-not (Test-Path $arrKey)) {
    $arrExe = "$tmp\ARRv3_setup_amd64_en-us.exe"
    $arrUrls = @(
        "https://download.microsoft.com/download/E/9/8/E9849D6A-020E-47E4-9FD0-A023E99B54EB/ARRv3_setup_amd64_en-us.exe"
    )
    foreach ($url in $arrUrls) {
        if (-not (Test-Path $arrExe)) {
            try { Invoke-WebRequest -Uri $url -OutFile $arrExe -UseBasicParsing -ErrorAction Stop } catch {}
        }
    }
    if ((Test-Path $arrExe) -and (Get-Item $arrExe).Length -gt 100000) {
        Info "Installing ARR ..."
        Start-Process $arrExe -ArgumentList "/quiet /norestart" -Wait
        OK "ARR installed"
    } else {
        Warn "ARR download unavailable -- portal still works at http://<ip>:8080"
        Remove-Item $arrExe -ErrorAction SilentlyContinue
    }
} else {
    OK "ARR already installed"
}

New-Item -ItemType Directory -Force -Path "C:\inetpub\wwwroot" | Out-Null
$wc = '<?xml version="1.0" encoding="utf-8"?><configuration><system.webServer><rewrite><rules><rule name="AngelBot" stopProcessing="true"><match url="(.*)" /><action type="Rewrite" url="http://localhost:8080/{R:1}" /></rule></rules></rewrite></system.webServer></configuration>'
$wc | Out-File "C:\inetpub\wwwroot\web.config" -Encoding ASCII -Force
OK "IIS reverse proxy configured (port 80 -> port 8080)"
Start-Service W3SVC -ErrorAction SilentlyContinue

# ---------------------------------------------------------------------------
# STEP 9 -- NSSM + Windows Services
# ---------------------------------------------------------------------------
Step 9 "NSSM + Windows Services"
$nssmDest = "C:\Windows\nssm.exe"
if (-not (Test-Path $nssmDest)) {
    $nssmBundled = "$BOT_DIR\prerequisite\setup\nssm\nssm.exe"
    if (Test-Path $nssmBundled) {
        Copy-Item $nssmBundled $nssmDest -Force
        OK "NSSM installed from repo"
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
            Copy-Item $found.FullName $nssmDest -Force
            OK "NSSM installed from download"
        } else {
            Err "NSSM not available -- services cannot be registered"; Read-Host; exit 1
        }
    }
} else {
    OK "NSSM already present"
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
        Start-Sleep -Seconds 1
        & $nssmDest remove $name confirm 2>$null
        Start-Sleep -Seconds 1
    }

    & $nssmDest install         $name $pyExe $script
    & $nssmDest set $name AppDirectory      $BOT_DIR
    & $nssmDest set $name AppStdout         $log
    & $nssmDest set $name AppStderr         $log
    & $nssmDest set $name AppRotateFiles    1
    & $nssmDest set $name AppRotateBytes    10485760
    & $nssmDest set $name Start             SERVICE_AUTO_START
    & $nssmDest set $name AppThrottle       5000
    # Pass working directory so load_dotenv() finds .env
    & $nssmDest set $name AppEnvironmentExtra "PYTHONPATH=$BOT_DIR"

    Start-Service -Name $name -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    $svcObj = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($svcObj -and ($svcObj.Status -eq "Running")) {
        OK "$name -- running"
    } else {
        Warn "$name -- installed (may take a few seconds to start)"
    }
}

# Firewall
New-NetFirewallRule -DisplayName "AngelBot Portal 8080" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "AngelBot IIS 80"      -Direction Inbound -Protocol TCP -LocalPort 80   -Action Allow -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "SQL Server 1433"       -Direction Inbound -Protocol TCP -LocalPort 1433 -Action Allow -ErrorAction SilentlyContinue | Out-Null

# Cloudflare (optional)
$cfDest = "C:\Windows\cloudflared.exe"
if (-not (Test-Path $cfDest)) {
    $cfExe = "$tmp\cloudflared.exe"
    Download "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" $cfExe
    if (Test-Path $cfExe) {
        Copy-Item $cfExe $cfDest -Force
        OK "Cloudflared installed (run: cloudflared tunnel --url http://localhost:8080)"
    } else {
        Warn "Cloudflared download failed -- skipping (optional)"
    }
} else {
    OK "Cloudflared already present"
}

# Done
Write-Host ""
Write-Host "  ============================================================" -ForegroundColor Green
Write-Host "    AngelBot installed and running!" -ForegroundColor Green
Write-Host "  ============================================================" -ForegroundColor Green
Write-Host "    Portal (direct)  : http://localhost:8080" -ForegroundColor Green
Write-Host "    Portal (via IIS) : http://localhost (port 80)" -ForegroundColor Green
Write-Host "    Default login    : admin / AngelBot@1234" -ForegroundColor Green
Write-Host "    SQL Server       : .\ANGELBOT  (SA password in .env)" -ForegroundColor Green
Write-Host "    Logs             : C:\AngelBot\logs\" -ForegroundColor Green
Write-Host "    Config file      : C:\AngelBot\.env" -ForegroundColor Green
Write-Host "    All 4 workers auto-start on every reboot." -ForegroundColor Green
Write-Host "  ============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  If any service shows yellow (not yet running), wait 30s" -ForegroundColor DarkGray
Write-Host "  and run fix_services.ps1 to diagnose." -ForegroundColor DarkGray
Write-Host ""
Log "=== Installation complete ==="

Start-Sleep -Seconds 3
Start-Process "http://localhost:8080"
Write-Host "  Press Enter to close ..." -ForegroundColor DarkGray
Read-Host | Out-Null
