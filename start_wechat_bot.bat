@echo off
title FuturesMind WeChat Bot

cd /d "%~dp0"

echo ============================================
echo   FuturesMind WeChat QA Bot (port 5001)
echo ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+
    pause
    exit /b 1
)

if not exist "venv\Scripts\python.exe" (
    echo Creating venv...
    python -m venv venv
    echo Installing dependencies (may take 2-5 min)...
    venv\Scripts\pip install -e . -q
    echo Done.
    echo.
)

echo Starting WeChat bot at http://localhost:5001
echo Press Ctrl+C to stop
echo.

venv\Scripts\python wechat_bot.py
pause
