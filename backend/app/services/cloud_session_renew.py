"""云端登录态静默续签。

背景（2026-09-17）：云端登录 JWT 之前是 7 天固定有效期且没有续签，到期后客户端只能提示
「登录状态已失效，请重新登录」，当天全站 /auth/me 有 9048 次 401（客户端过期后还在轮询）。

现在：云端 JWT 有效期 30 天，客户端在**剩余不足 20 天**时静默调 POST {AUTH_SERVER_BASE}/auth/refresh
换一个新 token（服务端保持 jti 不变，槽位占用等按会话 id 记录的状态不受影响），
并把新 token 写回 openclaw/.channel_fallback.json，后续云端调用与客户端界面都能继续用。

失败不打扰用户：仅供日志记录，下一次检查（或界面主动触发）再试。
"""
from __future__ import annotations

import base64
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from ..core.config import get_settings
from .oem_brand_context import with_oem_brand_header
from .openclaw_channel_auth_store import (
    persist_channel_fallback_for_login,
    read_channel_fallback,
)

logger = logging.getLogger(__name__)

RENEW_PATH = "/auth/refresh"
# 剩余有效期少于这个值就续签（30 天有效期，实际大约每 10 天换一次）
RENEW_BEFORE_SECONDS = 60 * 60 * 24 * 20
CHECK_INTERVAL_SECONDS = 6 * 60 * 60
# 同一进程内最短续签间隔，避免界面反复点/异常时打爆认证中心
MIN_RENEW_GAP_SECONDS = 60 * 5

_last_attempt_at: float = 0.0
_last_result: Dict[str, Any] = {}


def decode_jwt_payload(token: str) -> Dict[str, Any]:
    parts = (token or "").split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def token_expiry(token: str) -> Optional[datetime]:
    exp = decode_jwt_payload(token).get("exp")
    try:
        return datetime.fromtimestamp(float(exp), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def token_remaining_seconds(token: str) -> Optional[float]:
    exp = token_expiry(token)
    if exp is None:
        return None
    return (exp - datetime.now(timezone.utc)).total_seconds()


def read_cloud_token() -> str:
    """当前云端 token：优先本机登录写入的 openclaw/.channel_fallback.json。"""
    jwt_token, _installation_id = read_channel_fallback()
    jwt_token = (jwt_token or "").strip()
    if jwt_token:
        return jwt_token
    return (getattr(get_settings(), "openclaw_sutui_fallback_jwt", "") or "").strip()


def read_installation_id() -> str:
    _jwt_token, installation_id = read_channel_fallback()
    installation_id = (installation_id or "").strip()
    if installation_id:
        return installation_id
    return (getattr(get_settings(), "openclaw_sutui_fallback_installation_id", "") or "").strip()


def session_status() -> Dict[str, Any]:
    token = read_cloud_token()
    remaining = token_remaining_seconds(token)
    payload = decode_jwt_payload(token)
    return {
        "has_token": bool(token),
        "user_id": payload.get("sub"),
        "brand_mark": payload.get("brand_mark"),
        "session_id": payload.get("jti"),
        "expires_at": token_expiry(token).isoformat() if token_expiry(token) else None,
        "remaining_seconds": int(remaining) if remaining is not None else None,
        "renew_due": (remaining is None) or remaining <= RENEW_BEFORE_SECONDS,
        "last_attempt_at": _last_attempt_at or None,
        "last_result": dict(_last_result),
    }


async def renew_cloud_token(
    *,
    token: str = "",
    force: bool = False,
    min_remaining_seconds: int = RENEW_BEFORE_SECONDS,
    post_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    """用当前 token 静默换一个新的（30 天）。返回 {ok, refreshed, reason/error, ...}。"""
    global _last_attempt_at, _last_result
    raw = (token or read_cloud_token()).strip()
    if not raw:
        return {"ok": False, "refreshed": False, "reason": "no_token"}
    remaining = token_remaining_seconds(raw)
    if not force and remaining is not None and remaining > float(min_remaining_seconds):
        return {
            "ok": True,
            "refreshed": False,
            "reason": "not_due",
            "remaining_seconds": int(remaining),
        }
    now = time.monotonic()
    if not force and _last_attempt_at and (now - _last_attempt_at) < MIN_RENEW_GAP_SECONDS:
        return {"ok": False, "refreshed": False, "reason": "throttled", "error": "刚刚已经试过续签"}

    settings = get_settings()
    base = (getattr(settings, "auth_server_base", "") or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "refreshed": False, "reason": "no_auth_server"}
    installation_id = read_installation_id()
    headers = with_oem_brand_header({"Authorization": f"Bearer {raw}"})
    if installation_id:
        headers["X-Installation-Id"] = installation_id
    url = base + RENEW_PATH

    _last_attempt_at = now
    try:
        if post_fn is not None:
            resp = await post_fn(url, {}, headers)
        else:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, trust_env=False) as client:
                resp = await client.post(url, headers=headers)
    except Exception as exc:  # 网络问题：下次再试
        _last_result = {"ok": False, "refreshed": False, "reason": "request_failed", "error": str(exc)}
        logger.warning("[cloud-renew] 续签请求失败: %s", exc)
        return dict(_last_result)

    status = int(getattr(resp, "status_code", 0) or 0)
    body: Dict[str, Any] = {}
    try:
        parsed = resp.json()
        if isinstance(parsed, dict):
            body = parsed
    except Exception:
        body = {}
    if status != 200 or not str(body.get("access_token") or "").strip():
        detail = str(body.get("detail") or getattr(resp, "text", "") or "")[:200]
        _last_result = {"ok": False, "refreshed": False, "reason": "rejected",
                        "http": status, "error": detail}
        logger.info("[cloud-renew] 续签被拒 http=%s detail=%s", status, detail)
        return dict(_last_result)

    new_token = str(body["access_token"]).strip()
    user_id = body.get("user_id")
    try:
        persist_channel_fallback_for_login(
            jwt_token=new_token,
            installation_id=installation_id or None,
            user_id=int(user_id) if user_id else None,
        )
    except Exception as exc:
        logger.warning("[cloud-renew] 写入 .channel_fallback.json 失败: %s", exc)
    expires_at = token_expiry(new_token)
    _last_result = {
        "ok": True,
        "refreshed": True,
        "expires_at": expires_at.isoformat() if expires_at else body.get("expires_at"),
        "expires_in": body.get("expires_in"),
    }
    logger.info(
        "[cloud-renew] 续签成功 expires_at=%s user_id=%s",
        _last_result["expires_at"],
        user_id,
    )
    return {"token": new_token, **dict(_last_result)}


async def cloud_token_renew_loop() -> None:
    """常驻：每 6 小时检查一次，剩余不足 20 天就静默续签。"""
    import asyncio

    logger.info(
        "[cloud-renew] 登录态静默续签已启动：每 %ss 检查，剩余不足 %ss 时续签",
        CHECK_INTERVAL_SECONDS,
        RENEW_BEFORE_SECONDS,
    )
    while True:
        try:
            result = await renew_cloud_token()
            if result.get("refreshed"):
                logger.info("[cloud-renew] 本次已续签：%s", result.get("expires_at"))
            elif result.get("ok") and result.get("reason") == "not_due":
                logger.debug("[cloud-renew] 未到续签时机，剩余 %ss", result.get("remaining_seconds"))
            elif result.get("reason") == "no_token":
                logger.debug("[cloud-renew] 本机没有云端 token（未登录），跳过")
        except Exception as exc:
            logger.warning("[cloud-renew] 周期检查异常: %s", exc, exc_info=True)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
