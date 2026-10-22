@echo off
title Game Save Guardian Launcher
cd /d "%~dp0"

echo Launching Game Save Guardian...

if exist "C:\Users\dhirar\AppData\Local\Programs\Python\Python314\python.exe" (
    "C:\Users\dhirar\AppData\Local\Programs\Python\Python314\python.exe" main.py
    goto end
)

py -3 main.py
if %errorlevel% neq 0 (
    python main.py
)

:end
if %errorlevel% neq 0 (
    echo.
    echo An error occurred while launching.
    pause
)
