"""云端请求的 401 兜底：自动续签 + 重试一次（2026-10-21）。

背景：云端 token 失效后，客户端原来只在个别端点清 token，其余任务轮询/事件上报
继续拿旧 token 打，界面又显示"生成中"，任务就一直 pending（user 109 的数字人任务）。
这里做成统一入口：任何云端调用碰到 401 都先强制 /auth/refresh，换新 token 再重试一次。
续签失败返回原始 401，调用方再按"登录已失效，请重新登录"处理。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


def request_bearer(headers: Optional[Dict[str, str]]) -> str:
    auth = str((headers or {}).get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


async def refresh_token_after_401(token: str, *, reason: str = "") -> str:
    """强制续签一次；成功返回新 token，失败返回空串。"""
    token = str(token or "").strip()
    if not token:
        return ""
    try:
        from .cloud_session_renew import renew_cloud_token

        result = await renew_cloud_token(token=token, force=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AUTH-RETRY] 续签异常 reason=%s: %s: %r", reason, type(exc).__name__, exc)
        return ""
    new_token = str(result.get("token") or "").strip()
    if new_token:
        logger.info("[AUTH-RETRY] 云端登录态已续签 reason=%s", reason or "-")
        return new_token
    logger.warning(
        "[AUTH-RETRY] 云端登录态续签失败 reason=%s result=%s error=%s",
        reason or "-",
        result.get("reason"),
        str(result.get("error") or "")[:200],
    )
    return ""


async def request_with_auth_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    **kwargs: Any,
) -> httpx.Response:
    """发请求；401 时续签并带新 token 重试一次。"""
    hdrs: Dict[str, str] = dict(headers or {})
    response = await client.request(method, url, headers=hdrs, **kwargs)
    if int(response.status_code or 0) != 401:
        return response
    new_token = await refresh_token_after_401(request_bearer(hdrs), reason=f"{method} {url}")
    if not new_token:
        return response
    hdrs["Authorization"] = f"Bearer {new_token}"
    return await client.request(method, url, headers=hdrs, **kwargs)