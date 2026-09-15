<#
  Remove the Audit Network watchdog scheduled task (if present).

  Example:
    .\scripts\uninstall-watchdog-task.ps1
#>
$ErrorActionPreference = "Stop"
$taskName = "audit-network-watchdog"
schtasks /Delete /TN $taskName /F
Write-Output "[audit-network] watchdog task removed: $taskName"
