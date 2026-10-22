@echo off
title Game Save Guardian - Standalone Executable Launcher
cd /d "%~dp0"

if exist "dist\GameSaveGuardian\GameSaveGuardian.exe" (
    start "" "%~dp0dist\GameSaveGuardian\GameSaveGuardian.exe"
    exit /b 0
)

if exist "dist_package\GameSaveGuardian\GameSaveGuardian.exe" (
    start "" "%~dp0dist_package\GameSaveGuardian\GameSaveGuardian.exe"
    exit /b 0
)

echo [ERROR] GameSaveGuardian.exe not found.
echo Please run build_windows.bat first.
pause
