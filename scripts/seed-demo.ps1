<#
  Idempotent demo data provisioning (R0 remediation).

  Runs packages.demo.seed.seed_local_demo() against the configured database so
  every control-plane feature has reproducible, internally consistent data.
  Safe to re-run; control missions use scheduler idempotency keys, AIOps alerts
  upsert by fingerprint, and quant/audit guards skip when already present.

  Examples:
    .\scripts\seed-demo.ps1
    .\scripts\seed-demo.ps1 -DatabaseUrl "postgresql://audit_app:admin@localhost:5432/audit_network"
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
  & $Python -c "from packages.demo.seed import seed_local_demo; import os; r = seed_local_demo(os.environ['DATABASE_URL']); print(r)"
  if ($LASTEXITCODE -ne 0) { throw "seed-demo failed with exit code $LASTEXITCODE" }
} finally {
  Pop-Location
}
