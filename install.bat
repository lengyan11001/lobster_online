@echo off
cd /d "%~dp0"
chcp 65001 >nul 2>&1
title Lobster Install

echo ================================================
echo       Lobster Long Xia - Offline Install
echo ================================================
echo.

REM Step 0: Ensure .env from .env.example if missing
if not exist ".env" if exist ".env.example" (
    copy /y ".env.example" ".env" >nul
    echo [OK] Created .env from .env.example - please edit .env to set API keys / Sutui Token
    echo.
)

REM Step 1: Detect Python
set "PYTHON="
if exist "python\python.exe" (
    set "PYTHON=%CD%\python\python.exe"
    echo [OK] Using embedded Python
    goto :python_ok
)
where python >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=python"
    echo [OK] Using system Python
    goto :python_ok
)
echo [ERR] Python not found. Install Python 3.10+ or place embedded python in python\ folder
pause
exit /b 1

:python_ok
%PYTHON% --version
echo.

REM For embedded Python: enable import site in python*._pth
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

REM Step 1b: Ensure pip
echo [1/7] Checking pip...
%PYTHON% -m pip --version >nul 2>&1
if not errorlevel 1 (
    echo   [OK] pip already installed
    goto :pip_ready
)

echo   pip not found, bootstrapping...

REM Method 0: Bootstrap pip via scripts/pip_bootstrap_from_wheel.py when embedded Python has no pip
if exist "scripts\pip_bootstrap_from_wheel.py" (
    echo   Trying scripts\pip_bootstrap_from_wheel.py ...
    set "LOBSTER_ROOT=%CD%"
    %PYTHON% "%CD%\scripts\pip_bootstrap_from_wheel.py" 2>&1
    if not errorlevel 1 (
        %PYTHON% -m pip --version >nul 2>&1
        if not errorlevel 1 (
            echo   [OK] pip bootstrapped via pip_bootstrap_from_wheel.py
            goto :pip_ready
        )
    )
)

REM Method 1: Bootstrap from bundled pip wheel (most reliable offline method)
set "PIP_WHL="
for %%f in (deps\wheels\pip-*.whl) do set "PIP_WHL=%%f"
if not defined PIP_WHL goto :try_getpip

echo   Using pip wheel: %PIP_WHL%
set "PYTHONPATH=%PIP_WHL%"
%PYTHON% -m pip install --no-index --find-links deps\wheels pip setuptools wheel 2>&1
set "PYTHONPATH="
%PYTHON% -m pip --version >nul 2>&1
if not errorlevel 1 (
    echo   [OK] pip bootstrapped from wheel
    goto :pip_ready
)
echo   Wheel bootstrap failed, trying next method...

:try_getpip
REM Method 2: get-pip.py online
if not exist "deps\get-pip.py" goto :try_ensurepip
echo   Trying get-pip.py - may need internet...
%PYTHON% deps\get-pip.py 2>&1
%PYTHON% -m pip --version >nul 2>&1
if not errorlevel 1 (
    echo   [OK] pip installed via get-pip.py
    goto :pip_ready
)
echo   get-pip.py failed, trying next method...

:try_ensurepip
REM Method 3: ensurepip
echo   Trying ensurepip...
%PYTHON% -m ensurepip --default-pip 2>nul
%PYTHON% -m pip --version >nul 2>&1
if not errorlevel 1 (
    echo   [OK] pip installed via ensurepip
    goto :pip_ready
)

echo.
echo [ERR] Failed to install pip.
echo   Offline: ensure deps\wheels\ has pip-*.whl
echo   Online: check internet connection
pause
exit /b 1

:pip_ready
echo.

