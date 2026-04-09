@echo off
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"
if not exist "mcp" cd /d "%SCRIPT_DIR%\.."
set "ROOT=%CD%"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "PY=python"
if exist "%ROOT%\python\python.exe" set "PY=%ROOT%\python\python.exe"
if exist "%SCRIPT_DIR%\python\python.exe" set "PY=%SCRIPT_DIR%\python\python.exe"
cd /d "%ROOT%"
set "PYTHONPATH=%ROOT%"
"%PY%" -c "import sys; sys.path.insert(0, r'%ROOT%'); sys.argv = ['mcp', '--port', '8001']; import runpy; runpy.run_module('mcp', run_name='__main__', alter_sys=True)" 1>>"%ROOT%\mcp.log" 2>&1


