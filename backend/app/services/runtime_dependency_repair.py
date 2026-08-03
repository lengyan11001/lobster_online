from __future__ import annotations

import os
import hashlib
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
_REPAIR_LOCK = threading.Lock()
_REPAIR_MARKER = ROOT / ".updates" / "runtime_dependency_repair.json"
_REPAIR_SCHEMA_VERSION = 1
_TKINTER_STUB = '''"""Minimal tkinter stub for the embedded Lobster runtime."""

class TclError(RuntimeError):
    pass

class Tk:
    def __init__(self, *args, **kwargs):
        raise TclError("tkinter UI is not bundled in this runtime")

class Toplevel(Tk):
    pass

END = "end"
'''

_IMPORT_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "core",
        "核心服务",
        ("fastapi", "uvicorn", "pydantic", "httpx", "sqlalchemy", "playwright", "greenlet", "PIL", "tos"),
    ),
    ("desktop", "桌面窗口", ("webview",)),
    ("ppt", "PPT 创作", ("pptx", "svglib", "reportlab", "yaml", "lxml", "rich", "typer", "openai")),
    ("sam3d", "3D component split / SAM", ("torch", "torchvision", "cv2", "segment_anything")),
    (
        "douyin",
        "抖音能力",
        ("requests", "playwright.async_api", "execjs", "google.protobuf", "pandas", "openpyxl", "pymysql", "websockets.sync.client"),
    ),
    ("wechat", "微信能力", ("wxauto4", "uiautomation", "win32gui", "pywinauto", "pyperclip", "comtypes")),
)


class RuntimeDependencyRepairBusy(RuntimeError):
    pass


def _repair_fingerprint() -> str:
    digest = hashlib.sha256()
    digest.update(f"schema={_REPAIR_SCHEMA_VERSION}\npython={sys.version.split()[0]}\n".encode("utf-8"))
    for rel in ("requirements.txt", "desktop/requirements-desktop.txt"):
        path = ROOT / rel
        digest.update(f"path={rel}\n".encode("utf-8"))
        if path.is_file():
            digest.update(path.read_bytes())
    for key, _label, modules in _IMPORT_GROUPS:
        digest.update(f"group={key}:{','.join(modules)}\n".encode("utf-8"))
    return digest.hexdigest()


def runtime_dependency_repair_needed() -> bool:
    if not _REPAIR_MARKER.is_file():
        return True
    try:
        data = json.loads(_REPAIR_MARKER.read_text(encoding="utf-8"))
        return str(data.get("fingerprint") or "") != _repair_fingerprint()
    except Exception:
        return True


def _save_repair_marker(result: dict[str, Any]) -> None:
    _REPAIR_MARKER.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": _REPAIR_SCHEMA_VERSION,
        "fingerprint": _repair_fingerprint(),
        "python": sys.version.split()[0],
        "repaired_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": str((result.get("install") or {}).get("source") or ""),
    }
    temp = _REPAIR_MARKER.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(_REPAIR_MARKER)


def _creation_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _run(command: list[str], timeout: int) -> tuple[int, str]:
    try:
        cp = subprocess.run(
            command,
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            creationflags=_creation_flags(),
        )
        return int(cp.returncode), cp.stdout or ""
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return 124, f"{output}\nCommand timed out after {timeout} seconds."
    except Exception as exc:
        return 1, str(exc)


def _tail(output: str, limit: int = 80) -> str:
    lines = [line.rstrip() for line in (output or "").splitlines() if line.strip()]
    return "\n".join(lines[-limit:])


def _ensure_tkinter_stub() -> bool:
    runtime_root = ROOT / "python"
    bundled_python = runtime_root / "python.exe"
    if os.name != "nt" or not bundled_python.is_file():
        return False
    if (runtime_root / "DLLs" / "_tkinter.pyd").is_file():
        return False
    stub_file = runtime_root / "tkinter" / "__init__.py"
    previous = stub_file.read_text(encoding="utf-8") if stub_file.is_file() else ""
    if previous == _TKINTER_STUB:
        return False
    stub_file.parent.mkdir(parents=True, exist_ok=True)
    stub_file.write_text(_TKINTER_STUB, encoding="utf-8")
    return True


def _ensure_pip(timeout: int) -> tuple[bool, str]:
    code, output = _run([sys.executable, "-m", "pip", "--version"], min(timeout, 60))
    if code == 0:
        return True, _tail(output, 10)

    bootstrap = ROOT / "scripts" / "pip_bootstrap_from_wheel.py"
    attempts: list[str] = []
    if bootstrap.is_file():
        code, output = _run([sys.executable, str(bootstrap)], min(timeout, 300))
        attempts.append(_tail(output, 30))
        if code == 0:
            check_code, check_output = _run([sys.executable, "-m", "pip", "--version"], 60)
            if check_code == 0:
                return True, _tail("\n".join(attempts + [check_output]), 40)

    code, output = _run([sys.executable, "-m", "ensurepip", "--default-pip"], min(timeout, 300))
    attempts.append(_tail(output, 30))
    if code == 0:
        check_code, check_output = _run([sys.executable, "-m", "pip", "--version"], 60)
        if check_code == 0:
            return True, _tail("\n".join(attempts + [check_output]), 40)
    return False, _tail("\n".join(attempts), 60) or "pip 不可用"


