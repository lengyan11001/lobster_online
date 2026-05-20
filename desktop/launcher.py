from __future__ import annotations

import argparse
import ctypes
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
import json
import urllib.request
import webbrowser
from pathlib import Path
from ctypes import wintypes


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


def screen_work_area() -> tuple[int, int]:
    if os.name != "nt":
        return 1366, 768
    try:
        user32 = ctypes.windll.user32
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
        rect = wintypes.RECT()
        ok = ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        if ok:
            return max(900, rect.right - rect.left), max(640, rect.bottom - rect.top)
        return max(900, user32.GetSystemMetrics(0)), max(640, user32.GetSystemMetrics(1))
    except Exception as exc:
        log(f"detect screen size failed: {exc}")
        return 1366, 768


def adaptive_window_size(requested_width: int, requested_height: int) -> tuple[int, int]:
    work_w, work_h = screen_work_area()
    max_w = max(900, work_w - 48)
    max_h = max(640, work_h - 64)
    target_w = requested_width if requested_width > 0 else 1440
    target_h = requested_height if requested_height > 0 else 920
    width = min(target_w, max_w)
    height = min(target_h, max_h)
    width = max(900, width)
    height = max(640, height)
    log(f"screen work_area={work_w}x{work_h}, window={width}x{height}")
    return width, height


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


def http_json(url: str, timeout: float = 1.2) -> dict:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LobsterDesktop/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(256 * 1024)
        data = json.loads(raw.decode("utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def same_path(a: str, b: Path) -> bool:
    if not str(a or "").strip():
        return False
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(str(b.resolve()))
    except Exception:
        return False


def creation_flags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def safe_filename(name: str, fallback: str = "lobster-asset") -> str:
    value = Path(name or "").name.strip()
    if not value:
        value = fallback
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return value or fallback


def asset_file_for_id(asset_id: str) -> Path | None:
    aid = str(asset_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,80}", aid):
        return None
    assets_dir = (ROOT / "assets").resolve()
    if not assets_dir.is_dir():
        return None
    for path in sorted(assets_dir.glob(f"{aid}.*")):
        try:
            resolved = path.resolve()
            if assets_dir in resolved.parents and resolved.is_file():
                return resolved
        except Exception:
            continue
    exact = assets_dir / aid
    return exact if exact.is_file() else None


def netstat_listening_pids(port: int) -> set[int]:
    if os.name != "nt":
        return set()
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],
            text=True,
            errors="ignore",
            creationflags=creation_flags(),
        )
    except Exception as exc:
        log(f"netstat failed: {exc}")
        return set()
    pids: set[int] = set()
    markers = (f":{port} ", f":{port}\t")
    for raw in out.splitlines():
        line = raw.strip()
        if "LISTENING" not in line.upper():
            continue
        if not any(m in line for m in markers):
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            pids.add(int(parts[-1]))
        except Exception:
            pass
    return pids


def process_command_line(pid: int) -> str:
    if os.name != "nt":
        return ""
    try:
        out = subprocess.check_output(
            ["wmic", "process", "where", f"ProcessId={int(pid)}", "get", "CommandLine", "/value"],
            text=True,
            errors="ignore",
            creationflags=creation_flags(),
            timeout=4,
        )
    except Exception:
        return ""
    for raw in out.splitlines():
        line = raw.strip()
        if line.lower().startswith("commandline="):
            return line.split("=", 1)[1].strip()
    return ""


def process_looks_lobster(pid: int) -> bool:
    cmd = process_command_line(pid).lower()
    if not cmd:
        return False
    markers = (
        "lobster_online",
        "lobster-server",
        "lobster_server",
        "backend\\run.py",
        "backend/run.py",
        "run_mcp.bat",
        "run_backend.bat",
        "run_module('mcp'",
        'run_module("mcp"',
    )
    return any(m in cmd for m in markers)


