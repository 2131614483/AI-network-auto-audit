param(
  [int]$ApiPort = 8010,
  [string]$DatabaseUrl = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "env-common.ps1")

# Bare `python` used to be called here, which picks whatever interpreter PATH
# happens to expose and never touches the project virtualenv the dependency set
# was verified against.  Resolve it explicitly instead.
$python = Resolve-ProjectPython -ProjectRoot $projectRoot

# An explicitly passed -DatabaseUrl wins, then an already-exported
# DATABASE_URL, then the documented local default.
if (-not $DatabaseUrl) {
  $DatabaseUrl = if ($env:DATABASE_URL) {
    $env:DATABASE_URL
  } else {
    "postgresql://audit_app:admin@localhost:5432/audit_network"
  }
}

$env:DATABASE_URL = $DatabaseUrl
Write-Output "Starting Audit Network API on http://localhost:$ApiPort"
Write-Output "Database mode: native PostgreSQL (Docker is not required)"
& $python -m uvicorn apps.api.main:app --host 127.0.0.1 --port $ApiPort
