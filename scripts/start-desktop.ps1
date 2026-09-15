<#
  Starts the local control plane and then opens the Electron desktop console.
  The desktop renderer has no direct HTTP or plugin execution privileges; all
  declared actions cross the Electron main-process IPC boundary first.
#>
[CmdletBinding()]
param(
  [int]$ApiPort = 8010,
  [switch]$RunMigrations
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$desktopRoot = Join-Path $projectRoot "desktop"

if (-not (Test-Path -LiteralPath (Join-Path $desktopRoot "node_modules"))) {
  throw ("Desktop dependencies are missing (desktop\node_modules).`n" +
    "Install them with:`n" +
    "    npm install --prefix desktop`n" +
    "Or run the guided setup, which installs everything this project needs:`n" +
    "    powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1")
}

$brainArguments = @{ ApiPort = $ApiPort }
if ($RunMigrations) { $brainArguments.RunMigrations = $true }
& (Join-Path $projectRoot "scripts\start-brain.ps1") @brainArguments

Push-Location $projectRoot
try {
  & npm.cmd --prefix desktop run dev
} finally {
  Pop-Location
}
