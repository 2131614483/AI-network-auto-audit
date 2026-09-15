<#
  Daily PostgreSQL backup with retention (O9 remediation).

  Dumps audit_network (custom format) to .data\backups\, prunes files older than
  -RetentionDays, and writes a manifest with size + SHA256.  Safe to schedule
  daily; never deletes anything outside .data\backups.

  Examples:
    .\scripts\backup-db.ps1
    .\scripts\backup-db.ps1 -Database audit_network_test -RetentionDays 3
#>
param(
  [string]$Database = "audit_network",
  [string]$User = "audit_backup",
  [string]$Password = "admin",
  [string]$DbHost = "localhost",
  [int]$Port = 5432,
  [int]$RetentionDays = 14
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
# pg_dump is resolved through PATH and a version-agnostic install glob, so a
# PostgreSQL 17/18 install (or one on another drive) works; this used to name
# C:\Program Files\PostgreSQL\16\bin\pg_dump.exe.
. (Join-Path $PSScriptRoot "env-common.ps1")
$backupDir = Join-Path $projectRoot ".data\backups"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

$pgDump = Resolve-PgTool -Name pg_dump.exe

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$dumpPath = Join-Path $backupDir "$Database-$stamp.dump"
$env:PGPASSWORD = $Password
& $pgDump --host $DbHost --port $Port --username $User --dbname $Database `
  --format=custom --file $dumpPath
if ($LASTEXITCODE -ne 0) {
  Remove-Item -LiteralPath $dumpPath -ErrorAction SilentlyContinue
  throw "pg_dump failed (exit $LASTEXITCODE) - role $User must exist with BYPASSRLS; run scripts\init-backup-role.sql as the postgres superuser once"
}

$file = Get-Item -LiteralPath $dumpPath
$hash = (Get-FileHash -LiteralPath $dumpPath -Algorithm SHA256).Hash
$manifestLine = "{0}|{1}|{2}|{3}" -f $stamp, $file.Length, $hash, $Database
Add-Content -LiteralPath (Join-Path $backupDir "manifest.txt") -Value $manifestLine -Encoding UTF8

# Retention: prune dumps older than the retention window (manifest keeps history).
$cutoff = (Get-Date).AddDays(-$RetentionDays)
Get-ChildItem -LiteralPath $backupDir -Filter "$Database-*.dump" |
  Where-Object { $_.LastWriteTime -lt $cutoff } |
  Remove-Item -Force

Write-Output "[audit-network] backup ok: $dumpPath ($($file.Length) bytes, sha256=$hash)"
Write-Output "[audit-network] retention: keeping dumps newer than $RetentionDays day(s)"