REM Step 1c: MSVC runtime x64 (greenlet / native wheels). No manual download if this succeeds.
REM Skip entirely: set LOBSTER_SKIP_VCREDIST=1 before running install.bat
if /i "%LOBSTER_SKIP_VCREDIST%"=="1" goto :after_vcredist
reg query "HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" >nul 2>&1
if not errorlevel 1 goto :after_vcredist
echo   [1c/7] Microsoft VC++ 2015-2022 x64 (required for embedded Python + greenlet)
set "VC_SETUP="
if exist "deps\vc_redist.x64.exe" (
    echo   Using bundled deps\vc_redist.x64.exe ^(offline full pack^)
    set "VC_SETUP=%CD%\deps\vc_redist.x64.exe"
) else (
    if /i "%LOBSTER_OFFLINE_ONLY%"=="1" (
        echo [ERR] LOBSTER_OFFLINE_ONLY=1 but deps\vc_redist.x64.exe missing. Full offline pack must include it, or set LOBSTER_SKIP_VCREDIST=1 if VC++ 2015-2022 x64 is already installed.
        pause
        exit /b 1
    )
    echo   No bundled VC++ in deps\ - downloading ^(needs network^). You may see a UAC prompt once.
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $p = Join-Path $env:TEMP 'vc_redist_lobster_x64.exe'; Invoke-WebRequest -Uri 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile $p -UseBasicParsing; exit 0 } catch { exit 1 }"
    if errorlevel 1 (
        echo [ERR] Could not download VC++ redistributable. Bundle deps\vc_redist.x64.exe in full offline pack, or set LOBSTER_SKIP_VCREDIST=1 if already installed.
        pause
        exit /b 1
    )
    if not exist "%TEMP%\vc_redist_lobster_x64.exe" (
        echo [ERR] vc_redist download file missing.
        pause
        exit /b 1
    )
    set "VC_SETUP=%TEMP%\vc_redist_lobster_x64.exe"
)
if not defined VC_SETUP (
    echo [ERR] VC++ installer path not set.
    pause
    exit /b 1
)
start /wait "" "%VC_SETUP%" /install /quiet /norestart
set "VC_EC=%ERRORLEVEL%"
if "%VC_EC%"=="0" goto :after_vcredist
if "%VC_EC%"=="1638" goto :after_vcredist
if "%VC_EC%"=="3010" goto :after_vcredist
echo [ERR] VC++ installer failed with code %VC_EC%. Right-click install.bat - Run as administrator, then retry.
pause
exit /b 1
:after_vcredist
echo   [OK] VC++ runtime check done
echo.

REM Step 2: Install Python dependencies (aligned with standalone install1.bat / lobster install.bat)
echo [2/7] Installing Python packages...

set "PKG_IMPORT_CHECK=import fastapi,uvicorn,pydantic,httpx,sqlalchemy,playwright,greenlet,PIL"
set "REQ_FILE=requirements.txt"

REM If requirements.txt lists tos: drop old runtime file, regenerate, main pip skips tos (Step 2b installs tos)
findstr /R /I /C:"tos" "requirements.txt" >nul 2>&1
if errorlevel 1 goto :req_runtime_done
if exist "requirements.runtime.txt" del /f /q "requirements.runtime.txt" >nul 2>&1
echo   [INFO] Excluding tos from main step - will install in Step 2b...
findstr /V /R /I /C:"^ *tos" "requirements.txt" > "requirements.runtime.txt"
set "REQ_FILE=requirements.runtime.txt"
:req_runtime_done

REM Offline first if deps\wheels exists (same as standalone; empty dir falls through to online after pip error)
if not exist "deps\wheels" goto :try_online_pkgs
echo   Installing from offline wheels...
%PYTHON% -m pip install --no-index --find-links deps\wheels -r "%REQ_FILE%" 2>&1
if errorlevel 1 (
    if /i "%LOBSTER_OFFLINE_ONLY%"=="1" (
        echo   [ERR] Offline install failed and LOBSTER_OFFLINE_ONLY=1 - no network fallback.
        goto :packages_failed
    )
    echo   Offline install command failed, trying online...
    goto :try_online_pkgs
)
%PYTHON% -c "%PKG_IMPORT_CHECK%" >nul 2>&1
if not errorlevel 1 (
    echo   [OK] Python packages installed - offline
    goto :packages_done
)
echo   Offline install incomplete - missing modules, trying online...
if /i "%LOBSTER_OFFLINE_ONLY%"=="1" (
    echo [ERR] LOBSTER_OFFLINE_ONLY=1 - cannot use online pip.
    goto :packages_failed
)

