@echo off
cd /d "%~dp0"
REM Do not chcp 65001 here: slim pack stores this file as GBK for cmd; UTF-8 console breaks GBK text.
title Lobster Install (Slim / Online)

echo ================================================
echo   Lobster Long Xia - Slim Install (online only)
echo ================================================
echo   Slim pack: all deps online. If Python/Node missing, they are
echo   downloaded from python.org / nodejs.org ^(this PC must be online^).
echo   Full offline pack uses install.bat ^(embedded Python^).
echo.
REM Pinned URLs - bump together when changing versions
set "LOBSTER_PY_WIN_INSTALLER_URL=https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
set "LOBSTER_NODE_WIN_ZIP_URL=https://nodejs.org/dist/v20.18.1/node-v20.18.1-win-x64.zip"
set "LOBSTER_NODE_WIN_ZIP_DIR=node-v20.18.1-win-x64"

if /i "%LOBSTER_OFFLINE_ONLY%"=="1" (
    echo [ERR] Slim install requires network. Use full project zip + install.bat for offline.
    pause
    exit /b 1
)

if not exist ".env" if exist ".env.example" (
    copy /y ".env.example" ".env" >nul
    echo [OK] Created .env from .env.example
    echo.
)

set "PYTHON="
if exist "python\python.exe" (
    set "PYTHON=%CD%\python\python.exe"
    echo [OK] Using embedded Python
    goto :python_ok
)

python --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=python"
    echo [OK] Using: python
    goto :python_ok
)
python3 --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=python3"
    echo [OK] Using: python3
    goto :python_ok
)
py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=py -3"
    echo [OK] Using: py -3
    goto :python_ok
)
py -3.12 --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=py -3.12"
    echo [OK] Using: py -3.12
    goto :python_ok
)
py -3.11 --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=py -3.11"
    echo [OK] Using: py -3.11
    goto :python_ok
)
py -3.10 --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=py -3.10"
    echo [OK] Using: py -3.10
    goto :python_ok
)
py --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=py"
    echo [OK] Using: py ^(default interpreter^)
    goto :python_ok
)

echo [INFO] Python 3.10+ not in PATH. Downloading official installer ^(online^)...
set "PY_INSTALLER=%TEMP%\lobster_python312_installer.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%LOBSTER_PY_WIN_INSTALLER_URL%' -OutFile $env:TEMP\lobster_python312_installer.exe -UseBasicParsing; exit 0 } catch { exit 1 }"
if errorlevel 1 (
    echo [ERR] Could not download Python installer. Check network / proxy / firewall.
    pause
    exit /b 1
)
if not exist "%PY_INSTALLER%" (
    echo [ERR] Python installer file missing after download.
    pause
    exit /b 1
)
echo [INFO] Running Python installer ^(silent, per-user, pip+PATH^)...
start /wait "" "%PY_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_doc=0 Include_pip=1 Include_dev=0 Include_launcher=1
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
    echo [OK] Python installed from python.org ^(Python 3.12^)
    goto :python_ok
)
echo [ERR] Python installer ran but python.exe not found at:
echo   %LOCALAPPDATA%\Programs\Python\Python312\python.exe
echo   Use full offline zip + install.bat, or install Python manually from python.org.
pause
exit /b 1

:python_ok
%PYTHON% --version
echo.

if exist "python\python.exe" (
    for %%f in (python\python*._pth) do (
        findstr /C:"#import site" "%%f" >nul 2>&1
        if not errorlevel 1 (
            echo   Enabling site-packages in %%f ...
            %PYTHON% -c "p=r'%%f'; t=open(p).read().replace('#import site','import site'); open(p,'w').write(t)"
        )
    )
    if not exist "python\Lib\site-packages" mkdir "python\Lib\site-packages"
)

echo [1/7] Checking pip...
%PYTHON% -m pip --version >nul 2>&1
if not errorlevel 1 (
    echo   [OK] pip already installed
    goto :pip_ready
)

echo   pip not found, bootstrapping online...
%PYTHON% -m ensurepip --default-pip 2>nul
%PYTHON% -m pip --version >nul 2>&1
if not errorlevel 1 (
    echo   [OK] pip installed via ensurepip
    goto :pip_ready
)

