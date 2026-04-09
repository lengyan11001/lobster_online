#!/usr/bin/env python3
"""
本机一次跑通抖音「视频 + 图文」发布页流程（dry_run=True，不点最终发布）。

需已登录的 browser_data profile，例如 browser_data/douyin_<昵称>。

  cd lobster_online
  python3 scripts/dryrun_douyin_both_flows.py --profile browser_data/douyin_123

会生成临时小 mp4 / png 并依次调用 DouyinDriver.publish(..., dry_run=True)。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(ROOT))


def _tiny_mp4(path: Path) -> None:
    b64 = (
        "AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAACMbW9vdgAAAGxtdmhk"
        "AAAAAAAAAAAAAAAAAAAAAAAD6AAAA+gAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAA"
        "AAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAIAAAIVdHJhawAA"
        "AFx0a2hkAAAAAAAAAAAAAAAAAAAAAAABAAAAAAAAA+gAAAAAAAAAAAAAAAAEAAAAA"
        "AAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAIAAAABAAAAAQAAAAAAJGVkdHMAAAAc"
        "ZWxzdAAAAAAAAAABAAAD6AAAA+gAAAAAAAEabWRpYQAAACBtZGhkAAAAAAAAAAAA"
        "AAAAAAAAAAAyAAAAMgAAVcQAAAAAAC1oZGxyAAAAAAAAAAB2aWRlAAAAAAAAAAAA"
        "AAAAAFZpZGVvSGFuZGxlcgAAAAE3bWluZgAAABR2bWhkAAAAAAAAAAAAAAAALGRp"
        "bmYAAAAcZHJlZgAAAAAAAAABAAAADHVybCAAAAABAAAAK3N0YmwAAAAVc3RzZAAA"
        "AAEAAAANYXZjMQAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAUYXZjQwEB/4QAF2JtZGF0AAAAAA=="
    )
    path.write_bytes(base64.b64decode(b64))


def _tiny_png(path: Path) -> None:
    # 1x1 PNG
    raw = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    path.write_bytes(raw)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--profile",
        required=True,
        help="绝对或相对 lobster_online 的 profile 目录，如 browser_data/douyin_xxx",
    )
    args = ap.parse_args()
    prof = Path(args.profile)
    if not prof.is_absolute():
        prof = (ROOT / prof).resolve()
    if not prof.is_dir():
        raise SystemExit(f"profile 不存在: {prof}")

    os.environ.setdefault("PLAYWRIGHT_BROWSER_CHANNEL", "chrome")

    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        v = tdir / "dry.mp4"
        p = tdir / "dry.png"
        _tiny_mp4(v)
        _tiny_png(p)

        from publisher.browser_pool import dryrun_douyin_upload_in_context

        print("=== 视频 dry_run ===", flush=True)
        r1 = await dryrun_douyin_upload_in_context(
            str(prof), str(v), title="dry视频", description="dry", tags="test"
        )
        print(r1, flush=True)

        print("=== 图文 dry_run ===", flush=True)
        r2 = await dryrun_douyin_upload_in_context(
            str(prof), str(p), title="dry图文", description="dry", tags="test"
        )
        print(r2, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