:try_online_pkgs
if /i "%LOBSTER_OFFLINE_ONLY%"=="1" (
    echo [ERR] LOBSTER_OFFLINE_ONLY=1 - cannot use online pip. Ensure deps\wheels is complete.
    goto :packages_failed
)
echo   Installing packages online...
%PYTHON% -m pip install -r "%REQ_FILE%" 2>&1
if errorlevel 1 goto :packages_failed
%PYTHON% -c "%PKG_IMPORT_CHECK%" >nul 2>&1
if not errorlevel 1 (
    echo   [OK] Python packages installed
    goto :packages_done
)

:packages_failed
echo [ERR] Failed to install Python packages.
echo   Check:
echo     %PYTHON% -m pip install -r "%REQ_FILE%"
echo     %PYTHON% -c "%PKG_IMPORT_CHECK%"
echo ----- import check output -----
%PYTHON% -c "%PKG_IMPORT_CHECK%"
echo -----
pause
exit /b 1

:packages_done
if exist "requirements.runtime.txt" del /f /q "requirements.runtime.txt" >nul 2>&1
echo.

REM Step 2b: Skill extra deps - WeCom pycryptodome, Volcano tos
echo   [2b/7] Skill dependencies - WeCom pycryptodome, Volcano tos...
set "PYCRYPTO_OK=0"
if exist "deps\wheels" (
    %PYTHON% -m pip install --no-index --find-links deps\wheels pycryptodome 2>nul
    if not errorlevel 1 set "PYCRYPTO_OK=1"
)
if "%PYCRYPTO_OK%"=="0" (
    if /i not "%LOBSTER_OFFLINE_ONLY%"=="1" (
        %PYTHON% -m pip install pycryptodome 2>nul
        if not errorlevel 1 set "PYCRYPTO_OK=1"
    )
)
if "%PYCRYPTO_OK%"=="1" (
    echo   [OK] pycryptodome installed
) else (
    echo   [SKIP] pycryptodome not installed - WeCom reply will be disabled
)
set "TOS_OK=0"
if exist "deps\wheels\tos-*.whl" (
    %PYTHON% -m pip install --no-index --find-links deps\wheels tos 2>nul
    if not errorlevel 1 set "TOS_OK=1"
)
if "%TOS_OK%"=="0" (
    if exist "deps\wheels\tos-*.tar.gz" (
        REM tar.gz install needs setuptools first
        %PYTHON% -m pip install setuptools wheel 2>nul
        for %%f in (deps\wheels\tos-*.tar.gz) do (
            %PYTHON% -m pip install "%%f" 2>nul
            if not errorlevel 1 set "TOS_OK=1"
        )
    )
)
if "%TOS_OK%"=="0" (
    if /i not "%LOBSTER_OFFLINE_ONLY%"=="1" (
        %PYTHON% -m pip install tos 2>nul
        if not errorlevel 1 set "TOS_OK=1"
    )
)
if "%TOS_OK%"=="1" (
    echo   [OK] tos Volcano installed
) else (
    echo   [SKIP] tos not installed - upload to Volcano TOS will be disabled
)
echo.

REM Step 3: Check Node.js
echo [3/7] Checking Node.js...
set "NODE_OK=0"
if exist "nodejs\node.exe" (
    echo   [OK] Using embedded Node.js
    nodejs\node.exe --version
    set "NODE_OK=1"
    goto :node_done
)
where node >nul 2>&1
if errorlevel 1 goto :node_missing
echo   [OK] Using system Node.js
node --version
set "NODE_OK=1"
goto :node_done
:node_missing
echo   [WARN] Node.js not found - OpenClaw Gateway will not work
echo   Please place Node.js portable in nodejs\ folder or install Node.js
:node_done
echo.

