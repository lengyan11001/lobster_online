"""
Probe Douyin creator upload page after login.

默认使用独立目录 browser_data/probe_douyin，与账号管理/发布使用的 browser_data/douyin_* 隔离，
避免探测与「打开浏览器」争用同一 user-data、SingletonLock 或 Playwright 池缓存。

用法:
  cd lobster_online
  python3 scripts/probe_douyin_publish.py
  # 在弹窗里登录抖音后等待探测结束；或先登录再跑

若必须探测「已保存在某正式账号目录里」的登录态（会动正式目录，慎选）:
  python3 scripts/probe_douyin_publish.py --use-account-profile --nickname <昵称>

This will open a visible Chromium window (headless=False), navigate to the upload page,
and print which key UI features are detected.

Note: many settings only appear after a real video is uploaded. This probe still helps
confirm login state + page structure + whether commerce entrypoints exist for the account.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Tuple

import httpx

UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"

# Ensure repo root is on sys.path when running as a script.
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _auth_token(base_url: str, username: str, password: str) -> str:
    r = httpx.post(
        f"{base_url}/auth/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"username": username, "password": password},
        timeout=20.0,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _get_accounts(base_url: str, token: str) -> Dict[str, Any]:
    r = httpx.get(
        f"{base_url}/api/accounts",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20.0,
    )
    r.raise_for_status()
    return r.json()


async def _exists(page: Any, selector: str) -> bool:
    try:
        el = await page.query_selector(selector)
        if el:
            return True
    except Exception:
        pass
    try:
        for fr in getattr(page, "frames", []) or []:
            try:
                el = await fr.query_selector(selector)
                if el:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


async def _text_present(page: Any, text: str) -> bool:
    sel = f'text="{text}"'
    try:
        el = await page.query_selector(sel)
        if el:
            return True
    except Exception:
        pass
    try:
        for fr in getattr(page, "frames", []) or []:
            try:
                el = await fr.query_selector(sel)
                if el:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


async def probe(profile_dir: str) -> Dict[str, Any]:
    from publisher.browser_pool import _acquire_context
    from publisher.drivers.douyin import DouyinDriver

    ctx, _created_new = await _acquire_context(profile_dir)
    driver = DouyinDriver()
    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://creator.douyin.com", wait_until="domcontentloaded", timeout=30000)
        logged_in = await driver.check_login(page)
        await page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        checks: List[Tuple[str, bool]] = []
        checks.append(("file_input", await _exists(page, 'input[type=\"file\"]')))
        for t in ["发布", "定时发布", "可见", "谁可以看", "位置", "小黄车", "商品", "封面", "允许评论", "允许合拍", "允许拼接"]:
            checks.append((f"text:{t}", await _text_present(page, t)))

        title = ""
        url = page.url
        try:
            title = await page.title()
        except Exception:
            title = ""

        return {
            "logged_in": logged_in,
            "url": url,
            "title": title,
            "checks": [{"name": k, "present": v} for k, v in checks],
        }
    finally:
        # The context will stay open if the user keeps the window open.
        # We intentionally do not force-close it here.
        pass


async def main() -> None:
    root = Path(__file__).resolve().parent.parent
    default_isolated = root / "browser_data" / "probe_douyin"

    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--username", default="user@lobster.local")
    ap.add_argument("--password", default="lobster123")
    ap.add_argument(
        "--nickname",
        default="",
        help="仅与 --use-account-profile 一起使用：数据库里该抖音账号昵称",
    )
    ap.add_argument(
        "--use-account-profile",
        action="store_true",
        help="使用数据库中该账号的 browser_profile（与发布/打开浏览器共用目录，可能与正式流程冲突）",
    )
    ap.add_argument(
        "--profile-dir",
        default="",
        help=f"自定义持久化目录；未指定且未使用 --use-account-profile 时默认为 {default_isolated}",
    )
    ap.add_argument("--db", default=str(root / "lobster.db"))
    ap.add_argument("--keep-open-sec", type=int, default=120, help="探测后保持浏览器窗口打开的秒数（默认120）")
    args = ap.parse_args()

    status = ""
    if args.use_account_profile:
        nick = (args.nickname or "").strip()
        if not nick:
            raise SystemExit("--use-account-profile 需要 --nickname")

        token = _auth_token(args.base_url, args.username, args.password)
        data = _get_accounts(args.base_url, token)
        found = any(
            a.get("platform") == "douyin" and a.get("nickname") == nick
            for a in data.get("accounts", [])
        )
        if not found:
            raise SystemExit("未找到对应抖音账号，请先在 UI 添加该昵称的抖音账号。")

        db_path = Path(args.db)
        if not db_path.exists():
            raise SystemExit(f"找不到数据库文件: {db_path}")

        con = sqlite3.connect(str(db_path))
        try:
            cur = con.cursor()
            cur.execute(
                "SELECT browser_profile, status FROM publish_accounts WHERE platform=? AND nickname=? ORDER BY id DESC LIMIT 1",
                ("douyin", nick),
            )
            row = cur.fetchone()
            if not row:
                raise SystemExit("数据库里找不到该抖音账号记录。")
            profile_dir, status = row[0], row[1]
            if not profile_dir:
                raise SystemExit("该账号未配置 browser_profile。")
        finally:
            con.close()
    else:
        profile_dir = (args.profile_dir or "").strip() or str(default_isolated)

    result = await probe(profile_dir)
    print(
        json.dumps(
            {
                "account_status": status,
                "profile_dir": profile_dir,
                "isolated": not args.use_account_profile,
                "probe": result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if args.keep_open_sec > 0:
        # keep process alive so window remains open for manual look
        await asyncio.sleep(args.keep_open_sec)


if __name__ == "__main__":
    asyncio.run(main())

