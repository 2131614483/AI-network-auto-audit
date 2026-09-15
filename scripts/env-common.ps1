<#
  Shared environment discovery for the Audit Network scripts.

  Dot-source it and call the resolvers instead of naming an interpreter or a
  PostgreSQL client path directly:

      . (Join-Path $PSScriptRoot "env-common.ps1")
      $python = Resolve-ProjectPython -ProjectRoot $projectRoot
      $psql   = Resolve-Psql

  Why this file exists: the scripts used to hardcode `C:\Users\<name>\...`,
  `C:\ProgramData\Anaconda3\python.exe` and `C:\Program Files\PostgreSQL\16\bin\*`.
  Those paths only exist on the machine that wrote them, so every script that
  used them failed on a copied/checked-out repository.  Nothing here names a
  machine-specific location:

    * the project virtualenv is always preferred (the only interpreter the
      pinned dependency set is verified against);
    * a system `python` is used **only** when a caller opts in, because a bare
      `python` silently picks an unrelated interpreter;
    * PostgreSQL client tools are located through `PATH` first, then a
      version-agnostic glob, so 16/17/18 or a non-default install drive works.

  Every resolver throws with an actionable message rather than returning an
  empty string, so a missing prerequisite reads as "here is the next command"
  instead of a confusing failure further down.
#>

function Get-ProjectRoot {
  <# Repository root, derived from the calling script's own location. #>
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][string]$ScriptRoot)
  return (Resolve-Path (Join-Path $ScriptRoot "..")).Path
}

function Resolve-ProjectPython {
  <#
    Resolve the interpreter for project scripts.

    Default: the project virtualenv, or throw.  `-AllowSystemFallback` keeps the
    old "any python will do" behaviour for scripts that genuinely only need a
    Python interpreter (never a project import).
  #>
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [switch]$AllowSystemFallback
  )

  $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPython) { return $venvPython }

  if ($AllowSystemFallback) {
    $command = Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Source }
  }

  throw ("Project virtualenv not found: $venvPython`n" +
    "Create it first:`n" +
    "    python -m venv .venv`n" +
    "    .venv\Scripts\python.exe -m pip install -e `".[dev]`"`n" +
    "Or run the guided setup once:`n" +
    "    powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1")
}

function Get-PostgresInstallRoots {
  <# Candidate PostgreSQL installation roots, newest version first. #>
  [CmdletBinding()]
  param([string[]]$SearchRoots)

  $roots = @()
  if ($SearchRoots) {
    $roots = $SearchRoots
  } else {
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
      if ($base) { $roots += (Join-Path $base "PostgreSQL") }
    }
    $roots += "C:\PostgreSQL"
  }
  return @($roots | Where-Object { $_ -and (Test-Path -LiteralPath $_) })
}

function Get-PostgresVersionKey {
  <#
    Sort key that orders `PostgreSQL\9.6` < `PostgreSQL\16` < `PostgreSQL\17`.

    A plain string sort gets this wrong (`17` < `9`), which would silently pick
    an ancient client on a machine that has both.
  #>
  [CmdletBinding()]
  param([AllowNull()][AllowEmptyString()][string]$Path)

  if (-not $Path) { return 0 }
  $leaf = Split-Path -Leaf $Path
  $parts = @($leaf -split '\.')
  $major = 0
  $minor = 0
  if ($parts.Count -ge 1) { [void][int]::TryParse($parts[0], [ref]$major) }
  if ($parts.Count -ge 2) { [void][int]::TryParse($parts[1], [ref]$minor) }
  return ($major * 1000 + $minor)
}

function Resolve-PgTool {
  <#
    Locate a PostgreSQL client tool (psql.exe, pg_dump.exe, pg_restore.exe,
    createdb.exe, dropdb.exe, pg_isready.exe).

    Order: PATH -> every `*\PostgreSQL\*` install root, highest version first.

    Highest-first is deliberate, and it has a boundary worth stating because the
    two directions are not symmetric:

      * a **newer client against an older server works** -- `psql` 18 talks to a
        16 server, and `pg_dump` 18 may dump a 16 server.  That is the usual
        layout on a machine with one PostgreSQL server plus a second client
        pulled in by another tool;
      * a **client older than the server does not**: `pg_dump` refuses with
        "server version mismatch".  So a machine whose newest installed client
        is older than its server needs a matching client (or an explicit
        -PgTool path) before backup-db.ps1 / restore-db.ps1 will work.

    This resolver always picks the newest, which is the right default; it is
    not a promise that the newest is new enough.
  #>
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [string[]]$SearchRoots
  )

  $command = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($command) { return $command.Source }

  $found = @()
  foreach ($root in (Get-PostgresInstallRoots -SearchRoots $SearchRoots)) {
    $found += @(Get-ChildItem -LiteralPath $root -Filter $Name -Recurse -File -ErrorAction SilentlyContinue)
  }

  if ($found.Count -gt 0) {
    # `...\PostgreSQL\16\bin\psql.exe` -> version root `...\PostgreSQL\16`.
    # Anything shallower falls back to sort key 0 rather than throwing.
    $best = $found |
      Sort-Object -Property @{
        Expression = {
          $bin = $_.Directory
          $versionRoot = if ($bin -and $bin.Parent) { $bin.Parent.FullName } else { $null }
          Get-PostgresVersionKey -Path $versionRoot
        }
      } -Descending |
      Select-Object -First 1
    return $best.FullName
  }

  throw ("PostgreSQL client '$Name' was not found.`n" +
    "Install the PostgreSQL client tools, or add their bin directory to PATH.`n" +
    "Checked: PATH, " + ((Get-PostgresInstallRoots -SearchRoots $SearchRoots) -join ", ") + "`n" +
    "Setup guidance: scripts\bootstrap.ps1 -Check")
}

