@echo off
title AgentSense - Stop WeChat Bot
cd /d "%~dp0"

echo ============================================
echo   Stopping WeChat bot on :5001 ...
echo ============================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "& { $c = Get-NetTCPConnection -State Listen -LocalPort 5001 -ErrorAction SilentlyContinue; if (-not $c) { Write-Host '  Nothing on :5001 - already stopped.' } else { $pids = @($c.OwningProcess | Select-Object -Unique); foreach ($id in $pids) { $p = Get-Process -Id $id -ErrorAction SilentlyContinue; if ($p) { Write-Host ('  Killing PID {0} ({1})' -f $id, $p.ProcessName); Stop-Process -Id $id -Force } }; Write-Host '  Done. Port :5001 released.' } }"

echo.
pause
