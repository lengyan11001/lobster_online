@echo off
setlocal
chcp 65001 >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0repair_client_update.ps1"
if errorlevel 1 (
    echo [ERR] Update repair did not complete.
    pause
    exit /b 1
)
exit /b 0
