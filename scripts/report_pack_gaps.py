#!/usr/bin/env python3
"""Validate dependencies required by the encrypted desktop distribution."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def _has_glob(path: Path, pattern: str) -> bool:
    return path.is_dir() and any(path.glob(pattern))


def main() -> int:
    print("\n=== lobster_online package dependency check (read-only) ===\n")
    rows: list[tuple[str, bool, str]] = []
    for bat in ("install.bat", "start.bat", "run_backend.bat", "run_mcp.bat"):
        rows.append((bat, (BASE / bat).is_file(), "required launcher"))
    verify = subprocess.run(
        [sys.executable, str(BASE / "scripts" / "verify_offline_wheels.py")],
        cwd=str(BASE), capture_output=True, text=True,
    )
    rows.extend([
        ("deps/wheels (requirements)", verify.returncode == 0, "verify_offline_wheels.py"),
        ("deps/wheels/pip-*.whl", _has_glob(BASE / "deps" / "wheels", "pip-*.whl"), ""),
        ("deps/get-pip.py", (BASE / "deps" / "get-pip.py").is_file(), ""),
        ("python/python.exe", (BASE / "python" / "python.exe").is_file(), "embedded Python"),
        ("nodejs/node.exe", (BASE / "nodejs" / "node.exe").is_file(), "portable Node for Douyin protocol"),
        ("nodejs/package-lock.json", (BASE / "nodejs" / "package-lock.json").is_file(), "metadata only; no npm runtime"),
        ("deps/wheels/pycryptodome*.whl", _has_glob(BASE / "deps" / "wheels", "pycryptodome*.whl"), ""),
        ("deps/wheels/tos-*", _has_glob(BASE / "deps" / "wheels", "tos-*"), ""),
        ("scripts/pip_bootstrap_from_wheel.py", (BASE / "scripts" / "pip_bootstrap_from_wheel.py").is_file(), ""),
    ])
    width = max(len(row[0]) for row in rows)
    for name, ok, hint in rows:
        print(f"  {name:<{width}}  [{'OK' if ok else 'MISSING'}]" + (f"  # {hint}" if hint else ""))
    critical = any(not ok for name, ok, _ in rows if name in {
        "deps/wheels (requirements)", "deps/wheels/pip-*.whl", "deps/get-pip.py",
        "python/python.exe", "nodejs/node.exe", "scripts/pip_bootstrap_from_wheel.py",
    })
    if critical:
        print("\nERROR: missing a required Python/Node dependency; complete preparation before packaging.")
        return 1
    print("\nConclusion: required dependencies are present; OpenClaw and its WeChat plugin are retired and intentionally absent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
