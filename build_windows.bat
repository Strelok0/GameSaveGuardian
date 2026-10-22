@echo off
REM ============================================================================
REM Game Save Guardian - Windows PyInstaller Executable Build Script
REM ============================================================================

setlocal enabledelayedexpansion

echo [1/4] Verifying Python Environment...
py -3 --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON_CMD=py -3
) else (
    python --version >nul 2>&1
    if %errorlevel% equ 0 (
        set PYTHON_CMD=python
    ) else (
        echo [ERROR] Python 3 was not found in PATH or via Python Launcher (py.exe).
        echo Please ensure Python 3.12+ is installed and added to PATH.
        pause
        exit /b 1
    )
)

echo Using Python command: %PYTHON_CMD%

echo [2/4] Installing Required Build Dependencies...
%PYTHON_CMD% -m pip install --upgrade pip
%PYTHON_CMD% -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies from requirements.txt
    pause
    exit /b 1
)

echo [3/4] Running Automated Tests...
%PYTHON_CMD% -m unittest discover tests
if %errorlevel% neq 0 (
    echo [ERROR] Automated tests failed! Halting build.
    pause
    exit /b 1
)

echo [4/4] Building Standalone Windows Executable via PyInstaller...
if exist build rd /s /q build
if exist dist rd /s /q dist

%PYTHON_CMD% -m PyInstaller ^
    --noconfirm ^
    --onedir ^
    --windowed ^
    --name "GameSaveGuardian" ^
    --icon "assets\icon.ico" ^
    --add-data "assets;assets" ^
    --add-data "src;src" ^
    --exclude-module PySide6.QtWebEngineCore ^
    --exclude-module PySide6.QtWebEngineWidgets ^
    --exclude-module PySide6.QtQml ^
    --exclude-module PySide6.QtQuick ^
    --exclude-module PySide6.Qt3DCore ^
    --exclude-module PySide6.QtMultimedia ^
    --exclude-module PySide6.QtNetwork ^
    --exclude-module PySide6.QtOpenGL ^
    --exclude-module PySide6.QtSql ^
    --exclude-module tkinter ^
    --exclude-module unittest ^
    --exclude-module pydoc ^
    --clean ^
    main.py

if %errorlevel% neq 0 (
    echo [ERROR] PyInstaller build failed!
    pause
    exit /b 1
)

echo.
echo ============================================================================
echo [SUCCESS] Game Save Guardian has been built successfully!
echo Executable Location: dist\GameSaveGuardian\GameSaveGuardian.exe
echo ============================================================================
echo.
pause
