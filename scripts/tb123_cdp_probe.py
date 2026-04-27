"""最小 CDP 接管验证：通过 chromium.connect_over_cdp 连上用户手动开的 Chrome（9222 端口），
做轻量只读/低敏感操作，观察淘宝是否对 CDP attach 本身敏感。

前置（由用户手动执行）：
    chrome.exe --remote-debugging-port=9222 --user-data-dir=E:\\lobster_online\\browser_data\\taobao_tb123_clean --no-first-run --no-default-browser-check https://myseller.taobao.com/home.htm
    并完成扫码登录

本脚本不启动任何浏览器进程，只 attach。
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

os.chdir(r"E:\lobster_online")
sys.path.insert(0, r"E:\lobster_online")

CDP_URL = os.environ.get("TAOBAO_CDP_URL", "http://localhost:9222")


async def _pick_target_page(browser: Any):
    """找到一个已在淘宝卖家中心的 tab；找不到就挑第一个 context 的第一个 page。"""
    ctx_list = list(getattr(browser, "contexts", []) or [])
    if not ctx_list:
        raise RuntimeError("connect_over_cdp 返回的 browser 没有 contexts")
    for ctx in ctx_list:
        for page in ctx.pages:
            url = ""
            try:
                url = page.url or ""
            except Exception:
                pass
            if ("taobao.com" in url) or ("myseller" in url):
                return ctx, page
    ctx = ctx_list[0]
    pages = ctx.pages
    if not pages:
        page = await ctx.new_page()
    else:
        page = pages[0]
    return ctx, page


async def main() -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("[err] playwright 未安装", flush=True)
        return 2

    print(f"[boot] connect_over_cdp {CDP_URL}", flush=True)
    pw = await async_playwright().__aenter__()
    try:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
        print(f"[ok] connected, contexts={len(browser.contexts)}", flush=True)

        ctx, page = await _pick_target_page(browser)
        print(f"[attach] ctx.pages={len(ctx.pages)} picked url={page.url[:200]}", flush=True)

        # 最轻量只读
        try:
            title = await page.title()
        except Exception as e:
            title = f"<title-err {e}>"
        print(f"[read] title={title[:120]}", flush=True)

        # evaluate 简单读 userAgent（不改任何东西）
        try:
            ua = await page.evaluate("() => navigator.userAgent")
        except Exception as e:
            ua = f"<ua-err {e}>"
        print(f"[read] ua={ua[:160]}", flush=True)

        # 读 cookie 条数（只读）
        try:
            ck = await ctx.cookies()
            print(f"[read] cookies_count={len(ck)}", flush=True)
        except Exception as e:
            print(f"[read] cookies err {e}", flush=True)

        # 挂 10 秒再读 URL，看是否跳去风控页
        for i in range(5):
            await asyncio.sleep(2)
            try:
                cur = page.url
            except Exception:
                cur = "<err>"
            print(f"[observe t={i * 2}s] url={cur[:200]}", flush=True)

        print("[done] probe 结束。请你在窗口里确认当前页面是否仍正常（无滑块 / 无 baxia）。", flush=True)
        return 0
    finally:
        try:
            await pw.__aexit__(None, None, None)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n[interrupted]", flush=True)
        sys.exit(130)
