from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import threading
import time
from importlib import metadata
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SAM3D_REQUIREMENTS: tuple[str, ...] = (
    "torch==2.13.0",
    "torchvision==0.28.0",
    "opencv-python==5.0.0.93",
    "segment-anything==1.0",
)
SAM3D_MODULES: tuple[tuple[str, str], ...] = (
    ("torch", "torch"),
    ("torchvision", "torchvision"),
    ("opencv-python", "cv2"),
    ("segment-anything", "segment_anything"),
)
_INSTALL_TIMEOUT_SECONDS = 30 * 60
_STATE_LOCK = threading.Lock()
_INSTALL_THREAD: threading.Thread | None = None
_STATE: dict[str, Any] = {
    "status": "idle",
    "progress": 0,
    "message": "尚未检测 3D 拆件依赖",
    "error": "",
    "log": "",
}


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _package_version(distribution_name: str) -> str:
    try:
        return metadata.version(distribution_name)
    except metadata.PackageNotFoundError:
        return ""


def _dependency_rows() -> list[dict[str, Any]]:
    return [
        {
            "package": distribution_name,
            "module": module_name,
            "installed": _module_available(module_name),
            "version": _package_version(distribution_name),
        }
        for distribution_name, module_name in SAM3D_MODULES
    ]


def sam3d_runtime_status() -> dict[str, Any]:
    dependencies = _dependency_rows()
    ready = all(bool(item["installed"]) for item in dependencies)
    with _STATE_LOCK:
        state = dict(_STATE)
        installing = bool(_INSTALL_THREAD and _INSTALL_THREAD.is_alive())
    if ready and not installing:
        state.update(
            {
                "status": "ready",
                "progress": 100,
                "message": "3D 拆件依赖已安装",
                "error": "",
            }
        )
    elif not installing and state.get("status") == "ready":
        state.update(
            {
                "status": "idle",
                "progress": 0,
                "message": "3D 拆件依赖未完整安装",
            }
        )
    state["ready"] = ready
    state["installing"] = installing
    state["dependencies"] = dependencies
    state["requirements"] = list(SAM3D_REQUIREMENTS)
    return state


def _set_state(**updates: Any) -> None:
    with _STATE_LOCK:
        _STATE.update(updates)


def _wheel_dirs() -> list[Path]:
    candidates = (
        ROOT / "scripts" / "sam3d_runtime_wheels",
        ROOT / "deps" / "wheels",
    )
    return [path for path in candidates if path.is_dir() and any(path.iterdir())]


def _tail(text: str, limit: int = 100) -> str:
    lines = [line.rstrip() for line in str(text or "").splitlines() if line.strip()]
    return "\n".join(lines[-limit:])


def _install_worker() -> None:
    wheel_dirs = _wheel_dirs()
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--prefer-binary",
    ]
    for wheel_dir in wheel_dirs:
        command.extend(("--find-links", str(wheel_dir)))
    command.extend(SAM3D_REQUIREMENTS)
    source = "优先使用本地依赖包" if wheel_dirs else "联网下载"
    _set_state(
        status="installing",
        progress=12,
        message=f"正在安装 3D 拆件依赖（{source}）",
        error="",
        log="",
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )
    try:
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=max(
                300,
                int(os.environ.get("SAM3D_RUNTIME_INSTALL_TIMEOUT_SECONDS") or _INSTALL_TIMEOUT_SECONDS),
            ),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        output = _tail(completed.stdout or "")
        if completed.returncode:
            raise RuntimeError(f"pip 安装失败，退出码 {completed.returncode}")
        _set_state(progress=82, message="依赖已下载，正在验证运行环境", log=output)
        verify_code = "import torch, torchvision, cv2, segment_anything"
        verified = subprocess.run(
            [sys.executable, "-c", verify_code],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=180,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        verify_output = _tail(verified.stdout or "")
        if verified.returncode:
            raise RuntimeError(f"依赖验证失败：{verify_output or '模块无法导入'}")
        _set_state(
            status="ready",
            progress=100,
            message="3D 拆件依赖安装完成",
            error="",
            log=_tail("\n".join(item for item in (output, verify_output) if item)),
            finished_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        _set_state(
            status="failed",
            progress=0,
            message="3D 拆件依赖安装超时",
            error="安装超过 30 分钟，请检查网络后重试",
            log=_tail(output),
        )
    except Exception as exc:
        current_log = ""
        with _STATE_LOCK:
            current_log = str(_STATE.get("log") or "")
        _set_state(
            status="failed",
            progress=0,
            message="3D 拆件依赖安装失败",
            error=str(exc),
            log=current_log,
        )


def start_sam3d_runtime_install() -> dict[str, Any]:
    global _INSTALL_THREAD
    current = sam3d_runtime_status()
    if current["ready"] or current["installing"]:
        return current
    with _STATE_LOCK:
        if _INSTALL_THREAD and _INSTALL_THREAD.is_alive():
            return sam3d_runtime_status()
        _STATE.update(
            {
                "status": "installing",
                "progress": 4,
                "message": "正在准备 3D 拆件依赖安装",
                "error": "",
                "log": "",
            }
        )
        _INSTALL_THREAD = threading.Thread(
            target=_install_worker,
            name="sam3d-runtime-install",
            daemon=True,
        )
        _INSTALL_THREAD.start()
    return sam3d_runtime_status()
