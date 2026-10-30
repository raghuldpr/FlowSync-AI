<#
.SYNOPSIS
  Stops everything scripts\start_demo.ps1 started (use it when that window was closed or is unreachable).
#>
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PidFile = Join-Path $Root 'data\demo.pids'

if (-not (Test-Path $PidFile)) { Write-Host "No demo is recorded as running (no data\demo.pids)."; exit 0 }

$stopped = 0
foreach ($line in Get-Content $PidFile) {
  if ($line -notmatch '^(\d+)\s+(\S+)') { continue }
  $id = [int]$Matches[1]; $name = $Matches[2]
  if (Get-Process -Id $id -ErrorAction SilentlyContinue) {
    & taskkill /PID $id /T /F 2>$null | Out-Null       # /T also ends children (npm -> node, uvicorn workers)
    Write-Host "stopped $name (pid $id)"
    $stopped++
  }
}
Remove-Item $PidFile -Force
Write-Host "Done: $stopped process(es) stopped." -ForegroundColor Green
