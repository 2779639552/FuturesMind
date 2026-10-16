@echo off
rem AgentSense web_app one-click launcher (own console window).
rem 2026-09-17: anchor to this script's own folder. It used to be hardcoded to
rem another machine (C:\Users\19168\Desktop\project4\AgentSense), which does not
rem exist here, so the launcher silently failed.
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] venv python not found under "%~dp0venv\Scripts\"
    echo         Run start_web.bat instead to create it automatically.
    pause
    exit /b 1
)

venv\Scripts\python.exe web_app.py
pause
