@echo off
cd /d "%~dp0"
chcp 65001 >nul 2>&1
title Lobster Online
set LOBSTER_SKIP_INSTALL_PAUSE=1

REM 精简包：无嵌入式 Python、无离线 pip wheel → install_slim.bat；完整/代码包 → install.bat
set "LOBSTER_FULL_PACK=0"
if exist "python\python.exe" set "LOBSTER_FULL_PACK=1"
if "%LOBSTER_FULL_PACK%"=="0" (
  for %%f in (deps\wheels\pip-*.whl) do set "LOBSTER_FULL_PACK=1"
)
REM 2026-10-14: 启动不联网补 ffmpeg/ffprobe（以前会从 TOS 下一整个包，客户机卡很久）。
REM 这个标记只作用在下面这次 install 调用，清掉后传给后端的环境是干净的。
set "LOBSTER_SKIP_FFMPEG_DOWNLOAD=1"
if "%LOBSTER_FULL_PACK%"=="1" (
  call install.bat
) else (
  if exist "install_slim.bat" (
    call install_slim.bat
  ) else (
    call install.bat
  )
)
if errorlevel 1 (
  set "LOBSTER_SKIP_FFMPEG_DOWNLOAD="
  pause & exit /b 1
)
set "LOBSTER_SKIP_FFMPEG_DOWNLOAD="
start "Lobster" "%~dp0start.bat"
exit /b 0

