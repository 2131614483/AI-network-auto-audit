<#
  One-shot environment bootstrap for a freshly cloned or copied checkout.

  Goal: after `git clone`, one command takes you from "nothing installed" to
  "API + Worker + Electron console start".  Every step either succeeds or says
  exactly which command to run next -- it never fails silently and never
  pretends a missing prerequisite is fine.

  Usage:
    powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -Check
        Report only.  Writes nothing, installs nothing.  Run this first: it
        tells you what is missing on this machine.

    powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
        Do everything: create .venv, install Python deps, install desktop deps,
        create .env, initialise the database and run migrations.

  Useful switches:
    -SkipNpmInstall     Don't touch desktop\node_modules (offline / already have it)
    -SkipDatabase       Don't create roles/databases or run migrations
    -SkipMigrations     Create the database but leave migrations to you
    -SkipTestDatabase   Don't create the dedicated pytest database
    -SuperUser / -SuperPassword / -DbHost / -DbPort
                        PostgreSQL superuser connection used for step 5

  Why this file exists: the repository previously had no bootstrap at all.  A
  new machine had to reverse-engineer its setup from .github/workflows/quality.yml,
  and the launcher scripts contained paths that only exist on the author's
  machine (C:\Users\<name>\..., C:\ProgramData\Anaconda3\..., PostgreSQL\16\bin).
  All of that now resolves through scripts\env-common.ps1.
#>
[CmdletBinding()]
param(
  [switch]$Check,
  [switch]$SkipNpmInstall,
  [switch]$SkipDatabase,
  [switch]$SkipMigrations,
  [switch]$SkipTestDatabase,
  [string]$DbHost = "localhost",
  [int]$DbPort = 5432,
  [string]$SuperUser = "postgres",
  [string]$SuperPassword = "admin",
  [string]$MainDatabase = "audit_network",
  [string]$TestDatabase = "audit_network_test",
  [string]$AppRole = "audit_app",
  [string]$MigratorRole = "audit_migrator",
  [string]$DevPassword = "admin",
  [int]$MinimumPostgresMajor = 16
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "env-common.ps1")

$projectRoot = Get-ProjectRoot -ScriptRoot $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$desktopModules = Join-Path $projectRoot "desktop\node_modules"
$envPath = Join-Path $projectRoot ".env"
$envExamplePath = Join-Path $projectRoot ".env.example"

$script:Problems = New-Object System.Collections.ArrayList
$script:AutoFix = New-Object System.Collections.ArrayList
$script:ManualFix = New-Object System.Collections.ArrayList

function Write-Head([string]$Text) {
  Write-Output ""
  Write-Output "-- $Text"
}
function Write-Ok([string]$Text) { Write-Output "   [ok]   $Text" }
function Write-Skip([string]$Text) { Write-Output "   [skip] $Text" }
function Write-Act([string]$Text) { Write-Output "   [run]  $Text" }
function Write-Need([string]$Text) {
  Write-Output "   [need] $Text"
  [void]$script:Problems.Add($Text)
}
function Add-AutoFix([string]$Command) { [void]$script:AutoFix.Add($Command) }
function Add-ManualFix([string]$Text) { [void]$script:ManualFix.Add($Text) }

Write-Output "=============================================================="
Write-Output " Audit Network bootstrap"
Write-Output " project: $projectRoot"
if ($Check) {
  Write-Output " mode   : -Check (report only; nothing will be created or installed)"
} else {
  Write-Output " mode   : apply"
}
Write-Output "=============================================================="

# --------------------------------------------------------------------------
# 1. Python virtualenv
# --------------------------------------------------------------------------
Write-Head "1/6 Python virtualenv"

