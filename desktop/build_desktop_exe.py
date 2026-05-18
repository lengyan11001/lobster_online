from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


APP_NAME = "必火AI员工"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    python = root / "python" / "python.exe"
    py = str(python) if python.is_file() else sys.executable

    req = root / "desktop" / "requirements-desktop.txt"
    subprocess.check_call([py, "-m", "pip", "install", "-r", str(req)], cwd=str(root))

    icon = root / "static" / "bihu_box.ico"
    env_path = root / ".env"
    if env_path.is_file():
        for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not raw or raw.lstrip().startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            if key.strip().upper() == "LOBSTER_BRAND_MARK" and value.strip().lower() == "yingshi":
                yingshi_icon = root / "static" / "yingshi_box.ico"
                if yingshi_icon.is_file():
                    icon = yingshi_icon
                break

    cmd = [
        py,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        APP_NAME,
        "--icon",
        str(icon),
        str(root / "desktop" / "launcher.py"),
    ]
    subprocess.check_call(cmd, cwd=str(root))

    out = root / "dist" / f"{APP_NAME}.exe"
    print()
    print(f"[desktop] Built: {out}")
    print(f"[desktop] Copy {out.name} to project root before packaging/installing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