REM Step 4: Check OpenClaw + bundled plugins (e.g. @tencent-weixin/openclaw-weixin)
echo [4/7] Checking OpenClaw...
if not "%NODE_OK%"=="1" (
    echo   [SKIP] No Node.js available
    goto :oc_done
)
if exist "nodejs\package.json" (
    if exist "nodejs\node_modules\openclaw" if exist "nodejs\node_modules\@tencent-weixin\openclaw-weixin" (
        echo   [OK] OpenClaw + WeChat plugin pre-installed
        goto :oc_done
    )
    if /i "%LOBSTER_OFFLINE_ONLY%"=="1" (
        echo   [SKIP] LOBSTER_OFFLINE_ONLY=1 - need nodejs\node_modules\openclaw and @tencent-weixin\openclaw-weixin
        goto :oc_done
    )
    echo   Installing Node dependencies from nodejs\package.json ^(openclaw + weixin plugin^)...
    if exist "nodejs\node.exe" set "PATH=%CD%\nodejs;%PATH%"
    pushd nodejs
    call npm install --no-fund --no-audit
    popd
    if exist "nodejs\node_modules\openclaw" echo   [OK] OpenClaw installed
    if exist "nodejs\node_modules\@tencent-weixin\openclaw-weixin" echo   [OK] WeChat OpenClaw plugin installed
    goto :oc_done
)
if exist "nodejs\node_modules\openclaw" (
    echo   [OK] OpenClaw pre-installed
    goto :oc_done
)
if exist "node_modules\openclaw" (
    echo   [OK] OpenClaw found
    goto :oc_done
)
if /i "%LOBSTER_OFFLINE_ONLY%"=="1" (
    echo   [SKIP] LOBSTER_OFFLINE_ONLY=1 - OpenClaw must be pre-bundled under nodejs\node_modules\openclaw
    goto :oc_done
)
echo   Installing OpenClaw online...
if exist "nodejs\node.exe" (
    set "PATH=%CD%\nodejs;%PATH%"
    pushd nodejs
    call npm install openclaw@latest --save 2>nul
    popd
) else (
    call npm install openclaw@latest --save 2>nul
)
if exist "nodejs\node_modules\openclaw" echo   [OK] OpenClaw installed
if exist "node_modules\openclaw" echo   [OK] OpenClaw installed
:oc_done
echo.

REM Step 5: Configure OpenClaw Gateway
echo [5/7] Configuring OpenClaw Gateway...
%PYTHON% scripts\setup_openclaw.py
echo.

REM Step 6: Install Playwright Chromium
echo [6/7] Installing Playwright browser...
if exist "browser_chromium" (
    echo   [OK] Chromium already installed - offline
    set "PLAYWRIGHT_BROWSERS_PATH=%CD%\browser_chromium"
    goto :pw_done
)
if /i "%LOBSTER_OFFLINE_ONLY%"=="1" (
    echo   [SKIP] LOBSTER_OFFLINE_ONLY=1 - Playwright Chromium must be in browser_chromium\
    goto :pw_done
)
%PYTHON% -m playwright install chromium 2>nul
if not errorlevel 1 (
    echo   [OK] Chromium installed
) else (
    echo   [WARN] Playwright chromium not installed - publish features need manual install
    echo          Run: %PYTHON% -m playwright install chromium
)
:pw_done
echo.