function Find-BasePython {
  <#
    A system interpreter able to create a venv.  `py -3` is preferred on
    Windows because it consults the registered installations; a bare `python`
    is then used if `py` is absent (e.g. a Microsoft Store or conda install).
  #>
  $candidates = @(
    @{ File = "py"; Prefix = @("-3") },
    @{ File = "python"; Prefix = @() }
  )
  foreach ($candidate in $candidates) {
    $command = Get-Command $candidate.File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $command) { continue }
    # Splat through an array variable: `& $exe @("a","b")` passes the array as a
    # single argument, which silently breaks the -c probe.
    $probeArgs = @($candidate.Prefix) + @("-c", "import sys;print('%d.%d' % sys.version_info[:2])")
    $probe = Invoke-NativeCommand -File $command.Source -Arguments $probeArgs
    $text = ($probe.Output -join "").Trim()
    if ($probe.ExitCode -eq 0 -and $text -match '^\d+\.\d+$') {
      return @{ File = $command.Source; Prefix = $candidate.Prefix; Version = $text }
    }
  }
  return $null
}

$venvReady = $false
if (Test-Path -LiteralPath $venvPython) {
  $depProbe = Invoke-NativeCommand -File $venvPython -Arguments @(
    "-c", "import fastapi, psycopg2, alembic, pydantic; print('deps-ok')"
  )
  if ($depProbe.ExitCode -eq 0 -and (($depProbe.Output -join " ") -match "deps-ok")) {
    Write-Ok "virtualenv present with project dependencies (.venv)"
    $venvReady = $true
  } else {
    Write-Need "virtualenv exists but project dependencies are not importable"
    Add-AutoFix '$venvPython -m pip install -e ".[dev]"'
  }
} else {
  $base = Find-BasePython
  if (-not $base) {
    Write-Need "no Python 3 interpreter found on PATH (need 3.11+)"
    Add-ManualFix "Install Python 3.12 from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then re-run this script."
  } else {
    Write-Need "no .venv in the project root (found Python $($base.Version))"
    Add-AutoFix "python -m venv .venv   # using $($base.File)"
    Add-AutoFix '$venvPython -m pip install -e ".[dev]"'
    if (-not $Check) {
      Write-Act "creating .venv with Python $($base.Version)"
      $venvArgs = @($base.Prefix) + @("-m", "venv", (Join-Path $projectRoot ".venv"))
      & $base.File @venvArgs
      if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
        Write-Need "venv creation failed"
      } else {
        Write-Act 'installing project dependencies (pip install -e ".[dev]")'
        & $venvPython -m pip install --disable-pip-version-check -e ".[dev]"
        if ($LASTEXITCODE -ne 0) {
          Write-Need "pip install failed -- see the output above"
        } else {
          Write-Ok "virtualenv created and dependencies installed"
          $venvReady = $true
        }
      }
    }
  }
}

# --------------------------------------------------------------------------
# 2. Desktop (Electron) dependencies
# --------------------------------------------------------------------------
Write-Head "2/6 Desktop console dependencies"

if ($SkipNpmInstall) {
  Write-Skip "requested with -SkipNpmInstall"
} elseif (Test-Path -LiteralPath $desktopModules) {
  Write-Ok "desktop\node_modules present"
} else {
  $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
  if (-not $npmCommand) { $npmCommand = Get-Command npm -ErrorAction SilentlyContinue | Select-Object -First 1 }
  if (-not $npmCommand) {
    Write-Need "desktop dependencies are missing and npm was not found"
    Add-ManualFix "Install Node.js 20+ from https://nodejs.org/ then run: npm install --prefix desktop"
  } else {
    Write-Need "desktop\node_modules is missing (the desktop console cannot start without it)"
    Add-AutoFix "npm install --prefix desktop"
    if (-not $Check) {
      Write-Act "npm install --prefix desktop (downloads the Electron binary, can take a few minutes)"
      Push-Location $projectRoot
      try {
        & $npmCommand.Source install --prefix desktop
      } finally {
        Pop-Location
      }
      if ($LASTEXITCODE -ne 0) {
        Write-Need "npm install failed. Behind a proxy / offline? Set ELECTRON_MIRROR or copy desktop\node_modules from a machine that has it."
      } else {
        Write-Ok "desktop dependencies installed"
      }
    }
  }
}

