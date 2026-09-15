<#
  Exercise a complete migration bootstrap against a disposable native database.

  Audit migrations deliberately do not implement destructive Alembic downgrade:
  production evidence, approvals and execution history must be archived, not
  cascade-dropped.  This script verifies the equivalent safe Phase-0 property:
  an empty database can be created, upgraded to the single Alembic head and
  discarded without ever touching the running audit_network database.
#>
[CmdletBinding()]
param(
  [string]$DatabaseName = "audit_network_verify_bootstrap",
  [string]$HostName = "127.0.0.1",
  [int]$Port = 5432,
  [string]$AdminUser = "postgres",
  [string]$AdminPassword = "admin",
  [string]$MigratorUser = "audit_migrator",
  [string]$MigratorPassword = "admin"
)

$ErrorActionPreference = "Stop"
if ($DatabaseName -notmatch '^audit_network_verify_[a-z0-9_]+$') {
  throw "DatabaseName must use the disposable audit_network_verify_ prefix."
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
# Resolved, not hardcoded: this used to require a PostgreSQL 16 install and an
# Anaconda interpreter, so it failed on a 17/18 machine or a copied repository.
. (Join-Path $PSScriptRoot "env-common.ps1")
$psql = Resolve-PgTool -Name psql.exe
$python = Resolve-ProjectPython -ProjectRoot $projectRoot

function Invoke-AdminSql([string]$Sql) {
  $env:PGPASSWORD = $AdminPassword
  & $psql -X -v ON_ERROR_STOP=1 -h $HostName -p $Port -U $AdminUser -d postgres -c $Sql
  if ($LASTEXITCODE -ne 0) { throw "PostgreSQL command failed." }
}

try {
  # The database name is regex-validated above before it is interpolated.
  Invoke-AdminSql "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DatabaseName' AND pid <> pg_backend_pid();"
  Invoke-AdminSql "DROP DATABASE IF EXISTS $DatabaseName;"
  Invoke-AdminSql "CREATE DATABASE $DatabaseName OWNER $MigratorUser;"

  $env:PGPASSWORD = $AdminPassword
  & $psql -X -v ON_ERROR_STOP=1 -h $HostName -p $Port -U $AdminUser -d $DatabaseName -c "CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE EXTENSION IF NOT EXISTS ltree; CREATE EXTENSION IF NOT EXISTS vector;"
  if ($LASTEXITCODE -ne 0) { throw "Extension initialization failed." }

  $env:DATABASE_URL = "postgresql://${MigratorUser}:${MigratorPassword}@${HostName}:${Port}/${DatabaseName}"
  & $python -m alembic -c (Join-Path $projectRoot "alembic.ini") upgrade head
  if ($LASTEXITCODE -ne 0) { throw "Alembic bootstrap failed." }
  $head = (& $python -m alembic -c (Join-Path $projectRoot "alembic.ini") current) -join "`n"
  if ($LASTEXITCODE -ne 0 -or $head -notmatch '\(head\)') { throw "Alembic head verification failed." }
  Write-Output "[phase0] empty database bootstrap passed: $DatabaseName"
}
finally {
  Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
  try {
    Invoke-AdminSql "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DatabaseName' AND pid <> pg_backend_pid();"
    Invoke-AdminSql "DROP DATABASE IF EXISTS $DatabaseName;"
    Write-Output "[phase0] disposable database removed: $DatabaseName"
  } catch {
    Write-Warning "Could not remove disposable database ${DatabaseName}: $($_.Exception.Message)"
  }
}
