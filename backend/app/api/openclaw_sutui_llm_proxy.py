"""供 OpenClaw Gateway 使用的本机 OpenAI 兼容层：转发到认证中心 POST /api/sutui-chat/completions。

OpenClaw 对 api: openai-completions 会 POST {baseUrl}/chat/completions，
Authorization 常为 lobster-sutui 的 apiKey（OPENCLAW_SUTUI_PROXY_KEY），不是用户 JWT。
用户身份由 chat._try_openclaw 在请求 Gateway 前 set_mcp_token_for_agent(agent_id, jwt) 写入缓存；
本代理通过 get_mcp_token_from_request(..., exclude_authorization_bearer=代理密钥) 解析 JWT。
"""
from __future__ import annotations

import hmac
import json
import logging
import uuid
from typing import Any, AsyncIterator, Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from ..core.config import settings
from ..services.openclaw_channel_auth_store import (
    channel_fallback_path_for_logs,
    read_channel_fallback,
    read_weixin_peer_auth,
    weixin_openclaw_peers_path_for_logs,
)
from .mcp_gateway import (
    _installation_id_for_mcp_forward,
    extend_mcp_token_ttl_for_jwt,
    get_mcp_token_from_request,
    set_mcp_token_for_agent,
)

logger = logging.getLogger(__name__)

router = APIRouter()

TRACE_HEADER = "X-Lobster-Chat-Trace-Id"


def _upstream_resp_summary(data: Any) -> str:
    """认证中心 / 上游 JSON 摘要（无正文），供 chat_trace 落日志。"""
    if not isinstance(data, dict):
        return ""
    slim: Dict[str, Any] = {}
    for k in ("id", "object", "model", "usage", "error", "detail", "created"):
        if k in data:
            slim[k] = data[k]
    ch = data.get("choices")
    if isinstance(ch, list) and ch:
        c0 = ch[0]
        if isinstance(c0, dict):
            slim["choice0_finish"] = c0.get("finish_reason")
            msg = c0.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                slim["choice0_content_len"] = len(msg["content"])
    try:
        s = json.dumps(slim, ensure_ascii=False, default=str)
    except Exception:
        s = str(slim)
    return s[:1200] if len(s) > 1200 else s


def _proxy_key_ok(request: Request) -> bool:
    expected = (getattr(settings, "openclaw_sutui_proxy_key", None) or "").strip()
    if not expected:
        return False
    auth = (request.headers.get("Authorization") or "").strip()
    if not auth.lower().startswith("bearer "):
        return False
    got = auth.split(" ", 1)[-1].strip()
    return hmac.compare_digest(got, expected)


