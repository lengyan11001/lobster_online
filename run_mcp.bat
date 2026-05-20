@echo off
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"
if not exist "mcp" cd /d "%SCRIPT_DIR%\.."
set "ROOT=%CD%"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "PY="
if exist "%ROOT%\python\python.exe" set "PY=%ROOT%\python\python.exe"
if exist "%SCRIPT_DIR%\python\python.exe" set "PY=%SCRIPT_DIR%\python\python.exe"
if defined PY goto :python_ready
python --version >nul 2>&1
if not errorlevel 1 (
    set "PY=python"
    goto :python_ready
)
call :detect_py_launcher 3.12
if defined PY goto :python_ready
call :detect_py_launcher 3.11
if defined PY goto :python_ready
call :detect_py_launcher 3.10
if defined PY goto :python_ready
call :detect_py_launcher 3
if defined PY goto :python_ready
echo [ERR] Python not found >> "%ROOT%\mcp.log"
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

:python_ready
cd /d "%ROOT%"
set "PYTHONPATH=%ROOT%"
if not defined MCP_PORT set "MCP_PORT=8001"
"%PY%" -c "import os, sys; sys.path.insert(0, r'%ROOT%'); sys.argv = ['mcp', '--port', os.environ.get('MCP_PORT', '8001')]; import runpy; runpy.run_module('mcp', run_name='__main__', alter_sys=True)" 1>>"%ROOT%\mcp.log" 2>&1


