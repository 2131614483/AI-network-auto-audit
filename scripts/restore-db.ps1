<#
  Restore drill: restore the latest dump into a fresh scratch database and run
  a smoke check (table counts > 0).  Never touches the real databases.  The
  scratch database name includes a timestamp so repeated drills do not collide.

  Requires the audit_backup role (BYPASSRLS + CREATEDB), created once by the
  DBA via scripts\init-backup-role.sql.

  Examples:
    .\scripts\restore-db.ps1
    .\scripts\restore-db.ps1 -SourceDatabase audit_network_test
#>
param(
  [string]$SourceDatabase = "audit_network",
  [string]$User = "audit_backup",
  [string]$Password = "admin",
  [string]$DbHost = "localhost",
  [int]$Port = 5432
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "env-common.ps1")
$backupDir = Join-Path $projectRoot ".data\backups"
# Client tools are resolved (PATH first, then a version-agnostic install glob),
# so PostgreSQL 17/18 or an install on another drive works; these four paths
# used to be pinned to C:\Program Files\PostgreSQL\16\bin\.
$pgRestore = Resolve-PgTool -Name pg_restore.exe
$createdb = Resolve-PgTool -Name createdb.exe
$dropdb = Resolve-PgTool -Name dropdb.exe
$psql = Resolve-PgTool -Name psql.exe

$dump = Get-ChildItem -LiteralPath $backupDir -Filter "$SourceDatabase-*.dump" |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $dump) {
  throw "no backup found under $backupDir for $SourceDatabase"
}

$scratchDbUnique = "audit_network_restore_drill" + (Get-Date -Format "yyyyMMddHHmmss")
$env:PGPASSWORD = $Password

# Create the scratch database as audit_backup (CREATEDB), restore into it, check.
& $createdb --host $DbHost --port $Port --username $User --owner $User $scratchDbUnique
if ($LASTEXITCODE -ne 0) { throw "createdb failed (exit $LASTEXITCODE)" }

try {
  & $pgRestore --host $DbHost --port $Port --username $User --dbname $scratchDbUnique `
    --exit-on-error $dump.FullName
  if ($LASTEXITCODE -ne 0) { throw "drill restore failed (exit $LASTEXITCODE)" }

  $check = & $psql --host $DbHost --port $Port --username $User --dbname $scratchDbUnique -t -A `
    -c "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema');"
  Write-Output "[audit-network] restore drill ok: $scratchDbUnique restored from $($dump.Name) (tables=$check)"
} finally {
  & $dropdb --host $DbHost --port $Port --username $User --if-exists $scratchDbUnique | Out-Null
}
