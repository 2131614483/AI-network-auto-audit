<#
  Verify the native PostgreSQL this project depends on: reachable, and carrying
  the four extensions every migration assumes.

  Two deliberate changes from the previous version:

  * The client tool is *resolved* (PATH first, then a version-agnostic install
    glob) instead of being pinned to `C:\Program Files\PostgreSQL\16\bin`.
    A PostgreSQL 17/18 install, or one on another drive, used to fail here.
  * Missing extensions are now a **failure**, not a silent pass.  The old check
    ran `select extname ... where extname in (...)` and only looked at the exit
    code, so a database with none of the four extensions still reported
    "Native PostgreSQL verification passed" -- and the first migration then died
    with `permission denied to create extension "vector"`.  `vector` is the one
    a stock EDB Windows installer does not ship, so this is the single most
    likely thing to be missing on a fresh machine.
#>
param(
  [string]$HostName = "localhost",
  [int]$Port = 5432,
  [string]$Database = "audit_network",
  [string]$User = "audit_app",
  [string]$Password = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "env-common.ps1")

$psqlPath = Resolve-PgTool -Name psql.exe

$pgIsReady = Join-Path (Split-Path $psqlPath) "pg_isready.exe"
if (Test-Path -LiteralPath $pgIsReady) {
  & $pgIsReady -h $HostName -p $Port -d $Database
  if ($LASTEXITCODE -ne 0) { throw "PostgreSQL is not ready: $HostName`:$Port/$Database" }
}

$required = @("pgcrypto", "pg_trgm", "ltree", "vector")

$oldPassword = $env:PGPASSWORD
try {
  if ($Password) { $env:PGPASSWORD = $Password }

  & $psqlPath -h $HostName -p $Port -U $User -d $Database -v ON_ERROR_STOP=1 -c "select version();"
  if ($LASTEXITCODE -ne 0) {
    throw "Native PostgreSQL connection failed: $User@$HostName`:$Port/$Database"
  }

  $installed = @(
    & $psqlPath -X -tA -h $HostName -p $Port -U $User -d $Database `
      -c "select extname from pg_extension;" 2>$null |
      ForEach-Object { $_.Trim() } |
      Where-Object { $_ }
  )
  if ($LASTEXITCODE -ne 0) {
    throw "Could not read pg_extension from $Database."
  }
} finally {
  $env:PGPASSWORD = $oldPassword
}

$missing = @($required | Where-Object { $_ -notin $installed })
if ($missing.Count -gt 0) {
  $lines = @(
    "Database $Database is missing required extension(s): $($missing -join ', ')",
    "",
    "Create them as a superuser (the application role deliberately cannot):",
    "    psql -U postgres -d $Database -f scripts/init-native-postgres.sql",
    "or run the guided setup, which checks this for you:",
    "    powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -Check"
  )
  if ("vector" -in $missing) {
    $lines += @(
      "",
      "Note on 'vector': the official EDB Windows PostgreSQL installer does NOT",
      "ship pgvector, so it is the usual culprit. Install it separately, then",
      "re-run. scripts/bootstrap.ps1 -Check prints the exact steps and the",
      "pg_available_extensions result for this machine."
    )
  }
  throw ($lines -join "`n")
}

Write-Output "Native PostgreSQL verification passed (extensions: $($required -join ', '))."