echo   Downloading get-pip.py ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $p = Join-Path $env:TEMP 'get-pip_lobster.py'; Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile $p -UseBasicParsing; exit 0 } catch { exit 1 }"
if errorlevel 1 (
    echo [ERR] Could not download get-pip.py. Check network / proxy / firewall.
    pause
    exit /b 1
)
if not exist "%TEMP%\get-pip_lobster.py" (
    echo [ERR] get-pip file missing after download.
    pause
    exit /b 1
)
%PYTHON% "%TEMP%\get-pip_lobster.py" 2>&1
%PYTHON% -m pip --version >nul 2>&1
if not errorlevel 1 (
    echo   [OK] pip installed via get-pip.py
    goto :pip_ready
)

echo [ERR] Failed to install pip. Try: py -3 -m ensurepip --upgrade
pause
exit /b 1

:pip_ready
echo.

if /i "%LOBSTER_SKIP_VCREDIST%"=="1" goto :after_vcredist
reg query "HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" >nul 2>&1
if not errorlevel 1 goto :after_vcredist
echo   [1c/7] Microsoft VC++ 2015-2022 x64
set "VC_SETUP="
if exist "deps\vc_redist.x64.exe" (
    echo   Using bundled deps\vc_redist.x64.exe
    set "VC_SETUP=%CD%\deps\vc_redist.x64.exe"
) else (
    echo   Downloading VC++ ^(needs network^)...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $p = Join-Path $env:TEMP 'vc_redist_lobster_x64.exe'; Invoke-WebRequest -Uri 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile $p -UseBasicParsing; exit 0 } catch { exit 1 }"
    if errorlevel 1 (
        echo [ERR] VC++ download failed. Set LOBSTER_SKIP_VCREDIST=1 if already installed.
        pause
        exit /b 1
    )
    set "VC_SETUP=%TEMP%\vc_redist_lobster_x64.exe"
)
if not defined VC_SETUP goto :after_vcredist
start /wait "" "%VC_SETUP%" /install /quiet /norestart
set "VC_EC=%ERRORLEVEL%"
if "%VC_EC%"=="0" goto :after_vcredist
if "%VC_EC%"=="1638" goto :after_vcredist
if "%VC_EC%"=="3010" goto :after_vcredist
echo [ERR] VC++ installer failed code %VC_EC%.
pause
exit /b 1
:after_vcredist
echo   [OK] VC++ runtime check done
echo.

echo [2/7] Installing Python packages (online)...
set "PKG_IMPORT_CHECK=import fastapi,uvicorn,pydantic,httpx,sqlalchemy,playwright,greenlet,PIL"
set "REQ_FILE=requirements.txt"
findstr /R /I /C:"tos" "requirements.txt" >nul 2>&1
if errorlevel 1 goto :req_runtime_done
if exist "requirements.runtime.txt" del /f /q "requirements.runtime.txt" >nul 2>&1
echo   [INFO] Excluding tos from main step - Step 2b installs tos...
findstr /V /R /I /C:"^ *tos" "requirements.txt" > "requirements.runtime.txt"
set "REQ_FILE=requirements.runtime.txt"
:req_runtime_done

%PYTHON% -m pip install -r "%REQ_FILE%" 2>&1
if errorlevel 1 goto :packages_failed
%PYTHON% -c "%PKG_IMPORT_CHECK%" >nul 2>&1
if errorlevel 1 goto :packages_failed
echo   [OK] Python packages installed
goto :packages_done

:packages_failed
echo [ERR] Failed to install Python packages.
%PYTHON% -c "%PKG_IMPORT_CHECK%"
pause
exit /b 1

:packages_done
if exist "requirements.runtime.txt" del /f /q "requirements.runtime.txt" >nul 2>&1
echo.

echo   [2b/7] Skill deps - pycryptodome, tos...
%PYTHON% -m pip install pycryptodome 2>nul
%PYTHON% -m pip install tos 2>nul
echo.

