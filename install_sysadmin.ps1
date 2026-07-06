#Requires -RunAsAdministrator
# install_sysadmin.ps1 -- One-click deploy: sysadmin.creationsit.com on IIS + Cloudflare tunnel
# Usage: powershell -ExecutionPolicy Bypass -File install_sysadmin.ps1

param(
    [string]$GitHubPAT  = "",
    [string]$SiteDir    = "C:\inetpub\wwwroot\sysadmin",
    [int]   $SitePort   = 8081,
    [string]$Hostname   = "sysadmin.creationsit.com",
    [string]$CfConfig   = "C:\cloudflared\config.yml",
    [string]$CfService  = "cloudflared",
    [string]$Repo       = "https://github.com/HackMe7822/sysadmin-interview-qa"
)

function OK($m)   { Write-Host "  OK  $m" -ForegroundColor Green }
function Info($m) { Write-Host "  --> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  WARN $m" -ForegroundColor Yellow }
function Err($m)  { Write-Host "  ERR $m" -ForegroundColor Red }
function Step($n, $m) {
    Write-Host ""
    Write-Host "  [$n] $m" -ForegroundColor Magenta
}

Write-Host ""
Write-Host "  ============================================================" -ForegroundColor Cyan
Write-Host "    sysadmin.creationsit.com -- one-click deploy" -ForegroundColor Cyan
Write-Host "  ============================================================" -ForegroundColor Cyan

# -----------------------------------------------------------------------------
# STEP 1 -- GitHub PAT
# -----------------------------------------------------------------------------
Step 1 "GitHub authentication (private repo)"

if (-not $GitHubPAT) {
    Write-Host "  The repo is private. Enter a GitHub Personal Access Token (classic, 'repo' scope)." -ForegroundColor Yellow
    Write-Host "  Create one at: https://github.com/settings/tokens" -ForegroundColor DarkGray
    $GitHubPAT = Read-Host "  GitHub PAT"
}
if (-not $GitHubPAT) { Err "No PAT provided -- cannot clone private repo. Exiting."; exit 1 }

$cloneUrl = $Repo -replace "https://", "https://$GitHubPAT@"

# -----------------------------------------------------------------------------
# STEP 2 -- Clone or pull repo
# -----------------------------------------------------------------------------
Step 2 "Cloning / updating repo -> $SiteDir"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Err "git not found. Install Git first (or run AngelBot install.ps1 which installs Git)."; exit 1
}

if (Test-Path "$SiteDir\.git") {
    Info "Repo already cloned -- pulling latest..."
    $remote = "https://$GitHubPAT@github.com/HackMe7822/sysadmin-interview-qa"
    git -C $SiteDir remote set-url origin $remote 2>&1 | Out-Null
    git -C $SiteDir pull origin main 2>&1 | Out-Null
    OK "Repo updated"
} else {
    New-Item -ItemType Directory -Force -Path $SiteDir | Out-Null
    git clone $cloneUrl $SiteDir 2>&1 | Out-Null
    if (Test-Path "$SiteDir\index.html") { OK "Repo cloned" }
    else { Err "Clone failed -- check your PAT and internet access."; exit 1 }
}

# -----------------------------------------------------------------------------
# STEP 3 -- IIS site
# -----------------------------------------------------------------------------
Step 3 "Setting up IIS site on port $SitePort"

Import-Module WebAdministration -ErrorAction SilentlyContinue
if (-not (Get-Module WebAdministration)) {
    Warn "WebAdministration module not available -- trying to enable IIS..."
    Enable-WindowsOptionalFeature -Online -FeatureName IIS-WebServer -All -NoRestart | Out-Null
    Import-Module WebAdministration
}

# Remove existing site with same name if present
$existing = Get-Website -Name "sysadmin" -ErrorAction SilentlyContinue
if ($existing) {
    Remove-Website -Name "sysadmin"
    Info "Removed existing 'sysadmin' site"
}

# Create app pool
if (-not (Test-Path "IIS:\AppPools\sysadmin")) {
    New-WebAppPool -Name "sysadmin" | Out-Null
    Set-ItemProperty "IIS:\AppPools\sysadmin" managedRuntimeVersion ""  # No managed code (static)
}

# Create site
New-Website -Name "sysadmin" `
            -PhysicalPath $SiteDir `
            -ApplicationPool "sysadmin" `
            -Port $SitePort `
            -Force | Out-Null

