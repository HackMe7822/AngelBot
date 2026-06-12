#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

$BOT_DIR   = "C:\AngelBot"
$LOG_FILE  = "C:\angelbot_install.log"
$tmp       = "C:\angelbot_tmp"

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

# Banner
Clear-Host
Write-Host ""
Write-Host "  ============================================================" -ForegroundColor Cyan
Write-Host "        AngelBot  --  One-Click Windows Installer             " -ForegroundColor Cyan
Write-Host "   Python / Git / SQL Server / IIS / NSSM / Cloudflare        " -ForegroundColor Cyan
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
} else {
    Write-Host ""
    Write-Host "  Set a password for SQL Server SA account." -ForegroundColor White
    Write-Host "  Min 8 chars, must include uppercase + number" -ForegroundColor DarkGray
    $saSecure = Read-Host "  SA Password" -AsSecureString
    $saPass   = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($saSecure))

    # Step 1: download the SSEI web bootstrapper
    $ssei = "$tmp\sql_ssei.exe"
    if (-not (Test-Path $ssei)) {
        Info "Downloading SQL Server installer bootstrapper ..."
        Download "https://go.microsoft.com/fwlink/p/?linkid=866658" $ssei
    }

    # Step 2: use SSEI to download the full installer package (~280 MB)
    $sqlMedia = "$tmp\sql_media"
    New-Item -ItemType Directory -Force -Path $sqlMedia | Out-Null
    $fullExe = Get-ChildItem $sqlMedia -Filter "SQLEXPR*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $fullExe) {
        Info "Downloading SQL Server 2019 Express full package (280 MB) ..."
        Start-Process $ssei -ArgumentList "/Action=Download /MediaPath=`"$sqlMedia`" /MediaType=Advanced /Quiet" -Wait -NoNewWindow
        $fullExe = Get-ChildItem $sqlMedia -Filter "SQLEXPR*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    }

    if (-not $fullExe) {
        Warn "SQL Server download failed -- skipping. Install manually later if needed."
    } else {
        # Step 3: silent install from full package
        Info "Installing SQL Server 2019 Express (3-5 min) ..."
        $sqlArgs = "/Q /IACCEPTSQLSERVERLICENSETERMS /ACTION=Install /FEATURES=SQLEngine" +
                   " /INSTANCENAME=ANGELBOT" +
                   " /SQLSYSADMINACCOUNTS=`"$env:USERDOMAIN\$env:USERNAME`"" +
                   " /SECURITYMODE=SQL /SAPWD=`"$saPass`"" +
                   " /TCPENABLED=1 /BROWSERSVCSTARTUPTYPE=Automatic"
        Start-Process $fullExe.FullName -ArgumentList $sqlArgs -Wait -NoNewWindow
        Log "SQL Server install attempted"

        $sqlSvc = Get-Service -Name "MSSQL`$ANGELBOT" -ErrorAction SilentlyContinue
        if ($sqlSvc) { OK "SQL Server ANGELBOT installed" }
        else { Warn "SQL Server may need a reboot to complete -- continuing" }
    }
}

# ---------------------------------------------------------------------------
# STEP 5 -- IIS + URL Rewrite + ARR
# ---------------------------------------------------------------------------
Step 5 "IIS + URL Rewrite + ARR"

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

# URL Rewrite
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

# ARR 3.0
$arrKey = "HKLM:\SOFTWARE\Microsoft\IIS Extensions\Application Request Routing"
if (-not (Test-Path $arrKey)) {
    $arrExe = "$tmp\ARRv3_setup_amd64_en-us.exe"
    # Try multiple known mirrors
    $arrUrls = @(
        "https://download.microsoft.com/download/E/9/8/E9849D6A-020E-47E4-9FD0-A023E99B54EB/ARRv3_setup_amd64_en-us.exe",
        "https://www.iis.net/downloads/microsoft/application-request-routing"
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
        Warn "ARR download unavailable -- portal still works at http://localhost:8080"
        Remove-Item $arrExe -ErrorAction SilentlyContinue
    }
} else {
    OK "ARR already installed"
}

# IIS reverse proxy web.config
New-Item -ItemType Directory -Force -Path "C:\inetpub\wwwroot" | Out-Null
$wc = '<?xml version="1.0" encoding="utf-8"?><configuration><system.webServer><rewrite><rules><rule name="AngelBot" stopProcessing="true"><match url="(.*)" /><action type="Rewrite" url="http://localhost:8080/{R:1}" /></rule></rules></rewrite></system.webServer></configuration>'
$wc | Out-File "C:\inetpub\wwwroot\web.config" -Encoding ASCII -Force
OK "IIS reverse proxy configured (port 80 -> port 8080)"
Start-Service W3SVC -ErrorAction SilentlyContinue

