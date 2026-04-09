@echo off
cd /d "%~dp0"
chcp 65001 >nul 2>&1
title Lobster Verify Minimal

echo ================================================
echo   Lobster - 最小验证（CRLF + pip 离线引导）
echo ================================================
echo.

if not exist "python\python.exe" (
    echo [ERR] 缺少 python\python.exe — 验证包不完整
    pause
    exit /b 1
)
set "PYTHON=%CD%\python\python.exe"
echo [1] 若本行完整显示且无 ^'cho^' ^'/d^' 等乱码，说明 .bat 为 CRLF 且可被 cmd 正确解析
echo.

REM 与 install.bat 一致：启用 embedded 的 site-packages
for %%f in (python\python*._pth) do (
    findstr /C:"#import site" "%%f" >nul 2>&1
    if not errorlevel 1 (
        echo   Enabling site-packages in %%f ...
        %PYTHON% -c "p=r'%%f'; t=open(p).read().replace('#import site','import site'); open(p,'w').write(t)"
    )
)
if not exist "python\Lib\site-packages" mkdir "python\Lib\site-packages"

if not exist "scripts\pip_bootstrap_from_wheel.py" (
    echo [ERR] 缺少 scripts\pip_bootstrap_from_wheel.py
    pause
    exit /b 1
)
set "PIP_WHL="
for %%f in (deps\wheels\pip-*.whl) do set "PIP_WHL=%%f"
if not defined PIP_WHL (
    echo [ERR] 缺少 deps\wheels\pip-*.whl
    pause
    exit /b 1
)

echo [2] 检查 pip ...
%PYTHON% -m pip --version >nul 2>&1
if not errorlevel 1 (
    %PYTHON% -m pip --version
    echo [OK] pip 已存在
    goto :done
)

echo [3] 运行 pip_bootstrap_from_wheel.py （仅离线 wheel）...
set "LOBSTER_ROOT=%CD%"
%PYTHON% "%CD%\scripts\pip_bootstrap_from_wheel.py" 2>&1
if errorlevel 1 (
    echo [ERR] pip 引导失败
    pause
    exit /b 1
)
%PYTHON% -m pip --version 2>&1
if errorlevel 1 (
    echo [ERR] 引导后仍无法执行 python -m pip
    pause
    exit /b 1
)

:done
echo.
echo [OK] 最小验证通过 — 可再解压完整包跑 install.bat
pause
exit /b 0

