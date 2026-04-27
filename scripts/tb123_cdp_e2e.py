"""CDP 接管模式的 tb123 e2e。

前置（由你手动执行）：

    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" ^
        --remote-debugging-port=9222 ^
        --user-data-dir=E:\\lobster_online\\browser_data\\taobao_tb123_clean ^
        --no-first-run --no-default-browser-check ^
        https://myseller.taobao.com/home.htm

    扫码登录；让窗口保持打开。

本脚本不启动任何浏览器，仅通过 CDP attach 到 9222 端口，复用你已登录的 tab 跑 publish_from_job。
"""
from __future__ import annotations

import os

os.environ["TAOBAO_CDP_URL"] = os.environ.get("TAOBAO_CDP_URL", "http://localhost:9222").strip()
os.environ["PLAYWRIGHT_CDP_URL"] = os.environ["TAOBAO_CDP_URL"]
# CDP 模式下也不覆盖 UA（本来就用真实 Chrome）；这里保留 channel 兼容。
os.environ["PLAYWRIGHT_BROWSER_CHANNEL"] = os.environ.get("PLAYWRIGHT_BROWSER_CHANNEL", "chrome")

if not (os.environ.get("TAOBAO_PACE_MULT") or "").strip():
    os.environ["TAOBAO_PACE_MULT"] = "1.5"
if not (os.environ.get("TAOBAO_DETAIL_BATCH_SIZE") or "").strip():
    os.environ["TAOBAO_DETAIL_BATCH_SIZE"] = "6"

import asyncio
import json
import sys

os.chdir(r"E:\lobster_online")
sys.path.insert(0, r"E:\lobster_online")

PROFILE_DIR = r"E:\lobster_online\browser_data\taobao_tb123_clean"
JOB_ID = "8f4f2c36ebf04eae8004dc4be8bac36f"
TB123_USER_ID = 4
ACCOUNT_NICKNAME = "tb123_clean"


def ensure_account_row() -> int:
    from backend.app.db import SessionLocal
    from backend.app.models import PublishAccount

    db = SessionLocal()
    try:
        existing = (
            db.query(PublishAccount)
            .filter(
                PublishAccount.user_id == TB123_USER_ID,
                PublishAccount.platform == "taobao",
                PublishAccount.nickname == ACCOUNT_NICKNAME,
            )
            .first()
        )
        if existing:
            if existing.browser_profile != PROFILE_DIR:
                existing.browser_profile = PROFILE_DIR
                db.commit()
            print(f"[db] PublishAccount {ACCOUNT_NICKNAME} id={existing.id}", flush=True)
            return existing.id
        acct = PublishAccount(
            user_id=TB123_USER_ID,
            platform="taobao",
            nickname=ACCOUNT_NICKNAME,
            status="pending",
            browser_profile=PROFILE_DIR,
            meta={},
        )
        db.add(acct)
        db.commit()
        db.refresh(acct)
        print(f"[db] 新建 PublishAccount {ACCOUNT_NICKNAME} id={acct.id}", flush=True)
        return acct.id
    finally:
        db.close()


async def run_e2e() -> dict:
    from backend.app.api.ecommerce_publish import publish_from_job, PublishFromJobBody
    from backend.app.api.auth import _ServerUser
    from backend.app.db import SessionLocal

    body = PublishFromJobBody(
        job_id=JOB_ID,
        platform="taobao",
        account_nickname=ACCOUNT_NICKNAME,
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


async def main() -> int:
    ensure_account_row()

    from publisher.browser_pool import (
        _acquire_context,
        _default_browser_options,
        _get_page_with_reacquire,
    )

    opts = _default_browser_options()
    print(f"[boot] CDP attach {os.environ['TAOBAO_CDP_URL']} (profile cache-key = {PROFILE_DIR})", flush=True)

    ctx, _ = await _acquire_context(PROFILE_DIR, new_headless=False, browser_options=opts)
    page, ctx = await _get_page_with_reacquire(PROFILE_DIR, ctx, browser_options=opts)
    print(f"[attach] pages={len(ctx.pages)} first_url={page.url[:160]}", flush=True)

    # 确认登录状态
    cur = page.url
    if ("login" in cur.lower()) or ("passport" in cur.lower()):
        print("[abort] 当前 tab 还在登录页；请先在窗口内扫码，再重试本脚本", flush=True)
        return 2

    try:
        result = await run_e2e()
        ok = bool(result and result.get("ok"))
        print(f"[done] ok={ok}", flush=True)
        return 0 if ok else 1
    except Exception as exc:
        print(f"[err] publish_from_job exception: {exc}", flush=True)
        import traceback
        traceback.print_exc()
        return 1
    # 不关 context / 不关 browser：用户手动关 Chrome 即止


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n[interrupted]", flush=True)
        sys.exit(130)
