<#
.SYNOPSIS
  One command to start the FlowSync demo on this laptop (Windows PowerShell 5.1+).

.DESCRIPTION
  Starts the backend on 0.0.0.0 (so phones and boards can reach it), the virtual tabletop, and prints the join
  address. Optionally the 3D big-screen dashboard (-Web) and the rehearsal guest bots (-Bots). Logs go to data\logs,
  process ids to data\demo.pids. Ctrl+C (or scripts\stop_demo.ps1 from another window) stops everything it started.

  Passwords: if .env sets FLOWSYNC_OPERATOR_PASSWORD (python -m app.setup_demo) it is used; otherwise the public
  development passwords are in effect and the script says so. Nothing secret is printed.

.EXAMPLE
  .\scripts\start_demo.ps1                 # backend + virtual tabletop + join QR sheet
  .\scripts\start_demo.ps1 -Web -Bots      # plus the big screen (npm run dev) and looping rehearsal bots
#>
[CmdletBinding()]
param(
  [int]$Port = 8000,
  [switch]$Web,                 # also start the 3D dashboard (needs Node.js; first run does npm install)
  [switch]$Bots,                # also start looping rehearsal guest bots (SIMULATED guests, not phones)
  [switch]$NoSim,               # do not start the virtual tabletop (use when real boards or another simulator feed the backend)
  [int]$Vehicles = 5,
  [int]$Ambulance = 1
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
$LogDir = Join-Path $Root 'data\logs'
$PidFile = Join-Path $Root 'data\demo.pids'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Fail([string]$msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

# ---- find Python ---------------------------------------------------------------------------------------------------
$Py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if (-not $cmd) { Fail "Python not found. Install Python 3.11+ and run: python -m venv .venv ; .\.venv\Scripts\python -m pip install -r requirements.txt" }
  $Py = $cmd.Source
}
& $Py -c "import fastapi, uvicorn, httpx, yaml" 2>$null
if ($LASTEXITCODE -ne 0) { Fail "Python packages missing. Run: $Py -m pip install -r requirements.txt" }

if ($Web) {
  if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Fail "-Web needs Node.js (npm). Install it from nodejs.org, or start without -Web." }
}

# ---- refuse to start on a busy port or on top of a previous run -----------------------------------------------------
if (Test-Path $PidFile) {
  $old = @(Get-Content $PidFile | Where-Object { $_ -match '^\d+\s' } | ForEach-Object { [int]($_ -split '\s')[0] } |
           Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
  if ($old.Count -gt 0) { Fail "A demo seems to be running already (pids $($old -join ', ')). Run .\scripts\stop_demo.ps1 first." }
  Remove-Item $PidFile -Force
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
  Fail "Port $Port is already in use. Close what is using it, or pick another with -Port."
}

# ---- environment: .env values for this script's children; PYTHONPATH as the README says ---------------------------------
$env:PYTHONPATH = Join-Path $Root 'backend'
$dotenv = Join-Path $Root '.env'
$OperatorPassword = 'operator-dev'
if (Test-Path $dotenv) {
  foreach ($line in Get-Content $dotenv) {
    if ($line -match '^\s*FLOWSYNC_OPERATOR_PASSWORD\s*=\s*(.+?)\s*$') { $OperatorPassword = $Matches[1].Trim('"').Trim("'") }
  }
}
if ($OperatorPassword -eq 'operator-dev') {
  Write-Host "NOTE: using the public DEVELOPMENT passwords. For a demo others can reach, run: python -m app.setup_demo" -ForegroundColor Yellow
}

$started = New-Object System.Collections.ArrayList
function Start-Tracked([string]$name, [string]$file, [string[]]$arguments, [string]$workDir) {
  $out = Join-Path $LogDir "$name.out.log"; $err = Join-Path $LogDir "$name.err.log"
  $p = Start-Process -FilePath $file -ArgumentList $arguments -WorkingDirectory $workDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $out -RedirectStandardError $err
  [void]$started.Add($p)
  Add-Content -Path $PidFile -Value "$($p.Id) $name"
  Write-Host ("  started {0,-10} pid {1}   log: data\logs\{0}.*.log" -f $name, $p.Id)
}
function Stop-All {
  foreach ($p in $started) { if (-not $p.HasExited) { & taskkill /PID $p.Id /T /F 2>$null | Out-Null } }
  if (Test-Path $PidFile) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue }
  Write-Host "Stopped." -ForegroundColor Green
}

try {
  Write-Host "Starting FlowSync demo from $Root"
  Start-Tracked 'backend' $Py @('-m', 'uvicorn', 'app.main:app_factory', '--factory', '--host', '0.0.0.0', '--port', "$Port") $Root

  # wait for the backend (up to 40 s)
  $up = $false
  for ($i = 0; $i -lt 40 -and -not $up; $i++) {
    if ($started[0].HasExited) { Fail "The backend exited. See data\logs\backend.err.log" }
    try { $up = (Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2).StatusCode -eq 200 } catch { Start-Sleep -Seconds 1 }
  }
  if (-not $up) { Fail "The backend did not answer on port $Port within 40 s. See data\logs\backend.err.log" }
  Write-Host "  backend is up on port $Port" -ForegroundColor Green

  if (-not $NoSim) {
    Start-Tracked 'simulator' $Py @('-m', 'simulator.run_sim', '--url', "http://127.0.0.1:$Port", '--password', $OperatorPassword) $Root
  }
  if ($Web) {
    $webDir = Join-Path $Root 'web'
    if (-not (Test-Path (Join-Path $webDir 'node_modules'))) {
      Write-Host "  installing web dependencies (first run only)..."
      Push-Location $webDir; & npm install --no-audit --no-fund | Out-Null; Pop-Location
      if ($LASTEXITCODE -ne 0) { Fail "npm install failed in web\" }
    }
    Start-Tracked 'web' 'cmd.exe' @('/c', 'npm run dev -- --host') $webDir
  }
  if ($Bots) {
    Start-Tracked 'bots' $Py @('-m', 'simulator.guest_bots', '--base', "http://127.0.0.1:$Port", '--vehicles', "$Vehicles", '--ambulance', "$Ambulance", '--loop') $Root
  }

  Write-Host ""
  & $Py -m app.joinqr --port $Port
  if ($LASTEXITCODE -ne 0) { Write-Host "(QR sheet not written; the addresses above still work. pip install -r requirements-demo.txt)" -ForegroundColor Yellow }
  Write-Host ""
  Write-Host "Pages:  /presenter.html  /operator.html  /driver.html   (phones: use the addresses above, same Wi-Fi)"
  if ($Web) { Write-Host "Big screen: http://localhost:5173  (sign in as the operator)" }
  Write-Host "Check readiness in another window:  python -m app.preflight --demo --url http://127.0.0.1:$Port"
  Write-Host "Running. Press Ctrl+C to stop everything this script started." -ForegroundColor Cyan

  while ($true) {
    Start-Sleep -Seconds 2
    if ($started[0].HasExited) { Write-Host "The backend stopped. See data\logs\backend.err.log" -ForegroundColor Red; break }
  }
}
finally {
  Stop-All
}
