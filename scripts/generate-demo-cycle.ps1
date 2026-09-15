<#
  Run one demo AIOps generation cycle against the configured database.
  Intended for 24x7 scheduling (Task Scheduler / watchdog); the worker also
  does this natively when AUDIT_NETWORK_DEMO_GENERATE=1.

  Example:
    .\scripts\generate-demo-cycle.ps1
#>
param(
  [string]$DatabaseUrl = "postgresql://audit_app:admin@localhost:5432/audit_network",
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) { $Python = $venvPython }
Push-Location $projectRoot
try {
  $env:DATABASE_URL = $DatabaseUrl
  & $Python -c "from packages.demo.generator import run_demo_generation_once; import os; print(run_demo_generation_once(os.environ['DATABASE_URL']))"
  if ($LASTEXITCODE -ne 0) { throw "generate-demo-cycle failed with exit code $LASTEXITCODE" }
} finally {
  Pop-Location
}
