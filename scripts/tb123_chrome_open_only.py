"""只打开 tb123_chrome profile 的真实 Chrome 窗口，不跑 e2e、不关窗口。

用途：风控期间人工接管——扫码、人工点点看、拖滑块、检查淘宝当前 session 状态。
配置沿用 tb123_chrome_e2e_once.py（PROFILE_DIR + PLAYWRIGHT_BROWSER_CHANNEL=chrome）。
"""
from __future__ import annotations

import asyncio
import os
import sys

os.environ["PLAYWRIGHT_BROWSER_CHANNEL"] = "chrome"
os.chdir(r"E:\lobster_online")
sys.path.insert(0, r"E:\lobster_online")

PROFILE_DIR = r"E:\lobster_online\browser_data\taobao_tb123_chrome"
LANDING_URL = "https://myseller.taobao.com/home.htm"


async def main() -> int:
    from publisher.browser_pool import (
        _acquire_context,
        _default_browser_options,
        _ensure_visible_interactive_context,
        _get_page_with_reacquire,
    )

    opts = _default_browser_options()
    print(f"[boot] REAL CHROME profile = {PROFILE_DIR}", flush=True)

    await _ensure_visible_interactive_context(PROFILE_DIR, browser_options=opts)
    ctx, _ = await _acquire_context(PROFILE_DIR, new_headless=False, browser_options=opts)
    page, ctx = await _get_page_with_reacquire(PROFILE_DIR, ctx, browser_options=opts)

    try:
        print(f"[goto] {LANDING_URL}", flush=True)
        try:
            await page.goto(LANDING_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"[goto-warn] {e}", flush=True)

        print(f"[url] {page.url}", flush=True)
        print("[HOLD] 浏览器已就绪。你在窗口里自己操作（扫码 / 点点看 / 拖滑块）。", flush=True)
        print("[HOLD] 按 Ctrl+C 退出本脚本时会关闭窗口。", flush=True)
        while True:
            await asyncio.sleep(30)
            try:
                cur = page.url
            except Exception:
                cur = "<page-closed>"
            print(f"[heartbeat] url={cur[-120:]}", flush=True)
    finally:
        try:
            await ctx.close()
            print("[done] browser closed", flush=True)
        except Exception as e:
            print(f"[done-warn] {e}", flush=True)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n[interrupted]", flush=True)
        sys.exit(130)
