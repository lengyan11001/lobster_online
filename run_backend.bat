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
set "PY="
if exist "%ROOT%\python\python.exe" set "PY=%ROOT%\python\python.exe"
if exist "%SCRIPT_DIR%\python\python.exe" set "PY=%SCRIPT_DIR%\python\python.exe"
if defined PY call :probe_python
if defined PY goto :python_ready
python --version >nul 2>&1
if not errorlevel 1 (
    set "PY=python"
    call :probe_python
    goto :python_ready
)
call :detect_py_launcher 3.12
if defined PY call :probe_python
if defined PY goto :python_ready
call :detect_py_launcher 3.11
if defined PY call :probe_python
if defined PY goto :python_ready
call :detect_py_launcher 3.10
if defined PY call :probe_python
if defined PY goto :python_ready
call :detect_py_launcher 3
if defined PY call :probe_python
if defined PY goto :python_ready
echo [ERR] Python not found >> backend.log
exit /b 1

:detect_py_launcher
set "PY_PROBE=%TEMP%\lobster_py_path_%RANDOM%.txt"
py -%~1 -c "import sys; print(sys.executable)" > "%PY_PROBE%" 2>nul
if errorlevel 1 (
    if exist "%PY_PROBE%" del /f /q "%PY_PROBE%" >nul 2>&1
    exit /b 1
)
set /p PY=<"%PY_PROBE%"
if exist "%PY_PROBE%" del /f /q "%PY_PROBE%" >nul 2>&1
if not exist "%PY%" (
    set "PY="
    exit /b 1
)
exit /b 0

:probe_python
"%PY%" -c "import uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [WARN] Selected Python missing uvicorn: %PY%>> backend.log
    set "PY="
    exit /b 1
)
exit /b 0

:python_ready
cd /d "%ROOT%"
set "PYTHONPATH=%ROOT%"
"%PY%" backend\run.py 1>>backend.log 2>&1


