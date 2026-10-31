"""把客户端生成类请求统一转发到认证中心（服务器）。

客户端不允许直连上游（速推 / Comfly / 闪剪 / HiFly）。
生成、素材、任务查询都走 {AUTH_SERVER_BASE}，计费与预扣在服务器完成。
"""
from __future__ import annotations

import logging
import os

import httpx
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

PASSTHROUGH_PREFIXES = (
    "/api/comfly-proxy/",
    "/api/shanjian-smart-clip/",
    "/api/hifly/my/",
)
PASSTHROUGH_EXACT = frozenset({
    "/api/hifly/avatar/library",
    "/api/hifly/voice/library",
    "/api/hifly/avatar/cover",
})
PATH_MAP = {
    "/api/hifly/video/task": "/api/hifly/my/video/task",
}
BLOCKED_EXACT = frozenset({
    "/api/comfly/config",
    "/api/sutui/config",
})

_HOP_BY_HOP = {"host", "content-length", "connection", "transfer-encoding", "keep-alive",
               "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade"}


def _auth_base() -> str:
    return (os.environ.get("AUTH_SERVER_BASE") or "").strip().rstrip("/")


def _wants_forward(path: str) -> bool:
    if path in PATH_MAP or path in PASSTHROUGH_EXACT:
        return True
    return any(path.startswith(p) for p in PASSTHROUGH_PREFIXES)


class ServerGatewayProxyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in BLOCKED_EXACT:
            return JSONResponse(
                status_code=410,
                content={"detail": "\u5ba2\u6237\u7aef\u4e0d\u518d\u4fdd\u5b58\u4e0a\u6e38 Key\uff0c\u8bf7\u76f4\u63a5\u4f7f\u7528\u5e73\u53f0\u7b97\u529b\uff08\u5df2\u7edf\u4e00\u8d70\u670d\u52a1\u5668\uff09\u3002"},
            )
        if not _wants_forward(path):
            return await call_next(request)
        base = _auth_base()
        if not base:
            return JSONResponse(status_code=503, content={"detail": "AUTH_SERVER_BASE \u672a\u914d\u7f6e"})

        url = base + PATH_MAP.get(path, path)
        body = await request.body()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(900.0, connect=30.0), follow_redirects=False) as client:
                resp = await client.request(
                    request.method, url,
                    params=dict(request.query_params),
                    content=body or None,
                    headers=headers,
                )
        except Exception as exc:
            logger.warning("[server-proxy] %s %s failed: %s", request.method, url, exc)
            return JSONResponse(status_code=502, content={"detail": f"\u670d\u52a1\u5668\u8f6c\u53d1\u5931\u8d25\uff1a{exc}"})

        out_headers = {}
        for name in ("content-type", "content-length", "content-disposition", "cache-control", "etag"):
            if name in resp.headers:
                out_headers[name] = resp.headers[name]
        return Response(content=resp.content, status_code=resp.status_code, headers=out_headers)
