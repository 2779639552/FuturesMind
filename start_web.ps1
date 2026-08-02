$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  FuturesMind v2.9 — Web Dashboard" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

try {
    $pyVer = python --version 2>&1
    Write-Host "[OK] $pyVer"
} catch {
    Write-Host "[ERROR] Python 3.10+ not found" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Set-Location $scriptDir

if (-not (Test-Path "venv\Scripts\python.exe")) {
    Write-Host "[1/2] Creating venv..." -ForegroundColor Yellow
    python -m venv venv
    Write-Host "[2/2] Installing dependencies (2-5 min)..." -ForegroundColor Yellow
    venv\Scripts\pip install -e . -q
    Write-Host "[OK] Environment ready" -ForegroundColor Green
    Write-Host ""
}

Write-Host "Starting server at http://localhost:5000" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop"
Write-Host ""

& venv\Scripts\python web_app.py

Write-Host "Server stopped" -ForegroundColor Yellow
Read-Host "Press Enter to exit"
