<#
.SYNOPSIS
  Write deploy\catalyst\appsail\app-config.json: the startup command and memory from app-config.example.json, plus the three
  account passwords from your .env (made by `python -m app.setup_demo`), so the first start passes the "no development
  credentials on a public host" check. Nothing secret is printed.

.DESCRIPTION
  appsail\ is git-ignored and is uploaded to YOUR Catalyst account together with your private device keys, so the passwords
  are no more exposed there than those keys. Do not commit or share the file. Run this again after `catalyst init` if init
  replaced the file. The LLM settings are NOT written here: set LLM_* in the Catalyst console (the key stays out of files).
  FLOWSYNC_PUBLIC_URL is not written either: set it in the console once you know the service address.

.EXAMPLE
  .\deploy\catalyst\write-config.ps1
#>
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Dir  = Join-Path $PSScriptRoot 'appsail'
if (-not (Test-Path $Dir)) { throw "Run .\deploy\catalyst\build.ps1 first (it creates $Dir)." }

$envFile = Join-Path $Root '.env'
if (-not (Test-Path $envFile)) { throw "No .env. Run 'python -m app.setup_demo' first." }
$vals = @{}
foreach ($line in Get-Content $envFile -Encoding UTF8) {
  if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
  $k, $v = $line.Split('=', 2)
  $vals[$k.Trim()] = $v.Trim().Trim('"').Trim("'")
}
$names = 'FLOWSYNC_VIEWER_PASSWORD', 'FLOWSYNC_OPERATOR_PASSWORD', 'FLOWSYNC_ADMIN_PASSWORD'
foreach ($n in $names) {
  if (-not $vals[$n]) { throw "$n is missing in .env. Run 'python -m app.setup_demo'." }
  if ($vals[$n] -in 'operator-dev', 'viewer-dev', 'admin-dev') { throw "$n is still a development default." }
}

$example = Get-Content (Join-Path $PSScriptRoot 'app-config.example.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$envVars = [ordered]@{}
foreach ($p in $example.env_variables.PSObject.Properties) {
  if ($p.Name -eq 'FLOWSYNC_PUBLIC_URL') { continue }        # unknown until the first deploy: set it in the console
  $envVars[$p.Name] = $p.Value
}
foreach ($n in $names) { $envVars[$n] = $vals[$n] }

$cfg = [ordered]@{ command = $example.command; memory = $example.memory; env_variables = $envVars }
$out = Join-Path $Dir 'app-config.json'
[System.IO.File]::WriteAllText($out, ($cfg | ConvertTo-Json -Depth 5), (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Wrote $out"
Write-Host ("  command, memory {0} MB, {1} environment variables: {2}" -f $cfg.memory, $envVars.Count, ($envVars.Keys -join ', '))
Write-Host "  (values not shown; the file is git-ignored; do not commit or share it)"
