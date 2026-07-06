param([switch]$NoGit)

$SERVICE = "AngelBot-Portal"
$TIMEOUT = 30

Write-Host "=== AngelBot Portal Update ===" -ForegroundColor Cyan

# 1. Pull latest code
if (-not $NoGit) {
    Write-Host "`n[1/3] Pulling latest code..." -ForegroundColor Yellow
    git pull origin main
    if ($LASTEXITCODE -ne 0) { Write-Host "git pull failed - aborting." -ForegroundColor Red; exit 1 }
}

# 2. Stop service and wait until fully stopped
Write-Host "`n[2/3] Stopping $SERVICE..." -ForegroundColor Yellow
nssm stop $SERVICE 2>$null | Out-Null

$elapsed = 0
while ($elapsed -lt $TIMEOUT) {
    $state = (sc.exe query $SERVICE | Select-String "STATE") -replace '.*STATE\s*:\s*\d+\s*', ''
    if ($state -match "STOPPED") { break }
    Start-Sleep 1
    $elapsed++
    Write-Host "  Waiting for stop... ($elapsed s)" -ForegroundColor DarkGray
}

$finalState = (sc.exe query $SERVICE | Select-String "STATE") -replace '.*STATE\s*:\s*\d+\s*', ''
if ($finalState -notmatch "STOPPED") {
    Write-Host "  Service did not stop cleanly - killing process..." -ForegroundColor Yellow
    $pid = (sc.exe queryex $SERVICE | Select-String "PID") -replace '\D', ''
    if ($pid -match '^\d+$' -and [int]$pid -gt 0) {
        Stop-Process -Id ([int]$pid) -Force -ErrorAction SilentlyContinue
        Start-Sleep 2
    }
}

Write-Host "  Service stopped." -ForegroundColor Green

# 3. Start service
Write-Host "`n[3/3] Starting $SERVICE..." -ForegroundColor Yellow
nssm start $SERVICE 2>$null | Out-Null
Start-Sleep 4

$startState = (sc.exe query $SERVICE | Select-String "STATE") -replace '.*STATE\s*:\s*\d+\s*', ''
if ($startState -match "RUNNING") {
    Write-Host "  Service is RUNNING." -ForegroundColor Green
} else {
    Write-Host "  Service state: $startState" -ForegroundColor Red
    Write-Host "  Check logs: Get-Content C:\AngelBot\logs\AngelBot-Portal.log -Tail 30" -ForegroundColor Yellow
}

Write-Host "`nDone." -ForegroundColor Cyan
