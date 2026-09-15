<#
Read-only acceptance check for Unified Plugin Protocol contract packages.
It inventories user-provided samples and verifies that the local database has
representative domain records. It never registers, installs, starts or calls a plugin.
#>
[CmdletBinding()]
param(
  # No default: the previous G:\数据 only existed on the author's machine.  Pass
  # the directory holding the samples to inventory, e.g.
  #   .\scripts\verify-plugin-protocol-data.ps1 -DataRoot D:\samples
  [string]$DataRoot = "",
  [string]$Database = "audit_network",
  [string]$DatabaseUser = "postgres",
  [string]$DatabasePassword = "admin"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "env-common.ps1")
$projectRoot = Get-ProjectRoot -ScriptRoot $PSScriptRoot
$schemaPath = Join-Path $projectRoot "contracts\jsonschema\unified-plugin-protocol.schema.json"
$protocolPackages = Get-ChildItem -LiteralPath (Join-Path $projectRoot "plugins\builtin") -Filter "plugin.protocol.json" -File -Recurse

if (-not (Test-Path -LiteralPath $schemaPath)) {
  throw "未找到统一插件协议 Schema：$schemaPath"
}
if ($protocolPackages.Count -lt 4) {
  throw "至少需要四个内置协议插件包；当前仅找到 $($protocolPackages.Count) 个。"
}

Get-Content -Raw -LiteralPath $schemaPath | ConvertFrom-Json | Out-Null
foreach ($package in $protocolPackages) {
  $descriptor = Get-Content -Raw -LiteralPath $package.FullName | ConvertFrom-Json
  if ($descriptor.lifecycle -ne "contract_only") { throw "协议包必须保持 contract_only：$($package.FullName)" }
  if ($null -ne $descriptor.entrypoint -or $null -ne $descriptor.command) { throw "协议包不能包含执行绑定：$($package.FullName)" }
}

if (-not $DataRoot) {
  throw ("No -DataRoot was given. Pass the directory that holds the user samples to inventory, e.g.`n" +
    "    .\scripts\verify-plugin-protocol-data.ps1 -DataRoot D:\samples")
}
if (-not (Test-Path -LiteralPath $DataRoot)) {
  throw "未找到用户数据目录：$DataRoot。请提供数据目录或改用协议夹具验收。"
}

$files = @(Get-ChildItem -LiteralPath $DataRoot -File -Recurse -ErrorAction SilentlyContinue)
if ($files.Count -eq 0) { throw "用户数据目录中没有可读取文件：$DataRoot" }
$extensions = @($files | ForEach-Object { if ($_.Extension) { $_.Extension.ToLowerInvariant() } else { "[no extension]" } } | Sort-Object -Unique)
$requiredFormats = @(".md", ".csv", ".json")
$missingFormats = @($requiredFormats | Where-Object { $_ -notin $extensions })
if ($missingFormats.Count -gt 0) { throw "用户数据缺少协议验收需要的格式：$($missingFormats -join ', ')" }

# Resolved through PATH and a version-agnostic install glob; this used to be a
# three-entry literal list pinned to 16/17/18 under C:\Program Files.
$psql = Resolve-PgTool -Name psql.exe

$env:PGPASSWORD = $DatabasePassword
$query = @"
SELECT 'semantic.documents' AS source, count(*) AS row_count FROM semantic.documents
UNION ALL SELECT 'graph.nodes', count(*) FROM graph.nodes
UNION ALL SELECT 'audit.evidence', count(*) FROM audit.evidence
UNION ALL SELECT 'quant.backtests', count(*) FROM quant.backtests
UNION ALL SELECT 'aiops.alerts', count(*) FROM aiops.alerts
ORDER BY source;
"@
$rows = & $psql -h localhost -p 5432 -U $DatabaseUser -d $Database -v ON_ERROR_STOP=1 -t -A -F '|' -c $query
if ($LASTEXITCODE -ne 0) { throw "无法读取本机 PostgreSQL 验收数据。" }

$databaseCounts = @{}
foreach ($row in $rows) {
  $parts = $row -split '\|', 2
  if ($parts.Count -eq 2) { $databaseCounts[$parts[0]] = [int64]$parts[1] }
}
$missingDomains = @($databaseCounts.GetEnumerator() | Where-Object { $_.Value -le 0 } | ForEach-Object { $_.Key })

Write-Output "[plugin-protocol] Schema: $([IO.Path]::GetFileName($schemaPath)); protocol packages: $($protocolPackages.Count)"
Write-Output "[plugin-protocol] G data: $($files.Count) files; formats include $($requiredFormats -join ', ')"
$databaseCounts.GetEnumerator() | Sort-Object Key | ForEach-Object { Write-Output "[plugin-protocol] $($_.Key): $($_.Value) rows" }
if ($missingDomains.Count -gt 0) {
  Write-Warning "这些领域没有真实验收记录，应在后续独立模块中使用固定夹具：$($missingDomains -join ', ')"
} else {
  Write-Output "[plugin-protocol] Real-data compatibility evidence is available for knowledge, graph, audit, quant and AIOps."
}
Write-Output "[plugin-protocol] Read-only acceptance complete. No plugin was registered, installed, started or called."
