@echo off
setlocal EnableDelayedExpansion
REM Resolves ROOT: current dir or parent if backend is one level up (nested layout).
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"
if not exist "backend" (
  cd /d "%ROOT%\.."
  set "ROOT=%CD%"
) else (
  set "ROOT=%CD%"
)
cd /d "%ROOT%"
chcp 65001 >nul 2>&1
title Lobster

if exist "browser_chromium" (
    set "PLAYWRIGHT_BROWSERS_PATH=%ROOT%\browser_chromium"
)

set "PYTHON="
if exist "python\python.exe" (
    set "PYTHON=%ROOT%\python\python.exe"
    goto :check_uvicorn
)
where python >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=python"
    goto :check_uvicorn
)
echo [ERR] Python not found
pause
exit /b 1

:check_uvicorn
"%PYTHON%" -m uvicorn --version >nul 2>&1
if errorlevel 1 goto :try_alt_python
goto :python_ready

:try_alt_python
where python >nul 2>&1
if errorlevel 1 goto :no_uvicorn
python -m uvicorn --version >nul 2>&1
if errorlevel 1 goto :no_uvicorn
set "PYTHON=python"
goto :python_ready

:no_uvicorn
echo [ERR] uvicorn not installed. Run install.bat first.
pause
exit /b 1

:python_ready
echo [OK] Python: %PYTHON%

set "NODE_CMD="
if exist "nodejs\node.exe" (
    set "NODE_CMD=%ROOT%\nodejs\node.exe"
    set "PATH=%ROOT%\nodejs;%PATH%"
    goto :node_detected
)
where node >nul 2>&1
if not errorlevel 1 set "NODE_CMD=node"
:node_detected

set "PORT=8000"
set "MCP_PORT=8001"
set "HOST=0.0.0.0"
if exist ".env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do (
        if "%%a"=="PORT" set "PORT=%%b"
        if "%%a"=="MCP_PORT" set "MCP_PORT=%%b"
        if "%%a"=="HOST" set "HOST=%%b"
    )
)

REM LAN IP: prefer 192.168.x / 10.x / 172.x from ipconfig
set "LAN_IP=127.0.0.1"
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /R "IPv4"') do (
    for /f "tokens=* delims= " %%b in ("%%a") do set "TMP=%%b"
    echo !TMP! | findstr /R /C:"192.168." /C:"^10\." /C:"^172\." >nul && set "LAN_IP=!TMP!"
)

echo ================================================
echo          Lobster Starting...
echo ================================================
echo.
echo   Local:   http://localhost:%PORT%
echo   LAN:     http://%LAN_IP%:%PORT%
echo.
echo   Share the LAN address with other devices
echo ================================================
echo.

echo [Code] checking client code pack update...
"%PYTHON%" "%ROOT%\scripts\check_client_code_update.py"
echo.

if exist "openclaw\.env" (
    for /f "usebackq eol=# tokens=1,2 delims==" %%a in ("openclaw\.env") do (
        if not "%%a"=="" if not "%%b"=="" set "%%a=%%b"
    )
)

echo.

REM Clear local logs before MCP/Backend start (fresh run for debugging).
echo [Logs] clearing previous log files...
if exist "%ROOT%\backend.log" del /f /q "%ROOT%\backend.log" >nul 2>&1
if exist "%ROOT%\mcp.log" del /f /q "%ROOT%\mcp.log" >nul 2>&1
if exist "%ROOT%\logs\app.log" del /f /q "%ROOT%\logs\app.log" >nul 2>&1
if exist "%ROOT%\_pack_zip.log" del /f /q "%ROOT%\_pack_zip.log" >nul 2>&1
if exist "%ROOT%\_pack_run.log" del /f /q "%ROOT%\_pack_run.log" >nul 2>&1
echo   [OK] Cleared: backend.log, mcp.log, logs\app.log, _pack_*.log
echo.

echo [MCP] Starting MCP Server on port %MCP_PORT%...
start "Lobster-MCP" /B cmd /c "%ROOT%\run_mcp.bat"
timeout /t 2 /nobreak >nul
echo   [OK] MCP Server started
echo.

echo [Backend] Starting on port %PORT% - log file backend.log
start "Lobster-Backend" /B cmd /c "%ROOT%\run_backend.bat"

echo [Backend] Waiting for service up to 60 seconds...
set "_WAIT_COUNT=0"
:wait_backend
timeout /t 1 /nobreak >nul
set /a _WAIT_COUNT+=1
netstat -ano 2>nul | findstr ":%PORT% " | findstr "LISTENING" >nul
if not errorlevel 1 goto backend_ready
if !_WAIT_COUNT! GEQ 60 goto backend_failed
echo   [..] waiting !_WAIT_COUNT! of 60
goto wait_backend

:backend_failed
echo.
echo   [ERR] Backend did not start within 60 seconds.
echo   Check backend.log in this folder for errors.
echo   ----------------------------------------
if exist backend.log type backend.log
if not exist backend.log echo   No backend.log yet.
echo   ----------------------------------------
pause
exit /b 1

:backend_ready
timeout /t 2 /nobreak >nul
echo   [OK] Backend is listening on port %PORT%.
netstat -ano 2>nul | findstr ":%MCP_PORT% " | findstr "LISTENING" >nul
if not errorlevel 1 (
  echo   [OK] MCP is ready on port %MCP_PORT%.
) else (
  echo   [WARN] MCP not listening on port %MCP_PORT% - check mcp.log
)
echo.

echo [Browser] Opening http://localhost:%PORT%
start "" "http://localhost:%PORT%"

echo ================================================
echo   All services running. Press Ctrl+C to stop.
echo ================================================
echo.

:keep_alive
timeout /t 3600 /nobreak >nul
goto :keep_alive

:cleanup
echo.
echo Stopping services...
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
)
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":%MCP_PORT% " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
)
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":18789 " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
)
echo Lobster stopped.


