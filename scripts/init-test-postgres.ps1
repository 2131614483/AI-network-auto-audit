<#
  Initialize the one persistent, dedicated native PostgreSQL database used by
  integration tests.  It never drops a database or touches audit_network.
#>
[CmdletBinding()]
param(
  [string]$DatabaseName = "audit_network_test",
  [string]$HostName = "127.0.0.1",
  [int]$Port = 5432,
  [string]$AdminUser = "postgres",
  [string]$AdminPassword = "admin",
  [string]$MigratorUser = "audit_migrator",
  [string]$MigratorPassword = "admin"
)

$ErrorActionPreference = "Stop"
if ($DatabaseName -ne "audit_network_test") {
  throw "Only the dedicated audit_network_test database may be initialized by this script."
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
# Interpreter and client tools are resolved, never hardcoded: this script used
# to name C:\ProgramData\Anaconda3\python.exe and a PostgreSQL\*\bin glob that
# both only resolve on the author's machine.
. (Join-Path $PSScriptRoot "env-common.ps1")
$python = Resolve-ProjectPython -ProjectRoot $projectRoot
$psql = Resolve-PgTool -Name psql.exe

function Invoke-AdminSql([string]$Sql) {
  $env:PGPASSWORD = $AdminPassword
  & $psql -X -v ON_ERROR_STOP=1 -h $HostName -p $Port -U $AdminUser -d postgres -c $Sql
  if ($LASTEXITCODE -ne 0) { throw "PostgreSQL administration command failed." }
}

$oldPassword = $env:PGPASSWORD
$oldDatabaseUrl = $env:DATABASE_URL
try {
  $env:PGPASSWORD = $AdminPassword
  $existingOutput = @(& $psql -X -tA -h $HostName -p $Port -U $AdminUser -d postgres -c "SELECT 1 FROM pg_database WHERE datname='$DatabaseName';")
  $existing = [string]::Join("`n", $existingOutput).Trim()
  if ($LASTEXITCODE -ne 0) { throw "Could not check dedicated test database." }
  if ($existing -ne "1") { Invoke-AdminSql "CREATE DATABASE $DatabaseName OWNER $MigratorUser;" }

  Invoke-AdminSql "GRANT CONNECT ON DATABASE $DatabaseName TO audit_app;"
  $env:PGPASSWORD = $AdminPassword
  & $psql -X -v ON_ERROR_STOP=1 -h $HostName -p $Port -U $AdminUser -d $DatabaseName -c "CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE EXTENSION IF NOT EXISTS ltree; CREATE EXTENSION IF NOT EXISTS vector;"
  if ($LASTEXITCODE -ne 0) { throw "Test database extension initialization failed." }

  $env:DATABASE_URL = "postgresql://${MigratorUser}:${MigratorPassword}@${HostName}:${Port}/${DatabaseName}"
  & $python -m alembic -c (Join-Path $projectRoot "alembic.ini") upgrade head
  if ($LASTEXITCODE -ne 0) { throw "Test database migrations failed." }
  Write-Output "Dedicated native test database is ready: $DatabaseName"
}
finally {
  $env:PGPASSWORD = $oldPassword
  if ($null -eq $oldDatabaseUrl) { Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue } else { $env:DATABASE_URL = $oldDatabaseUrl }
}
