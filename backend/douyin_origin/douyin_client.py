from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

import requests
from win_subprocess import run_hidden


def _append_windows_chrome_registry_candidates(candidates: list[str]) -> None:
    if sys.platform != "win32":
        return
    try:
        import winreg
    except Exception:
        return

    registry_locations = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe", ""),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe", ""),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe", ""),
        (winreg.HKEY_CURRENT_USER, r"Software\Clients\StartMenuInternet\Google Chrome\shell\open\command", ""),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Clients\StartMenuInternet\Google Chrome\shell\open\command", ""),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Clients\StartMenuInternet\Google Chrome\shell\open\command", ""),
    )
    for root, key_path, value_name in registry_locations:
        try:
            with winreg.OpenKey(root, key_path) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
        except Exception:
            continue
        path = _extract_windows_executable_path(str(value or ""))
        if path:
            candidates.append(path)


def _extract_windows_executable_path(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith('"'):
        end = text.find('"', 1)
        if end > 1:
            return text[1:end]
    marker = ".exe"
    index = text.lower().find(marker)
    if index >= 0:
        return text[: index + len(marker)].strip()
    return text


def _iter_existing_paths(candidates: List[str]) -> List[str]:
    seen = set()
    resolved: List[str] = []
    for candidate in candidates:
        path = str(candidate or "").strip()
        if not path:
            continue
        normalized = path.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        if os.path.isfile(path):
            resolved.append(path)
    return resolved


def get_chrome_path() -> str:
    candidates = []
    explicit = os.environ.get("CHROME_PATH", "").strip()
    if explicit:
        candidates.append(_extract_windows_executable_path(explicit))

    if sys.platform == "win32":
        for env_var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env_var, "")
            if base:
                candidates.append(
                    os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
                )
        _append_windows_chrome_registry_candidates(candidates)
    elif sys.platform == "darwin":
        candidates.extend(
            [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ]
        )
    else:
        candidates.extend(
            [
                "/usr/bin/google-chrome",
                "/usr/bin/google-chrome-stable",
                "/usr/bin/chromium-browser",
                "/usr/bin/chromium",
            ]
        )

    existing = _iter_existing_paths(candidates)
    if existing:
        return existing[0]

    found = (
        shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or shutil.which("chromium-browser")
        or shutil.which("chromium")
        or shutil.which("chrome")
        or shutil.which("chrome.exe")
    )
    if found:
        return found

    raise FileNotFoundError("Chrome not found. Please install Google Chrome first.")


def get_douyin_browser_candidate() -> dict[str, str]:
    return {
        "name": "chrome",
        "label": "Google Chrome",
        "path": get_chrome_path(),
        "process_name": "chrome.exe",
    }


def is_port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        try:
            sock.connect((host, port))
            return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            return False


def _windows_quote_for_single_quoted_ps(value: str) -> str:
    return str(value or "").replace("'", "''")


