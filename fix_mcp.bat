@echo off
chcp 65001 >nul 2>&1
echo ========================================
echo MCP 服务快速修复脚本
echo ========================================
echo.

REM 停止现有服务
echo [1/4] 停止现有服务...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq Lobster-MCP*" >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":8001 " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
    echo   已停止进程 %%p
)
timeout /t 2 /nobreak >nul

REM 检查端口
echo [2/4] 检查端口占用...
netstat -ano | findstr ":8001 " | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo   警告: 8001 端口仍被占用
    netstat -ano | findstr ":8001 " | findstr "LISTENING"
) else (
    echo   ✓ 8001 端口可用
)

REM 启动 MCP 服务
echo [3/4] 启动 MCP 服务...
cd /d "%~dp0"
if exist "python\python.exe" (
    set "PY=python\python.exe"
) else (
    set "PY=python"
)
start "Lobster-MCP" /B cmd /c "%ROOT%\run_mcp.bat"
timeout /t 3 /nobreak >nul

REM 测试连接
echo [4/4] 测试 MCP 服务...
timeout /t 2 /nobreak >nul
python test_mcp.py
if errorlevel 1 (
    echo.
    echo ========================================
    echo ✗ MCP 服务启动失败
    echo ========================================
    echo 请检查:
    echo 1. Python 环境是否正确
    echo 2. 依赖是否完整: pip install -r requirements.txt
    echo 3. 查看 mcp.log 了解详细错误
    echo.
) else (
    echo.
    echo ========================================
    echo ✓ MCP 服务修复完成
    echo ========================================
    echo.
)

pause

