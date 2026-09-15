@echo off
setlocal EnableExtensions

rem Audit Network desktop launcher for standard Windows PowerShell.
rem Double-click to start the API, Worker and Electron desktop console.
rem Migrations are intentionally opt-in: run this file with --migrate only
rem when you have reviewed the database migration changes.

set "PROJECT_ROOT=%~dp0"
set "START_SCRIPT=%PROJECT_ROOT%scripts\start-desktop.ps1"

if /I "%~1"=="--help" goto :help
if /I "%~1"=="-h" goto :help

if not exist "%START_SCRIPT%" (
  echo [audit-network] Start script not found:
  echo %START_SCRIPT%
  goto :failed
)

where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo [audit-network] Windows PowerShell was not found.
  echo Please install or enable Windows PowerShell, then run this file again.
  goto :failed
)

pushd "%PROJECT_ROOT%" >nul
if /I "%~1"=="--migrate" (
  echo [audit-network] Starting with explicit database migration...
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%START_SCRIPT%" -RunMigrations
) else (
  echo [audit-network] Starting desktop control plane...
  echo [audit-network] Database migrations are not applied automatically.
  echo [audit-network] To apply reviewed migrations, run: "%~nx0" --migrate
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%START_SCRIPT%"
)
set "EXIT_CODE=%ERRORLEVEL%"
popd >nul

if not "%EXIT_CODE%"=="0" (
  echo.
  echo [audit-network] Startup stopped with exit code %EXIT_CODE%.
  goto :failed
)

endlocal
exit /b 0

:help
echo Audit Network desktop launcher
echo.
echo Usage:
echo   "%~nx0"             Start API, Worker and Electron desktop console.
echo   "%~nx0" --migrate   Explicitly apply pending project database migrations first.
echo.
echo The default command never applies migrations automatically.
endlocal
exit /b 0

:failed
echo.
echo Press any key to close this window.
pause >nul
endlocal
exit /b 1
