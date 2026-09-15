# Register / unregister the CW2 log collector as a Windows Scheduled Task.
#
# The collector keeps log spool maintenance running even when the GUI is
# closed: every 10 minutes one maintenance pass (rotation/archive/index/health
# report) runs under the current user.  `--once` mode is used so each trigger
# is a short, observable process whose JSON report lands on stdout.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/register-collector-task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/register-collector-task.ps1 -Unregister
#   powershell -ExecutionPolicy Bypass -File scripts/register-collector-task.ps1 -WhatIf
#
# This script only registers a scheduled task; it never touches databases or
# production systems.  Re-running is idempotent (task replaced in place).

param(
    [switch]$Unregister,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$TaskName = "AuditNetworkLogCollector"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$SpoolRoot = Join-Path $ProjectRoot ".data\isolated\logs"

if (-not (Test-Path $VenvPython)) {
    throw "venv python not found: $VenvPython"
}

$Action = New-ScheduledTaskAction `
    -Execute $VenvPython `
    -Argument "-m packages.observability.collector --root `"$SpoolRoot`" --once" `
    -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger -AtStartup
$Repetition = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10)

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -MultipleInstances IgnoreNew

if ($Unregister) {
    if ($WhatIf) {
        Write-Output "WhatIf: would unregister scheduled task '$TaskName'"
        exit 0
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Unregistered scheduled task '$TaskName' (if it existed)."
    exit 0
}

if ($WhatIf) {
    Write-Output "WhatIf: would register scheduled task '$TaskName'"
    Write-Output "  python: $VenvPython"
    Write-Output "  args:   -m packages.observability.collector --root `"$SpoolRoot`" --once"
    Write-Output "  triggers: at startup + every 10 minutes"
    exit 0
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger @($Trigger, $Repetition) `
    -Settings $Settings `
    -Description "Audit Network CW2 log spool maintenance (rotation/archive/index). GUI-independent." `
    -Force | Out-Null

Write-Output "Registered scheduled task '$TaskName': startup + every 10 minutes -> collector --once"
Write-Output "Spool root: $SpoolRoot"
