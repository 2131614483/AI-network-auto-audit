<#
  Build the code-location index (docs/code-map.json) with the project venv.

  Regenerates the deterministic file -> line -> endpoint -> tables index that
  /api/v1/observability/code-map serves (R3 L2).  Run whenever the API routes
  change, or on a schedule:
    .\scripts\build-code-map.ps1
#>
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
  throw "venv python not found: $venvPython"
}
Push-Location $projectRoot
try {
  & $venvPython -c "from packages.observability.code_map import main; main()" --out docs\code-map.json
  if ($LASTEXITCODE -ne 0) { throw "code map build failed (exit $LASTEXITCODE)" }
} finally {
  Pop-Location
}