@router.post("/internal/openclaw-sutui/v1/chat/completions", include_in_schema=False)
async def openclaw_sutui_chat_completions(request: Request):
    trace_id = uuid.uuid4().hex
    if not _proxy_key_ok(request):
        raise HTTPException(
            status_code=401,
            detail="OpenClaw 速推代理：请在 Authorization 中携带与龙虾 .env OPENCLAW_SUTUI_PROXY_KEY 一致的 Bearer",
        )
    asb = (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
    if not asb:
        raise HTTPException(status_code=503, detail="未配置 AUTH_SERVER_BASE，无法转发速推对话")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体须为 JSON")

    proxy_key = (getattr(settings, "openclaw_sutui_proxy_key", None) or "").strip()

    wx_hdr = (request.headers.get("X-Lobster-Weixin-From-User-Id") or request.headers.get("x-lobster-weixin-from-user-id") or "").strip()
    body_user = ""
    _bu = body.get("user")
    if isinstance(_bu, str):
        body_user = _bu.strip()
    wx_uid = wx_hdr or body_user

    user_jwt: Optional[str] = None
    peer_iid: Optional[str] = None
    auth_source = "-"
    if wx_uid and not getattr(settings, "openclaw_weixin_single_device_jwt", True):
        user_jwt, peer_iid = read_weixin_peer_auth(wx_uid)
        if user_jwt:
            auth_source = "weixin_peer"
            logger.info("[openclaw-sutui-proxy] 使用微信好友绑定 JWT weixin_user_id=%s", wx_uid[:32] if len(wx_uid) > 32 else wx_uid)

    if not user_jwt:
        user_jwt = get_mcp_token_from_request(
            request,
            exclude_authorization_bearer=proxy_key or None,
        )
        if user_jwt:
            auth_source = "mcp_header_or_agent_cache"
    if not user_jwt:
        pj, _pi = read_channel_fallback()
        if pj:
            user_jwt = pj
            peer_iid = peer_iid or _pi
            auth_source = "channel_fallback_file"
            logger.info("[openclaw-sutui-proxy] 使用登录写入的 openclaw/.channel_fallback.json JWT")
    if not user_jwt:
        fb = (getattr(settings, "openclaw_sutui_fallback_jwt", None) or "").strip()
        if fb:
            user_jwt = fb
            auth_source = "env_OPENCLAW_SUTUI_FALLBACK_JWT"
            logger.info(
                "[openclaw-sutui-proxy] 使用 OPENCLAW_SUTUI_FALLBACK_JWT 转发（微信等渠道无网页会话 JWT）"
            )
    if not user_jwt:
        single_dev = getattr(settings, "openclaw_weixin_single_device_jwt", True)
        logger.warning(
            "[openclaw-sutui-proxy] 无用户 JWT：wx_uid=%s single_device=%s channel_fallback=%s peers=%s",
            wx_uid or "-",
            single_dev,
            channel_fallback_path_for_logs(),
            weixin_openclaw_peers_path_for_logs(),
        )
        if single_dev:
            detail = (
                "未能解析用户 JWT：请先在本机网页用要计费的账号登录（会写入 openclaw/.channel_fallback.json），"
                "或 POST /auth/persist-openclaw-channel-fallback 同步 token。"
                "单机单微信模式下不按微信好友 ID 区分账号；也可在 .env 配置 OPENCLAW_SUTUI_FALLBACK_JWT（不推荐多人共用）。"
            )
        else:
            detail = (
                "未能解析用户 JWT：请先在本机网页登录（会自动写入 openclaw/.channel_fallback.json），"
                "或 POST /auth/persist-openclaw-channel-fallback；"
                "若使用微信助手(OpenClaw)，请发 /myid 取得好友 ID 后，"
                "带 Bearer 调用 POST /auth/persist-weixin-openclaw-peer 绑定，并确保已升级微信插件补丁（请求 mcp-gateway 会带 X-Lobster-Weixin-From-User-Id）。"
                "也可在 .env 配置 OPENCLAW_SUTUI_FALLBACK_JWT（全员共用账号，不推荐）。"
            )
        raise HTTPException(status_code=401, detail=detail)

    # 长任务续期：避免仅依赖「最近缓存」时在 600s 后误用 fallback JWT，导致 sutui_chat 与当前用户不一致
    extend_mcp_token_ttl_for_jwt(user_jwt)

    agent_id = (request.headers.get("x-openclaw-agent-id") or request.headers.get("X-Openclaw-Agent-Id") or "").strip()
    if agent_id and user_jwt:
        xi_pre = (request.headers.get("X-Installation-Id") or request.headers.get("x-installation-id") or "").strip()
        if not xi_pre:
            xi_pre = (_installation_id_for_mcp_forward(request) or "").strip()
        if not xi_pre:
            xi_pre = (peer_iid or "").strip()
        set_mcp_token_for_agent(agent_id, user_jwt, installation_id=xi_pre or None)

    model_in = (body.get("model") or "").strip()
    ov = (getattr(settings, "openclaw_sutui_upstream_model", None) or "").strip()
    if ov:
        prev = (body.get("model") or "").strip()
        body["model"] = ov
        logger.info(
            "[openclaw-sutui-proxy] openclaw_sutui_upstream_model 覆盖 model: %s -> %s",
            prev or "(空)",
            ov,
        )

    url = f"{asb}/api/sutui-chat/completions"
    headers = {
        "Authorization": f"Bearer {user_jwt}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        TRACE_HEADER: trace_id,
    }
    xi = (request.headers.get("X-Installation-Id") or request.headers.get("x-installation-id") or "").strip()
    if not xi:
        xi = (_installation_id_for_mcp_forward(request) or "").strip()
    if not xi:
        xi = (peer_iid or "").strip()
    if not xi:
        logger.warning(
            "[openclaw-sutui-proxy] 转发 sutui-chat 未带 X-Installation-Id（OpenClaw 未透传、缓存/文件/.env 均无槽位）；"
            "认证中心常返回 401 且无正文。请在根 .env 设置 OPENCLAW_SUTUI_FALLBACK_INSTALLATION_ID 后重新登录，"
            "或确认网页客户端登录请求已带 X-Installation-Id"
        )
    if xi:
        headers["X-Installation-Id"] = xi

    stream = bool(body.get("stream"))
    model_forward = (body.get("model") or "").strip()
    wx_log = (wx_uid[-16:] if wx_uid and len(wx_uid) > 16 else wx_uid) or "-"
    logger.info(
        "[chat_trace] trace_id=%s path=openclaw_sutui_proxy route=lobster-sutui->POST_AUTH_SUTUI_CHAT "
        "model_in=%s model_forward=%s stream=%s user_auth=%s agent_id=%s wx_tail=%s installation_id_set=%s auth_center=%s",
        trace_id,
        model_in or "-",
        model_forward or "-",
        stream,
        auth_source,
        agent_id or "-",
        wx_log,
        bool(xi),
        asb,
    )

    if not stream:
        try:
            async with httpx.AsyncClient(timeout=300.0, trust_env=False) as client:
                r = await client.post(url, json=body, headers=headers)
        except httpx.RequestError as e:
            logger.exception("[openclaw-sutui-proxy] 转发失败: %s", e)
            raise HTTPException(status_code=502, detail=f"认证中心不可达: {e!s}") from e
        summary = ""
        try:
            ct_main = (r.headers.get("content-type") or "").lower().split(";")[0].strip()
            if r.status_code == 200 and ct_main == "application/json":
                jd = r.json()
                summary = _upstream_resp_summary(jd)
            elif r.text:
                summary = (r.text or "").replace("\n", " ")[:500]
        except Exception:
            summary = (r.text or "").replace("\n", " ")[:500]
        logger.info(
            "[chat_trace] trace_id=%s path=openclaw_sutui_proxy auth_center_roundtrip http=%s resp_bytes=%s summary=%s",
            trace_id,
            r.status_code,
            len(r.content or b""),
            summary,
        )
        if r.status_code >= 400:
            _prev = (r.text or "")[:400].replace("\n", " ")
            logger.warning(
                "[openclaw-sutui-proxy] 认证中心 sutui-chat HTTP %s（OpenClaw 模型将无正文/占位）url=%s 响应摘要=%r "
                "forwarded_x_installation_id=%r",
                r.status_code,
                url,
                _prev,
                (headers.get("X-Installation-Id") or "")[:80] or None,
            )
        out_h = {TRACE_HEADER: trace_id}
        ct = r.headers.get("content-type")
        if ct:
            out_h["content-type"] = ct
        return Response(content=r.content, status_code=r.status_code, headers=out_h)

    async def gen() -> AsyncIterator[bytes]:
        try:
            async with httpx.AsyncClient(timeout=300.0, trust_env=False) as client:
                async with client.stream("POST", url, json=body, headers=headers) as r:
                    logger.info(
                        "[chat_trace] trace_id=%s path=openclaw_sutui_proxy auth_center_stream_first_byte http=%s",
                        trace_id,
                        r.status_code,
                    )
                    if r.status_code >= 400:
                        logger.warning(
                            "[openclaw-sutui-proxy] 认证中心 sutui-chat HTTP %s（流式）url=%s",
                            r.status_code,
                            url,
                        )
                    async for chunk in r.aiter_bytes():
                        yield chunk
        except httpx.RequestError as e:
            logger.exception("[openclaw-sutui-proxy] 流式转发失败: %s", e)
            err = f"data: {json.dumps({'error': {'message': str(e)}}, ensure_ascii=False)}\n\n"
            yield err.encode("utf-8")

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={TRACE_HEADER: trace_id},
    )
