@echo off
cd /d "%~dp0"
chcp 65001 >nul 2>&1
title 龙虾 - 验证包占位 install.bat

echo.
echo ================================================
echo   这是「最小验证包」里的占位 install.bat
echo ================================================
echo   请关闭本窗口，双击运行 verify_install.bat
echo   完整安装请另下「完整项目包」，再运行其中 install.bat
echo ================================================
echo.
pause
exit /b 0

