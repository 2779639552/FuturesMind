@echo off
title AgentSense - Stop Web Server
cd /d "%~dp0"

echo ============================================
echo   Stopping AgentSense server on :5000 ...
echo ============================================
echo.

rem 2026-09-17: delegate to stop_web.ps1. The old inline version only killed
rem whatever was LISTENING on :5000, so it missed the rag_worker.py child
rem process and web_app.py instances stuck at the startup guard without ever
rem binding the port. Those leftovers are a known cause of double instances.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_web.ps1"

echo.
echo If the old server window is still open, it closes on its own now.
pause
