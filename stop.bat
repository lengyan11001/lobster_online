@echo off
cd /d "%~dp0"
chcp 65001 >nul 2>&1
echo Stopping Lobster services...

REM Kill backend by port 8000
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
    echo   [OK] Backend process %%p stopped
)

REM Kill MCP by port 8001
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":8001 " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
    echo   [OK] MCP process %%p stopped
)

REM Kill OpenClaw Gateway by port 18789
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":18789 " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
    echo   [OK] OpenClaw process %%p stopped
)

echo(
echo Lobster stopped.
pause

