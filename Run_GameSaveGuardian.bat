@echo off
title Game Save Guardian Launcher
cd /d "%~dp0"

echo Launching Game Save Guardian...

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py
    goto end
)

py -3 main.py >nul 2>&1
if %errorlevel% equ 0 (
    py -3 main.py
    goto end
)

python main.py
if %errorlevel% equ 0 goto end

:end
if %errorlevel% neq 0 (
    echo.
    echo An error occurred while launching Game Save Guardian.
    pause
)