# --------------------------------------------------------------------------
# 3. .env
# --------------------------------------------------------------------------
Write-Head "3/6 Local configuration (.env)"

if (Test-Path -LiteralPath $envPath) {
  Write-Ok ".env present (left untouched -- it holds your local values)"
} else {
  Write-Need ".env does not exist (the API falls back to built-in defaults, but AI features stay disabled)"
  Add-AutoFix "Copy-Item .env.example .env"
  if (-not $Check) {
    if (-not (Test-Path -LiteralPath $envExamplePath)) {
      Write-Need ".env.example is missing from the repository"
    } else {
      Copy-Item -LiteralPath $envExamplePath -Destination $envPath
      Write-Ok "created .env from .env.example"
    }
  }
}

# --------------------------------------------------------------------------
# 4. PostgreSQL client + server + extensions
# --------------------------------------------------------------------------
Write-Head "4/6 PostgreSQL $MinimumPostgresMajor+ with pgvector"

$psql = $null
# One list, used by the availability probe, the install probe and the message,
# so "we checked these" and "these are the ones we name" cannot drift apart.
$RequiredExtensions = @("pgcrypto", "pg_trgm", "ltree", "vector")
if (-not $SkipDatabase) {
  try {
    $psql = Resolve-PgTool -Name psql.exe
    Write-Ok "psql: $psql"
  } catch {
    Write-Need "PostgreSQL client tools (psql) were not found"
    Add-ManualFix "Install PostgreSQL $MinimumPostgresMajor (https://www.postgresql.org/download/windows/) and add its bin directory to PATH."
  }
}

$serverMajor = 0
if ($psql) {
  $previousPassword = $env:PGPASSWORD
  try {
    $env:PGPASSWORD = $SuperPassword
    # Captured through Invoke-NativeCommand: a refused connection is an expected
    # outcome here, and a raw `2>&1` would turn psql's stderr into a terminating
    # error under this script's $ErrorActionPreference = 'Stop'.
    $versionProbe = Invoke-NativeCommand -File $psql -Arguments @(
      "-X", "-tA", "-h", $DbHost, "-p", "$DbPort", "-U", $SuperUser, "-d", "postgres", "-c", "show server_version;"
    )
    $versionText = ($versionProbe.Output -join "`n").Trim()
    if ($versionProbe.ExitCode -ne 0) {
      Write-Need "could not connect to PostgreSQL at ${DbHost}:$DbPort as '$SuperUser'"
      Add-ManualFix "Check that the PostgreSQL service is running, then re-run with -SuperUser <name> -SuperPassword <password>. psql said: $(($versionProbe.Output | Select-Object -First 1))"
    } elseif ($versionText -match '^(\d+)') {
      $serverMajor = [int]$Matches[1]
      if ($serverMajor -lt $MinimumPostgresMajor) {
        Write-Need "PostgreSQL server is $versionText but this project needs $MinimumPostgresMajor or newer"
        Add-ManualFix "Upgrade PostgreSQL to $MinimumPostgresMajor+ (migrations rely on features absent in older majors)."
      } else {
        Write-Ok "server version $versionText"
      }
    }

    if ($serverMajor -ge $MinimumPostgresMajor) {
      # `Where-Object { $_ }` guards against an empty result being read as a
      # single blank name.
      $missingExtensions = @(Test-PgExtensionAvailable -Psql $psql -User $SuperUser -Password $SuperPassword -DbHost $DbHost -Port $DbPort -Names $RequiredExtensions | Where-Object { $_ })
      if ($missingExtensions.Count -eq 0) {
        Write-Ok "extensions available: $($RequiredExtensions -join ', ')"
      } else {
        Write-Need "extension(s) not available to install: $($missingExtensions -join ', ')"
        if ("vector" -in $missingExtensions) {
          Add-ManualFix @"
'vector' (pgvector) is missing.  The official EDB Windows installer does NOT
           include it, which is the single most common blocker on a fresh machine.
             * easiest: use the pgvector Docker image instead --
                 docker compose up -d postgres
                 then set DATABASE_URL to port 54329 (see docker-compose.yml);
             * or add pgvector to your native install by copying
               vector.dll / vector.control / vector--*.sql into the
               PostgreSQL lib\ and share\extension\ directories.
"@
        }
        foreach ($extension in @($missingExtensions | Where-Object { $_ -ne "vector" })) {
          Add-ManualFix "Extension '$extension' is not available on this server -- reinstall PostgreSQL with the contrib package included."
        }
      }
    }
  } finally {
    $env:PGPASSWORD = $previousPassword
  }
}