function Invoke-NativeCommand {
  <#
    Run a native executable and capture stdout+stderr plus its exit code.

        $result = Invoke-NativeCommand -File $psql -Arguments @("-c", "select 1")
        $result.Output      # string[]
        $result.ExitCode    # int

    This exists because a plain `& $exe ... 2>&1` is unsafe in these scripts:
    the callers run with `$ErrorActionPreference = 'Stop'`, under which a native
    command's redirected stderr is surfaced as a terminating NativeCommandError.
    A probe that is *supposed* to fail (psql against a stopped server, a missing
    interpreter) therefore killed the whole script instead of reporting it.
    Native stderr is captured as data here, never escalated to an exception.
  #>
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$File,
    [string[]]$Arguments = @()
  )

  $previous = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $output = & $File @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
    return @{
      Output   = @($output | ForEach-Object { "$_" })
      ExitCode = [int]$exitCode
    }
  } catch {
    return @{ Output = @("$($_.Exception.Message)"); ExitCode = 1 }
  } finally {
    $ErrorActionPreference = $previous
  }
}

function Test-PgExtensionAvailable {
  <#
    Ask a database which of the required extensions are available to install.

    Returns the subset of `$Names` that is NOT available, so callers can print
    one actionable line per missing extension.  `vector` is the one that a stock
    EDB Windows installer does not ship, so it fails on its own.
  #>
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$Psql,
    [Parameter(Mandatory = $true)][string]$User,
    [Parameter(Mandatory = $true)][string]$Password,
    [string]$DbHost = "localhost",
    [int]$Port = 5432,
    [string]$Database = "postgres",
    [string[]]$Names = @("pgcrypto", "pg_trgm", "ltree", "vector")
  )

  $previous = $env:PGPASSWORD
  try {
    $env:PGPASSWORD = $Password
    $quoted = ($Names | ForEach-Object { "'$_'" }) -join ","
    $sql = "SELECT name FROM pg_available_extensions WHERE name IN ($quoted);"
    $result = Invoke-NativeCommand -File $Psql -Arguments @(
      "-X", "-tA", "-h", $DbHost, "-p", "$Port", "-U", $User, "-d", $Database, "-c", $sql
    )
    if ($result.ExitCode -ne 0) {
      throw "Could not query pg_available_extensions: $($result.Output -join ' ')"
    }
    $available = @($result.Output | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    # NOTE for callers: an empty result surfaces as $null, and a single missing
    # extension unrolls into a bare string.  Always normalise the result:
    #     @(Test-PgExtensionAvailable ... | Where-Object { $_ })
    # otherwise "nothing missing" reads as "one missing extension with no name".
    # (Do not "fix" this with `return ,@(...)`: the comma makes the whole array a
    # single pipeline object, so `| Where-Object { $_ }` sees one opaque item.)
    return @($Names | Where-Object { $_ -notin $available })
  } finally {
    $env:PGPASSWORD = $previous
  }
}

function Test-PgExtensionInstalled {
  <#
    Ask a *database* which of `$Names` are already installed in it.

    The sibling of Test-PgExtensionAvailable, and the difference is the whole
    point: "available" describes the server's share\extension directory, while
    "installed" describes this database (CREATE EXTENSION has been run in it).
    A machine that ships pgvector on disk but never installed it into
    `audit_network` answers "available" and then dies inside the first migration
    that uses `vector` -- a false pass that reports the environment as ready.

    Returns the subset of `$Names` that is NOT installed, normalised the same
    way as its sibling: `@(Test-PgExtensionInstalled ... | Where-Object { $_ })`.
  #>
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$Psql,
    [Parameter(Mandatory = $true)][string]$User,
    [Parameter(Mandatory = $true)][string]$Password,
    [string]$DbHost = "localhost",
    [int]$Port = 5432,
    [string]$Database = "postgres",
    [string[]]$Names = @("pgcrypto", "pg_trgm", "ltree", "vector")
  )

  $previous = $env:PGPASSWORD
  try {
    $env:PGPASSWORD = $Password
    $quoted = ($Names | ForEach-Object { "'$_'" }) -join ","
    $sql = "SELECT extname FROM pg_extension WHERE extname IN ($quoted);"
    $result = Invoke-NativeCommand -File $Psql -Arguments @(
      "-X", "-tA", "-h", $DbHost, "-p", "$Port", "-U", $User, "-d", $Database, "-c", $sql
    )
    if ($result.ExitCode -ne 0) {
      throw "Could not query pg_extension in ${Database}: $($result.Output -join ' ')"
    }
    $installed = @($result.Output | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    return @($Names | Where-Object { $_ -notin $installed })
  } finally {
    $env:PGPASSWORD = $previous
  }
}
