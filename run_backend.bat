@echo off
set "SCRIPT_DIR=%~dp0"
set "LAST=%SCRIPT_DIR:~-1%"
if "%LAST%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"
if not exist "backend" cd /d "%SCRIPT_DIR%\.."
set "ROOT=%CD%"
REM default HOST=0.0.0.0 for LAN access when not set by start.bat
if not defined HOST set "HOST=0.0.0.0"
if not defined PORT set "PORT=8000"
set "PY=python"
if exist "%ROOT%\python\python.exe" set "PY=%ROOT%\python\python.exe"
if exist "%SCRIPT_DIR%\python\python.exe" set "PY=%SCRIPT_DIR%\python\python.exe"
cd /d "%ROOT%"
set "PYTHONPATH=%ROOT%"
"%PY%" backend\run.py 1>>backend.log 2>&1


