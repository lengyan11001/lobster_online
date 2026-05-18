@echo off
setlocal
cd /d "%~dp0\.."
set "PY=python"
if exist "python\python.exe" set "PY=%CD%\python\python.exe"

"%PY%" -c "import webview" >nul 2>&1
if errorlevel 1 (
  echo [desktop] Installing pywebview...
  "%PY%" -m pip install -r desktop\requirements-desktop.txt
  if errorlevel 1 (
    echo [desktop] Failed to install desktop requirements.
    pause
    exit /b 1
  )
)

"%PY%" desktop\launcher.py %*
