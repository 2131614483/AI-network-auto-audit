<#
  Start the native, single-machine Audit Network brain.

  This script deliberately starts the control-plane services only.  It does
  not open a browser and it does not treat the HTML shell as the brain.
  PostgreSQL remains the source of truth; API and Worker are restartable
  processes around it.
#>
[CmdletBinding()]
param(
  [int]$ApiPort = 8010,
  [string]$DatabaseUrl = "postgresql://audit_app:admin@localhost:5432/audit_network",
  [string]$MigrationDatabaseUrl = "postgresql://audit_migrator:admin@localhost:5432/audit_network",
  [switch]$RunMigrations
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
# The project virtualenv is the only interpreter this dependency set is
# verified against.  A machine-specific fallback path used to live here
# (C:\Users\<name>\...\Python311\python.exe) that could never match on any other
# machine; env-common.ps1 resolves the venv and, when it is missing, throws with
# the exact commands that create it.
. (Join-Path $PSScriptRoot "env-common.ps1")
$python = Resolve-ProjectPython -ProjectRoot $projectRoot

$env:DATABASE_URL = $DatabaseUrl
$runDir = Join-Path $projectRoot ".data\run"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

Write-Output "[audit-network] checking native PostgreSQL..."
& (Join-Path $projectRoot "scripts\verify-native-postgres.ps1") -User "audit_app" -Password "admin" -Database "audit_network"

if ($RunMigrations) {
  Write-Output "[audit-network] applying database migrations..."
  # Application credentials have deliberately restricted DDL privileges.  Keep
  # migration credentials scoped to this short, explicit operation.
  $env:DATABASE_URL = $MigrationDatabaseUrl
  & $python -m alembic -c (Join-Path $projectRoot "alembic.ini") upgrade head
  $env:DATABASE_URL = $DatabaseUrl
}

function Find-ProcessByCommand([string]$pattern) {
  return Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "python.exe" -and $_.CommandLine -like $pattern
  }
}

$apiPattern = "*uvicorn apps.api.main:app*--port $ApiPort*"
$apiProcess = Find-ProcessByCommand $apiPattern | Select-Object -First 1
if (-not $apiProcess) {
  # The API persists its own uvicorn/app logs to .data\api.log, which the
  # desktop unified-log panel tails for replay and debugging.
  $apiProcess = Start-Process -FilePath $python -ArgumentList @(
    "-m", "uvicorn", "apps.api.main:app", "--host", "127.0.0.1", "--port", "$ApiPort"
  ) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
  Write-Output "[audit-network] API started (PID $($apiProcess.Id))."
} else {
  Write-Output "[audit-network] API already running (PID $($apiProcess.ProcessId))."
}

$workerPattern = "*apps.worker.local*"
$workerProcess = Find-ProcessByCommand $workerPattern | Select-Object -First 1
if (-not $workerProcess) {
  # The worker persists its own logs to .data\worker.log.
  $workerProcess = Start-Process -FilePath $python -ArgumentList @("-m", "apps.worker.local") `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
  Write-Output "[audit-network] Worker started (PID $($workerProcess.Id))."
} else {
  Write-Output "[audit-network] Worker already running (PID $($workerProcess.ProcessId))."
}

$healthUri = "http://127.0.0.1:$ApiPort/health"
$healthy = $false
for ($attempt = 1; $attempt -le 20; $attempt++) {
  try {
    $health = Invoke-RestMethod -Uri $healthUri -TimeoutSec 2
    if ($health.status -eq "ok") {
      $healthy = $true
      break
    }
  } catch {
    Start-Sleep -Milliseconds 500
  }
}
if (-not $healthy) {
  throw "Audit Network API did not become healthy: $healthUri"
}

$workerCheck = Find-ProcessByCommand $workerPattern | Select-Object -First 1
if (-not $workerCheck) {
  throw "Audit Network Worker exited during startup."
}

@{
  api_pid = if ($apiProcess.PSObject.Properties.Name -contains "Id") { $apiProcess.Id } else { $apiProcess.ProcessId }
  worker_pid = if ($workerProcess.PSObject.Properties.Name -contains "Id") { $workerProcess.Id } else { $workerProcess.ProcessId }
  api_url = $healthUri
  database = "native-postgresql"
  started_at = (Get-Date).ToUniversalTime().ToString("o")
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runDir "brain.json") -Encoding UTF8

Write-Output "[audit-network] brain is running: API healthy, Worker alive, PostgreSQL verified."
Write-Output "[audit-network] no browser was opened; use the control API only when needed."
