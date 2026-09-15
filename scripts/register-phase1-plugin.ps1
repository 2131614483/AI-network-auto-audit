<#
  Explicitly publish the only verified Phase 1 plugin for one tenant.

  This script never installs code, grants new database privileges or starts a
  plugin.  It validates the checked-in binding and publishes its existing
  read-only manifest in the catalog.
#>
[CmdletBinding()]
param(
  [string]$DatabaseUrl = "postgresql://audit_app:admin@localhost:5432/audit_network",
  [string]$TenantSlug = "local-dev",
  [switch]$EnableReadOnlyPolicy,
  [switch]$EnableLedgerQualityPolicy,
  [switch]$EnableR1PluginsPolicy
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
# Resolved, not hardcoded: this used to name C:\ProgramData\Anaconda3\python.exe,
# which contradicts start-brain.ps1 and exists on no other machine.
. (Join-Path $PSScriptRoot "env-common.ps1")
$python = Resolve-ProjectPython -ProjectRoot $projectRoot

$arguments = @("-m", "packages.plugin_runtime.registration", "--database-url", $DatabaseUrl, "--tenant-slug", $TenantSlug)
if ($EnableReadOnlyPolicy) {
  $arguments += "--enable-read-only-policy"
}
if ($EnableLedgerQualityPolicy) {
  $arguments += "--enable-ledger-quality-policy"
}
if ($EnableR1PluginsPolicy) {
  $arguments += "--enable-r1-plugins-policy"
}
& $python @arguments
if ($LASTEXITCODE -ne 0) {
  throw "Phase 1 plugin registration failed."
}
