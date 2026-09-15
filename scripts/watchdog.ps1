<#
  Run one watchdog pass (Task Scheduler mode).  Probes /api/v1/health/ready and
  worker heartbeats, restarts missing services via start-brain.ps1 (bounded by
  a minimum interval), and exits 1 when a critical finding exists so Task
  Scheduler / alerting can react.

  Examples:
    .\scripts\watchdog.ps1
    .\scripts\watchdog.ps1 -DatabaseUrl "postgresql://audit_app:admin@localhost:5432/audit_network" -ApiUrl "http://127.0.0.1:8010"
#>
param(
  [string]$DatabaseUrl = "postgresql://audit_app:admin@localhost:5432/audit_network",
  [string]$ApiUrl = "http://127.0.0.1:8010"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
  throw "project virtualenv not found: $python"
}

& $python -m packages.ops.supervisor --once --database-url $DatabaseUrl --api-url $ApiUrl
exit $LASTEXITCODE