def _find_windows_stale_browser_pids(profile_dir: str, port: int, process_name: str) -> list[int]:
    if sys.platform != "win32":
        return []

    profile_text = _windows_quote_for_single_quoted_ps(profile_dir)
    port_text = str(int(port or 0))
    process_text = _windows_quote_for_single_quoted_ps(process_name or "chrome.exe")
    script = (
        f"$process='{process_text}';"
        f"$profile='{profile_text}';"
        f"$port='{port_text}';"
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -eq $process -and $_.CommandLine -and "
        "( $_.CommandLine -like \"*--user-data-dir=$profile*\" -or "
        "$_.CommandLine -like \"*--remote-debugging-port=$port*\" ) } | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        result = run_hidden(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except Exception:
        return []

    pids: list[int] = []
    for line in str(result.stdout or "").splitlines():
        text = str(line or "").strip()
        if not text:
            continue
        try:
            pids.append(int(text))
        except ValueError:
            continue
    return sorted(set(pid for pid in pids if pid > 0))


def _terminate_windows_process_tree(pid: int) -> bool:
    if sys.platform != "win32" or int(pid or 0) <= 0:
        return False
    try:
        result = run_hidden(
            ["taskkill", "/F", "/T", "/PID", str(int(pid))],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return int(result.returncode or 0) == 0
    except Exception:
        return False


def _cleanup_stale_browser_processes(profile_dir: str, port: int, process_name: str, browser_label: str) -> None:
    stale_pids = _find_windows_stale_browser_pids(profile_dir, port, process_name)
    if not stale_pids:
        return

    print(
        f"[抖音账号] 检测到账号浏览器残留进程，浏览器 {browser_label}，端口 {port}，profile={profile_dir}，"
        f"准备清理 PID: {', '.join(str(pid) for pid in stale_pids)}"
    )
    for pid in stale_pids:
        _terminate_windows_process_tree(pid)

    deadline = time.time() + 8
    while time.time() < deadline:
        if is_port_open(port):
            break
        if not _find_windows_stale_browser_pids(profile_dir, port, process_name):
            break
        time.sleep(0.5)


class DouyinClient:
    def __init__(self, port: int, account_id: int | None = None):
        self.port = port
        self.account_id = account_id
        self.last_launch_summary = ""

    def resolve_profile_dir(self) -> str:
        explicit = os.environ.get("DOUYIN_PROFILE_DIR", "").strip()
        if explicit:
            Path(explicit).mkdir(parents=True, exist_ok=True)
            return explicit

        local_appdata = os.environ.get("LOCALAPPDATA", "")
        default_base = os.path.join(local_appdata, "Google", "Chrome", "DouyinProfiles")

        base = Path(
            os.environ.get(
                "DOUYIN_PROFILE_BASE",
                default_base,
            )
        )
        base.mkdir(parents=True, exist_ok=True)

        if self.account_id is not None:
            chosen = base / f"account_{self.account_id}"
        else:
            chosen = base / f"port_{self.port}"

        chosen.mkdir(parents=True, exist_ok=True)
        return str(chosen)

    def launch_browser(self, start_url: str = "https://www.douyin.com/user/self") -> bool:
        if is_port_open(self.port):
            return True

        candidate = get_douyin_browser_candidate()
        browser_label = candidate["label"]
        browser_path = candidate["path"]
        process_name = candidate["process_name"]
        profile_dir = self.resolve_profile_dir()

        _cleanup_stale_browser_processes(profile_dir, self.port, process_name, browser_label)
        if is_port_open(self.port):
            self.last_launch_summary = f"复用已有调试端口 {self.port}"
            return True

        cmd = [
            browser_path,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-allow-origins=*",
            "--new-window",
            start_url,
        ]

        creationflags = 0
        if sys.platform == "win32":
            creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) or 0)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )

        deadline = time.time() + 15
        while time.time() < deadline:
            if is_port_open(self.port):
                self.last_launch_summary = (
                    f"已使用 {browser_label} 启动，profile={profile_dir}，port={self.port}"
                )
                print(f"[抖音账号] {self.last_launch_summary}")
                return True
            time.sleep(0.5)

        self.last_launch_summary = f"{browser_label} 启动失败（profile={profile_dir}）"
        _terminate_windows_process_tree(int(getattr(proc, "pid", 0) or 0))
        _cleanup_stale_browser_processes(profile_dir, self.port, process_name, browser_label)
        print(f"[抖音账号] 浏览器启动失败：{self.last_launch_summary}")
        return False

    def close_browser(self):
        try:
            resp = requests.get(f"http://127.0.0.1:{self.port}/json/version", timeout=2)
            if resp.ok:
                ws_url = resp.json().get("webSocketDebuggerUrl")
                if ws_url:
                    import websockets.sync.client as ws_client

                    ws = ws_client.connect(ws_url)
                    ws.send('{"id":1,"method":"Browser.close"}')
                    try:
                        ws.recv(timeout=2)
                    except Exception:
                        pass
                    ws.close()
        except Exception:
            pass

        time.sleep(1)

        if sys.platform == "win32" and is_port_open(self.port):
            try:
                result = run_hidden(
                    ["netstat", "-ano"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                for line in result.stdout.splitlines():
                    if f":{self.port}" in line and "LISTENING" in line:
                        pid = line.strip().split()[-1]
                        run_hidden(
                            ["taskkill", "/F", "/PID", pid],
                            capture_output=True,
                            timeout=5,
                        )
                        break
            except Exception:
                pass
