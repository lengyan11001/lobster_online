from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
import urllib.request
import webbrowser
from pathlib import Path


APP_NAME = "必火AI员工"
DEFAULT_PORT = 8000
DEFAULT_MCP_PORT = 8001


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resolve_root() -> Path:
    if _is_frozen():
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parents[1]
    if not (root / "backend").is_dir() and (root.parent / "backend").is_dir():
        root = root.parent
    return root


ROOT = resolve_root()
LOG_PATH = ROOT / "desktop_launcher.log"


def log(message: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if not _is_frozen():
        print(line)


def message_box(title: str, body: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, body, title, 0x40)
    except Exception:
        log(f"{title}: {body}")


def read_env_value(name: str, default: str) -> str:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return default
    try:
        for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == name:
                return value.strip() or default
    except Exception as exc:
        log(f"read .env failed: {exc}")
    return default


def ensure_env_template() -> None:
    return


def build_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["LOBSTER_DESKTOP"] = "1"
    chromium = ROOT / "browser_chromium"
    if chromium.is_dir():
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(chromium)
    node_dir = ROOT / "nodejs"
    if (node_dir / "node.exe").is_file():
        env["PATH"] = str(node_dir) + os.pathsep + env.get("PATH", "")
    return env


def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_ready(url: str, timeout: float = 1.2) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LobsterDesktop/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= int(resp.status) < 500
    except Exception:
        return False


def creation_flags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def start_bat(name: str, bat_name: str, env: dict[str, str]) -> subprocess.Popen | None:
    bat = ROOT / bat_name
    if not bat.is_file():
        log(f"{name}: missing {bat_name}")
        return None
    log(f"{name}: starting {bat_name}")
    try:
        return subprocess.Popen(
            ["cmd.exe", "/d", "/s", "/c", str(bat)],
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags(),
        )
    except Exception as exc:
        log(f"{name}: start failed: {exc}")
        return None


def wait_for_backend(port: int, seconds: int) -> bool:
    health = f"http://127.0.0.1:{port}/api/health"
    home = f"http://127.0.0.1:{port}/"
    deadline = time.time() + seconds
    while time.time() < deadline:
        if http_ready(health) or http_ready(home):
            return True
        time.sleep(0.8)
    return False


def open_browser(url: str) -> None:
    log(f"opening system browser fallback: {url}")
    webbrowser.open(url)


def stop_process(proc: subprocess.Popen | None, name: str) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        log(f"{name}: terminating")
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def run_window(url: str, title: str, width: int, height: int) -> bool:
    try:
        import webview  # type: ignore
    except Exception as exc:
        log(f"pywebview unavailable: {exc}")
        return False

    try:
        window = webview.create_window(
            title,
            url,
            width=width,
            height=height,
            min_size=(1100, 720),
            text_select=True,
        )
        webview.start(
            gui="edgechromium" if os.name == "nt" else None,
            debug=False,
            private_mode=False,
            storage_path=str(ROOT / "browser_data" / "desktop_webview"),
        )
        return True
    except Exception as exc:
        log(f"webview start failed: {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Lobster desktop launcher")
    parser.add_argument("--browser", action="store_true", help="Skip WebView and open the system browser.")
    parser.add_argument("--port", type=int, default=0, help="Backend port override.")
    parser.add_argument("--wait", type=int, default=75, help="Seconds to wait for backend startup.")
    parser.add_argument("--title", default="", help="Window title override.")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=920)
    args = parser.parse_args()

    if not (ROOT / "backend").is_dir() or not (ROOT / "static").is_dir():
        message_box(APP_NAME, f"客户端目录不完整，找不到 backend/static。\n\n当前目录：{ROOT}")
        return 2

    if not (ROOT / ".env").is_file():
        log(".env not found; launcher will continue with built-in/default environment values")
    port = args.port or int(read_env_value("PORT", str(DEFAULT_PORT)) or DEFAULT_PORT)
    mcp_port = int(read_env_value("MCP_PORT", str(DEFAULT_MCP_PORT)) or DEFAULT_MCP_PORT)
    title = args.title or read_env_value("LOBSTER_DESKTOP_TITLE", APP_NAME)
    url = f"http://127.0.0.1:{port}/?desktop=1&v={int(time.time())}-{uuid.uuid4().hex[:8]}"
    env = build_env()

    log(f"launcher root={ROOT}")
    log(f"target url={url}")

    mcp_proc = None
    backend_proc = None
    if not port_open("127.0.0.1", mcp_port):
        mcp_proc = start_bat("MCP", "run_mcp.bat", env)
        time.sleep(1.2)
    else:
        log(f"MCP: port {mcp_port} already open")

    if not wait_for_backend(port, 2):
        backend_proc = start_bat("Backend", "run_backend.bat", env)
    else:
        log(f"Backend: port {port} already ready")

    if not wait_for_backend(port, args.wait):
        body = (
            f"本机服务启动失败，页面无法打开。\n\n"
            f"请查看：\n{ROOT / 'backend.log'}\n{ROOT / 'mcp.log'}\n{LOG_PATH}\n\n"
            f"也可以先运行 install.bat 后重试。"
        )
        message_box(APP_NAME, body)
        stop_process(backend_proc, "Backend")
        stop_process(mcp_proc, "MCP")
        return 1

    if args.browser:
        open_browser(url)
        return 0

    ok = run_window(url, title, args.width, args.height)
    if not ok:
        open_browser(url)
        message_box(
            APP_NAME,
            "当前电脑的 WebView2/pywebview 环境不可用，已自动改用系统浏览器打开客户端。\n\n"
            "本机服务会继续在后台运行；如需停止，请关闭相关 python 进程或运行 stop.bat。\n\n"
            "后续安装器应内置 Microsoft Edge WebView2 Runtime 以减少此问题。",
        )
        return 0

    stop_process(backend_proc, "Backend")
    stop_process(mcp_proc, "MCP")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
