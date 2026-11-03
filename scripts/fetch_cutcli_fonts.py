#!/usr/bin/env python3
"""Fetch CutCLI template fonts from TOS into static/fonts.

Resources are build artifacts, not Git content. Run this before packaging/OTA
on a clean checkout so the preview @font-face and local ffmpeg renderer work.
"""
from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

BASE = "https://lobster-online-assets-2103871705.tos-cn-guangzhou.volces.com/assets/cutcli_templates/fonts"
FONTS = {
    "AlibabaPuHuiTi-3-105-Heavy.otf": "f2e848f94ac746f5a51ef1d71caea5cc268c920a9a63740423ee61abeac89bd6",
    "AlibabaPuHuiTi-3-95-ExtraBold.otf": "a8fb15c8685836d5041a4c47f2917fd57117182aff01350ea0335b216cc7993e",
    "AlibabaPuHuiTi-3-75-SemiBold.otf": "fd789c70fa4f1a265fe11a64d29bb48926aef7e14da338cb5ca5e6c359ae7e72",
    "AlibabaPuHuiTi-3-115-Black.otf": "3832ce0df4aa0d5493c759e6b16e128efc884950ceeb8e047a6443479816d921",
    "AlibabaPuHuiTi-3-65-Medium.otf": "00cd19ccd391bba61515361eea87163ba079c4bf691fa63c59e280fda690ada4",
}
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "static" / "fonts"


def valid(path: Path, sha: str) -> bool:
    if not path.is_file() or path.stat().st_size < 10000:
        return False
    return hashlib.sha256(path.read_bytes()).hexdigest().lower() == sha


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, sha in FONTS.items():
        target = OUT / name
        if valid(target, sha):
            print("ok", name)
            continue
        url = f"{BASE}/{name}"
        tmp = target.with_suffix(target.suffix + ".part")
        with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as fh:
            fh.write(resp.read())
        if not valid(tmp, sha):
            tmp.unlink(missing_ok=True)
            raise SystemExit(f"sha256 mismatch: {name}")
        tmp.replace(target)
        print("downloaded", name, target.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
