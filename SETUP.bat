@echo off
title Nyra — Setup
color 0B
chcp 65001 >nul

echo.
echo  ╔══════════════════════════════════════╗
echo  ║         NYRA  —  First-time Setup    ║
echo  ╚══════════════════════════════════════╝
echo.

:: ── Check Python ────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python is not installed or not in PATH.
    echo  Download Python 3.11 or newer from: https://www.python.org/downloads/
    echo  Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  Python found: %PYVER%

:: ── Create virtual environment ───────────────────────────────────────────────
if not exist ".venv" (
    echo.
    echo  [1/4] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo  [ERROR] Failed to create virtual environment.
        pause & exit /b 1
    )
)

:: ── Install packages ─────────────────────────────────────────────────────────
echo.
echo  [2/4] Installing packages (this may take 5-15 min on first run)...
echo        torch + faster-whisper are large downloads, please wait.
echo.

call .venv\Scripts\pip.exe install --upgrade pip -q
call .venv\Scripts\pip.exe install torch torchaudio --index-url https://download.pytorch.org/whl/cu121 -q
if errorlevel 1 (
    echo  [WARN] CUDA torch failed, installing CPU version instead...
    call .venv\Scripts\pip.exe install torch torchaudio --index-url https://download.pytorch.org/whl/cpu -q
)

call .venv\Scripts\pip.exe install -r requirements.txt -q
if errorlevel 1 (
    echo  [ERROR] Package installation failed. Check your internet connection.
    pause & exit /b 1
)

:: ── Create data folder ───────────────────────────────────────────────────────
echo.
echo  [3/4] Creating data directory...
if not exist "data" mkdir data
if not exist "data\secrets.env" (
    echo # Nyra secrets - fill in your API keys below > data\secrets.env
    echo GROQ_API_KEY= >> data\secrets.env
)

:: ── Update Nyra.bat to use new venv ──────────────────────────────────────────
echo @echo off > Nyra.bat
echo cd /d "%%~dp0" >> Nyra.bat
echo set PYTHONUTF8=1 >> Nyra.bat
echo ".venv\Scripts\pythonw.exe" main.py >> Nyra.bat

echo.
echo  [4/4] Setup complete!
echo.
echo  ═══════════════════════════════════════════
echo.
echo  BEFORE YOU START — you need one of these:
echo.
echo  Option A (Recommended - Free):
echo    1. Get a free Groq API key at: https://console.groq.com
echo    2. Open data\secrets.env and paste:
echo       GROQ_API_KEY=your_key_here
echo.
echo  Option B (Offline - Local AI):
echo    1. Download Ollama: https://ollama.com/download
echo    2. Run: ollama pull qwen2.5:7b
echo.
echo  ═══════════════════════════════════════════
echo.
echo  Then double-click Nyra.bat to start.
echo.
pause
