#!/usr/bin/env python3
"""将 lobster_online 下 *.bat 换行统一为 CRLF（Windows cmd 对仅 LF 的批处理会解析失败）。

**不在打包脚本中调用**（打包只 zip，不修改 install.bat 等）。开发机或提交前可手动执行：

  python3 scripts/ensure_bat_crlf.py

只改行尾，不改批处理逻辑。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    n_files = 0
    n_changed = 0
    for p in sorted(ROOT.rglob("*.bat")):
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        n_files += 1
        text = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        out = text.replace(b"\n", b"\r\n")
        if raw != out:
            p.write_bytes(out)
            n_changed += 1
    print(f"[ensure_bat_crlf] scanned {n_files} .bat, rewrote {n_changed} to CRLF", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
