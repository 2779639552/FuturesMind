@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

REM ============================================================
REM  import_variety_db.bat - import a variety database xlsx
REM  into the AgentSense "Variety Database" (品种数据库) tab.
REM
REM  Usage:
REM    1) drag the xlsx onto this file, OR
REM    2) import_variety_db.bat "C:\path\Xxx-永安.xlsx" --name 甲醇 --slug methanol
REM
REM  Variety name and folder id are inferred from the file name when
REM  not given (e.g. "沥青数据库-永安.xlsx" -> bitumen / 沥青).
REM  Data goes to dataariety\<id>\ ; the web page picks it up on refresh.
REM ============================================================

if "%~1"=="" (
  echo [usage] drag an xlsx onto this file, or pass its path as the 1st argument.
  echo [tip]   name/id are inferred from the file name; override with
  echo         --name ^<Chinese name^> --slug ^<latin id^>
  pause
  exit /b 1
)

set EXTRA=%2 %3 %4 %5

echo.
echo === import: %~1
echo.
"%~dp0venv\Scripts\python.exe" "%~dp0variety_convert.py" --src "%~1" %EXTRA%
set RC=%ERRORLEVEL%

echo.
echo === imported varieties
"%~dp0venv\Scripts\python.exe" "%~dp0variety_convert.py" --list %EXTRA%

echo.
if not "%RC%"=="0" (
  echo [FAILED] exit code = %RC%
) else (
  echo [OK] refresh the web page; the new variety shows up in the dropdown.
)
echo.
pause
exit /b %RC%
