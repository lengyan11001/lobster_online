"""全局「暂停任务」控制：悬浮键 + 各引擎协作停机（2026-10-16）。

用户口径：微信/抖音这些自动化一旦跑起来，界面上没有统一的"停"，只能去结束进程。
所以这里做三件事：

1. **暂停标记文件**（data/task_control/stop.flag）：长任务在自己的循环里查它，
   发现被置位就**优雅收尾**（把已经读到/已经发出去的数据落库再退出），
   而不是被 kill 掉 —— 半途被杀会留下"读了一半的通讯录"和"发到一半的队列"。
2. **运行状态文件**（data/task_control/status.json）：谁在跑、跑了多久，悬浮键读它显示。
3. **悬浮键进程托管**：scripts/task_overlay.py（pywebview 置顶小窗），点一下就写暂停标记。

为什么用文件而不是 HTTP：悬浮窗不需要登录态/token，后端正忙（UI 线程被自动化占着）时
也能立刻生效，不需要等任何一个请求排队。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

ROOT_DIR = Path(__file__).resolve().parents[3]
STATE_DIR = ROOT_DIR / "data" / "task_control"
STOP_FLAG = STATE_DIR / "stop.flag"
STATUS_FILE = STATE_DIR / "status.json"
OVERLAY_PID_FILE = STATE_DIR / "overlay.pid"
OVERLAY_HEARTBEAT_FILE = STATE_DIR / "overlay_heartbeat.json"
OVERLAY_SCRIPT = ROOT_DIR / "scripts" / "task_overlay.py"

_logger = logging.getLogger(__name__)

# 任务跑够这么久才自动拉起悬浮键：免得"发一条消息"这种秒级操作也弹一下窗。
OVERLAY_AUTOSTART_AFTER_SECONDS = 6.0


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat()


def _ensure_dir() -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        _logger.debug("failed to create task_control dir", exc_info=True)


# ── 暂停标记 ─────────────────────────────────────────────────────────────
def request_stop(reason: str = "overlay") -> Dict[str, Any]:
    _ensure_dir()
    payload = {"at": _now_iso(), "reason": str(reason or "")[:120]}
    try:
        STOP_FLAG.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        _logger.debug("failed to write stop flag", exc_info=True)
    return {"ok": True, "stopped": True, **payload}


def stop_requested() -> bool:
    try:
        return STOP_FLAG.exists()
    except Exception:
        return False


def stop_payload() -> Dict[str, Any]:
    try:
        if not STOP_FLAG.exists():
            return {}
        data = json.loads(STOP_FLAG.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def clear_stop() -> Dict[str, Any]:
    try:
        if STOP_FLAG.exists():
            STOP_FLAG.unlink()
    except Exception:
        _logger.debug("failed to clear stop flag", exc_info=True)
    return {"ok": True, "stopped": False}


# ── 运行状态（悬浮键显示"谁在跑"）─────────────────────────────────────────
def begin_task(kind: str, label: str = "", account_id: str = "") -> Dict[str, Any]:
    _ensure_dir()
    payload = {
        "running": True,
        "kind": str(kind or "")[:64],
        "label": str(label or "")[:120],
        "account_id": str(account_id or "")[:160],
        "started_at": _now_iso(),
        "started_monotonic": time.monotonic(),
        "stopped": stop_requested(),
    }
    try:
        STATUS_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        _logger.debug("failed to write task status", exc_info=True)
    try:
        import threading

        threading.Thread(
            target=_overlay_watchdog, args=(payload["started_at"],), daemon=True,
            name="lobster-overlay-watchdog",
        ).start()
    except Exception:
        _logger.debug("failed to schedule overlay watchdog", exc_info=True)
    return payload


def _overlay_watchdog(started_at: str, *, after_seconds: Optional[float] = None) -> None:
    """任务开始后等一小会儿，如果它还在跑，就把悬浮键拉起来（短操作不打扰）。

    after_seconds 默认在**运行时**从模块常量取（不要写成默认参数：那样一改常量就不生效了，
    测试和现场调参都调不动）。
    """
    delay = OVERLAY_AUTOSTART_AFTER_SECONDS if after_seconds is None else float(after_seconds)
    try:
        time.sleep(max(0.05, float(delay)))
    except Exception:
        return
    try:
        current = task_status()
    except Exception:
        return
    if not current.get("running") or str(current.get("started_at") or "") != started_at:
        return
    try:
        ensure_overlay()
    except Exception:
        _logger.debug("overlay autostart failed", exc_info=True)


def heartbeat(patch: Optional[Dict[str, Any]] = None) -> None:
    """长任务偶尔调一下，把"还在跑"和进度写给悬浮键（失败不影响任务）。"""
    current = task_status()
    if not current.get("running"):
        return
    try:
        data = {**current, **(patch or {})}
        data["updated_at"] = _now_iso()
        STATUS_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        _logger.debug("failed to update task status", exc_info=True)


def end_task() -> Dict[str, Any]:
    _ensure_dir()
    payload = {"running": False, "finished_at": _now_iso()}
    try:
        STATUS_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        _logger.debug("failed to clear task status", exc_info=True)
    return payload


def task_status() -> Dict[str, Any]:
    data: Dict[str, Any] = {"running": False}
    try:
        if STATUS_FILE.exists():
            loaded = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
    except Exception:
        data = {"running": False}
    if data.get("running"):
        try:
            started = float(data.get("started_monotonic") or 0.0)
            data["elapsed_seconds"] = max(0.0, round(time.monotonic() - started, 1)) if started else 0.0
        except Exception:
            data["elapsed_seconds"] = 0.0
    data["stopped"] = stop_requested()
    data["stop"] = stop_payload()
    return data


# ── 悬浮键进程 ───────────────────────────────────────────────────────────
def overlay_command(*, idle_seconds: int = 8, port: int = 0) -> list[str]:
    argv = [
        sys.executable,
        str(OVERLAY_SCRIPT),
        "--root", str(ROOT_DIR),
        "--idle-seconds", str(int(idle_seconds)),
    ]
    if port:
        argv += ["--port", str(int(port))]
    return argv


def _overlay_pid() -> int:
    try:
        if OVERLAY_PID_FILE.exists():
            return int((OVERLAY_PID_FILE.read_text(encoding="utf-8") or "0").strip() or 0)
    except Exception:
        return 0
    return 0


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        import psutil  # type: ignore

        return psutil.pid_exists(int(pid))
    except Exception:
        try:
            os.kill(int(pid), 0)
            return True
        except Exception:
            return False


def overlay_heartbeat() -> Dict[str, Any]:
    """悬浮键（页面 JS）每次调 status() 就落一次盘 —— 证明"它活着、而且桥是通的"。"""
    _ensure_dir()
    previous: Dict[str, Any] = {}
    try:
        if OVERLAY_HEARTBEAT_FILE.exists():
            loaded = json.loads(OVERLAY_HEARTBEAT_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                previous = loaded
    except Exception:
        previous = {}
    payload = {
        "at": _now_iso(),
        "calls": int(previous.get("calls") or 0) + 1,
        "first_at": str(previous.get("first_at") or _now_iso()),
    }
    try:
        OVERLAY_HEARTBEAT_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        _logger.debug("failed to write overlay heartbeat", exc_info=True)
    return payload


def overlay_status() -> Dict[str, Any]:
    pid = _overlay_pid()
    heartbeat: Dict[str, Any] = {}
    try:
        if OVERLAY_HEARTBEAT_FILE.exists():
            loaded = json.loads(OVERLAY_HEARTBEAT_FILE.read_text(encoding="utf-8"))
            heartbeat = loaded if isinstance(loaded, dict) else {}
    except Exception:
        heartbeat = {}
    return {
        "running": _pid_alive(pid),
        "pid": pid,
        "script": str(OVERLAY_SCRIPT),
        "script_exists": OVERLAY_SCRIPT.exists(),
        "heartbeat_at": str(heartbeat.get("at") or ""),
        "heartbeat_calls": int(heartbeat.get("calls") or 0),
    }


def ensure_overlay(*, idle_seconds: int = 8, port: int = 0) -> Dict[str, Any]:
    """确保悬浮键在跑（已经在跑就直接返回）。起不来不影响主任务。"""
    status = overlay_status()
    if status.get("running"):
        return status
    if not OVERLAY_SCRIPT.exists():
        return {"running": False, "error": "overlay_script_missing", "script": str(OVERLAY_SCRIPT)}
    try:
        _ensure_dir()
        flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        proc = subprocess.Popen(
            overlay_command(idle_seconds=idle_seconds, port=port),
            cwd=str(ROOT_DIR),
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        OVERLAY_PID_FILE.write_text(str(int(proc.pid)), encoding="utf-8")
        return {"running": True, "pid": int(proc.pid), "spawned": True}
    except Exception as exc:
        _logger.debug("failed to spawn overlay", exc_info=True)
        return {"running": False, "error": str(exc)[:200]}


def stop_overlay() -> Dict[str, Any]:
    pid = _overlay_pid()
    if not _pid_alive(pid):
        return {"running": False, "pid": pid, "killed": False}
    killed = False
    try:
        import psutil  # type: ignore

        proc = psutil.Process(int(pid))
        for child in proc.children(recursive=True):
            try:
                child.terminate()
            except Exception:
                pass
        proc.terminate()
        killed = True
    except Exception:
        try:
            subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            killed = True
        except Exception:
            killed = False
    return {"running": False, "pid": pid, "killed": killed}


def stop_all(reason: str = "overlay") -> Dict[str, Any]:
    """全局暂停：先置位（所有循环马上能看见），再尽力通知各引擎自己的 stop。"""
    result = request_stop(reason)
    details: Dict[str, Any] = {}
    try:
        from . import native_whatsapp_engine  # type: ignore

        details["whatsapp"] = native_whatsapp_engine.request_stop()
    except Exception as exc:
        details["whatsapp"] = {"ok": False, "error": str(exc)[:160]}
    try:
        from . import native_telegram_engine  # type: ignore

        details["telegram"] = native_telegram_engine.login_cancel()
    except Exception as exc:
        details["telegram"] = {"ok": False, "error": str(exc)[:160]}
    return {**result, "engines": details, "status": task_status()}