# --------------------------------------------------------------------------
# 5. Database, roles, migrations
# --------------------------------------------------------------------------
Write-Head "5/6 Database roles, databases and migrations"

function Invoke-AdminSql {
  param(
    [Parameter(Mandatory = $true)][string]$Sql,
    [string]$Database = "postgres"
  )
  $previousPassword = $env:PGPASSWORD
  try {
    $env:PGPASSWORD = $SuperPassword
    & $psql -X -q -v ON_ERROR_STOP=1 -h $DbHost -p $DbPort -U $SuperUser -d $Database -c $Sql
    if ($LASTEXITCODE -ne 0) { throw "psql failed: $Sql" }
  } finally {
    $env:PGPASSWORD = $previousPassword
  }
}

function Test-DatabaseExists {
  param([Parameter(Mandatory = $true)][string]$Name)
  $previousPassword = $env:PGPASSWORD
  try {
    $env:PGPASSWORD = $SuperPassword
    $probe = Invoke-NativeCommand -File $psql -Arguments @(
      "-X", "-tA", "-h", $DbHost, "-p", "$DbPort", "-U", $SuperUser, "-d", "postgres",
      "-c", "SELECT 1 FROM pg_database WHERE datname='$Name';"
    )
    # A refused connection must not be read as "database absent": that would send
    # the script on to CREATE DATABASE against a server it cannot reach.
    if ($probe.ExitCode -ne 0) {
      throw "could not query pg_database: $(($probe.Output | Select-Object -First 1))"
    }
    return (($probe.Output -join "").Trim() -eq "1")
  } finally {
    $env:PGPASSWORD = $previousPassword
  }
}

