@echo off
setlocal
cd /d "%~dp0\.."
set "PY=python"
if exist "python\python.exe" set "PY=%CD%\python\python.exe"

"%PY%" desktop\build_desktop_exe.py
exit /b %ERRORLEVEL%
