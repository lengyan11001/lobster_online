#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services.runtime_dependency_repair import (  # noqa: E402
    RuntimeDependencyRepairBusy,
    repair_runtime_dependencies,
    runtime_dependency_repair_needed,
)


def _check_only() -> int:
    """启动期入口：只查不装。返回 0=齐，3=缺（由调用方提示去界面点修复）。"""
    from backend.app.services.runtime_dependency_repair import runtime_dependency_status

    status = runtime_dependency_status()
    # 只看「有没有真的缺东西」。repair marker 过期不代表有问题（它只说明上次没跑过完整 repair），
    # 启动期不该为它报警，否则每台机器开机都弹一条 WARN。
    if status.get("ok"):
        print("[runtime-repair] startup check: dependencies ready (no download)", flush=True)
        return 0
    print(
        "[runtime-repair] startup check: "
        + str(status.get("message") or "")
        + " -> not downloading at startup; click System Config - Repair Runtime Dependencies",
        flush=True,
    )
    return 3


def main() -> int:
    if "--check-only" in sys.argv:
        return _check_only()
    if "--force" not in sys.argv and not runtime_dependency_repair_needed():
        print("[runtime-repair] dependencies already verified", flush=True)
        return 0

    print("[runtime-repair] checking and repairing runtime dependencies", flush=True)
    try:
        result = repair_runtime_dependencies()
    except RuntimeDependencyRepairBusy as exc:
        print(f"[runtime-repair] {exc}", flush=True)
        return 2
    except Exception as exc:
        print(f"[runtime-repair] failed: {exc}", flush=True)
        return 1

    install = result.get("install") if isinstance(result.get("install"), dict) else {}
    if install:
        print(f"[runtime-repair] install: {install.get('message') or '-'}", flush=True)
    for item in result.get("checks") or []:
        if not isinstance(item, dict):
            continue
        state = "ok" if item.get("ok") else "failed"
        failures = ",".join(
            str(failure.get("module") or "")
            for failure in (item.get("failures") or [])
            if isinstance(failure, dict)
        )
        suffix = f" ({failures})" if failures else ""
        print(f"[runtime-repair] {item.get('label') or item.get('key')}: {state}{suffix}", flush=True)
    if not result.get("ok"):
        log_text = str(install.get("log") or result.get("pip_log") or "")
        if log_text:
            print(log_text, flush=True)
        print("[runtime-repair] repair incomplete", flush=True)
        return 1
    print(
        "[runtime-repair] completed in " + str(result.get("duration_seconds") or 0) + "s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
