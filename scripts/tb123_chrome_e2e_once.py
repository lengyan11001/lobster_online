"""与 tb123_chrome_e2e 相同流程，但在 publish_from_job 结束后关闭浏览器并退出（供自动化跑通验证）。"""
from __future__ import annotations

import os

os.environ["PLAYWRIGHT_BROWSER_CHANNEL"] = "chrome"
# 未显式设置时略放慢节奏、详情图拆批，降低淘宝风控概率（可在命令行覆盖）
if not (os.environ.get("TAOBAO_PACE_MULT") or "").strip():
    os.environ["TAOBAO_PACE_MULT"] = "1.35"
if not (os.environ.get("TAOBAO_DETAIL_BATCH_SIZE") or "").strip():
    os.environ["TAOBAO_DETAIL_BATCH_SIZE"] = "6"

import asyncio
import json
import sys

os.chdir(r"E:\lobster_online")
sys.path.insert(0, r"E:\lobster_online")

PROFILE_DIR = r"E:\lobster_online\browser_data\taobao_tb123_chrome"
LANDING_URL = "https://myseller.taobao.com/home.htm"
JOB_ID = "8f4f2c36ebf04eae8004dc4be8bac36f"
TB123_USER_ID = 4
LOGIN_TIMEOUT_SEC = int(os.environ.get("TB_E2E_LOGIN_TIMEOUT", "300"))


async def wait_for_login(page, timeout_sec: int) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout_sec
    last_url = ""
    while asyncio.get_event_loop().time() < deadline:
        try:
            cur = page.url or ""
        except Exception:
            cur = ""
        is_login = ("login" in cur.lower()) or ("passport" in cur.lower())
        if not is_login and cur:
            return True
        if cur != last_url:
            print(f"[login-wait] url tail = {cur[-100:]}", flush=True)
            last_url = cur
        await asyncio.sleep(3)
    return False


async def run_e2e_in_same_process():
    from backend.app.api.ecommerce_publish import publish_from_job, PublishFromJobBody
    from backend.app.api.auth import _ServerUser
    from backend.app.db import SessionLocal

    body = PublishFromJobBody(
        job_id=JOB_ID,
        platform="taobao",
        account_nickname="tb123_chrome",
        brand="必火智能",
        price="299",
        stock=99,
        delivery_time="48小时内发货",
        delivery_location="浙江/金华",
        use_opportunity=False,
        opportunity_id=124108967,
        opportunity_type=2,
    )
    user = _ServerUser(id=TB123_USER_ID)
    db = SessionLocal()
    try:
        result = await publish_from_job(body, current_user=user, db=db)
        print("\n[e2e-result]", flush=True)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return result
    finally:
        db.close()


def ensure_account_row():
    from backend.app.db import SessionLocal
    from backend.app.models import PublishAccount

    db = SessionLocal()
    try:
        existing = (
            db.query(PublishAccount)
            .filter(
                PublishAccount.user_id == TB123_USER_ID,
                PublishAccount.platform == "taobao",
                PublishAccount.nickname == "tb123_chrome",
            )
            .first()
        )
        if existing:
            if existing.browser_profile != PROFILE_DIR:
                existing.browser_profile = PROFILE_DIR
                db.commit()
            print(f"[db] PublishAccount tb123_chrome id={existing.id}", flush=True)
            return existing.id
        acct = PublishAccount(
            user_id=TB123_USER_ID,
            platform="taobao",
            nickname="tb123_chrome",
            status="pending",
            browser_profile=PROFILE_DIR,
            meta={},
        )
        db.add(acct)
        db.commit()
        db.refresh(acct)
        print(f"[db] 新建 PublishAccount tb123_chrome id={acct.id}", flush=True)
        return acct.id
    finally:
        db.close()


async def main() -> int:
    ensure_account_row()

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
            await asyncio.sleep(3)
        except Exception as e:
            print(f"[goto-warn] {e}", flush=True)

        cur = page.url
        print(f"[url] {cur}", flush=True)
        is_login = ("login" in cur.lower()) or ("passport" in cur.lower())
        if is_login:
            print("[!] 需要登录，请在窗口内完成扫码（超时见 TB_E2E_LOGIN_TIMEOUT）", flush=True)
            ok = await wait_for_login(page, LOGIN_TIMEOUT_SEC)
            if not ok:
                print("[abort] 登录超时", flush=True)
                return 2
            print(f"[login-ok] {page.url}", flush=True)
            await asyncio.sleep(8)
        else:
            print("[OK] 已登录态", flush=True)

        result = await run_e2e_in_same_process()
        ok = bool(result and result.get("ok"))
        return 0 if ok else 1
    finally:
        try:
            await ctx.close()
            print("[done] browser closed", flush=True)
        except Exception as e:
            print(f"[done-warn] {e}", flush=True)


if __name__ == "__main__":
    try:
        code = asyncio.run(main())
        sys.exit(code)
    except KeyboardInterrupt:
        print("\n[interrupted]", flush=True)
        sys.exit(130)
