<#
  Register the Audit Network watchdog as a Windows scheduled task that runs
  every N minutes (default 5).  Each run is one supervision pass: probe API
  readiness + worker heartbeat, restart dead services via start-brain.ps1,
  exit 1 on critical findings.

  Requires the current user to have rights to create the task (an elevated
  PowerShell works everywhere; a standard user can usually create a task that
  runs only when that user is logged on).

  Examples:
    .\scripts\install-watchdog-task.ps1
    .\scripts\install-watchdog-task.ps1 -EveryMinutes 5 -UninstallExisting
#>
param(
  [int]$EveryMinutes = 5,
  [switch]$UninstallExisting,
  [switch]$SkipStart
)

$ErrorActionPreference = "Stop"
$taskName = "audit-network-watchdog"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$watchdogScript = Join-Path $projectRoot "scripts\watchdog.ps1"

if ($UninstallExisting) {
  schtasks /Delete /TN $taskName /F
  Write-Output "[audit-network] watchdog task removed: $taskName"
  return
}

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
  Write-Output "[audit-network] watchdog task already exists: $taskName (re-registering)"
  schtasks /Delete /TN $taskName /F | Out-Null
}

# schtasks stores the exact command line; quoting the script path keeps spaces safe.
schtasks /Create /TN $taskName /SC MINUTE /MO $EveryMinutes `
  /TR "powershell -NoProfile -ExecutionPolicy Bypass -File `"$watchdogScript`"" `
  /F
if ($LASTEXITCODE -ne 0) { throw "schtasks /Create failed (exit $LASTEXITCODE)" }

if (-not $SkipStart) {
  schtasks /Run /TN $taskName | Out-Null
  Write-Output "[audit-network] watchdog task started (every $EveryMinutes min): $taskName"
} else {
  Write-Output "[audit-network] watchdog task registered but not started: $taskName"
}
