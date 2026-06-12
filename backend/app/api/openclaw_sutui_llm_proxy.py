"""供 OpenClaw Gateway 使用的本机 OpenAI 兼容层：转发到认证中心 POST /api/sutui-chat/completions。

OpenClaw 对 api: openai-completions 会 POST {baseUrl}/chat/completions，
Authorization 常为 lobster-sutui 的 apiKey（OPENCLAW_SUTUI_PROXY_KEY），不是用户 JWT。
用户身份由 chat._try_openclaw 在请求 Gateway 前 set_mcp_token_for_agent(agent_id, jwt) 写入缓存；
本代理通过 get_mcp_token_from_request(..., exclude_authorization_bearer=代理密钥) 解析 JWT。
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
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
    openclaw_chat_turn_billing_headers_from_request,
    set_mcp_token_for_agent,
)

logger = logging.getLogger(__name__)

router = APIRouter()

TRACE_HEADER = "X-Lobster-Chat-Trace-Id"
CHAT_TURN_CHARGED_HEADER = "X-Lobster-Chat-Turn-Charged"
CHAT_TURN_ID_HEADER = "X-Lobster-Chat-Turn-Id"
_OPENCLAW_SKILL_AGENT_IDS = {"lobster-browser-use", "lobster-computer-use"}
_OPENCLAW_SKILL_SERVER_MODEL_ALIAS = "openclaw-skill-chat"
_TRANSIENT_LIMIT_MAX_ATTEMPTS = 3
_TRANSIENT_LIMIT_BACKOFF_SECONDS = (1.5, 3.0)
_REQUEST_ERROR_MAX_ATTEMPTS = 3
_REQUEST_ERROR_BACKOFF_SECONDS = (1.5, 3.0)
_FALSE_ENV_VALUES = {"0", "false", "no", "off"}

_FAKE_TOOL_CALL_RE = __import__("re").compile(
    r"tool_call|tool\u2581call|function_calls|<\|tool|<\s*[\|｜]\s*DSML\s*[\|｜]|```json\s*\{[^}]*capability",
    __import__("re").IGNORECASE,
)
_DSML_BLOCK_RE = __import__("re").compile(
    r"<\s*[\|｜]\s*DSML\s*[\|｜]\s*(?:tool_calls|function_calls)\s*>[\s\S]*?<\s*/\s*[\|｜]\s*DSML\s*[\|｜]\s*(?:tool_calls|function_calls)\s*>",
    __import__("re").IGNORECASE,
)
_DSML_TAG_RE = __import__("re").compile(
    r"<\s*/?\s*[\|｜]\s*DSML\s*[\|｜][^>]*>",
    __import__("re").IGNORECASE,
)


_UPSTREAM_CONCURRENCY_LIMIT_RE = __import__("re").compile(
    r"concurrent_requests_limit|Reached concurrent requests limit",
    __import__("re").IGNORECASE,
)


class _UpstreamConcurrentLimit(Exception):
    pass


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


def _message_from_error_payload(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("detail", "message", "msg"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    err = data.get("error")
    if isinstance(err, str) and err.strip():
        return err.strip()
    if isinstance(err, dict):
        for key in ("message", "detail", "msg", "code"):
            val = err.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def _looks_like_concurrent_limit_text(text: str) -> bool:
    return bool(text and _UPSTREAM_CONCURRENCY_LIMIT_RE.search(text))


def _response_looks_like_concurrent_limit(content: bytes) -> bool:
    raw = (content or b"").decode("utf-8", "replace").strip()
    if not raw:
        return False
    if _looks_like_concurrent_limit_text(raw):
        return True
    try:
        msg = _message_from_error_payload(json.loads(raw))
    except Exception:
        msg = ""
    return _looks_like_concurrent_limit_text(msg)


def _transient_limit_backoff(attempt: int) -> float:
    idx = max(0, min(attempt - 1, len(_TRANSIENT_LIMIT_BACKOFF_SECONDS) - 1))
    return _TRANSIENT_LIMIT_BACKOFF_SECONDS[idx]


def _request_error_backoff(attempt: int) -> float:
    idx = max(0, min(attempt - 1, len(_REQUEST_ERROR_BACKOFF_SECONDS) - 1))
    return _REQUEST_ERROR_BACKOFF_SECONDS[idx]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSE_ENV_VALUES


def _cloud_http_trust_env() -> bool:
    return _env_bool("OPENCLAW_SUTUI_PROXY_TRUST_ENV", False)


def _cloud_http_fallback_trust_env(primary: bool) -> Optional[bool]:
    if not _env_bool("OPENCLAW_SUTUI_PROXY_TRUST_ENV_FALLBACK", True):
        return None
    return not primary


def _cloud_http_trust_env_modes(primary: bool) -> tuple[bool, ...]:
    fallback = _cloud_http_fallback_trust_env(primary)
    if fallback is None or fallback == primary:
        return (primary,)
    return (primary, fallback)


def _upstream_concurrent_limit_message() -> str:
    return "上游模型当前并发已满，已自动重试但仍未拿到可用回复。请稍后再试。"


def _cloud_dialog_connect_failure_message() -> str:
    return "云端对话服务连接失败，已自动重试但仍未连通。请稍后重试。"


async def _post_upstream_with_request_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    json_body: Dict[str, Any],
    headers: Dict[str, str],
    trace_id: str,
    context: str,
    trust_env: Optional[bool] = None,
) -> httpx.Response:
    last_exc: Optional[httpx.RequestError] = None
    for attempt in range(1, _REQUEST_ERROR_MAX_ATTEMPTS + 1):
        try:
            return await client.post(url, json=json_body, headers=headers)
        except httpx.RequestError as exc:
            last_exc = exc
            logger.warning(
                "[chat_trace] trace_id=%s path=openclaw_sutui_proxy %s_request_failed attempt=%s/%s trust_env=%s err_type=%s err=%s",
                trace_id,
                context,
                attempt,
                _REQUEST_ERROR_MAX_ATTEMPTS,
                trust_env,
                type(exc).__name__,
                str(exc)[:500],
            )
            if attempt >= _REQUEST_ERROR_MAX_ATTEMPTS:
                break
            await asyncio.sleep(_request_error_backoff(attempt))
    assert last_exc is not None
    raise last_exc


async def _post_upstream_with_cloud_client_retry(
    url: str,
    *,
    json_body: Dict[str, Any],
    headers: Dict[str, str],
    trace_id: str,
    context: str,
    primary_trust_env: bool,
) -> httpx.Response:
    last_exc: Optional[httpx.RequestError] = None
    modes = _cloud_http_trust_env_modes(primary_trust_env)
    for index, trust_env in enumerate(modes):
        try:
            async with httpx.AsyncClient(timeout=300.0, trust_env=trust_env) as client:
                return await _post_upstream_with_request_retry(
                    client,
                    url,
                    json_body=json_body,
                    headers=headers,
                    trace_id=trace_id,
                    context=context,
                    trust_env=trust_env,
                )
        except httpx.RequestError as exc:
            last_exc = exc
            if index >= len(modes) - 1:
                break
            logger.warning(
                "[chat_trace] trace_id=%s path=openclaw_sutui_proxy %s_switch_trust_env after_request_error trust_env=%s next_trust_env=%s err_type=%s err=%s",
                trace_id,
                context,
                trust_env,
                modes[index + 1],
                type(exc).__name__,
                str(exc)[:500],
            )
    assert last_exc is not None
    raise last_exc


def _upstream_error_message(status_code: int, content: bytes, min_charge: str = "") -> str:
    raw = (content or b"").decode("utf-8", "replace").strip()
    msg = ""
    if raw:
        try:
            msg = _message_from_error_payload(json.loads(raw))
        except Exception:
            msg = raw
    if not msg:
        msg = f"上游模型服务返回 HTTP {status_code}。"
    if status_code == 402 and not any(k in msg for k in ("积分", "算力", "余额", "充值", "不足")):
        need = (min_charge or "").strip()
        need_text = f"单次最低需 {need} 算力" if need else "算力不足"
        msg = f"积分不足：LLM 对话{need_text}，请先充值后再试。"
    return msg[:1200]


def _openai_json_text_response(text: str, *, model: str) -> bytes:
    completion_id = f"chatcmpl_lobster_error_{uuid.uuid4().hex[:12]}"
    payload = {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model or "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")


def _openai_stream_text_event(text: str, *, model: str) -> bytes:
    completion_id = f"chatcmpl_lobster_error_{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    def _chunk(delta: Dict[str, Any], finish_reason: Optional[str] = None) -> str:
        payload = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model or "deepseek-chat",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"

    body = (
        _chunk({"role": "assistant"})
        + _chunk({"content": text})
        + _chunk({}, "stop")
        + "data: [DONE]\n\n"
    )
    return body.encode("utf-8")


def _openai_stream_from_completion_response(data: Any, *, model: str) -> bytes:
    """Convert a non-stream OpenAI chat completion into standard SSE chunks."""
    if not isinstance(data, dict):
        return _openai_stream_text_event("LLM upstream returned an invalid response.", model=model)
    if isinstance(data.get("error"), dict):
        msg = _message_from_error_payload(data) or "LLM upstream returned an error."
        return _openai_stream_text_event(msg, model=model)

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        msg = _message_from_error_payload(data) or "LLM upstream returned an empty response."
        return _openai_stream_text_event(msg, model=model)

    choice = choices[0]
    msg = choice.get("message")
    if not isinstance(msg, dict):
        msg = {}

    completion_id = str(data.get("id") or f"chatcmpl_lobster_bridge_{uuid.uuid4().hex[:12]}")
    created = int(data.get("created") or time.time())
    out_model = str(data.get("model") or model or "deepseek-chat")
    chunks: list[str] = []

    def _chunk(delta: Dict[str, Any], finish_reason: Optional[str] = None, *, usage: Any = None) -> None:
        payload: Dict[str, Any] = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": out_model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        if usage is not None:
            payload["usage"] = usage
        chunks.append(f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n")

    _chunk({"role": "assistant"})

    content = msg.get("content")
    if isinstance(content, str) and content:
        _chunk({"content": content})

    tool_calls = msg.get("tool_calls")
    if isinstance(tool_calls, list):
        normalized_calls: list[Dict[str, Any]] = []
        for idx, call in enumerate(tool_calls):
            if not isinstance(call, dict):
                continue
            fn = call.get("function")
            fn = fn if isinstance(fn, dict) else {}
            args = fn.get("arguments")
            if not isinstance(args, str):
                args = json.dumps(args if args is not None else {}, ensure_ascii=False, default=str)
            normalized_calls.append(
                {
                    "index": idx,
                    "id": str(call.get("id") or f"call_{uuid.uuid4().hex[:12]}"),
                    "type": str(call.get("type") or "function"),
                    "function": {
                        "name": str(fn.get("name") or ""),
                        "arguments": args,
                    },
                }
            )
        if normalized_calls:
            _chunk({"tool_calls": normalized_calls})

    function_call = msg.get("function_call")
    if isinstance(function_call, dict) and not isinstance(tool_calls, list):
        args = function_call.get("arguments")
        if not isinstance(args, str):
            args = json.dumps(args if args is not None else {}, ensure_ascii=False, default=str)
        _chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": f"call_{uuid.uuid4().hex[:12]}",
                        "type": "function",
                        "function": {
                            "name": str(function_call.get("name") or ""),
                            "arguments": args,
                        },
                    }
                ]
            }
        )

    finish_reason = choice.get("finish_reason")
    if not isinstance(finish_reason, str) or not finish_reason:
        finish_reason = "tool_calls" if isinstance(tool_calls, list) and tool_calls else "stop"
    _chunk({}, finish_reason)

    usage = data.get("usage")
    if isinstance(usage, dict):
        usage_payload = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": out_model,
            "choices": [],
            "usage": usage,
        }
        chunks.append(f"data: {json.dumps(usage_payload, ensure_ascii=False, default=str)}\n\n")

    chunks.append("data: [DONE]\n\n")
    return "".join(chunks).encode("utf-8")


def _response_has_fake_tool_text(data: Any) -> bool:
    """Detect text-embedded fake tool calls that OpenClaw may show to WeChat users."""
    if not isinstance(data, dict):
        return False
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return False
    msg = (choices[0] if isinstance(choices[0], dict) else {}).get("message", {})
    content = msg.get("content") if isinstance(msg, dict) else None
    return isinstance(content, str) and bool(_FAKE_TOOL_CALL_RE.search(content))


def _strip_fake_tool_text_from_response(data: Any) -> bool:
    """Strip DSML/fake tool-call markup in-place before the WeChat channel can display it."""
    if not isinstance(data, dict):
        return False
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return False
    msg = (choices[0] if isinstance(choices[0], dict) else {}).get("message")
    if not isinstance(msg, dict):
        return False
    content = msg.get("content")
    if not isinstance(content, str) or not _FAKE_TOOL_CALL_RE.search(content):
        return False

    cleaned = _DSML_BLOCK_RE.sub("", content)
    cleaned = _DSML_TAG_RE.sub("", cleaned).strip()
    if not cleaned:
        cleaned = "好的，我来为您总结一下已获取的信息。"
    if cleaned != content:
        msg["content"] = cleaned
        logger.warning(
            "[openclaw-sutui-proxy] stripped fake tool markup from response content (%d -> %d chars)",
            len(content),
            len(cleaned),
        )
        return True
    return False


def _should_retry_fake_tool_call(data: Any, request_body: Dict[str, Any]) -> bool:
    """Retry only when the model wrote a fake tool call as text; normal text replies stay untouched."""
    tools = request_body.get("tools")
    if not isinstance(tools, list) or not tools:
        return False
    tc = request_body.get("tool_choice") or "auto"
    if isinstance(tc, str) and tc.strip().lower() == "none":
        return False
    return _response_has_fake_tool_text(data)


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
    is_openclaw_skill_model = model_in == _OPENCLAW_SKILL_SERVER_MODEL_ALIAS
    is_openclaw_skill_request = agent_id in _OPENCLAW_SKILL_AGENT_IDS or is_openclaw_skill_model
    if is_openclaw_skill_request:
        prev = (body.get("model") or "").strip()
        body["model"] = _OPENCLAW_SKILL_SERVER_MODEL_ALIAS
        if prev != _OPENCLAW_SKILL_SERVER_MODEL_ALIAS:
            logger.info(
                "[openclaw-sutui-proxy] OpenClaw skill uses server-scheduled model alias: %s -> %s agent=%s",
                prev or "(empty)",
                _OPENCLAW_SKILL_SERVER_MODEL_ALIAS,
                agent_id,
            )
    ov = (getattr(settings, "openclaw_sutui_upstream_model", None) or "").strip()
    if ov and not is_openclaw_skill_request:
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
        "X-Lobster-OpenClaw-Internal": "1",
        "X-Lobster-LLM-Billing-Mode": "openclaw_internal",
    }
    billing_key = (getattr(settings, "lobster_mcp_billing_internal_key", None) or "").strip()
    if billing_key:
        headers["X-Lobster-Mcp-Billing"] = billing_key
    turn_headers = openclaw_chat_turn_billing_headers_from_request(request, user_jwt)
    if turn_headers.get(CHAT_TURN_CHARGED_HEADER):
        headers[CHAT_TURN_CHARGED_HEADER] = turn_headers[CHAT_TURN_CHARGED_HEADER]
        if turn_headers.get(CHAT_TURN_ID_HEADER):
            headers[CHAT_TURN_ID_HEADER] = turn_headers[CHAT_TURN_ID_HEADER][:128]
        headers["X-Lobster-LLM-Billing-Mode"] = "turn_precharged"
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
    cloud_trust_env = _cloud_http_trust_env()
    wx_log = (wx_uid[-16:] if wx_uid and len(wx_uid) > 16 else wx_uid) or "-"
    logger.info(
        "[chat_trace] trace_id=%s path=openclaw_sutui_proxy route=lobster-sutui->POST_AUTH_SUTUI_CHAT "
        "model_in=%s model_forward=%s stream=%s user_auth=%s agent_id=%s wx_tail=%s installation_id_set=%s auth_center=%s chat_turn_id=%s trust_env=%s",
        trace_id,
        model_in or "-",
        model_forward or "-",
        stream,
        auth_source,
        agent_id or "-",
        wx_log,
        bool(xi),
        asb,
        headers.get(CHAT_TURN_ID_HEADER, "-"),
        cloud_trust_env,
    )

    if not stream:
        try:
            max_attempts = _TRANSIENT_LIMIT_MAX_ATTEMPTS if is_openclaw_skill_request else 1
            for attempt in range(1, max_attempts + 1):
                if attempt > 1:
                    await asyncio.sleep(_transient_limit_backoff(attempt - 1))
                r = await _post_upstream_with_cloud_client_retry(
                    url,
                    json_body=body,
                    headers=headers,
                    trace_id=trace_id,
                    context="nonstream",
                    primary_trust_env=cloud_trust_env,
                )
                if not _response_looks_like_concurrent_limit(r.content):
                    break
                logger.warning(
                    "[chat_trace] trace_id=%s path=openclaw_sutui_proxy upstream_concurrent_limit "
                    "attempt=%s/%s http=%s",
                    trace_id,
                    attempt,
                    max_attempts,
                    r.status_code,
                )
            if _response_looks_like_concurrent_limit(r.content):
                out_h = {TRACE_HEADER: trace_id, "content-type": "application/json"}
                return Response(
                    content=_openai_json_text_response(_upstream_concurrent_limit_message(), model=model_forward),
                    status_code=200,
                    headers=out_h,
                )
        except httpx.RequestError as e:
            logger.exception("[openclaw-sutui-proxy] 转发失败: %s", e)
            raise HTTPException(status_code=502, detail=_cloud_dialog_connect_failure_message()) from e
        response_content = r.content
        json_data: Optional[Dict[str, Any]] = None
        json_overridden = False
        summary = ""
        try:
            ct_main = (r.headers.get("content-type") or "").lower().split(";")[0].strip()
            if r.status_code == 200 and ct_main == "application/json":
                jd = r.json()
                if isinstance(jd, dict):
                    json_data = jd
                if json_data is not None and _should_retry_fake_tool_call(json_data, body):
                    retry_body = dict(body)
                    retry_body["tool_choice"] = "required"
                    try:
                        r2 = await _post_upstream_with_cloud_client_retry(
                            url,
                            json_body=retry_body,
                            headers=headers,
                            trace_id=trace_id,
                            context="nonstream_fake_tool_retry",
                            primary_trust_env=cloud_trust_env,
                        )
                        ct2 = (r2.headers.get("content-type") or "").lower().split(";")[0].strip()
                        jd2 = r2.json() if r2.status_code == 200 and ct2 == "application/json" else None
                        if isinstance(jd2, dict) and not _response_has_fake_tool_text(jd2):
                            r = r2
                            json_data = jd2
                            json_overridden = True
                            logger.info(
                                "[chat_trace] trace_id=%s openclaw_sutui_proxy fake_tool_text retry succeeded",
                                trace_id,
                            )
                        else:
                            logger.warning(
                                "[chat_trace] trace_id=%s openclaw_sutui_proxy fake_tool_text retry ineffective http=%s",
                                trace_id,
                                getattr(r2, "status_code", "-"),
                            )
                    except Exception as e:
                        logger.warning(
                            "[chat_trace] trace_id=%s openclaw_sutui_proxy fake_tool_text retry failed: %s",
                            trace_id,
                            e,
                        )
                if json_data is not None and _strip_fake_tool_text_from_response(json_data):
                    json_overridden = True
                if json_data is not None and json_overridden:
                    response_content = json.dumps(json_data, ensure_ascii=False, default=str).encode("utf-8")
                summary = _upstream_resp_summary(json_data if json_data is not None else jd)
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
        return Response(content=response_content, status_code=r.status_code, headers=out_h)

    async def gen_nonstream_bridge() -> AsyncIterator[bytes]:
        bridge_body = dict(body)
        bridge_body["stream"] = False
        max_attempts = _TRANSIENT_LIMIT_MAX_ATTEMPTS if is_openclaw_skill_request else 1
        try:
            r: Optional[httpx.Response] = None
            for attempt in range(1, max_attempts + 1):
                if attempt > 1:
                    await asyncio.sleep(_transient_limit_backoff(attempt - 1))
                r = await _post_upstream_with_cloud_client_retry(
                    url,
                    json_body=bridge_body,
                    headers=headers,
                    trace_id=trace_id,
                    context="nonstream_bridge",
                    primary_trust_env=cloud_trust_env,
                )
                if not _response_looks_like_concurrent_limit(r.content):
                    break
                logger.warning(
                    "[chat_trace] trace_id=%s path=openclaw_sutui_proxy nonstream_bridge_upstream_concurrent_limit "
                    "attempt=%s/%s http=%s",
                    trace_id,
                    attempt,
                    max_attempts,
                    r.status_code,
                )
            if r is None:
                yield _openai_stream_text_event("LLM upstream returned no response.", model=model_forward)
                return
            if _response_looks_like_concurrent_limit(r.content):
                yield _openai_stream_text_event(_upstream_concurrent_limit_message(), model=model_forward)
                return
            if r.status_code >= 400:
                min_charge = (r.headers.get("X-Lobster-Min-Charge-Credits") or "").strip()
                yield _openai_stream_text_event(
                    _upstream_error_message(r.status_code, r.content, min_charge=min_charge),
                    model=model_forward,
                )
                return

            try:
                json_data = r.json()
            except Exception:
                logger.warning(
                    "[chat_trace] trace_id=%s path=openclaw_sutui_proxy nonstream_bridge_invalid_json http=%s bytes=%s",
                    trace_id,
                    r.status_code,
                    len(r.content or b""),
                )
                yield _openai_stream_text_event("LLM upstream returned invalid JSON.", model=model_forward)
                return

            if isinstance(json_data, dict) and _should_retry_fake_tool_call(json_data, bridge_body):
                retry_body = dict(bridge_body)
                retry_body["tool_choice"] = "required"
                try:
                    r2 = await _post_upstream_with_cloud_client_retry(
                        url,
                        json_body=retry_body,
                        headers=headers,
                        trace_id=trace_id,
                        context="nonstream_bridge_fake_tool_retry",
                        primary_trust_env=cloud_trust_env,
                    )
                    jd2 = r2.json() if r2.status_code == 200 else None
                except Exception as e:
                    logger.warning(
                        "[chat_trace] trace_id=%s openclaw_sutui_proxy nonstream_bridge fake_tool_text retry failed: %s",
                        trace_id,
                        e,
                    )
                    jd2 = None
                if isinstance(jd2, dict) and not _response_has_fake_tool_text(jd2):
                    json_data = jd2
                    logger.info(
                        "[chat_trace] trace_id=%s openclaw_sutui_proxy nonstream_bridge fake_tool_text retry succeeded",
                        trace_id,
                    )
            if isinstance(json_data, dict):
                _strip_fake_tool_text_from_response(json_data)
            logger.info(
                "[chat_trace] trace_id=%s path=openclaw_sutui_proxy nonstream_bridge_response http=%s summary=%s",
                trace_id,
                r.status_code,
                _upstream_resp_summary(json_data if isinstance(json_data, dict) else None),
            )
            yield _openai_stream_from_completion_response(json_data, model=model_forward)
        except httpx.RequestError as e:
            logger.exception("[openclaw-sutui-proxy] nonstream bridge forward failed: %s", e)
            yield _openai_stream_text_event(_cloud_dialog_connect_failure_message(), model=model_forward)

    return StreamingResponse(
        gen_nonstream_bridge(),
        media_type="text/event-stream",
        headers={TRACE_HEADER: trace_id, "X-Lobster-OpenClaw-Stream-Bridge": "nonstream"},
    )

    async def stream_once(client: httpx.AsyncClient, attempt: int, trust_env: bool) -> AsyncIterator[bytes]:
        async with client.stream("POST", url, json=body, headers=headers) as r:
            logger.info(
                "[chat_trace] trace_id=%s path=openclaw_sutui_proxy auth_center_stream_first_byte http=%s attempt=%s trust_env=%s",
                trace_id,
                r.status_code,
                attempt,
                trust_env,
            )
            if r.status_code >= 400:
                content = await r.aread()
                if _response_looks_like_concurrent_limit(content):
                    raise _UpstreamConcurrentLimit(content.decode("utf-8", "replace")[:800])
                min_charge = (r.headers.get("X-Lobster-Min-Charge-Credits") or "").strip()
                msg = _upstream_error_message(r.status_code, content, min_charge=min_charge)
                logger.warning(
                    "[openclaw-sutui-proxy] 认证中心 sutui-chat HTTP %s（流式）url=%s",
                    r.status_code,
                    url,
                )
                yield _openai_stream_text_event(msg, model=model_forward)
                return

            buffered: list[bytes] = []
            buffered_size = 0
            async for chunk in r.aiter_bytes():
                buffered.append(chunk)
                buffered_size += len(chunk)
                text = b"".join(buffered).decode("utf-8", "replace")
                if _looks_like_concurrent_limit_text(text):
                    raise _UpstreamConcurrentLimit(text[:800])
                if buffered_size >= 4096 or b"\n\n" in b"".join(buffered):
                    for item in buffered:
                        yield item
                    async for rest in r.aiter_bytes():
                        yield rest
                    return
            for item in buffered:
                yield item

    async def gen() -> AsyncIterator[bytes]:
        max_attempts = _TRANSIENT_LIMIT_MAX_ATTEMPTS if is_openclaw_skill_request else 1
        try:
            for attempt in range(1, max_attempts + 1):
                try:
                    if attempt > 1:
                        await asyncio.sleep(_transient_limit_backoff(attempt - 1))
                    modes = _cloud_http_trust_env_modes(cloud_trust_env)
                    for index, trust_env in enumerate(modes):
                        try:
                            async with httpx.AsyncClient(timeout=300.0, trust_env=trust_env) as client:
                                async for chunk in stream_once(client, attempt, trust_env):
                                    yield chunk
                            return
                        except httpx.RequestError as exc:
                            if index >= len(modes) - 1:
                                raise
                            logger.warning(
                                "[chat_trace] trace_id=%s path=openclaw_sutui_proxy stream_switch_trust_env "
                                "after_request_error trust_env=%s next_trust_env=%s err_type=%s err=%s",
                                trace_id,
                                trust_env,
                                modes[index + 1],
                                type(exc).__name__,
                                str(exc)[:500],
                            )
                except _UpstreamConcurrentLimit as e:
                    logger.warning(
                        "[chat_trace] trace_id=%s path=openclaw_sutui_proxy upstream_concurrent_limit "
                        "attempt=%s/%s detail=%s",
                        trace_id,
                        attempt,
                        max_attempts,
                        str(e).replace("\n", " ")[:500],
                    )
                    if attempt >= max_attempts:
                        yield _openai_stream_text_event(_upstream_concurrent_limit_message(), model=model_forward)
                        return
        except httpx.RequestError as e:
            logger.exception("[openclaw-sutui-proxy] 流式转发失败: %s", e)
            err = f"data: {json.dumps({'error': {'message': _cloud_dialog_connect_failure_message()}}, ensure_ascii=False)}\n\n"
            yield err.encode("utf-8")

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={TRACE_HEADER: trace_id},
    )
