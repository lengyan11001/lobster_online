from __future__ import annotations

import argparse
import base64
import ctypes
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import json
import urllib.request
import webbrowser
from pathlib import Path
from ctypes import wintypes

if os.name == "nt":
    import winreg
else:
    winreg = None


APP_NAME = "必火智能AI"
DEFAULT_PORT = 8000
DEFAULT_MCP_PORT = 8001
LOADING_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>必火智能AI</title>
  <style>
    html, body {
      margin: 0;
      width: 100%;
      height: 100%;
      background: #f7f9fc;
      color: #102033;
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
    }
    .wrap {
      min-height: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .panel {
      width: 420px;
      max-width: calc(100vw - 48px);
      padding: 34px 36px;
      border: 1px solid #dbe4f2;
      border-radius: 12px;
      background: #fff;
      box-shadow: 0 18px 45px rgba(22, 40, 70, .12);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      font-size: 24px;
      font-weight: 700;
    }
    .brand img {
      width: 36px;
      height: 36px;
      object-fit: contain;
    }
    .text {
      margin-top: 18px;
      font-size: 15px;
      line-height: 1.7;
      color: #526173;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel">
      <div class="brand">
        <img src="__LOADING_MARK__" alt="">
        <span>必火AI员工</span>
      </div>
      <div class="text">正在打开客户端，请稍候...</div>
    </div>
  </div>
</body>
</html>"""


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
APP_ICON_PATH = ROOT / "static" / "bihu_box.ico"
LOADING_MARK_PATH = ROOT / "static" / "bihu_64.png"


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


def load_env_file(path: Path, env: dict[str, str], *, override: bool = True) -> list[str]:
    if not path.is_file():
        return []
    loaded: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    except Exception as exc:
        log(f"read env file failed path={path}: {exc}")
        return loaded
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if override or key not in env:
            env[key] = value
        loaded.append(key)
    return loaded


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
    loaded_oc_env = load_env_file(ROOT / "openclaw" / ".env", env)
    if loaded_oc_env:
        log(f"loaded openclaw .env keys: {', '.join(sorted(set(loaded_oc_env)))}")
    env.setdefault("OPENCLAW_CONFIG_PATH", str(ROOT / "openclaw" / "openclaw.json"))
    env.setdefault("OPENCLAW_STATE_DIR", str(ROOT / "openclaw"))
    env.setdefault("OPENCLAW_DISABLE_BONJOUR", "1")
    env.setdefault("LOBSTER_OPENCLAW_DISABLE_SLACK_STAGE", "1")
    env.setdefault("LOBSTER_OPENCLAW_DISABLE_MODEL_PRICING", "1")
    return env


def find_fixed_webview2_runtime() -> Path | None:
    base = ROOT / "desktop" / "webview2"
    candidates = [
        base / "fixed-runtime",
        base / "Microsoft.WebView2.FixedVersionRuntime",
    ]
    candidates.extend(sorted(base.glob("Microsoft.WebView2.FixedVersionRuntime.*.x64")))
    candidates.extend(sorted(base.glob("Microsoft.WebView2.FixedVersionRuntime.*")))
    for path in candidates:
        if not path.is_dir():
            continue
        exe = path / "msedgewebview2.exe"
        if exe.is_file():
            return path
        try:
            nested = next(path.rglob("msedgewebview2.exe"), None)
        except Exception:
            nested = None
        if nested is not None and nested.is_file():
            return nested.parent
    return None


def system_webview2_runtime_available() -> bool:
    if os.name != "nt" or winreg is None:
        return True
    keys = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
    ]
    for hive, subkey in keys:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _ = winreg.QueryValueEx(key, "pv")
            if str(value or "").strip():
                return True
        except OSError:
            continue
    return False


def webview2_runtime_available() -> bool:
    return find_fixed_webview2_runtime() is not None or system_webview2_runtime_available()


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
        for raw in out.splitlines():
            line = raw.strip()
            if line.lower().startswith("commandline="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    try:
        out = subprocess.check_output(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter \"ProcessId = {int(pid)}\").CommandLine",
            ],
            text=True,
            errors="ignore",
            creationflags=creation_flags(),
            timeout=6,
        )
        return out.strip()
    except Exception:
        pass
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


def bundled_python() -> str:
    py = ROOT / "python" / "python.exe"
    if py.is_file():
        return str(py)
    if _is_frozen():
        return ""
    return sys.executable


def run_client_code_update(env: dict[str, str]) -> None:
    script = ROOT / "scripts" / "check_client_code_update.py"
    if not script.is_file():
        return
    py = bundled_python()
    if not py:
        log("CodeUpdate: skipped because no bundled python is available in frozen launcher")
        return
    log("CodeUpdate: checking client code pack update")
    try:
        cp = subprocess.run(
            [py, str(script)],
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=180,
            creationflags=creation_flags(),
        )
        tail = "\n".join((cp.stdout or "").splitlines()[-20:])
        if tail:
            log("CodeUpdate output:\n" + tail)
        if cp.returncode:
            log(f"CodeUpdate: exited with code {cp.returncode}; continuing startup")
    except subprocess.TimeoutExpired:
        log("CodeUpdate: timeout after 180s; continuing startup")
    except Exception as exc:
        log(f"CodeUpdate: failed: {exc}; continuing startup")


def ensure_desktop_runtime(env: dict[str, str]) -> bool:
    try:
        import webview  # noqa: F401

        return True
    except Exception as exc:
        log(f"DesktopRuntime: pywebview unavailable before install: {exc}")

    req = ROOT / "desktop" / "requirements-desktop.txt"
    if not req.is_file():
        log(f"DesktopRuntime: missing {req}")
        return False
    py = bundled_python()
    wheels = ROOT / "desktop" / "wheels"
    wheel_args: list[str] = []
    if wheels.is_dir():
        wheel_args = ["--no-index", "--find-links", str(wheels)]
        log(f"DesktopRuntime: installing desktop requirements from local wheels {wheels}")
    else:
        log("DesktopRuntime: installing desktop requirements")
    try:
        cp = subprocess.run(
            [py, "-m", "pip", "install", *wheel_args, "-r", str(req)],
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=300,
            creationflags=creation_flags(),
        )
        tail = "\n".join((cp.stdout or "").splitlines()[-30:])
        if tail:
            log("DesktopRuntime output:\n" + tail)
        if cp.returncode:
            log(f"DesktopRuntime: pip exited with code {cp.returncode}")
            return False
    except subprocess.TimeoutExpired:
        log("DesktopRuntime: pip install timeout after 300s")
        return False
    except Exception as exc:
        log(f"DesktopRuntime: pip install failed: {exc}")
        return False

    try:
        import importlib

        importlib.invalidate_caches()
        import webview  # noqa: F401

        log("DesktopRuntime: pywebview ready after install")
        return True
    except Exception as exc:
        log(f"DesktopRuntime: pywebview still unavailable after install: {exc}")
        return False


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
    health = f"http://127.0.0.1:{port}/api/health?fast=1"
    deadline = time.time() + seconds
    while time.time() < deadline:
        data = http_json(health, timeout=2.5)
        if same_path(str(data.get("client_root") or ""), ROOT):
            return True
        time.sleep(0.8)
    return False


def port_owned_by_this_root(port: int) -> bool:
    data = http_json(f"http://127.0.0.1:{port}/api/health?fast=1", timeout=2.5)
    return same_path(str(data.get("client_root") or ""), ROOT)


def choose_backend_port(preferred: int) -> int:
    if not port_open("127.0.0.1", preferred):
        return preferred
    if wait_for_own_backend(preferred, 4):
        log(f"Backend: port {preferred} became ready during startup grace period")
        return preferred
    pids = netstat_listening_pids(preferred)
    lobster_pids = sorted(pid for pid in pids if process_looks_lobster(pid))
    if lobster_pids:
        log(
            f"Backend: port {preferred} is occupied by lobster process {lobster_pids}; "
            "waiting for it to finish startup"
        )
        if wait_for_own_backend(preferred, 6):
            log(f"Backend: port {preferred} existing lobster process is ready")
            return preferred
        log(f"Backend: existing lobster process on port {preferred} did not become ready; restarting it")
        stop_port_processes(preferred, "Backend")
        return preferred
    if port_owned_by_this_root(preferred):
        log(f"Backend: port {preferred} is occupied by previous process from this root; restarting it")
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


def open_legacy_browser_mode(port: int, mcp_port: int, env: dict[str, str], wait_seconds: int) -> int:
    ok, url, backend_proc, mcp_proc, _error = start_services_blocking(port, mcp_port, env, wait_seconds, ensure_runtime=False)
    if ok:
        open_browser(url)
        return 0
    stop_process(backend_proc, "Backend")
    stop_process(mcp_proc, "MCP")
    start_bat("LegacyStart", "start.bat", env)
    return 0


def start_services_blocking(
    port: int,
    mcp_port: int,
    env: dict[str, str],
    wait_seconds: int,
    ensure_runtime: bool = True,
) -> tuple[bool, str, subprocess.Popen | None, subprocess.Popen | None, str]:
    port = choose_backend_port(port)
    mcp_port = choose_mcp_port(mcp_port, port)
    env["PORT"] = str(port)
    env["MCP_PORT"] = str(mcp_port)
    ready_url = f"http://127.0.0.1:{port}/?desktop=1&v={int(time.time())}-{uuid.uuid4().hex[:8]}"
    log(f"target url={ready_url}")
    run_client_code_update(env)
    if ensure_runtime:
        ensure_desktop_runtime(env)
    mcp_proc = start_bat("MCP", "run_mcp.bat", env)
    time.sleep(1.2)
    backend_proc = None
    if not wait_for_own_backend(port, 2):
        backend_proc = start_bat("Backend", "run_backend.bat", env)
    else:
        log(f"Backend: port {port} already ready")
    ok = wait_for_own_backend(port, wait_seconds)
    return ok, ready_url, backend_proc, mcp_proc, (
        "" if ok else f"本机服务启动失败，请查看：{ROOT / 'backend.log'} / {ROOT / 'mcp.log'} / {LOG_PATH}"
    )


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


def desktop_loading_html(url: str) -> str:
    match = re.match(r"^(https?://[^/]+)", url)
    base = match.group(1) if match else "http://127.0.0.1:8000"
    mark_src = f"{base}/static/bihu_64.png"
    try:
        if LOADING_MARK_PATH.is_file():
            data = base64.b64encode(LOADING_MARK_PATH.read_bytes()).decode("ascii")
            mark_src = f"data:image/png;base64,{data}"
    except Exception as exc:
        log(f"load desktop loading mark failed: {exc}")
    return LOADING_HTML.replace("__LOADING_MARK__", mark_src)


def desktop_error_html(message: str) -> str:
    safe_message = (
        str(message or "本机服务启动失败")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <style>
    body {{ margin:0; font-family:"Microsoft YaHei","Segoe UI",Arial,sans-serif; background:#f7f9fc; color:#102033; }}
    .wrap {{ min-height:100vh; display:flex; align-items:center; justify-content:center; padding:36px; box-sizing:border-box; }}
    .panel {{ max-width:620px; background:#fff; border:1px solid #dbe4f2; border-radius:12px; padding:32px; box-shadow:0 18px 45px rgba(22,40,70,.12); }}
    h1 {{ margin:0 0 14px; font-size:24px; }}
    p {{ margin:0; line-height:1.8; color:#526173; white-space:pre-wrap; }}
  </style>
</head>
<body><div class="wrap"><div class="panel"><h1>本机服务启动失败</h1><p>{safe_message}</p></div></div></body>
</html>"""


class NativeLoadingWindow:
    """Small Win32 splash window shown before the real WebView is created."""

    def __init__(self, title: str) -> None:
        self.title = title
        self.hwnd = None
        self._wndproc = None
        self._font_title = None
        self._font_text = None
        self._class_name = f"LobsterLoadingWindow_{os.getpid()}"

    def show(self) -> bool:
        if os.name != "nt":
            return False
        try:
            import ctypes.wintypes as wt

            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            kernel32 = ctypes.windll.kernel32
            lresult = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
            wndproc_type = ctypes.WINFUNCTYPE(lresult, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [
                    ("style", wt.UINT),
                    ("lpfnWndProc", wndproc_type),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wt.HINSTANCE),
                    ("hIcon", wt.HANDLE),
                    ("hCursor", wt.HANDLE),
                    ("hbrBackground", wt.HANDLE),
                    ("lpszMenuName", wt.LPCWSTR),
                    ("lpszClassName", wt.LPCWSTR),
                ]

            WM_CLOSE = 0x0010
            WM_ERASEBKGND = 0x0014
            WM_PAINT = 0x000F
            WM_SETICON = 0x0080
            ICON_SMALL = 0
            ICON_BIG = 1
            IMAGE_ICON = 1
            IMAGE_BITMAP = 0
            LR_LOADFROMFILE = 0x0010
            WS_OVERLAPPED = 0x00000000
            WS_CAPTION = 0x00C00000
            WS_SYSMENU = 0x00080000
            WS_VISIBLE = 0x10000000
            WS_EX_TOOLWINDOW = 0x00000080
            COLOR_WINDOW = 5
            SW_SHOW = 5
            TRANSPARENT = 1
            DT_LEFT = 0x00000000
            DT_SINGLELINE = 0x00000020
            DT_VCENTER = 0x00000004
            DI_NORMAL = 0x0003
            CS_VREDRAW = 0x0001
            CS_HREDRAW = 0x0002

            class PAINTSTRUCT(ctypes.Structure):
                _fields_ = [
                    ("hdc", wt.HDC),
                    ("fErase", wt.BOOL),
                    ("rcPaint", wt.RECT),
                    ("fRestore", wt.BOOL),
                    ("fIncUpdate", wt.BOOL),
                    ("rgbReserved", ctypes.c_byte * 32),
                ]

            user32.DefWindowProcW.restype = lresult
            user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
            user32.CreateWindowExW.restype = wt.HWND
            user32.LoadImageW.restype = wt.HANDLE
            user32.GetSysColorBrush.restype = wt.HBRUSH
            user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
            user32.BeginPaint.restype = wt.HDC
            user32.BeginPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
            user32.EndPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
            user32.GetDC.restype = wt.HDC
            user32.GetDC.argtypes = [wt.HWND]
            user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
            user32.DrawTextW.argtypes = [wt.HDC, wt.LPCWSTR, ctypes.c_int, ctypes.POINTER(wt.RECT), wt.UINT]
            user32.DrawTextW.restype = ctypes.c_int
            user32.FillRect.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH]
            user32.DrawIconEx.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, wt.HANDLE, ctypes.c_int, ctypes.c_int, wt.UINT, wt.HBRUSH, wt.UINT]
            user32.InvalidateRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT), wt.BOOL]
            gdi32.CreateFontW.restype = wt.HFONT
            gdi32.SetBkMode.argtypes = [wt.HDC, ctypes.c_int]
            gdi32.SetTextColor.argtypes = [wt.HDC, wt.DWORD]
            gdi32.SelectObject.restype = wt.HANDLE
            gdi32.SelectObject.argtypes = [wt.HDC, wt.HANDLE]
            gdi32.DeleteObject.argtypes = [wt.HANDLE]
            gdi32.CreateCompatibleDC.restype = wt.HDC
            gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
            gdi32.DeleteDC.argtypes = [wt.HDC]
            gdi32.SetStretchBltMode.argtypes = [wt.HDC, ctypes.c_int]
            gdi32.StretchBlt.argtypes = [
                wt.HDC,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wt.HDC,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wt.DWORD,
            ]
            bg_brush = user32.GetSysColorBrush(COLOR_WINDOW)
            self._font_title = gdi32.CreateFontW(32, 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 5, 0, "Microsoft YaHei")
            self._font_text = gdi32.CreateFontW(20, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Microsoft YaHei")
            hicon = None
            if APP_ICON_PATH.is_file():
                hicon = user32.LoadImageW(None, str(APP_ICON_PATH), IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
            mark_icon = None
            if LOADING_MARK_PATH.is_file():
                mark_icon = user32.LoadImageW(None, str(LOADING_MARK_PATH), IMAGE_BITMAP, 64, 64, LR_LOADFROMFILE)

            def paint(hwnd, hdc):
                rect = wt.RECT()
                user32.GetClientRect(hwnd, ctypes.byref(rect))
                user32.FillRect(hdc, ctypes.byref(rect), bg_brush)
                if mark_icon:
                    memdc = gdi32.CreateCompatibleDC(hdc)
                    old_bitmap = gdi32.SelectObject(memdc, mark_icon)
                    gdi32.SetStretchBltMode(hdc, 4)
                    gdi32.StretchBlt(hdc, 32, 34, 40, 40, memdc, 0, 0, 64, 64, 0x00CC0020)
                    gdi32.SelectObject(memdc, old_bitmap)
                    gdi32.DeleteDC(memdc)
                elif hicon:
                    user32.DrawIconEx(hdc, 32, 34, hicon, 40, 40, 0, None, DI_NORMAL)
                gdi32.SetBkMode(hdc, TRANSPARENT)
                title_rect = wt.RECT(88, 28, 486, 76)
                text_rect = wt.RECT(34, 104, 486, 148)
                if self._font_title:
                    old_font = gdi32.SelectObject(hdc, self._font_title)
                    gdi32.SetTextColor(hdc, 0x00102033)
                    user32.DrawTextW(hdc, "必火AI员工", -1, ctypes.byref(title_rect), DT_LEFT | DT_SINGLELINE | DT_VCENTER)
                    gdi32.SelectObject(hdc, old_font)
                if self._font_text:
                    old_font = gdi32.SelectObject(hdc, self._font_text)
                    gdi32.SetTextColor(hdc, 0x00526173)
                    user32.DrawTextW(hdc, "正在加载本机服务，请稍候...", -1, ctypes.byref(text_rect), DT_LEFT | DT_SINGLELINE | DT_VCENTER)
                    gdi32.SelectObject(hdc, old_font)

            def paint_now(hwnd):
                hdc = user32.GetDC(hwnd)
                if hdc:
                    try:
                        paint(hwnd, hdc)
                    finally:
                        user32.ReleaseDC(hwnd, hdc)

            def wndproc(hwnd, msg, wparam, lparam):
                if msg == WM_CLOSE:
                    return 0
                if msg == WM_ERASEBKGND:
                    rect = wt.RECT()
                    user32.GetClientRect(hwnd, ctypes.byref(rect))
                    user32.FillRect(wparam, ctypes.byref(rect), bg_brush)
                    return 1
                if msg == WM_PAINT:
                    ps = PAINTSTRUCT()
                    hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
                    paint(hwnd, hdc)
                    user32.EndPaint(hwnd, ctypes.byref(ps))
                    return 0
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

            self._wndproc = wndproc_type(wndproc)
            hinst = kernel32.GetModuleHandleW(None)
            wc = WNDCLASSW()
            wc.style = CS_HREDRAW | CS_VREDRAW
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinst
            wc.hIcon = hicon
            wc.hCursor = user32.LoadCursorW(None, 32512)
            wc.hbrBackground = bg_brush
            wc.lpszClassName = self._class_name
            user32.RegisterClassW(ctypes.byref(wc))

            width, height = 520, 210
            screen_w = user32.GetSystemMetrics(0)
            screen_h = user32.GetSystemMetrics(1)
            x = max(0, int((screen_w - width) / 2))
            y = max(0, int((screen_h - height) / 2))
            self.hwnd = user32.CreateWindowExW(
                WS_EX_TOOLWINDOW,
                self._class_name,
                self.title,
                WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_VISIBLE,
                x,
                y,
                width,
                height,
                None,
                None,
                hinst,
                None,
            )
            if not self.hwnd:
                return False
            if hicon:
                user32.SendMessageW(self.hwnd, WM_SETICON, ICON_SMALL, hicon)
                user32.SendMessageW(self.hwnd, WM_SETICON, ICON_BIG, hicon)

            user32.ShowWindow(self.hwnd, SW_SHOW)
            user32.InvalidateRect(self.hwnd, None, True)
            user32.UpdateWindow(self.hwnd)
            paint_now(self.hwnd)
            self.pump()
            return True
        except Exception as exc:
            log(f"native loading window failed: {exc}")
            return False

    def pump(self) -> None:
        if os.name != "nt":
            return
        try:
            import ctypes.wintypes as wt

            msg = wt.MSG()
            user32 = ctypes.windll.user32
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            pass

    def close(self) -> None:
        if os.name != "nt":
            return
        try:
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            if self.hwnd:
                user32.DestroyWindow(self.hwnd)
                self.hwnd = None
            if self._font_title:
                gdi32.DeleteObject(self._font_title)
                self._font_title = None
            if self._font_text:
                gdi32.DeleteObject(self._font_text)
                self._font_text = None
            self.pump()
        except Exception:
            pass


def start_native_loading_thread(title: str) -> threading.Event:
    stop_event = threading.Event()
    ready_event = threading.Event()

    def runner() -> None:
        splash = NativeLoadingWindow(title)
        splash.show()
        ready_event.set()
        try:
            while not stop_event.is_set():
                splash.pump()
                time.sleep(0.05)
        finally:
            splash.close()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    ready_event.wait(1.5)
    return stop_event


def start_services_with_loading(
    port: int,
    mcp_port: int,
    env: dict[str, str],
    wait_seconds: int,
    title: str,
) -> tuple[bool, str, subprocess.Popen | None, subprocess.Popen | None, str]:
    result: dict[str, object] = {"done": False}

    def worker() -> None:
        try:
            ok, actual_url, backend_proc, mcp_proc, error = start_services_blocking(port, mcp_port, env, wait_seconds)
            result.update(
                {
                    "done": True,
                    "ok": ok,
                    "url": actual_url,
                    "backend_proc": backend_proc,
                    "mcp_proc": mcp_proc,
                    "error": error,
                }
            )
        except Exception as exc:
            result.update({"done": True, "ok": False, "url": "", "backend_proc": None, "mcp_proc": None, "error": str(exc)})

    splash = NativeLoadingWindow(title)
    splash.show()
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    while not result.get("done"):
        splash.pump()
        time.sleep(0.05)
    splash.close()
    return (
        bool(result.get("ok")),
        str(result.get("url") or ""),
        result.get("backend_proc") if isinstance(result.get("backend_proc"), subprocess.Popen) else None,
        result.get("mcp_proc") if isinstance(result.get("mcp_proc"), subprocess.Popen) else None,
        str(result.get("error") or ""),
    )


def run_window(url: str, title: str, width: int, height: int, port: int, mcp_port: int, env: dict[str, str], wait_seconds: int) -> tuple[bool, subprocess.Popen | None, subprocess.Popen | None]:
    if not webview2_runtime_available():
        log("WebView2 runtime unavailable; using browser fallback")
        return False, None, None
    ensure_desktop_runtime(env)
    try:
        import webview  # type: ignore
    except Exception as exc:
        log(f"pywebview unavailable: {exc}")
        return False, None, None
    fixed_runtime = find_fixed_webview2_runtime()
    if fixed_runtime is not None:
        try:
            webview.settings["WEBVIEW2_RUNTIME_PATH"] = str(fixed_runtime)
            log(f"using bundled fixed WebView2 runtime: {fixed_runtime}")
        except Exception as exc:
            log(f"set bundled fixed WebView2 runtime failed: {exc}")

    runtime: dict[str, object] = {"backend_proc": None, "mcp_proc": None}
    try:
        def start_services_then_load(window) -> None:
            try:
                ok, actual_url, backend_proc, mcp_proc, error = start_services_blocking(port, mcp_port, env, wait_seconds, ensure_runtime=False)
                runtime["backend_proc"] = backend_proc
                runtime["mcp_proc"] = mcp_proc
                if ok:
                    window.load_url(actual_url)
                    log("webview client page loaded")
                else:
                    log(f"webview service startup failed: {error}")
                    window.load_html(
                        desktop_error_html(error or "本机服务启动失败，请查看日志。"),
                        base_uri=str(ROOT),
                    )
            except Exception as exc:
                log(f"webview startup workflow failed: {exc}")
                try:
                    window.load_html(desktop_error_html(str(exc)), base_uri=str(ROOT))
                except Exception:
                    pass

        window = webview.create_window(
            title,
            html=desktop_loading_html(url),
            width=width,
            height=height,
            min_size=(900, 640),
            text_select=True,
            js_api=DesktopApi(),
        )
        webview.start(
            start_services_then_load,
            [window],
            gui="edgechromium" if os.name == "nt" else None,
            debug=False,
            private_mode=False,
            storage_path=str(ROOT / "browser_data" / "desktop_webview"),
            icon=str(APP_ICON_PATH) if APP_ICON_PATH.is_file() else None,
        )
        return True, runtime.get("backend_proc") if isinstance(runtime.get("backend_proc"), subprocess.Popen) else None, runtime.get("mcp_proc") if isinstance(runtime.get("mcp_proc"), subprocess.Popen) else None
    except Exception as exc:
        log(f"webview start failed: {exc}")
        return False, None, None


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
    env["PORT"] = str(port)
    env["MCP_PORT"] = str(mcp_port)

    log(f"launcher root={ROOT}")

    if args.browser:
        return open_legacy_browser_mode(port, mcp_port, env, args.wait)

    window_width, window_height = adaptive_window_size(args.width, args.height)
    ok, backend_proc, mcp_proc = run_window(url, title, window_width, window_height, port, mcp_port, env, args.wait)
    if not ok:
        return open_legacy_browser_mode(port, mcp_port, env, args.wait)

    stop_process(backend_proc, "Backend")
    stop_process(mcp_proc, "MCP")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
