# Run this anytime to see the current public tunnel URL
$log = "C:\AngelBot\logs\AngelBot-Tunnel.log"

if (-not (Test-Path $log)) {
    Write-Host "Tunnel log not found. Is AngelBot-Tunnel service installed?" -ForegroundColor Red
    exit 1
}

$svc = Get-Service AngelBot-Tunnel -ErrorAction SilentlyContinue
if (-not $svc) {
    Write-Host "AngelBot-Tunnel service not found." -ForegroundColor Red
    exit 1
}

$status = $svc.Status
$color  = if ($status -eq "Running") { "Green" } else { "Red" }
Write-Host "AngelBot-Tunnel: $status" -ForegroundColor $color

$match = Select-String -Path $log -Pattern "https://.*\.trycloudflare\.com" | Select-Object -Last 1
if ($match) {
    $url = [regex]::Match($match.Line, "https://[^\s|]+").Value
    Write-Host ""
    Write-Host "  Current public URL:" -ForegroundColor Cyan
    Write-Host "  $url" -ForegroundColor Yellow
    Write-Host ""
    if ($status -ne "Running") {
        Write-Host "  WARNING: Service is not running. Start it with: nssm start AngelBot-Tunnel" -ForegroundColor Red
        Write-Host "  URL above is from previous session and will NOT work until service restarts." -ForegroundColor Red
    }
} else {
    Write-Host "No URL found in log yet. Wait 10 seconds and try again." -ForegroundColor Yellow
}