# ---------------------------------------------------------------------------
# STEP 6 -- API Keys (.env)
# ---------------------------------------------------------------------------
Step 6 "API Keys"
if (Test-Path "$BOT_DIR\.env") {
    OK ".env already exists -- skipping"
} else {
    Write-Host ""
    Write-Host "  Enter your API keys. Bot runs in PAPER mode." -ForegroundColor White
    Write-Host ""
    Write-Host "  Angel One (India)" -ForegroundColor Yellow
    $aKey     = Read-Host "     API Key"
    $aSecret  = Read-Host "     Client Secret"
    $aClient  = Read-Host "     Client ID"
    $aPin     = Read-Host "     MPIN 4-digit"
    $aTotp    = Read-Host "     TOTP Secret"
    Write-Host "  Alpaca (US paper)" -ForegroundColor Yellow
    $alKey    = Read-Host "     API Key"
    $alSecret = Read-Host "     Secret Key"
    Write-Host "  Binance (Crypto)" -ForegroundColor Yellow
    $bKey     = Read-Host "     API Key"
    $bSecret  = Read-Host "     Secret Key"
    Write-Host "  Telegram" -ForegroundColor Yellow
    $tgToken  = Read-Host "     Bot Token"
    $tgChatId = Read-Host "     Chat ID"
    Write-Host "  Portal login" -ForegroundColor Yellow
    $pUser = Read-Host "     Username  (Enter = admin)"
    $pPass = Read-Host "     Password  (Enter = AngelBot@1234)"
    if (-not $pUser) { $pUser = "admin" }
    if (-not $pPass) { $pPass = "AngelBot@1234" }

    $envContent = "ANGEL_API_KEY=$aKey`r`nANGEL_SECRET=$aSecret`r`nANGEL_CLIENT_ID=$aClient`r`nANGEL_PIN=$aPin`r`nANGEL_TOTP_SECRET=$aTotp`r`nALPACA_KEY=$alKey`r`nALPACA_SECRET=$alSecret`r`nALPACA_PAPER=true`r`nBINANCE_KEY=$bKey`r`nBINANCE_SECRET=$bSecret`r`nBINANCE_PAPER=true`r`nTELEGRAM_BOT_TOKEN=$tgToken`r`nTELEGRAM_CHAT_ID=$tgChatId`r`nINDIA_CAPITAL=10000`r`nUS_CAPITAL=10000`r`nCRYPTO_CAPITAL=1000`r`nPAPER_MODE=true`r`nPORTAL_USER=$pUser`r`nPORTAL_PASS=$pPass"
    $envContent | Out-File "$BOT_DIR\.env" -Encoding ASCII -Force
    OK ".env saved"
}

# ---------------------------------------------------------------------------
# STEP 7 -- Python packages
# ---------------------------------------------------------------------------
Step 7 "Python packages"
Info "Running pip install (2-4 min) ..."
python -m pip install --upgrade pip --quiet
python -m pip install -r "$BOT_DIR\requirements.txt" --quiet
OK "All packages installed"

# ---------------------------------------------------------------------------
# STEP 8 -- NSSM + Windows Services
# ---------------------------------------------------------------------------
Step 8 "NSSM + Windows Services"
$nssmDest = "C:\Windows\nssm.exe"
if (-not (Test-Path $nssmDest)) {
    # Use bundled nssm.exe from repo (most reliable)
    $nssmBundled = "$BOT_DIR\prerequisite\setup\nssm\nssm.exe"
    if (Test-Path $nssmBundled) {
        Copy-Item $nssmBundled $nssmDest -Force
        OK "NSSM installed from repo"
    } else {
        # Fallback: download from nssm.cc release (stable zip)
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
    $svcObj = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($svcObj -and ($svcObj.Status -eq "Running")) {
        OK "$name -- running"
    } else {
        Warn "$name -- installed (starting shortly)"
    }
}

# ---------------------------------------------------------------------------
# STEP 9 -- Cloudflare Tunnel
# ---------------------------------------------------------------------------
Step 9 "Cloudflare Tunnel"
$cfDest = "C:\Windows\cloudflared.exe"
if (-not (Test-Path $cfDest)) {
    $cfExe = "$tmp\cloudflared.exe"
    Download "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" $cfExe
    if (Test-Path $cfExe) {
        Copy-Item $cfExe $cfDest -Force
        OK "Cloudflared installed"
    } else {
        Warn "Cloudflared download failed -- skipping (optional)"
    }
} else {
    OK "Cloudflared already present"
}

# Firewall
New-NetFirewallRule -DisplayName "AngelBot 8080" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "AngelBot IIS"  -Direction Inbound -Protocol TCP -LocalPort 80   -Action Allow -ErrorAction SilentlyContinue | Out-Null

# Done
Write-Host ""
Write-Host "  ============================================================" -ForegroundColor Green
Write-Host "    AngelBot installed and running!" -ForegroundColor Green
Write-Host "  ============================================================" -ForegroundColor Green
Write-Host "    Portal (direct)  : http://localhost:8080" -ForegroundColor Green
Write-Host "    Portal (via IIS) : http://localhost" -ForegroundColor Green
Write-Host "    SQL Server       : .\ANGELBOT (port 1433)" -ForegroundColor Green
Write-Host "    Logs             : C:\AngelBot\logs\" -ForegroundColor Green
Write-Host "    All 4 workers auto-start on every reboot." -ForegroundColor Green
Write-Host "  ============================================================" -ForegroundColor Green
Write-Host ""
Log "=== Installation complete ==="

Start-Sleep -Seconds 3
Start-Process "http://localhost:8080"
Write-Host "  Press Enter to close ..." -ForegroundColor DarkGray
Read-Host | Out-Null
