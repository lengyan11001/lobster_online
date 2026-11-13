"""抖音/视频号登录态巡检：每天 08:00（本机错峰 0~10 分钟）检查一次。

用户 2026-11-13 决定：只保留两个触发点——
  1) 每天早上 8 点巡检一次；
  2) 发布时已有登录检查（need_login 立即上报）。
不做定时发布前巡检，也不做夜间检查。

掉线不立刻上报：隔 10 分钟复查一次，仍失败才上报云端（避免风控页/网络抖动误报）。
云端按 24 小时去重后发短信通知账号主人。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import SessionLocal
from ..models import PublishAccount
from .creator_metrics_daily_runner import (
    _httpx_post,
    read_login_context,
    resolve_local_user_id,
)

logger = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))
CHECK_START_MINUTE = 8 * 60
CHECK_SPAN_MINUTES = 10
RECHECK_DELAY_SECONDS = 10 * 60
PLATFORMS = ("douyin", "wechat_channels")
PLATFORM_LABELS = {"douyin": "抖音", "wechat_channels": "视频号"}
UPLOAD_PATH = "/api/publish/login-status:batch"
UPLOAD_MAX_ATTEMPTS = 3

PostFn = Callable[[str, Dict[str, Any], Dict[str, str]], Awaitable[Any]]
SleepFn = Callable[[float], Awaitable[None]]
CheckFn = Callable[[PublishAccount], Awaitable[Dict[str, Any]]]


def login_check_enabled() -> bool:
    raw = os.environ.get("LOBSTER_PUBLISH_LOGIN_CHECK", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def beijing_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(BEIJING_TZ)


def slot_minute(seed: str) -> int:
    """同一台机器稳定落在 08:00–08:10 的某一分钟，避免所有客户端同一时刻拉浏览器。"""
    digest = hashlib.sha256(str(seed or "default").encode("utf-8")).hexdigest()
    return CHECK_START_MINUTE + int(digest[:8], 16) % CHECK_SPAN_MINUTES


def next_check_at(now: Optional[datetime] = None, *, seed: str = "") -> datetime:
    current = (now or beijing_now()).astimezone(BEIJING_TZ)
    target = current.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        minutes=slot_minute(seed)
    )
    if target <= current:
        target += timedelta(days=1)
    return target


def _seed() -> str:
    jwt_token, installation_id = read_login_context()
    if installation_id:
        return f"install:{installation_id}"
    return f"user:{_jwt_sub(jwt_token)}" if jwt_token else "default"


def _jwt_sub(token: str) -> str:
    try:
        import base64
        import json

        payload = str(token or "").split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
        return str(data.get("sub") or "")
    except Exception:
        return ""


def account_rows(db: Session, user_id: int) -> List[PublishAccount]:
    return (
        db.query(PublishAccount)
        .filter(
            PublishAccount.user_id == user_id,
            PublishAccount.platform.in_(PLATFORMS),
        )
        .order_by(PublishAccount.id.asc())
        .all()
    )


async def check_account_login(account: PublishAccount) -> Dict[str, Any]:
    """单账号登录检查；只读现有浏览器 profile，不发布、不改资料。"""
    item: Dict[str, Any] = {
        "platform": account.platform,
        "account_id": int(getattr(account, "id", 0) or 0),
        "nickname": str(getattr(account, "nickname", "") or ""),
        "status": "offline",
        "checked_at": datetime.utcnow().isoformat(),
        "reason": "",
    }
    try:
        from publisher.browser_pool import check_browser_login

        from ..api.publish import _account_browser_profile_and_options

        profile_dir, opts = _account_browser_profile_and_options(account)
        ok = await check_browser_login(profile_dir, account.platform, opts, focus=False)
        item["status"] = "online" if ok else "offline"
        if not ok:
            item["reason"] = "check_login_false"
    except Exception as exc:  # 打不开浏览器也按掉线处理，交给 10 分钟后复查
        item["reason"] = ("check_error: " + str(exc))[:200]
        logger.warning(
            "[PUBLISH-LOGIN] 检查失败 platform=%s account=%s: %s",
            account.platform,
            item["account_id"],
            exc,
        )
    return item


async def upload_login_status(
    items: List[Dict[str, Any]],
    *,
    installation_id: str = "",
    post_fn: Optional[PostFn] = None,
    max_attempts: int = UPLOAD_MAX_ATTEMPTS,
) -> Dict[str, Any]:
    if not items:
        return {"ok": True, "uploaded": 0, "skipped": "no_items"}
    base = (getattr(settings, "auth_server_base", "") or "").strip().rstrip("/")
    jwt_token, token_installation = read_login_context()
    effective_installation = (installation_id or token_installation or "").strip()
    if not base or not jwt_token:
        logger.info(
            "[PUBLISH-LOGIN] 暂不上报：base=%s 登录凭证=%s 条数=%s",
            bool(base),
            bool(jwt_token),
            len(items),
        )
        return {"ok": False, "uploaded": 0, "skipped": "未登录或未配置 AUTH_SERVER_BASE"}

    url = f"{base}{UPLOAD_PATH}"
    headers = {"Authorization": f"Bearer {jwt_token}", "Content-Type": "application/json"}
    if effective_installation:
        headers["X-Installation-Id"] = effective_installation
    payload = {"installation_id": effective_installation, "items": items}
    poster = post_fn or _httpx_post
    last_error = ""
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        try:
            resp = await poster(url, payload, headers)
            status = int(getattr(resp, "status_code", 0) or 0)
            if 200 <= status < 300:
                logger.info("[PUBLISH-LOGIN] 已上报 %s 条登录状态", len(items))
                return {"ok": True, "uploaded": len(items), "attempts": attempt}
            last_error = f"HTTP {status}: {str(getattr(resp, 'text', ''))[:200]}"
        except Exception as exc:
            last_error = str(exc)[:200]
        if attempt < max_attempts:
            await asyncio.sleep(5 * attempt)
    logger.warning("[PUBLISH-LOGIN] 上报失败: %s", last_error)
    return {"ok": False, "uploaded": 0, "error": last_error}


async def run_daily_login_check(
    *,
    db: Optional[Session] = None,
    user_id: Optional[int] = None,
    installation_id: str = "",
    check_fn: Optional[CheckFn] = None,
    post_fn: Optional[PostFn] = None,
    sleep_fn: Optional[SleepFn] = None,
    recheck_delay: Optional[float] = None,
) -> Dict[str, Any]:
    """每日一轮：检查 → 掉线的 10 分钟后再查一次 → 落库 → 上报。"""
    owns_session = db is None
    session = db or SessionLocal()
    sleeper = sleep_fn or asyncio.sleep
    try:
        jwt_token, token_installation = read_login_context()
        effective_installation = (installation_id or token_installation or "").strip()
        uid = int(user_id or 0) or resolve_local_user_id(
            session, installation_id=effective_installation
        )
        if not uid:
            return {"ok": False, "error": "无法确定本机用户（未登录且本地无用户）", "accounts": []}
        accounts = account_rows(session, uid)
        if not accounts:
            logger.info("[PUBLISH-LOGIN] 本机没有抖音/视频号账号，跳过巡检")
            return {"ok": True, "accounts": [], "user_id": uid}

        runner = check_fn or check_account_login
        results: List[Dict[str, Any]] = []
        for account in accounts:
            result = await runner(account)
            result["_account"] = account
            results.append(result)

        offline = [r for r in results if r.get("status") == "offline"]
        if offline:
            delay = RECHECK_DELAY_SECONDS if recheck_delay is None else float(recheck_delay)
            logger.info("[PUBLISH-LOGIN] 首次检查掉线 %s 个，%s 秒后复查", len(offline), int(delay))
            await sleeper(delay)
            for item in offline:
                again = await runner(item["_account"])
                if again.get("status") == "online":
                    item["status"] = "online"
                    item["reason"] = "recheck_ok"
                else:
                    item["reason"] = again.get("reason") or item.get("reason") or ""

        now = datetime.utcnow()
        for item in results:
            account = item.pop("_account", None)
            if account is None:
                continue
            try:
                account.status = "online" if item.get("status") == "online" else "offline"
                if item.get("status") == "online":
                    account.last_login = now
            except Exception:
                pass
        session.commit()

        upload = await upload_login_status(
            [{k: v for k, v in item.items()} for item in results],
            installation_id=effective_installation,
            post_fn=post_fn,
        )
        summary = {
            "ok": True,
            "user_id": uid,
            "account_count": len(results),
            "offline": [r for r in results if r.get("status") == "offline"],
            "upload": upload,
        }
        logger.info(
            "[PUBLISH-LOGIN] 巡检完成 accounts=%s offline=%s uploaded=%s",
            len(results),
            len(summary["offline"]),
            upload.get("uploaded"),
        )
        return summary
    finally:
        if owns_session:
            session.close()


async def report_account_offline(account: PublishAccount, reason: str = "") -> None:
    """发布时 need_login：立刻上报一次（云端按 24h 去重后通知）。"""
    await upload_login_status(
        [
            {
                "platform": account.platform,
                "account_id": int(getattr(account, "id", 0) or 0),
                "nickname": str(getattr(account, "nickname", "") or ""),
                "status": "offline",
                "checked_at": datetime.utcnow().isoformat(),
                "reason": (reason or "publish_need_login")[:200],
            }
        ]
    )


async def publish_login_monitor_loop() -> None:
    """常驻循环：每天 08:00–08:10 之间本机固定那一分钟跑一轮。"""
    if not login_check_enabled():
        logger.info("[PUBLISH-LOGIN] 登录巡检已关闭（LOBSTER_PUBLISH_LOGIN_CHECK=0）")
        return
    while True:
        try:
            seed = _seed()
            target = next_check_at(seed=seed)
            delay = max(30.0, (target - beijing_now()).total_seconds())
            logger.info(
                "[PUBLISH-LOGIN] 下次巡检 %s", target.strftime("%Y-%m-%d %H:%M")
            )
            await asyncio.sleep(delay)
            await run_daily_login_check()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[PUBLISH-LOGIN] 巡检异常，5 分钟后重试: %s", exc)
            await asyncio.sleep(300)


__all__ = [
    "login_check_enabled",
    "next_check_at",
    "run_daily_login_check",
    "upload_login_status",
    "report_account_offline",
    "publish_login_monitor_loop",
    "check_account_login",
]
