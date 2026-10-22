@echo off
REM ============================================================================
REM Game Save Guardian - Environment Setup & Run Script
REM ============================================================================

setlocal enabledelayedexpansion

echo ============================================================================
echo   Setting up Game Save Guardian Environment
echo ============================================================================

py -3 --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON_CMD=py -3
) else (
    python --version >nul 2>&1
    if %errorlevel% equ 0 (
        set PYTHON_CMD=python
    ) else (
        echo [ERROR] Python 3 was not found. Please install Python 3.12 or newer.
        pause
        exit /b 1
    )
)

echo [1/3] Creating virtual environment (.venv)...
if not exist .venv (
    %PYTHON_CMD% -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo [2/3] Installing dependencies...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install requirements.
    pause
    exit /b 1
)

echo [3/3] Running automated test verification...
python -m unittest discover tests
if %errorlevel% neq 0 (
    echo [WARNING] Some tests did not pass. Proceeding anyway...
)

echo.
echo ============================================================================
echo Setup completed successfully!
echo To launch Game Save Guardian, run:
echo     .venv\Scripts\activate.bat
echo     python main.py
echo ============================================================================
echo.
set /p LAUNCH="Do you want to launch Game Save Guardian now? (Y/N): "
if /i "%LAUNCH%"=="Y" (
    python main.py
)
