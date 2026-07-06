param(
    [string]$LogDir    = "C:\AngelBot\logs",
    [int]$KeepDays     = 3,       # recent daily logs per market to leave unzipped
    [int]$NssmMaxMB    = 50,      # zip NSSM service logs when they exceed this size
    [switch]$Register,            # register as daily 2 AM scheduled task and exit
    [switch]$Unregister           # remove the scheduled task and exit
)

$TASK_NAME = "AngelBot-LogPurge"

# ── Scheduled task management ─────────────────────────────────────────────────
if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Scheduled task '$TASK_NAME' removed." -ForegroundColor Yellow
    exit 0
}

if ($Register) {
    $scriptPath = (Resolve-Path $PSCommandPath).Path
    $action   = New-ScheduledTaskAction -Execute "powershell.exe" `
                    -Argument "-NonInteractive -ExecutionPolicy Bypass -File `"$scriptPath`""
    $trigger  = New-ScheduledTaskTrigger -Daily -At "02:00"
    $settings = New-ScheduledTaskSettingsSet -RunOnlyIfIdle:$false -StartWhenAvailable
    Register-ScheduledTask -TaskName $TASK_NAME -Action $action -Trigger $trigger `
        -Settings $settings -RunLevel Highest -Force | Out-Null
    Write-Host "Scheduled task '$TASK_NAME' registered — runs daily at 02:00." -ForegroundColor Green
    exit 0
}

# ── Helpers ───────────────────────────────────────────────────────────────────
function Zip-And-Delete($path, $zipDest) {
    if (Test-Path $zipDest) { return $true }   # already zipped from a prior run
    try {
        Compress-Archive -Path $path -DestinationPath $zipDest -CompressionLevel Optimal -ErrorAction Stop
        return $true
    } catch {
        Write-Host "  [WARN] Could not zip $path : $_" -ForegroundColor Yellow
        return $false
    }
}

$zipped = 0; $skipped = 0

Write-Host "=== AngelBot Log Purge ===" -ForegroundColor Cyan
Write-Host "  Log dir : $LogDir"
Write-Host "  Keep    : last $KeepDays daily logs per market"
Write-Host "  NSSM    : rotate service logs over ${NssmMaxMB} MB"
Write-Host ""

# ── 1. Daily market logs: {market}_{YYYYMMDD}.log ────────────────────────────
# Group by market prefix, keep newest $KeepDays, zip the rest.
$daily = Get-ChildItem $LogDir -Filter '*_????????.log' -File `
         | Where-Object { $_.BaseName -match '^(.+)_(\d{8})$' } `
         | Sort-Object Name -Descending

$groups = @{}
foreach ($f in $daily) {
    if ($f.BaseName -match '^(.+)_(\d{8})$') {
        $mkt = $Matches[1]
        if (-not $groups[$mkt]) { $groups[$mkt] = [System.Collections.Generic.List[object]]::new() }
        $groups[$mkt].Add($f)
    }
}

foreach ($mkt in $groups.Keys | Sort-Object) {
    $files   = $groups[$mkt]   # already sorted newest-first
    $toKeep  = $files | Select-Object -First $KeepDays
    $toZip   = $files | Select-Object -Skip  $KeepDays

    Write-Host "  [$mkt] $($files.Count) files — keeping $([Math]::Min($KeepDays,$files.Count)), zipping $($toZip.Count)" -ForegroundColor DarkCyan
    foreach ($f in $toZip) {
        $zip = $f.FullName + ".zip"
        if (Zip-And-Delete $f.FullName $zip) {
            Remove-Item $f.FullName -Force
            Write-Host "    Zipped : $($f.Name)" -ForegroundColor DarkGray
            $zipped++
        } else { $skipped++ }
    }
}

# ── 2. NSSM service logs: AngelBot-*.log ─────────────────────────────────────
# These are single growing files (not daily). When over $NssmMaxMB MB:
#   - zip to AngelBot-{name}-{YYYYMMDD-HHmm}.log.zip
#   - truncate the original (NSSM keeps writing to the same filename)
Write-Host ""
Write-Host "  Checking NSSM service logs (>${NssmMaxMB} MB threshold)..." -ForegroundColor DarkCyan
foreach ($f in Get-ChildItem $LogDir -Filter 'AngelBot-*.log' -File) {
    $mb = [Math]::Round($f.Length / 1MB, 1)
    if ($f.Length -gt ($NssmMaxMB * 1MB)) {
        $stamp   = Get-Date -Format 'yyyyMMdd-HHmm'
        $zipName = [IO.Path]::GetFileNameWithoutExtension($f.Name) + "-$stamp.log.zip"
        $zipPath = Join-Path $LogDir $zipName
        if (Zip-And-Delete $f.FullName $zipPath) {
            # Truncate in-place — NSSM keeps the file handle open, so we clear content
            # rather than delete, so it keeps appending to the same path.
            try {
                [IO.File]::WriteAllText($f.FullName, '')   # fastest truncate
                Write-Host "    Rotated: $($f.Name) ($mb MB) -> $zipName" -ForegroundColor DarkGray
                $zipped++
            } catch {
                Write-Host "  [WARN] Could not truncate $($f.Name): $_" -ForegroundColor Yellow
                # Leave the zip but don't remove data from the live log
            }
        } else { $skipped++ }
    } else {
        Write-Host "    OK      : $($f.Name) ($mb MB)" -ForegroundColor DarkGray
    }
}

# ── 3. Date-based subdirectories: logs/YYYY-MM-DD/ ───────────────────────────
$dateDirs = Get-ChildItem $LogDir -Directory `
            | Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}$' } `
            | Sort-Object Name -Descending

if ($dateDirs.Count -gt 0) {
    Write-Host ""
    Write-Host "  Date subdirectories: $($dateDirs.Count) found, keeping $KeepDays" -ForegroundColor DarkCyan
    $dirsToZip = $dateDirs | Select-Object -Skip $KeepDays
    foreach ($d in $dirsToZip) {
        $zip = $d.FullName + ".zip"
        if (Zip-And-Delete $d.FullName $zip) {
            Remove-Item $d.FullName -Recurse -Force
            Write-Host "    Zipped dir: $($d.Name)" -ForegroundColor DarkGray
            $zipped++
        } else { $skipped++ }
    }
}

# ── 4. Already-zipped archives older than 90 days ────────────────────────────
# Safety net: delete very old .zip archives so disk never fills completely.
$cutoff = (Get-Date).AddDays(-90)
$oldZips = Get-ChildItem $LogDir -Filter '*.zip' -File | Where-Object { $_.LastWriteTime -lt $cutoff }
if ($oldZips.Count -gt 0) {
    Write-Host ""
    Write-Host "  Deleting $($oldZips.Count) zip archive(s) older than 90 days..." -ForegroundColor DarkCyan
    foreach ($z in $oldZips) {
        Remove-Item $z.FullName -Force
        Write-Host "    Deleted: $($z.Name)" -ForegroundColor DarkGray
    }
}

Write-Host ""
Write-Host "Done. Zipped/rotated: $zipped  |  Skipped (errors): $skipped" -ForegroundColor Cyan
