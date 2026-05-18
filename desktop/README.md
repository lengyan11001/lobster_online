# 必火AI员工 Desktop Launcher

This directory contains the lightweight desktop shell for `lobster_online`.

The launcher does not embed the whole client. It is a small Windows entrypoint
that starts the local services and opens the existing web UI in a WebView2
desktop window.

## Runtime Flow

1. Resolve the client root directory.
2. Start `run_mcp.bat` when MCP is not already listening.
3. Start `run_backend.bat` when the backend is not already ready.
4. Wait for `http://127.0.0.1:8000/`.
5. Open a pywebview window using Edge WebView2.
6. If WebView2/pywebview fails, fall back to the system browser.

Logs are written to:

```text
desktop_launcher.log
```

## Local Run

```bat
desktop\run_desktop.bat
```

Use the browser fallback directly:

```bat
desktop\run_desktop.bat --browser
```

## Build EXE

```bat
desktop\build_desktop_exe.bat
```

Output:

```text
dist\必火AI员工.exe
```

Copy `dist\必火AI员工.exe` to the project root before packaging or installing:

```text
lobster_online\必火AI员工.exe
```

The installer/shortcut should point to root `必火AI员工.exe`. If it is absent,
`scripts\create_desktop_shortcut.ps1` falls back to legacy `lobster.exe`, then
`start.bat`.

## WebView2 Note

This launcher uses the Microsoft Edge WebView2 runtime through pywebview. Some
Windows machines do not have WebView2 installed or have a damaged runtime. The
launcher therefore falls back to the system browser instead of leaving the user
with a blank window.

For production installers, bundle or install the Microsoft Edge WebView2 Runtime
before launching `必火AI员工.exe`.
