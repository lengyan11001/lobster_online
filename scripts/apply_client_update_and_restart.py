#!/usr/bin/env python3
"""Apply the existing client OTA after the desktop launcher exits, then relaunch."""
from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / ".updates" / "update_restart.log"


def _log(message: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")
    except OSError:
        pass


def _wait_for_process_exit(pid: int, timeout_seconds: int = 45) -> None:
    if pid <= 0:
        return
    if os.name == "nt":
        try:
            import ctypes

            synchronize = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, int(pid))
            if handle:
                try:
                    ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout_seconds * 1000))
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
                return
        except Exception as exc:
            _log(f"wait process handle failed: {exc}")
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.25)


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )


def _run_updater() -> None:
    updater = ROOT / "scripts" / "check_client_code_update.py"
    if not updater.is_file():
        _log(f"updater missing: {updater}")
        return
    env = os.environ.copy()
    env["CLIENT_CODE_UPDATE_STOP_SERVICES"] = "1"
    try:
        completed = subprocess.run(
            [sys.executable, str(updater)],
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=max(300, int(env.get("CLIENT_CODE_UPDATE_TIMEOUT_SECONDS") or 1800)),
            creationflags=_creation_flags(),
        )
        output = "\n".join((completed.stdout or "").splitlines()[-160:])
        _log(f"updater exit={completed.returncode}\n{output}")
    except Exception as exc:
        _log(f"updater failed: {exc}")


def _relaunch(launcher: str) -> None:
    target = Path(launcher).resolve() if launcher else ROOT / "必火智能AI.exe"
    if target.is_file():
        command = [str(target)]
    else:
        command = [sys.executable, str(ROOT / "desktop" / "launcher.py")]
    try:
        subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=_creation_flags(),
        )
        _log(f"relaunched: {command[0]}")
    except Exception as exc:
        _log(f"relaunch failed: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--launcher", default="")
    args = parser.parse_args()

    _log(f"scheduled by pid={args.parent_pid}")
    _wait_for_process_exit(args.parent_pid)
    time.sleep(0.8)
    try:
        _run_updater()
    finally:
        _relaunch(args.launcher)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