function Get-MissingPgRoles {
  <#
    Which of `$Names` are not cluster roles yet.  Read-only, so -Check may call
    it: it reads pg_roles and creates nothing.
  #>
  param([Parameter(Mandatory = $true)][string[]]$Names)
  $previousPassword = $env:PGPASSWORD
  try {
    $env:PGPASSWORD = $SuperPassword
    $quoted = ($Names | ForEach-Object { "'$_'" }) -join ","
    $probe = Invoke-NativeCommand -File $psql -Arguments @(
      "-X", "-tA", "-h", $DbHost, "-p", "$DbPort", "-U", $SuperUser, "-d", "postgres",
      "-c", "SELECT rolname FROM pg_roles WHERE rolname IN ($quoted);"
    )
    if ($probe.ExitCode -ne 0) {
      throw "could not query pg_roles: $(($probe.Output | Select-Object -First 1))"
    }
    $present = @($probe.Output | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    return @($Names | Where-Object { $_ -notin $present })
  } finally {
    $env:PGPASSWORD = $previousPassword
  }
}

$databaseReady = $false
if ($SkipDatabase) {
  Write-Skip "requested with -SkipDatabase"
} elseif (-not $psql -or $serverMajor -lt $MinimumPostgresMajor) {
  Write-Skip "PostgreSQL is not usable yet -- fix step 4 first"
} else {
  try {
    if (Test-DatabaseExists -Name $MainDatabase) {
      Write-Ok "database $MainDatabase exists"
    } elseif ($Check) {
      Write-Need "database $MainDatabase does not exist"
      Add-AutoFix "CREATE DATABASE $MainDatabase OWNER $MigratorRole;"
    } else {
      Invoke-AdminSql "CREATE DATABASE $MainDatabase OWNER $MigratorRole;"
      Write-Ok "created database $MainDatabase"
    }

    # Roles first: init-native-postgres.sql grants to them and does not create
    # the database, so on a fresh machine this order is the only one that works.
    if (-not $Check) {
      Invoke-AdminSql @"
DO `$`$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$AppRole') THEN
    CREATE ROLE $AppRole NOINHERIT LOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$MigratorRole') THEN
    CREATE ROLE $MigratorRole NOINHERIT LOGIN;
  END IF;
END
`$`$;
ALTER ROLE $AppRole PASSWORD '$DevPassword';
ALTER ROLE $MigratorRole PASSWORD '$DevPassword';
"@
      Write-Ok "roles $AppRole / $MigratorRole present"

      # Extensions + grants live in the checked-in script so there is one source
      # of truth shared with CI.
      $initSql = Join-Path $projectRoot "scripts\init-native-postgres.sql"
      $previousPassword = $env:PGPASSWORD
      try {
        $env:PGPASSWORD = $SuperPassword
        & $psql -X -q -v ON_ERROR_STOP=1 -h $DbHost -p $DbPort -U $SuperUser -d $MainDatabase -f $initSql
        if ($LASTEXITCODE -ne 0) { throw "init-native-postgres.sql failed" }
      } finally {
        $env:PGPASSWORD = $previousPassword
      }
      Write-Ok "extensions and grants applied to $MainDatabase"
    }

    if ($Check) {
      # -Check creates nothing, so it has to *look*.  "The database exists" is
      # not the same as "the database is usable": a machine missing the roles or
      # the extensions still reported RESULT: ready, and the gap only surfaced
      # later inside `alembic upgrade head` -- the same false pass
      # verify-native-postgres.ps1 was fixed for.  Both probes are read-only.
      $missingRoles = @(Get-MissingPgRoles -Names @($AppRole, $MigratorRole) | Where-Object { $_ })
      if ($missingRoles.Count -eq 0) {
        Write-Ok "roles $AppRole / $MigratorRole present"
      } else {
        Write-Need "database role(s) missing: $($missingRoles -join ', ')"
        Add-AutoFix "re-run without -Check to create roles $AppRole / $MigratorRole"
      }

      $missingInstalled = @(Test-PgExtensionInstalled -Psql $psql -User $SuperUser -Password $SuperPassword -DbHost $DbHost -Port $DbPort -Database $MainDatabase -Names $RequiredExtensions | Where-Object { $_ })
      if ($missingInstalled.Count -eq 0) {
        Write-Ok "extensions installed in ${MainDatabase}: $($RequiredExtensions -join ', ')"
      } else {
        Write-Need "extension(s) not installed in ${MainDatabase}: $($missingInstalled -join ', ')"
        Add-AutoFix "re-run without -Check to apply scripts\init-native-postgres.sql to $MainDatabase"
      }

      Add-AutoFix "alembic upgrade head   # or: run the root launcher .bat with --migrate (see README)"
    } elseif ($SkipMigrations) {
      Write-Skip "migrations left to you (-SkipMigrations)"
      $databaseReady = $true
    } elseif (-not (Test-Path -LiteralPath $venvPython)) {
      Write-Need "cannot apply migrations: the project virtualenv is missing (fix step 1 first)"
    } else {
      $migratorUrl = "postgresql://${MigratorRole}:${DevPassword}@${DbHost}:${DbPort}/${MainDatabase}"
      Write-Act "alembic upgrade head on $MainDatabase"
      $previousUrl = $env:DATABASE_URL
      try {
        $env:DATABASE_URL = $migratorUrl
        & $venvPython -m alembic -c (Join-Path $projectRoot "alembic.ini") upgrade head
        if ($LASTEXITCODE -ne 0) { throw "alembic upgrade head failed" }
      } finally {
        if ($null -eq $previousUrl) { Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue } else { $env:DATABASE_URL = $previousUrl }
      }
      Write-Ok "schema is at the Alembic head"
      $databaseReady = $true
    }
  } catch {
    Write-Need "database setup failed: $($_.Exception.Message)"
    Add-ManualFix "Re-run with the correct -SuperUser/-SuperPassword, or initialise PostgreSQL manually (docs/README section 'Initialize the database')."
  }
}

# --------------------------------------------------------------------------
# 6. Test database (optional, needed only for pytest)
# --------------------------------------------------------------------------
Write-Head "6/6 Dedicated pytest database"

if ($SkipDatabase -or $SkipTestDatabase) {
  Write-Skip "not requested"
} elseif (-not $psql -or $serverMajor -lt $MinimumPostgresMajor) {
  Write-Skip "PostgreSQL is not usable yet -- fix step 4 first"
} elseif (-not $databaseReady -and -not (Test-DatabaseExists -Name $MainDatabase)) {
  Write-Skip "main database is not ready yet -- finish step 5 first"
} elseif ($Check) {
  if (Test-DatabaseExists -Name $TestDatabase) {
    Write-Ok "database $TestDatabase exists"
  } else {
    Write-Need "database $TestDatabase does not exist (only pytest needs it)"
    Add-AutoFix "powershell -ExecutionPolicy Bypass -File scripts\init-test-postgres.ps1"
  }
} else {
  Write-Act "initialising $TestDatabase via scripts\init-test-postgres.ps1"
  try {
    & (Join-Path $PSScriptRoot "init-test-postgres.ps1") `
      -DatabaseName $TestDatabase -HostName $DbHost -Port $DbPort `
      -AdminUser $SuperUser -AdminPassword $SuperPassword `
      -MigratorUser $MigratorRole -MigratorPassword $DevPassword
    if ($LASTEXITCODE -ne 0) { throw "init-test-postgres.ps1 exited $LASTEXITCODE" }
    Write-Ok "$TestDatabase is ready"
  } catch {
    Write-Need "test database setup failed: $($_.Exception.Message)"
  }
}

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------
Write-Output ""
Write-Output "=============================================================="
if ($script:Problems.Count -eq 0) {
  Write-Output " RESULT: ready"
  Write-Output "=============================================================="
  Write-Output ""
  Write-Output " Start the desktop control plane:"
  Write-Output "     .\启动审计智能中枢.bat"
  Write-Output ""
  Write-Output " Quality gates:"
  Write-Output "     .venv\Scripts\python.exe -m pytest -q"
  Write-Output "     .venv\Scripts\python.exe -m ruff check ."
  Write-Output "     .venv\Scripts\python.exe -m mypy packages"
  Write-Output "     npm run typecheck ; npm test"
} else {
  Write-Output " RESULT: $($script:Problems.Count) item(s) still missing"
  Write-Output "=============================================================="
  Write-Output ""
  Write-Output " Missing:"
  foreach ($problem in $script:Problems) { Write-Output "   * $problem" }
  if ($script:AutoFix.Count -gt 0) {
    Write-Output ""
    Write-Output " Commands this script would run (re-run without -Check to apply):"
    foreach ($command in $script:AutoFix) { Write-Output "   $command" }
  }
  if ($script:ManualFix.Count -gt 0) {
    Write-Output ""
    Write-Output " Manual steps:"
    foreach ($step in $script:ManualFix) { Write-Output "   $step" }
  }
}
Write-Output ""
# The verdict has to be machine-readable.  Without this the exit code was
# whatever native command happened to run last, so "3 items missing" could exit
# 0 on one machine and 2 on another -- the same false-pass shape this script
# exists to remove, one layer down.
if ($script:Problems.Count -gt 0) { exit 1 }
