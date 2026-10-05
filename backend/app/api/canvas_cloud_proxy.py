"""灵感画布（canvas_web）：本机 → 服务器 /canvas-api 转发。

画布页面由本机后端托管（/static/canvas-web/），页面里的接口根地址是 /canvas-api，
所以模型 / 任务 / 上传请求都先到本机；本机用**当前云端登录态**
（openclaw/.channel_fallback.json，取法与 services/cloud_session_renew 一致）转发到
服务器 /canvas-api/...，服务器只做 apiz key 中转与算力计费 —— 本机不接触 apiz key，
浏览器更不接触。

- 转发统一带 Authorization: Bearer <cloud token> 与 X-Installation-Id；
- 上游返回什么就原样返回什么（状态码 / 内容类型一致），画布自己判断错误；
- 本机没有登录态时直接 401，不去打服务器（省一次无谓请求）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional, Tuple

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ..core.config import settings
from ..services.cloud_session_renew import decode_jwt_payload, read_cloud_token, read_installation_id
from ..services.oem_brand_context import with_oem_brand_header

logger = logging.getLogger(__name__)
router = APIRouter()

_DEFAULT_TIMEOUT = 120.0
_UPLOAD_TIMEOUT = 600.0
_UPLOAD_HINTS = ("upload",)


def auth_context() -> Tuple[str, str]:
    """当前云端登录态：(jwt, installation_id)，与 h5_chat_channel 同一套取法。"""
    token = read_cloud_token()
    installation_id = read_installation_id()
    if token and not installation_id:
        sub = str(decode_jwt_payload(token).get("sub") or "").strip()
        installation_id = f"canvas-local-{sub}" if sub else "canvas-local"
    return token, installation_id


def cloud_base() -> str:
    base = (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="未配置服务器地址（AUTH_SERVER_BASE），无法转发画布请求")
    return base


_SESSION_PATHS = {
    "api/user_info",
    "api/get_user_money",
    "api/check_admin_permission",
}

_PROFILE_TTL_SECONDS = 60.0
_profile_cache: Dict[str, Any] = {"at": 0.0, "data": {}}


async def cloud_profile(force: bool = False) -> Dict[str, Any]:
    """当前登录态的用户资料（认证中心 /auth/me，带 60s 缓存）。

    拿不到就不阻塞画布：退回 JWT 里的 claims（id/邮箱等），credits 留空。
    """
    token, installation_id = auth_context()
    if not token:
        return {}
    claims = decode_jwt_payload(token)
    profile: Dict[str, Any] = {
        "id": claims.get("sub"),
        "email": claims.get("email") or "",
        "phone": claims.get("phone") or claims.get("mobile") or "",
        "name": claims.get("name") or claims.get("nickname") or "",
    }
    now = time.monotonic()
    if not force and _profile_cache["data"] and (now - float(_profile_cache["at"])) < _PROFILE_TTL_SECONDS:
        return dict(_profile_cache["data"])
    base = (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
    if base:
        headers = with_oem_brand_header({"Authorization": f"Bearer {token}", "Accept": "application/json"})
        if installation_id:
            headers["X-Installation-Id"] = installation_id
        try:
            async with httpx.AsyncClient(timeout=8.0, trust_env=False, follow_redirects=True) as client:
                resp = await client.get(f"{base}/auth/me", headers=headers)
            if resp.status_code < 400:
                data = resp.json()
                if isinstance(data, dict):
                    for key in ("id", "name", "nickname", "phone", "email", "credits", "balance", "points_balance"):
                        value = data.get(key)
                        if value not in (None, ""):
                            profile[key] = value
                    if "credits" not in profile:
                        for key in ("points_balance", "balance"):
                            if data.get(key) not in (None, ""):
                                profile["credits"] = data.get(key)
                                break
        except Exception as exc:  # noqa: BLE001 认证中心不可达时不阻塞画布
            logger.info("[canvas] 取用户资料失败，用登录态兜底: %s", exc)
    if not profile.get("name"):
        profile["name"] = "用户 %s" % (profile.get("id") or "")
    _profile_cache.update({"at": now, "data": dict(profile)})
    return profile


def canvas_session_payload(profile: Dict[str, Any]) -> Dict[str, Any]:
    """画布页面要的会话：token 是本机占位值（真 token 由本机代理注入，不下发浏览器）。"""
    credits = profile.get("credits")
    return {
        "token": "lobster-canvas",
        "id": profile.get("id"),
        "name": profile.get("name") or "",
        "phone": profile.get("phone") or "",
        "email": profile.get("email") or "",
        "points_balance": credits,
        "credits": credits,
    }


def _session_answer(normalized: str, profile: Dict[str, Any]) -> Optional[Response]:
    """画布问「我是谁 / 我的余额 / 我是不是管理员」：本机直接回答，不打上游。"""
    session = canvas_session_payload(profile)
    if normalized == "api/user_info":
        return JSONResponse({"code": 200, **session})
    if normalized == "api/get_user_money":
        credits = profile.get("credits")
        return JSONResponse({"code": 200, "points_balance": credits, "money": credits, "data": {"points_balance": credits}})
    if normalized == "api/check_admin_permission":
        return JSONResponse({"code": 200, "data": {"is_admin": False}})
    return None


@router.api_route(
    "/canvas-api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def canvas_cloud_proxy(path: str, request: Request) -> Response:
    normalized = str(path or "").strip().lstrip("/")
    if not normalized:
        raise HTTPException(status_code=404, detail="缺少接口路径")

    token, installation_id = auth_context()
    if not token:
        raise HTTPException(status_code=401, detail="画布需要登录：本机还没有云端登录态，请先在客户端登录")

    # 会话类接口本机回答：画布按这套判断「已登录」，不该再去问上游（上游只认服务器 key）
    if normalized in _SESSION_PATHS:
        return _session_answer(normalized, await cloud_profile())

    headers: Dict[str, str] = with_oem_brand_header(
        {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    )
    if installation_id:
        headers["X-Installation-Id"] = installation_id
    for name in ("content-type", "accept", "accept-language"):
        value = request.headers.get(name)
        if value:
            headers[name] = value

    body = await request.body()
    timeout = _UPLOAD_TIMEOUT if any(hint in normalized.lower() for hint in _UPLOAD_HINTS) else _DEFAULT_TIMEOUT
    url = f"{cloud_base()}/canvas-api/{normalized}"
    # 2026-10-05：画布轮询断一次就被前端判失败（用户看到「暂时无法读取任务状态」），
    # 这里对「网络错误 / 上游 5xx」做两次静默重试（只重试幂等的读接口 + 查询类），
    # 生成类 POST 不重试（避免重复下单）。
    retryable = (request.method.upper() in {"GET", "HEAD"}
                 or "tasks/query" in normalized or "tasks/info" in normalized)
    attempts = 3 if retryable else 1
    last_error: Optional[Exception] = None
    upstream = None
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
                upstream = await client.request(
                    request.method,
                    url,
                    content=body or None,
                    headers=headers,
                    params=dict(request.query_params),
                )
        except httpx.TimeoutException as exc:
            last_error = exc
        except httpx.TransportError as exc:
            last_error = exc
        else:
            if upstream.status_code < 500 or attempt == attempts - 1:
                break
            last_error = None
            logger.warning("[canvas] %s %s 上游 %s，重试 %s/%s",
                           request.method, normalized, upstream.status_code, attempt + 2, attempts)
        if attempt < attempts - 1:
            await asyncio.sleep(0.6 * (attempt + 1))
    if upstream is None:
        if isinstance(last_error, httpx.TimeoutException):
            raise HTTPException(status_code=504, detail=f"服务器画布接口超时：{last_error}") from last_error
        raise HTTPException(status_code=502, detail=f"连不上服务器画布接口：{last_error}") from last_error

    logger.info("[canvas] %s %s -> %s", request.method, normalized, upstream.status_code)
    media_type = upstream.headers.get("content-type") or "application/json"
    return Response(content=upstream.content, status_code=upstream.status_code, media_type=media_type)
