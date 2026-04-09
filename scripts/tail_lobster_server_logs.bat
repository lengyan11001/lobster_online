@echo off
setlocal EnableExtensions
REM Wrapper: runs lobster-server/scripts/tail_remote_logs.sh (needs Git Bash + lobster-server/.env.deploy)
REM Optional: set TAIL_LINES=500 before running this bat
set "GB=C:\Program Files\Git\bin\bash.exe"
if not exist "%GB%" set "GB=C:\Program Files (x86)\Git\bin\bash.exe"
if not exist "%GB%" (
  echo [ERR] Git Bash not found
  exit /b 1
)
pushd "%~dp0..\..\lobster-server"
if errorlevel 1 (
  echo [ERR] lobster-server sibling folder not found
  exit /b 1
)
if not exist "scripts\tail_remote_logs.sh" (
  echo [ERR] scripts\tail_remote_logs.sh missing
  popd
  exit /b 1
)
"%GB%" -lc "cd \"$(cygpath -u '%CD%')\" && export TAIL_LINES=${TAIL_LINES:-200} && bash scripts/tail_remote_logs.sh"
set "EC=%ERRORLEVEL%"
popd
exit /b %EC%