REM Step 6b: ffmpeg for media.edit (Windows: deps\ffmpeg\ffmpeg.exe)
echo [6b/7] ffmpeg for media edit...
if exist "deps\ffmpeg\ffmpeg.exe" (
    echo   [OK] deps\ffmpeg\ffmpeg.exe
    goto :ffmpeg_install_done
)
if /i "%LOBSTER_OFFLINE_ONLY%"=="1" (
    echo   [ERR] LOBSTER_OFFLINE_ONLY=1 but deps\ffmpeg\ffmpeg.exe missing. Full offline pack must include it ^(or copy from build machine^).
    pause
    exit /b 1
)
if not exist "scripts\ensure_ffmpeg_windows.py" (
    echo   [ERR] scripts\ensure_ffmpeg_windows.py missing
    pause
    exit /b 1
)
echo   Downloading ffmpeg ^(media.edit, needs network^)...
%PYTHON% "%~dp0scripts\ensure_ffmpeg_windows.py"
if errorlevel 1 (
    echo   [ERR] ffmpeg download failed. Fix network or place ffmpeg.exe in deps\ffmpeg\
    pause
    exit /b 1
)
if not exist "deps\ffmpeg\ffmpeg.exe" (
    echo   [ERR] deps\ffmpeg\ffmpeg.exe still missing after ensure script
    pause
    exit /b 1
)
echo   [OK] deps\ffmpeg\ffmpeg.exe
:ffmpeg_install_done
echo.

REM Step 7: Firewall
echo   [7/7] Configuring firewall - needs admin...
netsh advfirewall firewall show rule name="Lobster-Backend" >nul 2>&1
if not errorlevel 1 (
    echo   [OK] Firewall rule exists
    goto :fw_done
)
netsh advfirewall firewall add rule name="Lobster-Backend" dir=in action=allow protocol=tcp localport=8000 >nul 2>&1
if errorlevel 1 (
    echo   [WARN] Firewall config failed - may need admin rights
) else (
    echo   [OK] Firewall rule added for port 8000
)
:fw_done
echo.

REM Desktop shortcut: paths and .lnk name from static\branding\brands.json (marks.%LOBSTER_BRAND_MARK%.install)
REM If user already set LOBSTER_BRAND_MARK in the shell, keep it; else read from .env / .env.example (double-click has no env)
if defined LOBSTER_BRAND_MARK goto :brand_mark_done
if exist ".env" for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do (
  if /i "%%~a"=="LOBSTER_BRAND_MARK" set "LOBSTER_BRAND_MARK=%%b"
)
if not defined LOBSTER_BRAND_MARK if exist ".env.example" for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env.example") do (
  if /i "%%~a"=="LOBSTER_BRAND_MARK" set "LOBSTER_BRAND_MARK=%%b"
)
if not defined LOBSTER_BRAND_MARK set "LOBSTER_BRAND_MARK=yingshi"
:brand_mark_done
if /i "%LOBSTER_SKIP_DESKTOP_SHORTCUT%"=="1" goto :after_desktop_shortcut
if not exist "static\branding\brands.json" (
    echo   [WARN] static\branding\brands.json missing - desktop shortcut skipped
    goto :after_desktop_shortcut
)
if not exist "scripts\create_desktop_shortcut.ps1" (
    echo   [WARN] scripts\create_desktop_shortcut.ps1 missing - desktop shortcut skipped
    goto :after_desktop_shortcut
)
echo   Creating desktop shortcut ^(LOBSTER_BRAND_MARK=%LOBSTER_BRAND_MARK%^)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\create_desktop_shortcut.ps1" -Root "%CD%" -BrandMark "%LOBSTER_BRAND_MARK%"
REM exit: 0=ok 1=fail 2=skip - do not use set to save ERRORLEVEL
if errorlevel 2 if not errorlevel 3 (
    echo   [WARN] Desktop shortcut skipped - need start.bat, static\bihu_box.ico, and Desktop folder
    goto :after_desktop_shortcut
)
if errorlevel 1 if not errorlevel 2 (
    echo   [WARN] Desktop shortcut failed - you can still run start.bat from this folder
    goto :after_desktop_shortcut
)
if not errorlevel 1 (
    echo   [OK] Desktop shortcut created
)
:after_desktop_shortcut
echo.

echo ================================================
echo   Install complete!
echo.
echo   Next steps:
echo     1. Double-click the desktop shortcut ^(see brands.json for name^) or start.bat in this folder
echo     2. Go to System Config to set API keys
echo     3. Start chatting!
echo ================================================
echo.
if not defined LOBSTER_SKIP_INSTALL_PAUSE pause


