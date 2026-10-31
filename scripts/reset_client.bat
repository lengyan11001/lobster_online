@echo off
chcp 65001 >nul
setlocal EnableExtensions
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
if not exist "%ROOT%\backend" (
    echo [ERR] 这个 bat 要放在 lobster_online 安装目录根下：%ROOT%
    pause
    exit /b 1
)
echo [1/4] 结束后台进程...
powershell -NoProfile -Command "Get-CimInstance Win32_Process ^| Where-Object { $_.CommandLine -and ($_.CommandLine -match 'launcher\.py^|backend\\\\run\.py^|aihypit^|rotating_log_runner') } ^| ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }; Get-Process msedgewebview2 -ErrorAction SilentlyContinue ^| Stop-Process -Force -ErrorAction SilentlyContinue"
taskkill /F /IM 必火智能AI.exe >nul 2>&1
taskkill /F /IM lobster.exe >nul 2>&1
timeout /t 3 /nobreak >nul

echo [2/4] 清 WebView 缓存（保留登录态）...
rd /s /q "%ROOT%\browser_data\desktop_webview\EBWebView\Default\Cache" 2>nul
rd /s /q "%ROOT%\browser_data\desktop_webview\EBWebView\Default\Code Cache" 2>nul
rd /s /q "%ROOT%\browser_data\desktop_webview\EBWebView\Default\GPUCache" 2>nul

echo [3/4] 清 __pycache__...
for /d /r "%ROOT%\backend" %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d" 2>nul
for /d /r "%ROOT%\desktop" %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d" 2>nul

echo [4/4] 启动客户端...
start "" "%ROOT%\python\pythonw.exe" "%ROOT%\desktop\launcher.py"
timeout /t 8 /nobreak >nul
if exist "%ROOT%\static\client_version.json" (
    echo ---- 本机代码版本 ----
    type "%ROOT%\static\client_version.json"
)
echo 完成。若版本没变，等 1 分钟让它自动拉更新（2.0.223 起会自动重启）。
pause