def _wheel_dirs() -> list[Path]:
    candidates = (
        ROOT / "deps" / "wheels",
        ROOT / "scripts" / "ppt_runtime_wheels",
        ROOT / "scripts" / "douyin_runtime_wheels",
        ROOT / "scripts" / "wechat_runtime_wheels",
        ROOT / "desktop" / "wheels",
    )
    return [path for path in candidates if path.is_dir() and any(path.iterdir())]


def _install_requirements(timeout: int) -> dict[str, Any]:
    requirements = ROOT / "requirements.txt"
    if not requirements.is_file():
        return {"ok": False, "source": "none", "message": "未找到 requirements.txt", "log": ""}

    wheel_dirs = _wheel_dirs()
    base = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    offline = [*base, "--no-index"]
    for wheel_dir in wheel_dirs:
        offline.extend(["--find-links", str(wheel_dir)])
    offline.extend(["-r", str(requirements)])

    offline_code, offline_output = _run(offline, timeout)
    if offline_code == 0:
        return {
            "ok": True,
            "source": "offline",
            "message": "已使用本地依赖包完成修复",
            "log": _tail(offline_output),
        }

    if str(os.environ.get("LOBSTER_OFFLINE_ONLY") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return {
            "ok": False,
            "source": "offline",
            "message": "本地依赖包不完整，当前配置禁止联网补齐",
            "log": _tail(offline_output),
        }

    online = [*base, "--prefer-binary", "-r", str(requirements)]
    online_code, online_output = _run(online, timeout)
    combined = _tail(f"[offline]\n{offline_output}\n[online]\n{online_output}")
    if online_code == 0:
        return {
            "ok": True,
            "source": "online",
            "message": "本地依赖包不完整，已联网补齐",
            "log": combined,
        }
    return {
        "ok": False,
        "source": "online",
        "message": "依赖安装失败，请展开日志查看具体缺失项",
        "log": combined,
    }


def _verify_import(module_name: str) -> tuple[bool, str]:
    command = [sys.executable, "-c", f"import importlib; importlib.import_module({module_name!r})"]
    code, output = _run(command, 60)
    return code == 0, _tail(output, 20)


def _verify_groups() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for key, label, modules in _IMPORT_GROUPS:
        failures: list[dict[str, str]] = []
        for module_name in modules:
            ok, output = _verify_import(module_name)
            if not ok:
                failures.append({"module": module_name, "error": output or "导入失败"})
        results.append(
            {
                "key": key,
                "label": label,
                "ok": not failures,
                "message": "正常" if not failures else f"{len(failures)} 项仍不可用",
                "failures": failures,
            }
        )
    return results


def repair_runtime_dependencies() -> dict[str, Any]:
    if not _REPAIR_LOCK.acquire(blocking=False):
        raise RuntimeDependencyRepairBusy("依赖修复正在运行，请等待当前任务完成")
    started = time.monotonic()
    try:
        timeout = max(300, int(os.environ.get("CLIENT_RUNTIME_REPAIR_TIMEOUT_SECONDS") or 1200))
        tkinter_stub_created = _ensure_tkinter_stub()
        pip_ok, pip_log = _ensure_pip(timeout)
        if not pip_ok:
            return {
                "ok": False,
                "message": "pip 修复失败",
                "python": sys.version.split()[0],
                "pip_log": pip_log,
                "checks": [],
                "duration_seconds": round(time.monotonic() - started, 1),
            }

        install_result = _install_requirements(timeout)
        checks = _verify_groups()
        ok = bool(install_result.get("ok")) and all(bool(item.get("ok")) for item in checks)
        result = {
            "ok": ok,
            "message": "运行依赖已修复" if ok else "部分运行依赖仍不可用",
            "python": sys.version.split()[0],
            "tkinter_stub_created": tkinter_stub_created,
            "install": install_result,
            "checks": checks,
            "restart_recommended": bool(install_result.get("ok")),
            "duration_seconds": round(time.monotonic() - started, 1),
        }
        if ok:
            try:
                _save_repair_marker(result)
                result["marker_saved"] = True
            except Exception as exc:
                result["marker_saved"] = False
                result["marker_error"] = str(exc)
        return result
    finally:
        _REPAIR_LOCK.release()