# Allow static content (should already be on, but ensure)
Set-WebConfigurationProperty -Filter "system.webServer/staticContent" `
    -PSPath "IIS:\Sites\sysadmin" -Name "." -Value @{} -ErrorAction SilentlyContinue

Start-Website -Name "sysadmin" -ErrorAction SilentlyContinue

# Verify
$site = Get-Website -Name "sysadmin"
if ($site.State -eq "Started") {
    OK "IIS site 'sysadmin' running on http://localhost:$SitePort"
} else {
    Warn "IIS site created but state is: $($site.State) -- check IIS Manager"
}

# Open firewall for the port
New-NetFirewallRule -DisplayName "Sysadmin Site $SitePort" `
    -Direction Inbound -Protocol TCP -LocalPort $SitePort `
    -Action Allow -ErrorAction SilentlyContinue | Out-Null

# Quick local test
try {
    $r = Invoke-WebRequest "http://localhost:$SitePort" -UseBasicParsing -TimeoutSec 5
    if ($r.StatusCode -eq 200) { OK "Local test passed (HTTP 200)" }
} catch { Warn "Local test failed -- check IIS logs if site doesn't load" }

# -----------------------------------------------------------------------------
# STEP 4 -- Cloudflare tunnel config
# -----------------------------------------------------------------------------
Step 4 "Adding $Hostname to cloudflared config"

if (-not (Test-Path $CfConfig)) {
    Warn "cloudflared config not found at $CfConfig -- skipping tunnel setup"
    Warn "Add manually: hostname $Hostname -> http://localhost:$SitePort"
} else {
    $yml = Get-Content $CfConfig -Raw

    if ($yml -match [regex]::Escape($Hostname)) {
        OK "$Hostname already in config -- no change needed"
    } else {
        $newRule = "  - hostname: $Hostname`r`n    service: http://localhost:$SitePort"
        # Insert before the final catch-all line
        $updated = $yml -replace '(\r?\n\s*-\s+service:\s+http_status:\d+)', "`r`n$newRule`$1"
        if ($updated -eq $yml) {
            # Fallback: append before EOF if catch-all line format didn't match
            $updated = $yml.TrimEnd() + "`r`n$newRule`r`n  - service: http_status:404`r`n"
            Warn "Could not find catch-all line -- appended entry to end of config"
        }
        Set-Content $CfConfig $updated.TrimEnd() -Encoding ASCII -Force
        OK "Added $Hostname -> http://localhost:$SitePort to config"
    }

    # Restart cloudflared to pick up new rule
    $cfSvc = Get-Service -Name $CfService -ErrorAction SilentlyContinue
    if ($cfSvc) {
        Info "Restarting $CfService service..."
        try {
            if (Get-Command nssm -ErrorAction SilentlyContinue) {
                & nssm restart $CfService 2>&1 | Out-Null
            } else {
                Restart-Service $CfService -Force
            }
            Start-Sleep -Seconds 5
            $cfSvc.Refresh()
            if ($cfSvc.Status -eq "Running") { OK "$CfService restarted" }
            else { Warn "$CfService may not have restarted -- check manually" }
        } catch { Warn "Restart failed: $_ -- run: nssm restart $CfService" }
    } else {
        Warn "$CfService service not found -- restart cloudflared manually after adding DNS"
    }
}

# -----------------------------------------------------------------------------
# STEP 5 -- Done
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "  ============================================================" -ForegroundColor Green
Write-Host "    Sysadmin site deployed!" -ForegroundColor Green
Write-Host "  ============================================================" -ForegroundColor Green
Write-Host "    Local URL    : http://localhost:$SitePort" -ForegroundColor Green
Write-Host "    Public URL   : https://$Hostname" -ForegroundColor Green
Write-Host "    Site files   : $SiteDir" -ForegroundColor Green
Write-Host ""
Write-Host "  NEXT: Add DNS record in Cloudflare dashboard:" -ForegroundColor Yellow
Write-Host "    Type   : CNAME" -ForegroundColor White
Write-Host "    Name   : sysadmin" -ForegroundColor White

$tunnelId = if ($yml -match 'tunnel:\s+(\S+)') { $Matches[1] } else { "<tunnel-uuid>" }
Write-Host "    Target : $tunnelId.cfargotunnel.com" -ForegroundColor White
Write-Host "    Proxy  : ON (orange cloud)" -ForegroundColor White
Write-Host ""
Write-Host "  To update the site later, just re-run this script." -ForegroundColor DarkGray
Write-Host "  Press Enter to close..."
Read-Host | Out-Null
