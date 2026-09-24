@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
title 审计智能中枢 - 启动器

set "ROOT=%~dp0"
set "VENV=%ROOT%.venv\Scripts\python.exe"
set "PS1=%ROOT%scripts\start-desktop.ps1"

echo ============================================
echo   审计智能中枢  Audit Network
echo ============================================
echo.

REM --- 检查 PowerShell ---
where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 PowerShell.exe
  pause & exit /b 1
)

REM --- 检查启动脚本 ---
if not exist "%PS1%" (
  echo [错误] 启动脚本不存在: %PS1%
  pause & exit /b 1
)

REM --- 检查虚拟环境 ---
if not exist "%VENV%" (
  echo [提示] Python 虚拟环境不存在，正在引导安装...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\bootstrap.ps1"
  if errorlevel 1 (
    echo [错误] 引导安装失败
    pause & exit /b 1
  )
)

REM --- 检查桌面依赖 ---
if not exist "%ROOT%desktop\node_modules" (
  echo [提示] 正在安装桌面端依赖...
  call npm install --prefix "%ROOT%desktop"
  if errorlevel 1 (
    echo [错误] npm install 失败
    pause & exit /b 1
  )
)

REM --- 命令行参数 ---
if /I "%~1"=="--migrate" (
  echo [启动] 执行数据库迁移...
  powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -RunMigrations
) else if /I "%~1"=="--help" (
  echo 用法:
  echo   %~nx0              启动 API + Worker + 桌面端
  echo   %~nx0 --migrate    先执行数据库迁移再启动
  pause & exit /b 0
) else (
  echo [启动] 启动控制平面...
  echo [提示] 如需执行迁移，请运行: %~nx0 --migrate
  powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
)

if errorlevel 1 (
  echo.
  echo [错误] 启动失败，退出码 %ERRORLEVEL%
  pause & exit /b 1
)

endlocal
