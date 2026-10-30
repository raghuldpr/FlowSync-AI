<#
.SYNOPSIS
  Fill deploy\catalyst\app-config.json (the file `catalyst init` creates next to catalyst.json): the startup command and memory
  from app-config.example.json, plus the three account passwords from your .env (made by `python -m app.setup_demo`), so the
  first start passes the "no development credentials on a public host" check. Nothing secret is printed.

.DESCRIPTION
  The keys `catalyst init` wrote (build_path, stack, scripts) are kept; command, memory and env_variables are replaced.
  The file is git-ignored (deploy\catalyst\.gitignore) because it holds your passwords: do not commit or share it. It goes to
  YOUR Catalyst account with the deploy, as do your private device keys. Run this again if init replaced the file.
  The LLM settings are NOT written here: set LLM_* in the Catalyst console (the key stays out of files).
  FLOWSYNC_PUBLIC_URL is written only when you pass -PublicUrl (you know the service address after the first deploy).

.EXAMPLE
  .\deploy\catalyst\write-config.ps1
  .\deploy\catalyst\write-config.ps1 -PublicUrl https://flowsync-123.development.catalystappsail.in
#>
[CmdletBinding()]
param([string]$PublicUrl = '')       # the service address, e.g. https://name-id.development.catalystappsail.in (no trailing slash)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Dir  = Join-Path $PSScriptRoot 'appsail'
if (-not (Test-Path $Dir)) { throw "Run .\deploy\catalyst\build.ps1 first (it creates $Dir)." }
$out = Join-Path $PSScriptRoot 'app-config.json'
if (-not (Test-Path $out)) { throw "No $out. Run 'catalyst init' in deploy\catalyst first; it creates this file." }
$cfg = Get-Content $out -Raw -Encoding UTF8 | ConvertFrom-Json

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
if ($PublicUrl) {
  if ($PublicUrl -notmatch '^https://[^/\s]+$') { throw 'PublicUrl must look like https://host (https, no path, no trailing slash).' }
  $envVars['FLOWSYNC_PUBLIC_URL'] = $PublicUrl
}

$cfg | Add-Member -NotePropertyName command -NotePropertyValue $example.command -Force
$cfg | Add-Member -NotePropertyName memory -NotePropertyValue $example.memory -Force
$cfg | Add-Member -NotePropertyName env_variables -NotePropertyValue ([pscustomobject]$envVars) -Force
[System.IO.File]::WriteAllText($out, ($cfg | ConvertTo-Json -Depth 6), (New-Object System.Text.UTF8Encoding($false)))

# An earlier version of this script wrote a copy inside appsail\ (it would be uploaded with the code); the CLI does not read it.
$stray = Join-Path $Dir 'app-config.json'
if (Test-Path $stray) { Remove-Item $stray -Force }

Write-Host "Wrote $out"
Write-Host ("  keys: {0}; memory {1} MB; {2} environment variables: {3}" -f (($cfg.PSObject.Properties.Name) -join ', '), $cfg.memory, $envVars.Count, ($envVars.Keys -join ', '))
Write-Host "  (values not shown; the file is git-ignored; do not commit or share it)"
