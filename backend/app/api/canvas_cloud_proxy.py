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

import logging
from typing import Dict, Tuple

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

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
        raise HTTPException(status_code=504, detail=f"服务器画布接口超时：{exc}") from exc
    except httpx.TransportError as exc:
        raise HTTPException(status_code=502, detail=f"连不上服务器画布接口：{exc}") from exc

    logger.info("[canvas] %s %s -> %s", request.method, normalized, upstream.status_code)
    media_type = upstream.headers.get("content-type") or "application/json"
    return Response(content=upstream.content, status_code=upstream.status_code, media_type=media_type)
