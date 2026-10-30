<#
.SYNOPSIS
  Build the folder that `catalyst deploy` uploads as the AppSail service: deploy\catalyst\appsail\

.DESCRIPTION
  AppSail runs Linux, so the Python packages are downloaded as Linux wheels (this works from Windows). The app code goes under
  backend\, config\ and dashboard\ so the app finds them the same way as in the repository.
  The development device keys in config\devices.yaml are public (they are in the repository), so by default this REFUSES to build
  without your private device keys from `python -m app.setup_demo` (data\secrets\devices.demo.yaml). -AllowDevKeys is for a
  dry run only; never deploy such a build.
  Secrets are never copied from .env. Passwords and the LLM key go into the Catalyst console as environment variables.
  An app-config.json created by `catalyst init` inside appsail\ is kept.

.EXAMPLE
  .\deploy\catalyst\build.ps1
  .\deploy\catalyst\build.ps1 -AllowDevKeys -NoDeps      # layout check on this PC (not deployable)
#>
[CmdletBinding()]
param(
  [switch]$AllowDevKeys,
  [switch]$NoDeps,                 # skip the Linux packages (only for a local layout check)
  [string]$PythonVersion = "3.12"  # must match the managed Python runtime you pick in Catalyst (3.10 to 3.13 are listed)
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Out  = Join-Path $PSScriptRoot 'appsail'

$devices = Join-Path $Root 'data\secrets\devices.demo.yaml'
if (-not (Test-Path $devices)) {
  if (-not $AllowDevKeys) {
    throw "Missing $devices. Run 'python -m app.setup_demo' first (private device keys). The development keys are public; do not host with them."
  }
  Write-Warning "Using the PUBLIC development device keys. This build must not be deployed."
  $devices = Join-Path $Root 'config\devices.yaml'
}

New-Item -ItemType Directory -Force $Out | Out-Null
Get-ChildItem $Out -Force | Where-Object { $_.Name -ne 'app-config.json' } | Remove-Item -Recurse -Force

if (-not $NoDeps) {
  $abi = 'cp' + $PythonVersion.Replace('.', '')
  # pip writes warnings (for example about packages already installed on this PC) to stderr; Windows PowerShell 5.1 would turn
  # that into a terminating error under 'Stop', so judge success by pip's exit code only.
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  python -m pip install --quiet --target $Out --platform manylinux2014_x86_64 --only-binary=:all: `
    --python-version $PythonVersion --implementation cp --abi $abi -r (Join-Path $PSScriptRoot 'requirements.txt') 2>&1 |
    ForEach-Object { if ($_ -is [System.Management.Automation.ErrorRecord]) { Write-Host ("  pip: " + $_.Exception.Message) } else { Write-Host ("  pip: " + $_) } }
  $pipExit = $LASTEXITCODE
  $ErrorActionPreference = $prev
  if ($pipExit -ne 0) { throw "pip could not download Linux packages for Python $PythonVersion" }
}

$app = Join-Path $Out 'backend\app'
New-Item -ItemType Directory -Force $app | Out-Null
Copy-Item (Join-Path $Root 'backend\app\*') $app -Recurse -Force -Exclude '__pycache__'
Get-ChildItem $app -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force

New-Item -ItemType Directory -Force (Join-Path $Out 'config') | Out-Null
Copy-Item (Join-Path $Root 'config\topology.yaml') (Join-Path $Out 'config\topology.yaml')
Copy-Item $devices (Join-Path $Out 'config\devices.yaml')
Copy-Item (Join-Path $Root 'dashboard') (Join-Path $Out 'dashboard') -Recurse -Force

$mb = [math]::Round(((Get-ChildItem $Out -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 1)
Write-Host "Built $Out ($mb MB). Next: see docs\CATALYST.md (catalyst init / deploy)."
