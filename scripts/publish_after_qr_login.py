#!/usr/bin/env python3
"""
先打开可见浏览器 → 你在抖音创作者首页扫码登录 → 倒计时结束后自动用「在线素材库」本地文件走抖音发布自动化。

素材解析（禁止静默乱选）：
  1) 若指定 --asset-id，用 DB 中该条目的 filename + assets/ 目录
  2) 否则：DB 中按 created_at 最新一条且本地文件存在的素材
  3) 若 DB 无匹配：报错退出（不随便猜目录）

用法（在 lobster_online 根目录）:
  python3 scripts/publish_after_qr_login.py --nickname 123

指定素材与等待扫码时间:
  python3 scripts/publish_after_qr_login.py --nickname 123 --asset-id 0e098318d007 --wait 240

环境变量:
  PLAYWRIGHT_CHROMIUM_PATH  可选，指定 Chromium
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from pathlib import Path
from typing import Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_SCRIPTS_DIR))

from playwright_lobster_env import ensure_playwright_browsers_path

HOME_URL = "https://creator.douyin.com/creator-micro/home"
ASSETS_DIR = ROOT / "assets"
DB_PATH = ROOT / "lobster.db"


def _chromium_path() -> str:
    return os.environ.get("PLAYWRIGHT_CHROMIUM_PATH", "").strip()


def _pick_asset_file(asset_id: Optional[str]) -> Tuple[str, str]:
    """返回 (绝对路径, 说明)。"""
    if not DB_PATH.is_file():
        raise SystemExit(f"未找到数据库 {DB_PATH}（在线版素材依赖此库）")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        if asset_id and asset_id.strip():
            row = conn.execute(
                "SELECT filename, asset_id FROM assets WHERE asset_id = ? LIMIT 1",
                (asset_id.strip(),),
            ).fetchone()
            if not row:
                raise SystemExit(f"素材库中无 asset_id={asset_id!r}")
            fname, aid = row[0], row[1]
            path = ASSETS_DIR / fname
            if not path.is_file():
                raise SystemExit(f"本地文件不存在: {path}（请先确认素材已下载到 assets/）")
            return str(path.resolve()), f"asset_id={aid} file={fname}"

        rows = conn.execute(
            "SELECT filename, asset_id, media_type FROM assets ORDER BY created_at DESC LIMIT 80"
        ).fetchall()
    finally:
        conn.close()

    for fname, aid, _mt in rows:
        path = ASSETS_DIR / (fname or "")
        if fname and path.is_file():
            return str(path.resolve()), f"最新可用 asset_id={aid} file={fname}"

    raise SystemExit(
        "素材库无已落盘的本地文件：请先在在线版生成/保存素材，或指定 --asset-id。"
    )


async def _wait_qr_phase(profile_dir: Path, wait_sec: int) -> None:
    ensure_playwright_browsers_path(ROOT)
    from playwright.async_api import async_playwright

    profile_dir.mkdir(parents=True, exist_ok=True)
    launch_kwargs: dict = {
        "headless": False,
        "viewport": {"width": 1400, "height": 900},
        "locale": "zh-CN",
    }
    exe = _chromium_path()
    if exe and Path(exe).exists():
        launch_kwargs["executable_path"] = exe

    print("=" * 60, flush=True)
    print("Profile:", profile_dir, flush=True)
    print(f"请在 {wait_sec} 秒内完成抖音扫码登录（当前页为创作者首页）。", flush=True)
    print("=" * 60, flush=True)

    async with async_playwright() as p:
        try:
            ctx = await p.chromium.launch_persistent_context(
                str(profile_dir), **launch_kwargs
            )
        except Exception as e:
            msg = str(e).lower()
            if "executable" in msg and "doesn't exist" in msg:
                kw = {k: v for k, v in launch_kwargs.items() if k != "executable_path"}
                kw["channel"] = "chrome"
                ctx = await p.chromium.launch_persistent_context(str(profile_dir), **kw)
            else:
                raise
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=120000)
            for i in range(wait_sec):
                if i and i % 30 == 0:
                    print(f"… 剩余约 {wait_sec - i} 秒", flush=True)
                await asyncio.sleep(1)
        finally:
            await ctx.close()


async def _run_publish(
    profile_dir: Path, file_path: str, title: str, description: str, tags: str
) -> dict:
    from publisher.browser_pool import run_publish_task

    return await run_publish_task(
        str(profile_dir.resolve()),
        "douyin",
        file_path,
        title,
        description,
        tags,
        options={},
        cover_path=None,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nickname", required=True, help="发布账号昵称，对应 browser_data/douyin_<nickname>")
    ap.add_argument("--asset-id", default="", help="可选，素材库 asset_id")
    ap.add_argument("--wait", type=int, default=180, help="扫码等待秒数（默认 180）")
    ap.add_argument("--title", default="素材库自动发布测试", help="发布标题")
    ap.add_argument("--description", default="", help="描述（可空）")
    ap.add_argument("--tags", default="", help="标签，逗号分隔")
    args = ap.parse_args()

    profile = ROOT / "browser_data" / f"douyin_{args.nickname.strip()}"
    file_path, picked = _pick_asset_file(args.asset_id.strip() or None)

    print("将使用素材:", picked, flush=True)
    print("本地路径:", file_path, flush=True)

    wait_sec = max(30, args.wait)
    asyncio.run(_wait_qr_phase(profile, wait_sec))

    print("\n扫码阶段结束，开始自动发布…", flush=True)
    result = asyncio.run(
        _run_publish(profile, file_path, args.title, args.description, args.tags)
    )
    print(result, flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