def wait_port_closed(port: int, seconds: float = 6.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not port_open("127.0.0.1", port, timeout=0.2):
            return True
        time.sleep(0.25)
    return not port_open("127.0.0.1", port, timeout=0.2)


def kill_pid_tree(pid: int, name: str) -> None:
    if os.name == "nt":
        try:
            log(f"{name}: taskkill /T /F pid={pid}")
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                cwd=str(ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags(),
                timeout=8,
            )
            return
        except Exception as exc:
            log(f"{name}: taskkill failed pid={pid}: {exc}")
    try:
        os.kill(pid, 15)
    except Exception:
        pass


def stop_port_processes(port: int, name: str) -> None:
    pids = netstat_listening_pids(port)
    if not pids:
        return
    for pid in sorted(pids):
        kill_pid_tree(pid, name)
    wait_port_closed(port, 6.0)


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


def wait_for_own_backend(port: int, seconds: int) -> bool:
    health = f"http://127.0.0.1:{port}/api/health"
    deadline = time.time() + seconds
    while time.time() < deadline:
        data = http_json(health)
        if same_path(str(data.get("client_root") or ""), ROOT):
            return True
        time.sleep(0.8)
    return False


def port_owned_by_this_root(port: int) -> bool:
    data = http_json(f"http://127.0.0.1:{port}/api/health", timeout=0.8)
    return same_path(str(data.get("client_root") or ""), ROOT)


def choose_backend_port(preferred: int) -> int:
    if not port_open("127.0.0.1", preferred):
        return preferred
    pids = netstat_listening_pids(preferred)
    if port_owned_by_this_root(preferred):
        log(f"Backend: port {preferred} is occupied by previous process from this root; restarting it")
    elif any(process_looks_lobster(pid) for pid in pids):
        log(f"Backend: port {preferred} is occupied by another lobster process; stopping it to keep default port")
    else:
        log(f"Backend: port {preferred} is occupied by an unknown process; keep default port and report startup failure")
        return preferred
    stop_port_processes(preferred, "Backend")
    return preferred


def choose_mcp_port(preferred: int, backend_port: int) -> int:
    if not port_open("127.0.0.1", preferred):
        return preferred
    pids = netstat_listening_pids(preferred)
    if any(process_looks_lobster(pid) for pid in pids):
        log(f"MCP: port {preferred} is occupied by previous lobster MCP; stopping it to keep default port")
        stop_port_processes(preferred, "MCP")
    else:
        log(f"MCP: port {preferred} is occupied by an unknown process; keep default port and report startup failure")
    return preferred


def open_browser(url: str) -> None:
    log(f"opening system browser fallback: {url}")
    webbrowser.open(url)


def stop_process(proc: subprocess.Popen | None, name: str) -> None:
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        kill_pid_tree(int(proc.pid), name)
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


class DesktopApi:
    def save_asset_as(self, asset_id: str, suggested_name: str = "") -> dict:
        source = asset_file_for_id(asset_id)
        if not source:
            return {"ok": False, "error": "本机素材文件不存在"}
        try:
            import webview  # type: ignore

            window = webview.active_window()
            default_name = safe_filename(suggested_name or source.name, source.name)
            result = window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=default_name,
                file_types=("视频文件 (*.mp4;*.webm;*.mov;*.m4v)", "图片文件 (*.png;*.jpg;*.jpeg;*.webp)", "所有文件 (*.*)"),
            )
        except Exception as exc:
            log(f"save dialog failed: {exc}")
            return {"ok": False, "error": f"无法打开保存窗口：{exc}"}

        if not result:
            return {"ok": False, "cancelled": True}
        target_raw = result[0] if isinstance(result, (list, tuple)) else result
        target = Path(str(target_raw)).expanduser()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        except Exception as exc:
            log(f"save asset failed asset_id={asset_id} target={target}: {exc}")
            return {"ok": False, "error": f"保存失败：{exc}"}
        return {"ok": True, "path": str(target), "filename": target.name}


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
            min_size=(900, 640),
            text_select=True,
            js_api=DesktopApi(),
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
    configured_port = args.port or int(read_env_value("PORT", str(DEFAULT_PORT)) or DEFAULT_PORT)
    port = choose_backend_port(configured_port)
    configured_mcp_port = int(read_env_value("MCP_PORT", str(DEFAULT_MCP_PORT)) or DEFAULT_MCP_PORT)
    mcp_port = choose_mcp_port(configured_mcp_port, port)
    title = args.title or read_env_value("LOBSTER_DESKTOP_TITLE", APP_NAME)
    url = f"http://127.0.0.1:{port}/?desktop=1&v={int(time.time())}-{uuid.uuid4().hex[:8]}"
    env = build_env()
    env["PORT"] = str(port)
    env["MCP_PORT"] = str(mcp_port)

    log(f"launcher root={ROOT}")
    log(f"target url={url}")

    mcp_proc = None
    backend_proc = None
    mcp_proc = start_bat("MCP", "run_mcp.bat", env)
    time.sleep(1.2)

    if not wait_for_own_backend(port, 2):
        backend_proc = start_bat("Backend", "run_backend.bat", env)
    else:
        log(f"Backend: port {port} already ready")

    if not wait_for_own_backend(port, args.wait):
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

    window_width, window_height = adaptive_window_size(args.width, args.height)
    ok = run_window(url, title, window_width, window_height)
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
