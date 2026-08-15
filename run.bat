@echo off
cd /d "%~dp0"

set "PY=C:\Users\GOODLIZI\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if exist "%PY%" goto :run

where python >nul 2>nul
if not errorlevel 1 (
    set "PY=python"
    goto :run
)

where py >nul 2>nul
if not errorlevel 1 (
    set "PY=py"
    goto :run
)

echo [ERROR] Python not found. Install Python 3.10+ or edit the PY path in run.bat.
pause
exit /b 1

:run
"%PY%" app.py
pause