echo [3/7] Checking Node.js...
set "NODE_OK=0"
if exist "nodejs\node.exe" (
    echo   [OK] Embedded Node.js
    nodejs\node.exe --version
    set "NODE_OK=1"
    goto :node_done
)
where node >nul 2>&1
if errorlevel 1 goto :node_missing
echo   [OK] System Node.js
node --version
set "NODE_OK=1"
goto :node_done
:node_missing
echo   [INFO] Node.js not found. Downloading portable Node ^(online^)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $z=Join-Path $env:TEMP 'lobster_node_win_x64.zip'; Invoke-WebRequest -Uri '%LOBSTER_NODE_WIN_ZIP_URL%' -OutFile $z -UseBasicParsing; $a=Join-Path $env:TEMP 'lobster_node_extract'; Remove-Item $a -Recurse -Force -ErrorAction SilentlyContinue; Expand-Archive -Path $z -DestinationPath $a -Force; $src=Join-Path $a '%LOBSTER_NODE_WIN_ZIP_DIR%'; $out=Join-Path '%CD%' 'nodejs'; if (-not (Test-Path (Join-Path $src 'node.exe'))) { exit 1 }; New-Item -ItemType Directory -Force -Path $out | Out-Null; Copy-Item -Path (Join-Path $src '*') -Destination $out -Recurse -Force; if (-not (Test-Path (Join-Path $out 'node.exe'))) { exit 1 }"
if errorlevel 1 (
    echo   [ERR] Node.js download or extract failed. Check network / disk space.
    pause
    exit /b 1
)
if not exist "nodejs\node.exe" (
    echo   [ERR] nodejs\node.exe missing after extract.
    pause
    exit /b 1
)
set "PATH=%CD%\nodejs;%PATH%"
echo   [OK] Portable Node.js installed
nodejs\node.exe --version
set "NODE_OK=1"
:node_done
echo.

echo [4/7] OpenClaw...
if not "%NODE_OK%"=="1" goto :oc_done
if exist "nodejs\node_modules\openclaw" goto :oc_done
if exist "node_modules\openclaw" goto :oc_done
echo   Installing OpenClaw online...
if exist "nodejs\node.exe" (
    set "PATH=%CD%\nodejs;%PATH%"
    pushd nodejs
    call npm install openclaw@latest --save
    if errorlevel 1 (
        echo [ERR] npm install openclaw failed
        popd
        pause
        exit /b 1
    )
    popd
) else (
    call npm install openclaw@latest --save
    if errorlevel 1 (
        echo [ERR] npm install openclaw failed
        pause
        exit /b 1
    )
)
:oc_done
echo.

echo [5/7] Configuring OpenClaw Gateway...
%PYTHON% scripts\setup_openclaw.py
echo.

echo [6/7] Playwright Chromium...
if exist "browser_chromium" (
    set "PLAYWRIGHT_BROWSERS_PATH=%CD%\browser_chromium"
    echo   [OK] Using browser_chromium
    goto :pw_done
)
%PYTHON% -m playwright install chromium
if errorlevel 1 (
    echo [ERR] playwright install chromium failed
    pause
    exit /b 1
)
:pw_done
echo.

echo [6b/7] ffmpeg...
if exist "deps\ffmpeg\ffmpeg.exe" goto :ffmpeg_ok
if not exist "scripts\ensure_ffmpeg_windows.py" (
    echo   [WARN] ensure_ffmpeg_windows.py missing
    goto :ffmpeg_ok
)
mkdir "deps\ffmpeg" 2>nul
%PYTHON% "%~dp0scripts\ensure_ffmpeg_windows.py"
:ffmpeg_ok
echo.

echo   [7/7] Firewall...
netsh advfirewall firewall show rule name="Lobster-Backend" >nul 2>&1
if errorlevel 1 netsh advfirewall firewall add rule name="Lobster-Backend" dir=in action=allow protocol=tcp localport=8000 >nul 2>&1
echo.

if defined LOBSTER_BRAND_MARK goto :brand_mark_done
if exist ".env" for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do (
  if /i "%%~a"=="LOBSTER_BRAND_MARK" set "LOBSTER_BRAND_MARK=%%b"
)
if not defined LOBSTER_BRAND_MARK if exist ".env.example" for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env.example") do (
  if /i "%%~a"=="LOBSTER_BRAND_MARK" set "LOBSTER_BRAND_MARK=%%b"
)
if not defined LOBSTER_BRAND_MARK set "LOBSTER_BRAND_MARK=yingshi"
:brand_mark_done
if /i "%LOBSTER_SKIP_DESKTOP_SHORTCUT%"=="1" goto :after_shortcut
if exist "scripts\create_desktop_shortcut.ps1" if exist "static\branding\brands.json" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\create_desktop_shortcut.ps1" -Root "%CD%" -BrandMark "%LOBSTER_BRAND_MARK%"
)
:after_shortcut

echo ================================================
echo   Install complete (slim / online)
echo   Next: start.bat
echo ================================================
echo.
if not defined LOBSTER_SKIP_INSTALL_PAUSE pause

