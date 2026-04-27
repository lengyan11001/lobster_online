"""MCP 代理：Gateway 调用本端点时代入当前用户的 JWT，再转发到真实 MCP（8001）。

- 智能对话时 chat 接口会按 agent_id 将用户 token 写入缓存（TTL 10 分钟）。
- OpenClaw Gateway 应配置 MCP URL 为本代理（如 http://127.0.0.1:8000/mcp-gateway）。
- 代理收到请求时优先从 Header（x-user-authorization / Authorization）取 token（若 Gateway 透传），
  否则从缓存按 x-openclaw-agent-id 取 token，再转发到 MCP 并注入 Authorization。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..core.config import settings
from ..services.openclaw_channel_auth_store import read_channel_fallback, read_weixin_peer_auth
from ..services.openclaw_tool_scope import (
    HEADER_ALLOWED_CAPABILITIES,
    HEADER_ALLOWED_TOOLS,
    HEADER_INTENT,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# 默认转发到同机 MCP 服务
MCP_BACKEND_URL = os.environ.get("AI_TEST_PLATFORM_MCP_GATEWAY_BACKEND_URL", "http://127.0.0.1:8001/mcp").rstrip("/")
MCP_TOKEN_TTL_SECONDS = int(os.environ.get("MCP_GATEWAY_TOKEN_TTL_SECONDS", "600"))
# OpenClaw 单次 tools/call 可长达数十分钟（如 task.get_result 等效于网页对话轮询）；默认 120s 会先断流导致 MCP -32001
MCP_GATEWAY_FORWARD_TIMEOUT_SEC = float(os.environ.get("MCP_GATEWAY_FORWARD_TIMEOUT_SEC", "2400"))

# agent_id -> (token, expiry_ts, installation_id 可选)
# OpenClaw 调 MCP 时常不带 X-Installation-Id；认证中心 capabilities 在槽位开启时必填该头，故与 JWT 一并缓存。
_mcp_token_cache: dict[str, tuple[str, float, Optional[str]]] = {}
_openclaw_tool_scope_cache: dict[str, tuple[dict[str, str], float]] = {}
_cache_lock = threading.Lock()


def set_mcp_token_for_agent(
    agent_id: str,
    token: str,
    ttl_seconds: int = MCP_TOKEN_TTL_SECONDS,
    installation_id: Optional[str] = None,
) -> None:
    """在发起智能对话前调用，将当前用户的 token（及可选 X-Installation-Id）按 agent_id 写入缓存。"""
    if not agent_id or not token:
        return
    xi = (installation_id or "").strip() or None
    if xi and xi.lower().startswith("lobster-internal-"):
        # 本机内部 JWT 只用于绕过本地接口鉴权，认证中心不认可；写入缓存会污染 OpenClaw 后续上游调用。
        logger.debug("mcp_gateway: skip caching internal lobster JWT for agent=%s", agent_id)
        return
    expiry = time.time() + ttl_seconds
    with _cache_lock:
        _mcp_token_cache[agent_id] = (token.strip(), expiry, xi)


def set_openclaw_tool_scope_for_agent(
    agent_id: str,
    scope_headers: dict[str, str],
    ttl_seconds: int = MCP_TOKEN_TTL_SECONDS,
) -> None:
    """Cache the per-turn OpenClaw MCP tool scope; mcp-remote often drops request headers."""
    if not agent_id:
        return
    headers = {
        k: str(v or "").strip()
        for k, v in (scope_headers or {}).items()
        if k in {HEADER_INTENT, HEADER_ALLOWED_TOOLS, HEADER_ALLOWED_CAPABILITIES}
    }
    if not headers:
        return
    with _cache_lock:
        _openclaw_tool_scope_cache[agent_id] = (headers, time.time() + float(ttl_seconds))


def extend_mcp_token_ttl_for_jwt(token: str, ttl_seconds: int = MCP_TOKEN_TTL_SECONDS) -> None:
    """将缓存里等于该 JWT 的条目全部续期（滑动窗口）。

    OpenClaw + /openclaw 多轮工具（如生图）时，每轮都会打 lobster-sutui 代理；若网关
    未带 x-openclaw-agent-id，仅依赖「最近缓存」兜底，默认 600s 会过期，后续请求会落到
    channel_fallback / OPENCLAW_SUTUI_FALLBACK_JWT，认证中心 sutui_chat 记到别的 user_id，
    而用户仍能看到自己名下的 MCP 预扣。"""
    if not token:
        return
    t = token.strip()
    if not t:
        return
    expiry = time.time() + float(ttl_seconds)
    with _cache_lock:
        for k, e in list(_mcp_token_cache.items()):
            if not e or len(e) < 2:
                continue
            if e[0] != t:
                continue
            xi = e[2] if len(e) >= 3 else None
            _mcp_token_cache[k] = (t, expiry, xi)


def get_mcp_token_from_request(
    request: Request,
    *,
    exclude_authorization_bearer: Optional[str] = None,
) -> Optional[str]:
    """从代理收到的请求中解析用户 token：Header 优先，agent_id 缓存次之，最近缓存兜底。

    mcp-remote (stdio→HTTP bridge) 不会转发应用层 Header，因此当 OpenClaw 通过
    mcp-remote 调用本代理时，Header 中不会有 token 也不会有 agent_id。
    兜底策略：取缓存中最近写入（expiry 最大）的 token，因为 chat.py 在调用 OpenClaw
    前刚刚 set_mcp_token_for_agent()，时间差通常 < 1 秒。

    exclude_authorization_bearer：若与 Authorization Bearer 相等则忽略（用于 OpenClaw 调
    /internal/openclaw-sutui 时 Authorization 仅为 OPENCLAW_SUTUI_PROXY_KEY 的场景）。
    """
    # 0) 用户 JWT 显式在 x-user-authorization（优先于 Authorization，避免与 provider apiKey 混淆）
    xua = (request.headers.get("x-user-authorization") or "").strip()
    if xua and "bearer" in xua.lower():
        token = xua.split(" ", 1)[-1].strip() if " " in xua else xua.strip()
        if token:
            return token
    # 0b) OpenClaw 微信插件注入：微信好友 ID → peers 文件中的 JWT（多用户同机时可按人扣费）
    wx_uid = (request.headers.get("X-Lobster-Weixin-From-User-Id") or request.headers.get("x-lobster-weixin-from-user-id") or "").strip()
    if wx_uid and not getattr(settings, "openclaw_weixin_single_device_jwt", True):
        peer_jwt, _peer_iid = read_weixin_peer_auth(wx_uid)
        if peer_jwt:
            return peer_jwt
    # 1) Authorization（可排除与 OpenClaw lobster-sutui provider key 相同的值）
    auth = (request.headers.get("Authorization") or "").strip()
    if auth and "bearer" in auth.lower():
        token = auth.split(" ", 1)[-1].strip() if " " in auth else auth.strip()
        if token:
            ex = (exclude_authorization_bearer or "").strip()
            if ex and token == ex:
                pass
            else:
                return token
    # 2) 按 agent_id 从缓存取（Gateway 透传 x-openclaw-agent-id 时生效）
    agent_id = (request.headers.get("x-openclaw-agent-id") or "").strip()
    if agent_id:
        with _cache_lock:
            entry = _mcp_token_cache.get(agent_id)
        if entry:
            token, expiry = entry[0], entry[1]
            if time.time() < expiry and token:
                return token
            with _cache_lock:
                _mcp_token_cache.pop(agent_id, None)
    # 3) mcp-remote 不传 Header：取缓存中最近写入且未过期的 token（兜底）
    now = time.time()
    with _cache_lock:
        best_token: Optional[str] = None
        best_expiry = 0.0
        stale_keys: list[str] = []
        for k, e in _mcp_token_cache.items():
            t, exp = e[0], e[1]
            if exp <= now:
                stale_keys.append(k)
                continue
            if exp > best_expiry:
                best_expiry = exp
                best_token = t
        for k in stale_keys:
            _mcp_token_cache.pop(k, None)
    if best_token:
        logger.debug("mcp_gateway: using most-recent cached token (no agent_id in headers)")
        return best_token
    # 4) 登录时写入的 openclaw/.channel_fallback.json
    pj, _pi = read_channel_fallback()
    if pj:
        return pj
    # 5) .env 静态兜底
    fb = (getattr(settings, "openclaw_sutui_fallback_jwt", None) or "").strip()
    if fb:
        return fb
    return None


def _installation_id_for_mcp_forward(request: Request) -> Optional[str]:
    """供转发 MCP 时补全 X-Installation-Id：优先请求头，否则与同 agent 缓存的 JWT 一并写入的值。"""
    xi = (request.headers.get("X-Installation-Id") or request.headers.get("x-installation-id") or "").strip()
    if xi.lower().startswith("lobster-internal-"):
        xi = ""
    if xi:
        return xi
    aid = (request.headers.get("x-openclaw-agent-id") or "").strip()
    now = time.time()
    with _cache_lock:
        if aid:
            e = _mcp_token_cache.get(aid)
            if e and len(e) >= 3 and now < e[1] and e[0]:
                iid = (e[2] or "").strip()
                if iid:
                    return iid
        best_iid: Optional[str] = None
        best_exp = 0.0
        for _, e in _mcp_token_cache.items():
            if len(e) < 3:
                continue
            t, exp, iid = e[0], e[1], e[2]
            if exp <= now or not t:
                continue
            if exp > best_exp:
                best_exp = exp
                best_iid = (iid or "").strip() or None
        if best_iid:
            return best_iid
    _pj_unused, pi = read_channel_fallback()
    if pi:
        return pi.strip() or None
    fb_iid = (getattr(settings, "openclaw_sutui_fallback_installation_id", None) or "").strip()
    return fb_iid or None


def _openclaw_tool_scope_headers_for_mcp_forward(request: Request) -> dict[str, str]:
    """Return the current OpenClaw tool scope headers for MCP forwarding."""
    explicit = {}
    for h in (HEADER_INTENT, HEADER_ALLOWED_TOOLS, HEADER_ALLOWED_CAPABILITIES):
        v = (request.headers.get(h) or "").strip()
        if v:
            explicit[h] = v
    if explicit:
        return explicit

    aid = (request.headers.get("x-openclaw-agent-id") or "").strip()
    now = time.time()
    with _cache_lock:
        if aid:
            entry = _openclaw_tool_scope_cache.get(aid)
            if entry:
                headers, exp = entry
                if exp > now:
                    return dict(headers)
                _openclaw_tool_scope_cache.pop(aid, None)

        best_headers: Optional[dict[str, str]] = None
        best_exp = 0.0
        stale_keys: list[str] = []
        for k, entry in _openclaw_tool_scope_cache.items():
            headers, exp = entry
            if exp <= now:
                stale_keys.append(k)
                continue
            if exp > best_exp:
                best_exp = exp
                best_headers = dict(headers)
        for k in stale_keys:
            _openclaw_tool_scope_cache.pop(k, None)
    return best_headers or {}


@router.post("/mcp-gateway", include_in_schema=False)
async def mcp_gateway_proxy(request: Request) -> Response:
    """将 Gateway 的 MCP 请求转发到真实 MCP，并注入当前用户 token（若有）。"""
    try:
        body = await request.body()
    except Exception as e:
        logger.warning("mcp_gateway read body error: %s", e)
        return Response(content=b"", status_code=400)
    token = get_mcp_token_from_request(request)
    headers = dict(request.headers)
    # 去掉可能影响后端的 hop-by-hop 头，并注入用户 JWT
    for h in ("host", "content-length", "connection", "transfer-encoding"):
        headers.pop(h, None)
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["x-user-authorization"] = f"Bearer {token}"
    _xi_mcp = _installation_id_for_mcp_forward(request)
    if _xi_mcp:
        headers["X-Installation-Id"] = _xi_mcp
    scope_headers = _openclaw_tool_scope_headers_for_mcp_forward(request)
    if scope_headers:
        headers.update(scope_headers)
    mcp_method = ""
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            mcp_method = str(parsed.get("method") or "")[:96]
    except Exception:
        pass
    t_fwd0 = time.perf_counter()
    try:
        _t = MCP_GATEWAY_FORWARD_TIMEOUT_SEC
        _timeout = httpx.Timeout(_t, connect=min(60.0, _t))
        async with httpx.AsyncClient(timeout=_timeout) as client:
            r = await client.post(MCP_BACKEND_URL, content=body, headers=headers)
        fwd_ms = int((time.perf_counter() - t_fwd0) * 1000)
        logger.info(
            "[mcp_gateway] -> %s method=%s status=%s duration_ms=%s req_bytes=%s agent=%s intent=%s",
            MCP_BACKEND_URL,
            mcp_method or "?",
            r.status_code,
            fwd_ms,
            len(body),
            (request.headers.get("x-openclaw-agent-id") or "")[:48] or "-",
            scope_headers.get(HEADER_INTENT, "-"),
        )
        # 只透传对 JSON-RPC 有用的响应头，避免 hop-by-hop 等干扰
        out_headers = {}
        for name in ("content-type", "content-length"):
            if name in r.headers:
                out_headers[name] = r.headers[name]
        return Response(
            content=r.content,
            status_code=r.status_code,
            headers=out_headers,
        )
    except Exception as e:
        logger.exception("mcp_gateway forward error: %s", e)
        return Response(content=b"", status_code=502)
