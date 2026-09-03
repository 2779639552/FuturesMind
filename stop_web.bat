@echo off
title AgentSense - Stop Web Server
cd /d "%~dp0"

echo ============================================
echo   Stopping AgentSense server on :5000 ...
echo ============================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "& { $c = Get-NetTCPConnection -State Listen -LocalPort 5000 -ErrorAction SilentlyContinue; if (-not $c) { Write-Host '  Nothing on :5000 - already stopped.' } else { $pids = @($c.OwningProcess | Select-Object -Unique); foreach ($id in $pids) { $p = Get-Process -Id $id -ErrorAction SilentlyContinue; if ($p) { Write-Host ('  Killing PID {0} ({1})' -f $id, $p.ProcessName); Stop-Process -Id $id -Force } }; Write-Host '  Done. Port :5000 released.' } }"

echo.
echo If the old server window is still open, it closes on its own now.
pause
