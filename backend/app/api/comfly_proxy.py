"""Comfly 透明 Proxy：让用户客户端 (lobster_online) 内的爆款TVC pipeline 走云端 Comfly Token + 龙虾积分计费。

为什么需要：
- 爆款TVC pipeline (skills/comfly_veo3_daihuo_video) 内部会调 Comfly 的 4 个端点：
  POST /v1/chat/completions       (分镜规划，按 token usage 计费)
  POST /v1/images/generations     (分镜图，按 per_call 计费)
  POST /v2/videos/generations     (Veo 视频提交，按 per_call 计费)
  GET  /v2/videos/generations/{id}(Veo 任务轮询，不计费)
- 之前每个用户必须自己在「技能商店」配 Comfly API Key，按 Comfly 账户余额扣费。
- 现在改成统一走云端 server token (env: COMFLY_API_KEY[_<GROUP>])，按 comfly_pricing.json 扣龙虾积分。

设计：
- 透明转发：proxy 不重新组装 body，直接把客户端构造好的 body POST 给 Comfly，只替换 Authorization。
- 计费：① 调用前按估算预扣 → ② 调 Comfly → ③ chat 按 usage 结算差额；image/video 按 per_call 实扣（估算==实际）；失败全额退款。
- 鉴权：用户 JWT。
- token_group：按 model 在 comfly_pricing.json 配置的 token_group 选用对应 env 的 Key。
"""
from __future__ import annotations

import asyncio
import ast
import json
import logging
import mimetypes
import os
import sys
import tempfile
from collections import OrderedDict
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import SessionLocal, get_db
from ..models import Asset, User
from ..services.credit_ledger import append_credit_ledger
from ..services.credits_amount import quantize_credits, credits_json_float, user_balance_decimal
from ..services.model_usage_monitor import log_model_usage_event
from .assets import _save_bytes_or_tos
from .auth import ALGORITHM, get_current_user
from .mobile_identity import online_user_for_mobile_user

# 让本模块能 import mcp/ 下的 comfly_upstream
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mcp.comfly_upstream import (  # noqa: E402
    estimate_comfly_credits,
    get_comfly_config,
    lookup_comfly_model,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_PROXY_AUDIT_LOGGER = logging.getLogger("comfly_proxy_audit")

# Comfly 上游超时（与 pipeline 默认 poll 间隔对齐，video submit 通常很快返回 task_id）
_TIMEOUT_CHAT = 120.0
_TIMEOUT_IMAGE = 300.0
_TIMEOUT_OPENAI_OFFICIAL_IMAGE = 90.0
_TIMEOUT_SEEDREAM_IMAGE = 180.0
_TIMEOUT_FILE_UPLOAD = 120.0
_TIMEOUT_VIDEO_SUBMIT = 60.0
_TIMEOUT_OPENMIND_VIDEO_SUBMIT = 60.0
_TIMEOUT_VIDEO_POLL = 30.0
_MAX_PROXY_VIDEO_TASK_TRACK = 5000
_MAX_GROK_REFERENCE_BYTES = 30 * 1024 * 1024
_proxy_video_task_meta: "OrderedDict[str, Tuple[str, str]]" = OrderedDict()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "y"}


def _env_int(name: str, default: int, *, min_value: int = 1, max_value: int = 5) -> int:
    try:
        value = int(str(os.environ.get(name) or "").strip() or default)
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(max_value, value))


def _should_deduct_credits() -> bool:
    """与 capabilities.py / sutui_chat_proxy.py 一致：在线版独立认证才扣积分。"""
    edition = (getattr(settings, "lobster_edition", None) or "online").strip().lower()
    return edition == "online" and getattr(settings, "lobster_independent_auth", True)


def _bearer_token_from_request(request: Request) -> str:
    auth_header = str(request.headers.get("Authorization") or "").strip()
    if not auth_header:
        raise HTTPException(
            status_code=401,
            detail="Authorization Bearer missing",
            headers={"WWW-Authenticate": "Bearer"},
        )
    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return parts[1].strip()


def _resolve_proxy_user_ids_from_request(
    request: Request,
    *,
    map_to_online_user: bool = False,
) -> Tuple[int, int]:
    token = _bearer_token_from_request(request)
    credentials_exception = HTTPException(
        status_code=401,
        detail="无法验证凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        request_user_id = int(payload.get("sub"))
    except (JWTError, ValueError, TypeError):
        raise credentials_exception

    db = SessionLocal()
    try:
        request_user = db.query(User).filter(User.id == request_user_id).first()
        if request_user is None:
            raise credentials_exception
        billing_user = online_user_for_mobile_user(db, request_user) if map_to_online_user else request_user
        return int(request_user.id), int(billing_user.id)
    finally:
        db.close()


def _do_pre_deduct_by_user_id(
    user_id: int,
    credits: int,
    *,
    capability_id: str,
    model: str,
    endpoint: str,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> Decimal:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == int(user_id)).first()
        if user is None:
            raise HTTPException(status_code=401, detail="用户不存在")
        return _do_pre_deduct(
            db,
            user,
            credits,
            capability_id=capability_id,
            model=model,
            endpoint=endpoint,
            extra_meta=extra_meta,
        )
    finally:
        db.close()


def _do_full_refund_by_user_id(
    user_id: int,
    *,
    pre: Decimal,
    capability_id: str,
    model: str,
    endpoint: str,
    error: str = "",
) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == int(user_id)).first()
        if user is None:
            raise HTTPException(status_code=401, detail="用户不存在")
        _do_full_refund(
            db,
            user,
            pre=pre,
            capability_id=capability_id,
            model=model,
            endpoint=endpoint,
            error=error,
        )
    finally:
        db.close()


def _do_settle_by_user_id(
    user_id: int,
    *,
    pre: Decimal,
    actual: int,
    capability_id: str,
    model: str,
    endpoint: str,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == int(user_id)).first()
        if user is None:
            raise HTTPException(status_code=401, detail="用户不存在")
        _do_settle(
            db,
            user,
            pre=pre,
            actual=actual,
            capability_id=capability_id,
            model=model,
            endpoint=endpoint,
            extra_meta=extra_meta,
        )
    finally:
        db.close()


async def _save_generated_images_best_effort_by_user_id(
    user_id: int,
    *,
    response_payload: Dict[str, Any],
    prompt: str,
    model: str,
    limit: int,
    exclude_urls: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        return await _save_generated_images_best_effort(
            db,
            user_id=user_id,
            response_payload=response_payload,
            prompt=prompt,
            model=model,
            limit=limit,
            exclude_urls=exclude_urls,
        )
    finally:
        db.close()


def _model_token_group(model_id: str) -> str:
    entry = lookup_comfly_model(model_id) or {}
    return (entry.get("token_group") or "").strip()


def _normalized_model_id(model_id: str) -> str:
    return (model_id or "").strip().lower().replace("_", "-")


def _collect_image_ref_values(value: Any, *, max_depth: int = 4) -> List[str]:
    refs: List[str] = []

    def add(item: Any) -> None:
        text = str(item or "").strip()
        if text and text not in refs:
            refs.append(text)

    def visit(item: Any, depth: int = 0) -> None:
        if item is None or depth > max_depth:
            return
        if isinstance(item, str):
            text = item.strip()
            if not text:
                return
            if text.startswith(("[", "{")):
                try:
                    visit(json.loads(text), depth + 1)
                    return
                except Exception:
                    try:
                        visit(ast.literal_eval(text), depth + 1)
                        return
                    except Exception:
                        pass
            add(text)
            return
        if isinstance(item, (list, tuple, set)):
            for sub in item:
                visit(sub, depth + 1)
            return
        if isinstance(item, dict):
            for key in ("url", "image_url", "image", "source_url", "public_url", "file_url"):
                if key in item:
                    visit(item.get(key), depth + 1)
            return
        add(item)

    visit(value)
    return refs


def _normalized_image_refs_from_payload(payload: Dict[str, Any]) -> Tuple[str, List[str]]:
    refs: List[str] = []
    for key in ("image", "image_url", "image_urls", "images"):
        for value in _collect_image_ref_values(payload.get(key)):
            if value not in refs:
                refs.append(value)
    primary = refs[0] if refs else ""
    return primary, refs


def _image_generation_model_attempts(model: str, body: Optional[Dict[str, Any]] = None) -> List[str]:
    """Return billing model ids to try for one image generation request."""
    normalized = _normalized_model_id(model)
    if normalized in {"gpt-image-2", "gpt-image2", "gpt-image", "gpt-image-2-vip", "gpt-image-2-official"}:
        attempts: List[str] = ["gpt-image-2", "doubao-seedream-5-0-260128"]
        attempts.extend(["gpt-image-2", "gpt-image-2-openmindapi", "gpt-image-2-yunwu"])
        ordered: List[str] = []
        for item in attempts:
            if item and item not in ordered:
                ordered.append(item)
        return ordered
    return [model]


def _audit(event: str, **kw: Any) -> None:
    """JSONL 审计日志（与 sutui_audit 同 logger 风格）。"""
    try:
        payload = {"event": event, **kw}
        _PROXY_AUDIT_LOGGER.info("[comfly_proxy_audit] %s", json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        pass


def _check_request_authorized_for_billing(request: Request) -> None:
    """与 /capabilities/pre-deduct 同口径：非本机回环且无 X-Lobster-Mcp-Billing 时拒绝，避免被外部直接打。

    爆款TVC proxy 是用户客户端发来的，只要带有效 JWT 即可，不强制 billing key（与 sutui_chat_proxy 一致）。
    本函数预留扩展点：如未来要求强制 billing key，把判断打开即可。
    """
    return None


def _do_pre_deduct(
    db: Session, user: User, credits: int, *,
    capability_id: str, model: str, endpoint: str, extra_meta: Optional[Dict[str, Any]] = None,
) -> Decimal:
    """直接扣账（与 capabilities.py force_credits 路径一致）。返回实际扣的 Decimal。"""
    if not _should_deduct_credits() or credits <= 0:
        return Decimal("0")
    fc = quantize_credits(credits)
    db.refresh(user)
    if user_balance_decimal(user) < fc:
        raise HTTPException(
            status_code=402,
            detail=f"积分不足：本次预扣 {float(fc)}，当前余额 {float(user_balance_decimal(user))}。",
        )
    user.credits = user_balance_decimal(user) - fc
    bal = quantize_credits(user.credits)
    append_credit_ledger(
        db, user.id, -fc, "pre_deduct", bal,
        description=f"Comfly proxy 预扣 ({endpoint})",
        ref_type="comfly_proxy",
        meta={
            "capability_id": capability_id, "model": model, "endpoint": endpoint,
            "pre_estimated": credits_json_float(fc), "upstream": "comfly",
            **(extra_meta or {}),
        },
    )
    db.commit()
    return fc


def _do_settle(
    db: Session, user: User, *, pre: Decimal, actual: int,
    capability_id: str, model: str, endpoint: str, extra_meta: Optional[Dict[str, Any]] = None,
) -> None:
    """实际 vs 预扣的差额结算。actual<pre 退差额，actual>pre 再扣差额。"""
    if not _should_deduct_credits():
        return
    actual_dec = quantize_credits(max(0, int(actual)))
    delta = actual_dec - pre  # >0 需补扣，<0 需退款
    if delta == 0:
        return
    db.refresh(user)
    if delta > 0:
        # 补扣：余额不足时不阻断（已经走完上游），只能记账让管理员对账
        cur_bal = user_balance_decimal(user)
        deduct_now = min(cur_bal, delta) if cur_bal > 0 else Decimal("0")
        user.credits = cur_bal - deduct_now
        bal = quantize_credits(user.credits)
        append_credit_ledger(
            db, user.id, -deduct_now, "settle", bal,
            description=f"Comfly proxy 结算补扣 ({endpoint}) actual={actual} pre={float(pre)}",
            ref_type="comfly_proxy",
            meta={
                "capability_id": capability_id, "model": model, "endpoint": endpoint,
                "pre_estimated": credits_json_float(pre), "actual": credits_json_float(actual_dec),
                "delta": credits_json_float(delta), "upstream": "comfly",
                **(extra_meta or {}),
            },
        )
        if deduct_now < delta:
            logger.warning(
                "[comfly_proxy] 用户 %s 结算补扣不足额：需 %s，仅扣 %s（余额耗尽）",
                user.id, float(delta), float(deduct_now),
            )
    else:
        # 退款
        refund_amt = -delta
        user.credits = user_balance_decimal(user) + refund_amt
        bal = quantize_credits(user.credits)
        append_credit_ledger(
            db, user.id, refund_amt, "refund", bal,
            description=f"Comfly proxy 结算退款 ({endpoint}) actual={actual} pre={float(pre)}",
            ref_type="comfly_proxy",
            meta={
                "capability_id": capability_id, "model": model, "endpoint": endpoint,
                "pre_estimated": credits_json_float(pre), "actual": credits_json_float(actual_dec),
                "delta": credits_json_float(delta), "upstream": "comfly",
                **(extra_meta or {}),
            },
        )
    db.commit()


def _do_full_refund(
    db: Session, user: User, *, pre: Decimal,
    capability_id: str, model: str, endpoint: str, error: str = "",
) -> None:
    if not _should_deduct_credits() or pre <= 0:
        return
    db.refresh(user)
    user.credits = user_balance_decimal(user) + pre
    bal = quantize_credits(user.credits)
    append_credit_ledger(
        db, user.id, pre, "refund", bal,
        description=f"Comfly proxy 调用失败全额退款 ({endpoint})",
        ref_type="comfly_proxy",
        meta={
            "capability_id": capability_id, "model": model, "endpoint": endpoint,
            "refunded": credits_json_float(pre), "upstream": "comfly",
            "error": (error or "")[:500],
        },
    )
    db.commit()


async def _comfly_request(
    method: str, url: str, body: Optional[Dict[str, Any]], headers: Dict[str, str], timeout: float,
) -> Dict[str, Any]:
    """统一封装 httpx 调用 Comfly。失败抛 RuntimeError，含状态码与文本片段。"""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method.upper() == "GET":
                r = await client.get(url, headers=headers)
            else:
                r = await client.post(url, headers=headers, json=body or {})
    except httpx.TimeoutException as exc:
        raise RuntimeError(f"Comfly timeout after {int(timeout)}s") from exc
    if r.status_code >= 400:
        raise RuntimeError(f"Comfly HTTP {r.status_code}: {(r.text or '')[:500]}")
    try:
        return r.json() if r.content else {}
    except Exception:
        return {"_raw_text": r.text}


async def _yunwu_request(
    method: str, url: str, body: Optional[Dict[str, Any]], headers: Dict[str, str], timeout: float,
) -> Dict[str, Any]:
    """Yunwu HTTP wrapper. Keep the provider name out of Comfly error text."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method.upper() == "GET":
                r = await client.get(url, headers=headers)
            else:
                r = await client.post(url, headers=headers, json=body or {})
    except httpx.TimeoutException as exc:
        raise RuntimeError(f"Yunwu timeout after {int(timeout)}s") from exc
    if r.status_code >= 400:
        raise RuntimeError(f"Yunwu HTTP {r.status_code}: {(r.text or '')[:500]}")
    try:
        return r.json() if r.content else {}
    except Exception:
        return {"_raw_text": r.text}


async def _comfly_multipart_request(
    url: str,
    data: Dict[str, str],
    files: List[Tuple[str, Tuple[Any, ...]]],
    headers: Dict[str, str],
    timeout: float,
) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, headers=headers, data=data, files=files)
    except httpx.TimeoutException as exc:
        raise RuntimeError(f"Comfly timeout after {int(timeout)}s") from exc
    if r.status_code >= 400:
        raise RuntimeError(f"Comfly HTTP {r.status_code}: {(r.text or '')[:500]}")
    try:
        return r.json() if r.content else {}
    except Exception:
        return {"_raw_text": r.text}


async def _yunwu_multipart_request(
    url: str,
    data: Dict[str, str],
    files: List[Tuple[str, Tuple[Any, ...]]],
    headers: Dict[str, str],
    timeout: float,
) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, headers=headers, data=data, files=files)
    except httpx.TimeoutException as exc:
        raise RuntimeError(f"Yunwu timeout after {int(timeout)}s") from exc
    if r.status_code >= 400:
        raise RuntimeError(f"Yunwu HTTP {r.status_code}: {(r.text or '')[:500]}")
    try:
        return r.json() if r.content else {}
    except Exception:
        return {"_raw_text": r.text}


def _comfly_url(path: str, model: str = "") -> str:
    base, _ = get_comfly_config(_model_token_group(model))
    if not base:
        raise HTTPException(503, "服务端未配置 Comfly：缺少环境变量 COMFLY_API_BASE")
    return base.rstrip("/") + path


def _comfly_headers(model: str = "") -> Dict[str, str]:
    headers = _comfly_auth_headers(model)
    headers["Content-Type"] = "application/json"
    return headers


def _comfly_auth_headers(model: str = "") -> Dict[str, str]:
    _, key = get_comfly_config(_model_token_group(model))
    if not key:
        raise HTTPException(503, "服务端未配置 Comfly Key：缺少环境变量 COMFLY_API_KEY")
    return {"Authorization": f"Bearer {key}", "Accept": "application/json"}


def _yunwu_base_url() -> str:
    base = (os.environ.get("YUNWU_API_BASE") or "https://yunwu.ai").strip().rstrip("/")
    return base or "https://yunwu.ai"


def _yunwu_api_key() -> str:
    key = (os.environ.get("YUNWU_API_KEY") or os.environ.get("COMFLY_API_KEY_YUNWU") or "").strip()
    if not key:
        raise HTTPException(503, "Server missing YUNWU_API_KEY")
    return key


def _yunwu_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_yunwu_api_key()}", "Accept": "application/json", "Content-Type": "application/json"}


def _yunwu_auth_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_yunwu_api_key()}", "Accept": "application/json"}


def _is_retryable_image_error(exc: BaseException) -> bool:
    msg = str(exc or "").lower()
    if (
        "comfly http 400" in msg
        or "comfly http 401" in msg
        or "comfly http 403" in msg
        or "comfly http 404" in msg
        or "comfly http 451" in msg
        or "policyviolation" in msg
        or "copyright restrictions" in msg
    ):
        return False
    retry_tokens = (
        "comfly http 408",
        "comfly http 409",
        "comfly http 425",
        "comfly http 429",
        "comfly http 5",
        "timeout",
        "connect",
        "connection",
        "read",
        "network",
        "new_api_error",
        "unknown_error",
        "upstream",
        "上游",
        "未接收到上游响应内容",
    )
    return any(token in msg for token in retry_tokens)


def _public_image_failure_detail() -> str:
    return "图片生成失败，已自动重试但仍未成功，请稍后重试或切换模型。"


def _openmind_image_fallback_enabled() -> bool:
    return _env_bool("OPENMIND_IMAGE_FALLBACK_ENABLED", False) and bool((os.environ.get("OPENMIND_API_KEY") or "").strip())


def _should_try_openmind_image_fallback(attempt_model: str, entry: Dict[str, Any]) -> bool:
    api_format = str((entry or {}).get("api_format") or "").strip().lower()
    if api_format in {"openai_official", "seedream_large"}:
        return False
    return True


def _image_attempt_route_meta(entry: Dict[str, Any]) -> Tuple[str, str, str]:
    api_format = str((entry or {}).get("api_format") or "").strip().lower()
    token_group = str((entry or {}).get("token_group") or "").strip().lower()
    if api_format == "openai_official":
        return "openai_official", "openai_official", "openai_official"
    if api_format == "seedream_large":
        return "comfly", "seedream_large", "comfly"
    channel = token_group or "comfly"
    return channel, channel, "comfly"


def _image_retry_attempts_for_entry(entry: Dict[str, Any], default_attempts: int) -> int:
    api_format = str((entry or {}).get("api_format") or "").strip().lower()
    if api_format in {"openai_official", "seedream_large"}:
        return 1
    return max(1, int(default_attempts or 1))


def _image_timeout_for_entry(entry: Dict[str, Any], default_timeout: float) -> float:
    api_format = str((entry or {}).get("api_format") or "").strip().lower()
    token_group = str((entry or {}).get("token_group") or "").strip().lower()
    upstream_model = str((entry or {}).get("comfly_model") or "").strip().lower()
    if api_format == "dalle" and not token_group and upstream_model == "gpt-image-2":
        return 120.0
    if api_format == "seedream_large":
        return _TIMEOUT_SEEDREAM_IMAGE
    return default_timeout


def _openmind_image_url() -> str:
    base = (os.environ.get("OPENMIND_API_BASE") or "https://www.openmindapi.com").strip().rstrip("/")
    return (base or "https://www.openmindapi.com") + "/v1/images/generations"


def _openmind_image_headers() -> Dict[str, str]:
    key = (os.environ.get("OPENMIND_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENMIND_API_KEY is not configured")
    return {
        "User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36",
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _openmind_image_body(source_body: Dict[str, Any]) -> Dict[str, Any]:
    prompt = str(source_body.get("prompt") or "").strip()
    if not prompt:
        raise RuntimeError("OpenMind image fallback missing prompt")
    body: Dict[str, Any] = {
        "model": (os.environ.get("OPENMIND_IMAGE_MODEL") or "gpt-image-2").strip() or "gpt-image-2",
        "prompt": prompt,
        "size": _coerce_openmind_image_size(
            source_body.get("size")
            or source_body.get("image_size")
            or source_body.get("aspect_ratio")
            or source_body.get("ratio")
            or "1024x1024"
        ),
        "n": int(source_body.get("n") or 1),
        "response_format": str(source_body.get("response_format") or "url").strip() or "url",
    }
    image_url, image_urls = _normalized_image_refs_from_payload(source_body)
    if image_url:
        body["image_url"] = image_url
        body["image"] = image_url
    if image_urls:
        body["image_urls"] = image_urls
    return body


def _extract_image_result_urls(payload: Any) -> List[str]:
    result: List[str] = []

    def add(value: Any) -> None:
        url = str(value or "").strip()
        if not url:
            return
        if url.startswith("data:image/") or url.startswith(("http://", "https://")):
            if url not in result:
                result.append(url)

    def visit(value: Any, depth: int = 0) -> None:
        if value is None or depth > 6:
            return
        if isinstance(value, str):
            add(value)
            if value.strip().startswith(("{", "[")):
                try:
                    visit(json.loads(value), depth + 1)
                except Exception:
                    pass
            return
        if isinstance(value, list):
            for item in value:
                visit(item, depth + 1)
            return
        if not isinstance(value, dict):
            return
        for key in ("url", "image_url", "source_url", "public_url", "file_url", "b64_json"):
            if key in value:
                val = value.get(key)
                if key == "b64_json" and val:
                    add(f"data:image/png;base64,{val}")
                else:
                    add(val)
        for item in value.values():
            visit(item, depth + 1)

    visit(payload)
    return result


def _guess_image_ext(content_type: str, url: str) -> str:
    lower_url = str(url or "").split("?", 1)[0].split("#", 1)[0].lower()
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        if lower_url.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    ct = (content_type or "").lower()
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "webp" in ct:
        return ".webp"
    if "gif" in ct:
        return ".gif"
    return ".png"


async def _download_image_bytes(url: str) -> Tuple[bytes, str, str]:
    src = str(url or "").strip()
    if src.startswith("data:image/"):
        header, _, b64 = src.partition(",")
        media = header[5:].split(";", 1)[0] if ":" in header else "image/png"
        import base64
        return base64.b64decode(b64), media or "image/png", ".png"
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True, trust_env=False) as client:
        resp = await client.get(src, headers={"User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36"})
    resp.raise_for_status()
    media_type = (resp.headers.get("content-type") or "image/png").split(";", 1)[0].strip() or "image/png"
    return resp.content, media_type, _guess_image_ext(media_type, src)


async def _persist_generated_image_asset(
    db: Session,
    *,
    user_id: int,
    url: str,
    prompt: str,
    model: str,
    job_id: str = "",
) -> Dict[str, Any]:
    data, media_type, ext = await _download_image_bytes(url)
    aid, fname_or_key, fsize, tos_public_url = _save_bytes_or_tos(data, ext, media_type)
    if not tos_public_url:
        local_path = Path(__file__).resolve().parent.parent.parent.parent / "assets" / fname_or_key
        try:
            if local_path.exists():
                local_path.unlink()
        except OSError:
            pass
        raise RuntimeError("图片结果保存失败：TOS 公网链接不可用")
    asset = Asset(
        asset_id=aid,
        user_id=user_id,
        filename=fname_or_key,
        media_type="image",
        file_size=fsize,
        source_url=tos_public_url,
        prompt=prompt,
        model=model,
        tags="auto,image_generate,miniprogram",
        meta={"source": "miniprogram_image_generate", "job_id": job_id, "origin_url": url},
    )
    db.add(asset)
    db.flush()
    return {
        "asset_id": aid,
        "media_type": "image",
        "url": tos_public_url,
        "source_url": tos_public_url,
        "file_size": fsize,
        "prompt": prompt,
        "model": model,
    }


async def _save_generated_images_best_effort(
    db: Session,
    *,
    user_id: int,
    response_payload: Dict[str, Any],
    prompt: str,
    model: str,
    limit: int,
    exclude_urls: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    saved_assets: List[Dict[str, Any]] = []
    excluded = {str(url or "").strip().rstrip("/") for url in (exclude_urls or []) if str(url or "").strip()}
    for url in _extract_image_result_urls(response_payload)[: max(1, min(9, int(limit or 1)))]:
        if str(url or "").strip().rstrip("/") in excluded:
            logger.info("[image_generate] skip echoed reference image url=%s", str(url)[:120])
            continue
        try:
            saved_assets.append(
                await _persist_generated_image_asset(
                    db,
                    user_id=user_id,
                    url=url,
                    prompt=prompt,
                    model=model,
                )
            )
        except Exception as exc:
            logger.warning("[image_generate] save generated image failed user_id=%s url=%s err=%s", user_id, url[:120], exc)
    if saved_assets:
        db.commit()
    return saved_assets


async def _openmind_image_request(source_body: Dict[str, Any]) -> Dict[str, Any]:
    body = _openmind_image_body(source_body)
    async with httpx.AsyncClient(timeout=_TIMEOUT_IMAGE, trust_env=False) as client:
        resp = await client.post(_openmind_image_url(), headers=_openmind_image_headers(), json=body)
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenMind HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    try:
        payload = resp.json() if resp.content else {}
    except Exception:
        payload = {"_raw_text": resp.text}
    if isinstance(payload, dict):
        payload.setdefault("fallback_used", True)
        payload.setdefault("fallback_provider", "openmind")
    return payload





def _openmind_video_base_url() -> str:
    base = (os.environ.get("OPENMIND_API_BASE") or "https://www.openmindapi.com").strip().rstrip("/")
    return base or "https://www.openmindapi.com"


def _openmind_video_api_key() -> str:
    key = (os.environ.get("OPENMIND_API_KEY") or "").strip()
    if not key:
        raise HTTPException(503, "Server missing OPENMIND_API_KEY")
    return key


def _openmind_video_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_openmind_video_api_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36",
    }


def _openmind_enabled_for_video() -> bool:
    raw = (os.environ.get("OPENMIND_VIDEO_ENABLED") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


def _openmind_video_model(model: str) -> str:
    raw = (model or "").strip()
    low = raw.lower().replace("_", "-").replace(" ", "")
    explicit = {
        "veo3.1": os.environ.get("OPENMIND_VEO31_MODEL") or "veo31",
        "veo3.1-fast": os.environ.get("OPENMIND_VEO31_FAST_MODEL") or "veo31-fast",
        "veo31": os.environ.get("OPENMIND_VEO31_MODEL") or "veo31",
        "veo31-fast": os.environ.get("OPENMIND_VEO31_FAST_MODEL") or "veo31-fast",
        "grok-video-3": os.environ.get("OPENMIND_GROK_VIDEO_MODEL") or "grok-imagine-video-1.5-preview",
        "grok-imagine-video-1.5-preview": os.environ.get("OPENMIND_GROK_VIDEO_MODEL") or "grok-imagine-video-1.5-preview",
        "doubao-seedance-2-0-260128": os.environ.get("OPENMIND_SEEDANCE_MODEL") or "doubao-seedance-2-0-260128",
        "doubao-seedance-2-0-fast-260128": os.environ.get("OPENMIND_SEEDANCE_FAST_MODEL") or "doubao-seedance-2-0-260128",
    }
    if low in explicit:
        return (explicit[low] or "").strip() or raw
    return raw


def _openmind_video_body(body: Dict[str, Any], model: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    forwarded = dict(body or {})
    forwarded["model"] = _openmind_video_model(model)
    for key in ("seconds", "duration"):
        if key in forwarded and forwarded.get(key) is not None:
            try:
                val = float(forwarded.get(key))
                forwarded[key] = int(val) if val.is_integer() else val
            except (TypeError, ValueError):
                forwarded.pop(key, None)
    if "duration" not in forwarded and forwarded.get("seconds") is not None:
        forwarded["duration"] = forwarded.get("seconds")
    if "seconds" not in forwarded and forwarded.get("duration") is not None:
        forwarded["seconds"] = forwarded.get("duration")
    if not forwarded.get("aspect_ratio") and forwarded.get("ratio"):
        forwarded["aspect_ratio"] = forwarded.get("ratio")
    forwarded.setdefault("aspect_ratio", "9:16")
    forwarded.setdefault("resolution", "720p")
    if not forwarded.get("size"):
        forwarded["size"] = "720x1280" if str(forwarded.get("aspect_ratio") or "") == "9:16" else "1280x720"
    image_ref = forwarded.get("image") or forwarded.get("image_url")
    images = forwarded.get("images")
    if not isinstance(images, list):
        images = []
    images = [str(x).strip() for x in images if str(x or "").strip()]
    if image_ref and not images:
        images = [str(image_ref).strip()]
    if images:
        forwarded["images"] = images
        forwarded.setdefault("image", images[0])
        forwarded.setdefault("image_url", images[0])
    return forwarded


async def _openmind_video_submit(body: Dict[str, Any], model: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    if not _openmind_enabled_for_video():
        raise RuntimeError("OpenMind video channel disabled")
    upstream_body = _openmind_video_body(body, model, entry)
    url = f"{_openmind_video_base_url()}/v1/videos"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_OPENMIND_VIDEO_SUBMIT, follow_redirects=True) as client:
            r = await client.post(url, headers=_openmind_video_headers(), json=upstream_body)
    except httpx.TimeoutException as exc:
        raise RuntimeError(f"OpenMind videos submit timeout after {_TIMEOUT_OPENMIND_VIDEO_SUBMIT}s") from exc
    except httpx.TransportError as exc:
        raise RuntimeError(f"OpenMind videos transport error: {exc!r}") from exc
    if r.status_code >= 400:
        raise RuntimeError(f"OpenMind videos HTTP {r.status_code}: {(r.text or '')[:500]}")
    try:
        payload = r.json() if r.content else {}
    except Exception:
        payload = {"_raw_text": r.text}
    if isinstance(payload, dict):
        payload.setdefault("_provider", "openmind")
        payload.setdefault("_requested_model", upstream_body.get("model"))
    return payload


async def _openmind_video_poll(task_id: str) -> Dict[str, Any]:
    if not _openmind_enabled_for_video():
        raise RuntimeError("OpenMind video channel disabled")
    url = f"{_openmind_video_base_url()}/v1/videos/{task_id}"
    async with httpx.AsyncClient(timeout=_TIMEOUT_VIDEO_POLL, follow_redirects=True) as client:
        r = await client.get(url, headers=_openmind_video_headers())
    if r.status_code >= 400:
        raise RuntimeError(f"OpenMind videos poll HTTP {r.status_code}: {(r.text or '')[:500]}")
    try:
        payload = r.json() if r.content else {}
    except Exception:
        payload = {"_raw_text": r.text}
    if isinstance(payload, dict):
        payload.setdefault("_provider", "openmind")
    return payload


def _task_id_from_response(resp: Dict[str, Any]) -> str:
    if not isinstance(resp, dict):
        return ""
    for key in ("id", "task_id", "video_id", "job_id", "request_id", "generation_id", "run_id"):
        value = resp.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    data = resp.get("data")
    if isinstance(data, dict):
        for key in ("id", "task_id", "video_id", "job_id", "request_id", "generation_id", "run_id"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _remember_proxy_video_task(task_id: str, api_kind: str = "", model: str = "") -> None:
    tid = (task_id or "").strip()
    if not tid:
        return
    _proxy_video_task_meta[tid] = ((api_kind or "").strip(), (model or "").strip())
    while len(_proxy_video_task_meta) > _MAX_PROXY_VIDEO_TASK_TRACK:
        _proxy_video_task_meta.popitem(last=False)


def _proxy_video_task_hint(task_id: str) -> Tuple[str, str]:
    return _proxy_video_task_meta.get((task_id or "").strip(), ("", ""))

def _require_model_entry(model: str) -> Dict[str, Any]:
    entry = lookup_comfly_model(model)
    if not entry:
        raise HTTPException(400, f"模型 {model} 未在 comfly_pricing.json 注册，无法计费")
    return entry


def _upstream_model(model: str, entry: Dict[str, Any]) -> str:
    return str(entry.get("comfly_model") or model).strip() or model


def _coerce_grok_video_resolution(raw: Any) -> str:
    s = str(raw or "").strip().lower().replace(" ", "")
    if "480" in s:
        return "480p"
    return "720p"


def _is_grok_api_format(entry: Dict[str, Any]) -> bool:
    return str((entry or {}).get("api_format") or "").strip().lower() == "grok"


def _coerce_grok15_model(duration: Any) -> str:
    try:
        seconds = int(float(duration or 0))
    except (TypeError, ValueError):
        seconds = 6
    if seconds <= 6:
        return "grok-1.5-video-6s"
    if seconds <= 10:
        return "grok-1.5-video-10s"
    return "grok-1.5-video-15s"


def _coerce_video_size_from_ratio(raw: Any) -> str:
    ratio = str(raw or "").strip().lower().replace(" ", "")
    mapping = {
        "16:9": "1280x720",
        "9:16": "720x1280",
        "1:1": "1024x1024",
        "4:3": "1280x960",
        "3:4": "960x1280",
        "3:2": "1200x800",
        "2:3": "800x1200",
    }
    return mapping.get(ratio, "720x1280")


def _coerce_openmind_image_size(raw: Any) -> str:
    value = str(raw or "").strip().lower().replace(" ", "")
    if "x" in value:
        parts = value.split("x", 1)
        try:
            width = int(parts[0])
            height = int(parts[1])
        except (TypeError, ValueError):
            width = 0
            height = 0
        if width > 0 and height > 0:
            if width % 16 == 0 and height % 16 == 0:
                return f"{width}x{height}"
    mapping = {
        "1:1": "1024x1024",
        "4:3": "1024x768",
        "3:4": "768x1024",
        "16:9": "1536x864",
        "9:16": "864x1536",
        "3:2": "1152x768",
        "2:3": "768x1152",
    }
    return mapping.get(value, "1024x1024")


def _coerce_image_edit_size(raw: Any) -> str:
    value = str(raw or "").strip().lower().replace(" ", "")
    if "x" in value:
        parts = value.split("x", 1)
        try:
            width = int(parts[0])
            height = int(parts[1])
        except (TypeError, ValueError):
            width = 0
            height = 0
        if width > 0 and height > 0:
            return f"{width}x{height}"
    mapping = {
        "1:1": "1024x1024",
        "4:3": "1440x1080",
        "3:4": "1080x1440",
        "16:9": "1920x1080",
        "9:16": "1080x1920",
        "3:2": "1440x960",
        "2:3": "960x1440",
    }
    return mapping.get(value, "1024x1024")


def _first_grok_reference(forwarded: Dict[str, Any]) -> str:
    primary, refs = _normalized_image_refs_from_payload(forwarded)
    if refs:
        return refs[0]
    return primary


def _is_http_url(value: str) -> bool:
    lower = str(value or "").strip().lower()
    return lower.startswith("http://") or lower.startswith("https://")


async def _download_reference_url_to_temp_file(url: str) -> Tuple[Path, str, str]:
    src = str(url or "").strip()
    if not _is_http_url(src):
        raise RuntimeError("reference image url must start with http:// or https://")
    tmp_path = ""
    total = 0
    media_type = "image/jpeg"
    suffix = ".jpg"
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True, trust_env=False) as client:
            async with client.stream("GET", src, headers={"User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36"}) as resp:
                resp.raise_for_status()
                media_type = (resp.headers.get("content-type") or "image/jpeg").split(";", 1)[0].strip() or "image/jpeg"
                if not media_type.lower().startswith("image/"):
                    raise RuntimeError(f"reference url is not an image: {media_type}")
                suffix = _guess_image_ext(media_type, src)
                with tempfile.NamedTemporaryFile(prefix="grok-reference-", suffix=suffix, delete=False) as tmp:
                    tmp_path = tmp.name
                    async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > _MAX_GROK_REFERENCE_BYTES:
                            raise RuntimeError("reference image exceeds max size")
                        tmp.write(chunk)
        if total <= 0:
            raise RuntimeError("reference image download is empty")
        return Path(tmp_path), f"reference{suffix}", media_type
    except Exception:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass
        raise


async def _reference_url_to_file_tuple(url: str) -> Tuple[str, bytes, str]:
    data, media_type, ext = await _download_image_bytes(url)
    filename = f"reference{ext or '.png'}"
    return filename, data, media_type


async def _build_image_edit_request_parts(
    body: Dict[str, Any],
    model: str,
    entry: Dict[str, Any],
    reference_urls: List[str],
) -> Tuple[Dict[str, str], List[Tuple[str, Tuple[Any, ...]]]]:
    forwarded = _body_for_upstream_model(body, model, entry)
    prompt = str(forwarded.get("prompt") or body.get("prompt") or "").strip()
    raw_size = (
        forwarded.get("size")
        or forwarded.get("image_size")
        or forwarded.get("aspect_ratio")
        or forwarded.get("ratio")
        or body.get("size")
        or body.get("image_size")
        or body.get("aspect_ratio")
        or body.get("ratio")
        or "1024x1024"
    )
    api_format = str((entry or {}).get("api_format") or "").strip().lower()
    if api_format == "seedream_large":
        image_size = _coerce_seedream_large_image_size(raw_size)
    else:
        image_size = _coerce_image_edit_size(raw_size)
    try:
        num_images = max(1, int(forwarded.get("num_images") or forwarded.get("n") or body.get("n") or 1))
    except (TypeError, ValueError):
        num_images = 1
    data: Dict[str, str] = {
        "model": _upstream_model(model, entry),
        "prompt": prompt,
        "size": image_size,
        "n": str(num_images),
    }
    response_format = str(forwarded.get("response_format") or body.get("response_format") or "").strip()
    if response_format:
        data["response_format"] = response_format
    files: List[Tuple[str, Tuple[Any, ...]]] = []
    for index, ref in enumerate(reference_urls):
        filename, raw, media_type = await _reference_url_to_file_tuple(ref)
        field_name = "image" if index == 0 else "image[]"
        files.append((field_name, (filename, raw, media_type)))
    if not files:
        raise RuntimeError("image edit request missing reference image")
    return data, files


async def _build_comfly_grok15_multipart(
    body: Dict[str, Any],
    model: str,
    entry: Dict[str, Any],
) -> Tuple[Dict[str, str], List[Tuple[str, Tuple[Any, ...]]], str, List[Any], List[Path]]:
    forwarded = dict(body or {})
    prompt = str(forwarded.get("prompt") or "").strip()
    duration = forwarded.get("duration") or forwarded.get("seconds") or 6
    upstream_model = _coerce_grok15_model(duration)
    ratio = forwarded.get("ratio") or forwarded.get("aspect_ratio") or "9:16"
    data: Dict[str, str] = {
        "model": upstream_model,
        "prompt": prompt,
        "size": _coerce_video_size_from_ratio(ratio),
    }
    files: List[Tuple[str, Tuple[Any, ...]]] = []
    open_files: List[Any] = []
    temp_paths: List[Path] = []
    first_ref = _first_grok_reference(forwarded)
    if first_ref:
        path = Path(first_ref)
        if path.exists() and path.is_file():
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            handle = path.open("rb")
            open_files.append(handle)
            files.append(("input_reference", (path.name, handle, content_type)))
        elif _is_http_url(first_ref):
            temp_path, filename, content_type = await _download_reference_url_to_temp_file(first_ref)
            temp_paths.append(temp_path)
            handle = temp_path.open("rb")
            open_files.append(handle)
            files.append(("input_reference", (filename, handle, content_type)))
        elif str(first_ref).startswith("data:image/"):
            filename, raw, content_type = await _reference_url_to_file_tuple(first_ref)
            files.append(("input_reference", (filename, raw, content_type)))
        else:
            raise RuntimeError("Grok 1.5 video requires input_reference as a file, local path, data image, or http image URL")
    return data, files, upstream_model, open_files, temp_paths


async def _submit_comfly_grok15_video(
    body: Dict[str, Any],
    model: str,
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    data, files, upstream_model, open_files, temp_paths = await _build_comfly_grok15_multipart(body, model, entry)
    try:
        resp = await _comfly_multipart_request(
            _comfly_url("/v1/videos", model),
            data,
            files,
            _comfly_auth_headers(model),
            _TIMEOUT_VIDEO_SUBMIT,
        )
    finally:
        for handle in open_files:
            try:
                handle.close()
            except Exception:
                pass
        for temp_path in temp_paths:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass
    if isinstance(resp, dict):
        resp.setdefault("_provider", "comfly")
        resp.setdefault("_api_format", "grok_v1")
        resp.setdefault("_requested_model", upstream_model)
    return resp


def _should_try_comfly_v1_poll_fallback(exc: Exception) -> bool:
    msg = str(exc or "").lower()
    return "http 404" in msg or "http 400" in msg


async def _poll_comfly_video_task(task_id: str, model: str = "", api_kind: str = "") -> Dict[str, Any]:
    tid = (task_id or "").strip()
    if not tid:
        raise HTTPException(400, "missing task_id")
    kind = (api_kind or "").strip().lower()
    route_model = (model or "").strip()
    if kind == "grok_v1":
        resp = await _comfly_request(
            "GET",
            _comfly_url(f"/v1/videos/{tid}", route_model or "grok-video-3"),
            None,
            _comfly_auth_headers(route_model or "grok-video-3"),
            _TIMEOUT_VIDEO_POLL,
        )
        if isinstance(resp, dict):
            resp.setdefault("_provider", "comfly")
            resp.setdefault("_api_format", "grok_v1")
        return resp
    try:
        resp = await _comfly_request(
            "GET",
            _comfly_url(f"/v2/videos/generations/{tid}", route_model),
            None,
            _comfly_headers(route_model),
            _TIMEOUT_VIDEO_POLL,
        )
        if isinstance(resp, dict):
            resp.setdefault("_provider", "comfly")
            resp.setdefault("_api_format", "veo_v2")
        return resp
    except Exception as exc:
        if not _should_try_comfly_v1_poll_fallback(exc):
            raise
        resp = await _comfly_request(
            "GET",
            _comfly_url(f"/v1/videos/{tid}", route_model or "grok-video-3"),
            None,
            _comfly_auth_headers(route_model or "grok-video-3"),
            _TIMEOUT_VIDEO_POLL,
        )
        if isinstance(resp, dict):
            resp.setdefault("_provider", "comfly")
            resp.setdefault("_api_format", "grok_v1")
        _remember_proxy_video_task(tid, "grok_v1", route_model or "grok-video-3")
        return resp


_COMFLY_IMAGE_RATIO_ALIASES = {
    "portrait_9_16": "9:16",
    "landscape_16_9": "16:9",
    "square_hd": "1:1",
    "square": "1:1",
    "vertical": "9:16",
    "portrait": "9:16",
    "horizontal": "16:9",
    "landscape": "16:9",
}

_COMFLY_IMAGE_RATIO_VALUES = {"1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3"}

_OPENAI_OFFICIAL_IMAGE_RATIO_TO_SIZE = {
    "1:1": "1024x1024",
    "4:3": "1536x1024",
    "3:4": "1024x1536",
    "16:9": "1536x1024",
    "9:16": "1024x1536",
    "3:2": "1536x1024",
    "2:3": "1024x1536",
}

_SEEDREAM_LARGE_RATIO_TO_SIZE = {
    "1:1": "2048x2048",
    "4:3": "2432x1664",
    "3:4": "1664x2432",
    "16:9": "2560x1440",
    "9:16": "1440x2560",
    "3:2": "2496x1664",
    "2:3": "1664x2496",
}


def _coerce_comfly_image_ratio_size(*values: Any) -> str:
    for raw in values:
        value = str(raw or "").strip().lower().replace(" ", "")
        if not value:
            continue
        value = _COMFLY_IMAGE_RATIO_ALIASES.get(value, value)
        if value in _COMFLY_IMAGE_RATIO_VALUES:
            return value
        if "x" in value:
            parts = value.split("x", 1)
            try:
                width = int(parts[0])
                height = int(parts[1])
            except (TypeError, ValueError):
                continue
            if width <= 0 or height <= 0:
                continue
            known = {
                "1:1": (1, 1),
                "4:3": (4, 3),
                "3:4": (3, 4),
                "16:9": (16, 9),
                "9:16": (9, 16),
                "3:2": (3, 2),
                "2:3": (2, 3),
            }
            ratio = width / height
            return min(known, key=lambda key: abs(ratio - (known[key][0] / known[key][1])))
    return "1:1"


def _openai_official_image_enabled() -> bool:
    return bool((os.environ.get("OPENAI_IMAGE_API_BASE") or "").strip())


def _openai_official_image_url(path: str) -> str:
    base = (os.environ.get("OPENAI_IMAGE_API_BASE") or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("OPENAI_IMAGE_API_BASE is not configured")
    if base.lower().endswith("/v1") and path.startswith("/v1/"):
        base = base[:-3].rstrip("/")
    return f"{base}{path}"


def _openai_official_image_headers(*, json_body: bool = True) -> Dict[str, str]:
    headers = {
        "Accept": "application/json",
    }
    key = (os.environ.get("OPENAI_IMAGE_API_KEY") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _coerce_openai_official_image_size(*values: Any) -> str:
    for raw in values:
        value = str(raw or "").strip().lower().replace(" ", "")
        if not value:
            continue
        if value in {"1024x1024", "1024x1536", "1536x1024"}:
            return value
        if "x" in value:
            parts = value.split("x", 1)
            try:
                width = int(parts[0])
                height = int(parts[1])
            except (TypeError, ValueError):
                continue
            if width > 0 and height > 0:
                if width == height:
                    return "1024x1024"
                return "1536x1024" if width > height else "1024x1536"
        ratio = _COMFLY_IMAGE_RATIO_ALIASES.get(value, value)
        if ratio in _OPENAI_OFFICIAL_IMAGE_RATIO_TO_SIZE:
            return _OPENAI_OFFICIAL_IMAGE_RATIO_TO_SIZE[ratio]
    return "1024x1024"


def _coerce_seedream_large_image_size(*values: Any) -> str:
    ratio = _coerce_comfly_image_ratio_size(*values)
    return _SEEDREAM_LARGE_RATIO_TO_SIZE.get(ratio, "2048x2048")


def _openai_official_image_body(source_body: Dict[str, Any]) -> Dict[str, Any]:
    prompt = str(source_body.get("prompt") or "").strip()
    if not prompt:
        raise RuntimeError("OpenAI official image channel missing prompt")
    try:
        num_images = max(1, int(source_body.get("n") or source_body.get("num_images") or 1))
    except (TypeError, ValueError):
        num_images = 1
    body: Dict[str, Any] = {
        "model": (os.environ.get("OPENAI_OFFICIAL_IMAGE_MODEL") or "gpt-image-2").strip() or "gpt-image-2",
        "prompt": prompt,
        "size": _coerce_openai_official_image_size(
            source_body.get("size"),
            source_body.get("image_size"),
            source_body.get("aspect_ratio"),
            source_body.get("ratio"),
        ),
        "n": num_images,
    }
    for key in ("quality", "background", "output_format", "output_compression", "moderation"):
        value = source_body.get(key)
        if value not in (None, ""):
            body[key] = value
    response_format = str(source_body.get("response_format") or "").strip()
    if response_format:
        body["response_format"] = response_format
    _primary_image, image_urls = _normalized_image_refs_from_payload(source_body)
    if image_urls:
        body["images"] = [{"image_url": url} for url in image_urls if _is_http_url(url) or str(url).startswith("data:image/")]
    return body


async def _openai_official_image_request(source_body: Dict[str, Any]) -> Dict[str, Any]:
    body = _openai_official_image_body(source_body)
    path = "/v1/images/edits" if body.get("images") else "/v1/images/generations"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_OPENAI_OFFICIAL_IMAGE, trust_env=False) as client:
            resp = await client.post(
                _openai_official_image_url(path),
                headers=_openai_official_image_headers(json_body=True),
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise RuntimeError(f"OpenAI official timeout after {int(_TIMEOUT_OPENAI_OFFICIAL_IMAGE)}s") from exc
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenAI official HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    try:
        payload = resp.json() if resp.content else {}
    except Exception:
        raise RuntimeError(f"OpenAI official returned invalid JSON: {(resp.text or '')[:300]}")
    if not isinstance(payload, dict):
        raise RuntimeError("OpenAI official returned non-object JSON payload")
    payload.setdefault("_provider", "openai_official")
    return payload


def _body_for_upstream_model(body: Dict[str, Any], model: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    upstream = _upstream_model(model, entry)
    forwarded = dict(body)
    forwarded["model"] = upstream
    api_format = str(entry.get("api_format") or "").strip().lower()
    model_low = str(upstream or model or "").strip().lower()
    if api_format == "openai_official":
        return _openai_official_image_body(forwarded)
    if api_format == "seedream_large":
        prompt = str(forwarded.get("prompt") or "").strip()
        if not prompt:
            raise RuntimeError("Seedream image fallback missing prompt")
        out: Dict[str, Any] = {
            "model": upstream,
            "prompt": prompt,
            "size": _coerce_seedream_large_image_size(
                forwarded.get("size"),
                forwarded.get("image_size"),
                forwarded.get("aspect_ratio"),
                forwarded.get("ratio"),
            ),
            "response_format": str(forwarded.get("response_format") or "url").strip() or "url",
        }
        for key in ("seed", "guidance_scale", "watermark"):
            value = forwarded.get(key)
            if value not in (None, ""):
                out[key] = value
        return out
    if api_format == "dalle" and ("gpt-image-2" in model_low or "gpt-image2" in model_low or "gptimage2" in model_low):
        prompt = str(forwarded.get("prompt") or "").strip()
        ratio = _coerce_comfly_image_ratio_size(
            forwarded.get("aspect_ratio"),
            forwarded.get("ratio"),
            forwarded.get("size"),
            forwarded.get("image_size"),
        )
        try:
            num_images = max(1, int(forwarded.get("num_images") or forwarded.get("n") or 1))
        except (TypeError, ValueError):
            num_images = 1
        image_url, image_urls = _normalized_image_refs_from_payload(forwarded)
        out: Dict[str, Any] = {
            "model": upstream,
            "prompt": prompt,
            "size": ratio,
            "num_images": num_images,
            "n": num_images,
            "response_format": str(forwarded.get("response_format") or "url"),
        }
        if image_url:
            out["image_url"] = image_url
            out["image"] = image_url
        if image_urls:
            out["image_urls"] = image_urls
        return out
    if api_format == "grok":
        prompt = str(forwarded.get("prompt") or "").strip()
        grok_body: Dict[str, Any] = {"model": upstream, "prompt": prompt}
        _primary_image, images = _normalized_image_refs_from_payload(forwarded)
        if images:
            grok_body["images"] = images[:1]
        if "ratio" not in forwarded and forwarded.get("aspect_ratio"):
            forwarded["ratio"] = forwarded.get("aspect_ratio")
        grok_body["ratio"] = str(forwarded.get("ratio") or "9:16")
        grok_body["resolution"] = _coerce_grok_video_resolution(forwarded.get("resolution"))
        try:
            duration = int(forwarded.get("duration") or forwarded.get("seconds") or 6)
        except (TypeError, ValueError):
            duration = 6
        grok_body["duration"] = 10 if duration == 10 else 6
        return grok_body
    return forwarded


def _image_reference_urls(body: Dict[str, Any]) -> List[str]:
    _primary, refs = _normalized_image_refs_from_payload(body)
    return refs


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

_CAPABILITY_FOR_BILLING = "comfly.daihuo.pipeline"


@router.post("/api/comfly-proxy/v1/files", summary="Comfly files upload transparent proxy")
async def proxy_files_upload(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    _check_request_authorized_for_billing(request)
    form = await request.form()
    data: Dict[str, str] = {}
    files: List[Tuple[str, Tuple[str, bytes, str]]] = []
    for key, value in form.multi_items():
        if hasattr(value, "filename"):
            raw = await value.read()
            if not raw:
                continue
            files.append(
                (
                    key,
                    (
                        value.filename or "file",
                        raw,
                        (getattr(value, "content_type", None) or "application/octet-stream"),
                    ),
                )
            )
        else:
            data[key] = str(value)
    if not files:
        raise HTTPException(400, "缺少 file 文件")

    try:
        resp = await _comfly_multipart_request(
            _comfly_url("/v1/files"),
            data,
            files,
            _comfly_auth_headers(),
            _TIMEOUT_FILE_UPLOAD,
        )
    except Exception as e:
        _audit("file_upload_failed", user_id=current_user.id, error=str(e)[:300])
        raise HTTPException(502, f"Comfly files 上传失败：{e}")

    _audit("file_upload_ok", user_id=current_user.id, file_count=len(files))
    return JSONResponse(resp)


@router.post("/api/comfly-proxy/v1/chat/completions", summary="Comfly chat 透明 proxy（按 token usage 计费）")
async def proxy_chat_completions(
    request: Request,
):
    _check_request_authorized_for_billing(request)
    body = await request.json()
    model = (body.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "缺少 model")
    entry = _require_model_entry(model)
    upstream_body = _body_for_upstream_model(body, model, entry)
    request_user_id, billing_user_id = _resolve_proxy_user_ids_from_request(request, map_to_online_user=False)

    # 预扣（按典型 token 估算）
    estimated = estimate_comfly_credits(model, {}, for_user=True) or 1
    pre = _do_pre_deduct_by_user_id(
        billing_user_id,
        estimated,
        capability_id=_CAPABILITY_FOR_BILLING,
        model=model,
        endpoint="chat",
    )
    _audit("chat_pre_deduct", user_id=billing_user_id, request_user_id=request_user_id, model=model, estimated=estimated)

    try:
        resp = await _comfly_request("POST", _comfly_url("/v1/chat/completions", model),
                                     upstream_body, _comfly_headers(model), _TIMEOUT_CHAT)
    except Exception as e:
        _do_full_refund_by_user_id(billing_user_id, pre=pre,
                        capability_id=_CAPABILITY_FOR_BILLING, model=model, endpoint="chat", error=str(e))
        _audit("chat_failed", user_id=billing_user_id, request_user_id=request_user_id, model=model, error=str(e)[:300])
        raise HTTPException(502, f"Comfly chat 调用失败：{e}")

    # 按 usage 结算
    usage = resp.get("usage") if isinstance(resp.get("usage"), dict) else {}
    actual = estimate_comfly_credits(model, {"usage": usage}, for_user=True) or estimated
    _do_settle_by_user_id(billing_user_id, pre=pre, actual=int(actual),
               capability_id=_CAPABILITY_FOR_BILLING, model=model, endpoint="chat",
               extra_meta={"usage": usage})
    _audit("chat_settled", user_id=billing_user_id, request_user_id=request_user_id, model=model,
           pre=credits_json_float(pre), actual=int(actual), usage=usage)
    return JSONResponse(resp)


@router.post("/api/comfly-proxy/v1/images/generations", summary="Comfly images 透明 proxy（按 per_call 计费）")
async def proxy_images_generations(
    request: Request,
):
    _check_request_authorized_for_billing(request)
    body = await request.json()
    model = (body.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "缺少 model")
    attempt_models = _image_generation_model_attempts(model, body)
    if len(attempt_models) == 1:
        _require_model_entry(model)

    request_user_id, billing_user_id = _resolve_proxy_user_ids_from_request(request, map_to_online_user=True)
    errors: List[str] = []
    last_error = ""
    attempts_per_model = _env_int("COMFLY_IMAGE_RETRY_ATTEMPTS", 2, min_value=1, max_value=4)
    reference_urls = _image_reference_urls(body)

    for index, attempt_model in enumerate(attempt_models, start=1):
        try:
            entry = _require_model_entry(attempt_model)
        except HTTPException as e:
            last_error = str(e.detail)
            errors.append(f"{attempt_model}: {last_error}")
            _audit(
                "image_channel_skipped",
                user_id=billing_user_id,
                request_user_id=request_user_id,
                requested_model=model,
                model=attempt_model,
                attempt=index,
                error=last_error[:300],
            )
            continue

        upstream_body = _body_for_upstream_model(body, attempt_model, entry)
        if reference_urls:
            upstream_body.setdefault("image", reference_urls[0])
            upstream_body.setdefault("image_url", reference_urls[0])
            upstream_body.setdefault("image_urls", reference_urls)
        logger.info(
            "[image_generate] request model=%s attempt_model=%s upstream_model=%s refs=%d first_ref=%s image_size=%s num_images=%s",
            model,
            attempt_model,
            upstream_body.get("model"),
            len(reference_urls),
            (reference_urls[0][:120] if reference_urls else ""),
            upstream_body.get("image_size") or upstream_body.get("size") or upstream_body.get("aspect_ratio"),
            upstream_body.get("num_images") or upstream_body.get("n"),
        )

        estimated = estimate_comfly_credits(attempt_model, body, for_user=True) or 1
        pre = _do_pre_deduct_by_user_id(
            billing_user_id,
            estimated,
            capability_id=_CAPABILITY_FOR_BILLING,
            model=attempt_model,
            endpoint="image",
            extra_meta={"requested_model": model, "attempt": index},
        )
        _audit(
            "image_pre_deduct",
            user_id=billing_user_id,
            request_user_id=request_user_id,
            requested_model=model,
            model=attempt_model,
            attempt=index,
            estimated=estimated,
        )

        channel_succeeded = False
        retries_for_model = _image_retry_attempts_for_entry(entry, attempts_per_model)
        for retry_index in range(1, retries_for_model + 1):
            try:
                endpoint_path = "/v1/images/edits" if reference_urls else "/v1/images/generations"
                _audit(
                    "image_channel_attempt",
                    user_id=billing_user_id,
                    request_user_id=request_user_id,
                    requested_model=model,
                    model=attempt_model,
                    attempt=index,
                    retry=retry_index,
                    token_group=(entry.get("token_group") or ""),
                    refs=len(reference_urls),
                )
                token_group = str(entry.get("token_group") or "").strip().lower()
                api_format = str(entry.get("api_format") or "").strip().lower()
                request_timeout = _image_timeout_for_entry(entry, _TIMEOUT_IMAGE)
                if api_format == "openai_official":
                    resp = await _openai_official_image_request(body)
                elif reference_urls:
                    if token_group == "openmindapi":
                        resp = await _openmind_image_request(upstream_body)
                    else:
                        edit_data, edit_files = await _build_image_edit_request_parts(body, attempt_model, entry, reference_urls)
                        if token_group == "yunwu":
                            resp = await _yunwu_multipart_request(
                                f"{_yunwu_base_url()}/v1/images/edits",
                                edit_data,
                                edit_files,
                                _yunwu_auth_headers(),
                                request_timeout,
                            )
                        else:
                            resp = await _comfly_multipart_request(
                                _comfly_url(endpoint_path, attempt_model),
                                edit_data,
                                edit_files,
                                _comfly_auth_headers(attempt_model),
                                request_timeout,
                            )
                else:
                    resp = await _comfly_request(
                        "POST",
                        _comfly_url(endpoint_path, attempt_model),
                        upstream_body,
                        _comfly_headers(attempt_model),
                        request_timeout,
                    )
                saved_assets = await _save_generated_images_best_effort_by_user_id(
                    billing_user_id,
                    response_payload=resp,
                    prompt=str(body.get("prompt") or ""),
                    model=attempt_model,
                    limit=int(body.get("n") or body.get("num_images") or 1),
                    exclude_urls=reference_urls,
                )
                if isinstance(resp, dict):
                    if saved_assets:
                        resp = dict(resp)
                        resp["saved_assets"] = saved_assets
                    if attempt_model != model:
                        fallback = resp.setdefault("_lobster_fallback", {})
                        if isinstance(fallback, dict):
                            fallback.update({"requested_model": model, "used_model": attempt_model, "attempt": index})
                _audit(
                    "image_ok",
                    user_id=billing_user_id,
                    request_user_id=request_user_id,
                    requested_model=model,
                    model=attempt_model,
                    attempt=index,
                    retry=retry_index,
                    pre=credits_json_float(pre),
                    saved_assets=len(saved_assets),
                    refs=len(reference_urls),
                )
                provider, channel, route_name = _image_attempt_route_meta(entry)
                log_model_usage_event(
                    None,
                    category="image",
                    event_kind="attempt",
                    success=True,
                    user_id=billing_user_id,
                    requested_model=model,
                    model=attempt_model,
                    provider=provider,
                    channel=channel,
                    route=route_name,
                    endpoint=endpoint_path,
                    meta={"attempt": index, "retry": retry_index, "saved_assets": len(saved_assets), "refs": len(reference_urls)},
                )
                log_model_usage_event(
                    None,
                    category="image",
                    event_kind="request",
                    success=True,
                    user_id=billing_user_id,
                    requested_model=model,
                    model=attempt_model,
                    provider=provider,
                    channel=channel,
                    route=route_name,
                    endpoint=endpoint_path,
                    meta={"attempt": index, "retry": retry_index, "saved_assets": len(saved_assets), "refs": len(reference_urls)},
                )
                channel_succeeded = True
                return JSONResponse(resp)
            except Exception as e:
                last_error = str(e)
                errors.append(f"{attempt_model}: {last_error[:300]}")
                _audit(
                    "image_channel_attempt_failed",
                    user_id=billing_user_id,
                    request_user_id=request_user_id,
                    requested_model=model,
                    model=attempt_model,
                    attempt=index,
                    retry=retry_index,
                    retries=retries_for_model,
                    error=last_error[:300],
                )
                provider, channel, route_name = _image_attempt_route_meta(entry)
                log_model_usage_event(
                    None,
                    category="image",
                    event_kind="attempt",
                    success=False,
                    user_id=billing_user_id,
                    requested_model=model,
                    model=attempt_model,
                    provider=provider,
                    channel=channel,
                    route=route_name,
                    endpoint=endpoint_path,
                    error_message=last_error[:1000],
                    meta={"attempt": index, "retry": retry_index, "retries": retries_for_model, "refs": len(reference_urls)},
                )
                if retry_index >= retries_for_model or not _is_retryable_image_error(e):
                    break
                await asyncio.sleep(0.8 * retry_index)

        if (
            _should_try_openmind_image_fallback(attempt_model, entry)
            and _openmind_image_fallback_enabled()
            and (not last_error or _is_retryable_image_error(RuntimeError(last_error)))
        ):
            try:
                resp = await _openmind_image_request(upstream_body)
                saved_assets = await _save_generated_images_best_effort_by_user_id(
                    billing_user_id,
                    response_payload=resp,
                    prompt=str(body.get("prompt") or ""),
                    model=attempt_model,
                    limit=int(body.get("n") or body.get("num_images") or 1),
                    exclude_urls=reference_urls,
                )
                if isinstance(resp, dict):
                    if saved_assets:
                        resp = dict(resp)
                        resp["saved_assets"] = saved_assets
                    fallback = resp.setdefault("_lobster_fallback", {})
                    if isinstance(fallback, dict):
                        fallback.update({"requested_model": model, "used_model": attempt_model, "provider": "openmind", "attempt": index})
                _audit(
                    "image_openmind_fallback_ok",
                    user_id=billing_user_id,
                    request_user_id=request_user_id,
                    requested_model=model,
                    model=attempt_model,
                    pre=credits_json_float(pre),
                    comfly_error=last_error[:300],
                    saved_assets=len(saved_assets),
                )
                log_model_usage_event(
                    None,
                    category="image",
                    event_kind="attempt",
                    success=True,
                    user_id=billing_user_id,
                    requested_model=model,
                    model=attempt_model,
                    provider="openmind",
                    channel="openmind",
                    route="openmind",
                    endpoint="/openmind/images",
                    meta={"attempt": index, "saved_assets": len(saved_assets), "refs": len(reference_urls)},
                )
                log_model_usage_event(
                    None,
                    category="image",
                    event_kind="request",
                    success=True,
                    user_id=billing_user_id,
                    requested_model=model,
                    model=attempt_model,
                    provider="openmind",
                    channel="openmind",
                    route="openmind",
                    endpoint="/openmind/images",
                    meta={"attempt": index, "saved_assets": len(saved_assets), "refs": len(reference_urls)},
                )
                channel_succeeded = True
                return JSONResponse(resp)
            except Exception as fallback_error:
                _audit(
                    "image_openmind_fallback_failed",
                    user_id=billing_user_id,
                    request_user_id=request_user_id,
                    requested_model=model,
                    model=attempt_model,
                    comfly_error=last_error[:300],
                    error=str(fallback_error)[:300],
                )
                log_model_usage_event(
                    None,
                    category="image",
                    event_kind="attempt",
                    success=False,
                    user_id=billing_user_id,
                    requested_model=model,
                    model=attempt_model,
                    provider="openmind",
                    channel="openmind",
                    route="openmind",
                    endpoint="/openmind/images",
                    error_message=str(fallback_error)[:1000],
                    meta={"attempt": index, "refs": len(reference_urls)},
                )
                last_error = f"{last_error}; OpenMind fallback failed: {fallback_error}"
                errors.append(f"{attempt_model}/openmind: {str(fallback_error)[:300]}")

        if not channel_succeeded:
            _do_full_refund_by_user_id(
                billing_user_id,
                pre=pre,
                capability_id=_CAPABILITY_FOR_BILLING,
                model=attempt_model,
                endpoint="image",
                error=last_error,
            )
            _audit(
                "image_failed",
                user_id=billing_user_id,
                request_user_id=request_user_id,
                requested_model=model,
                model=attempt_model,
                attempt=index,
                error=last_error[:300],
            )
            log_model_usage_event(
                None,
                category="image",
                event_kind="request",
                success=False,
                user_id=billing_user_id,
                requested_model=model,
                model=attempt_model,
                provider="all",
                channel="all",
                route="final",
                endpoint="/v1/images/generations",
                error_message=last_error[:1000],
                meta={"attempt": index, "refs": len(reference_urls)},
            )

    detail = "; ".join(errors[-3:]) or last_error or "unknown error"
    _audit(
        "image_all_channels_failed",
        user_id=billing_user_id,
        request_user_id=request_user_id,
        model=model,
        errors=errors[-5:],
    )
    log_model_usage_event(
        None,
        category="image",
        event_kind="request",
        success=False,
        user_id=billing_user_id,
        requested_model=model,
        model=model,
        provider="all",
        channel="all",
        route="final",
        endpoint="/v1/images/generations",
        error_message=detail[:1000],
        meta={"errors": errors[-5:]},
    )
    raise HTTPException(502, _public_image_failure_detail())


@router.post("/api/comfly-proxy/v1/images/edits", summary="Comfly image edits 透明 proxy（multipart，按 per_call 计费）")
async def proxy_images_edits(
    request: Request,
):
    _check_request_authorized_for_billing(request)
    form = await request.form()
    model = str(form.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "缺少 model")
    entry = _require_model_entry(model)

    data: Dict[str, str] = {}
    files: List[Tuple[str, Tuple[str, bytes, str]]] = []
    for key, value in form.multi_items():
        if hasattr(value, "filename"):
            raw = await value.read()
            if not raw:
                continue
            files.append(
                (
                    key,
                    (
                        value.filename or "image.png",
                        raw,
                        (getattr(value, "content_type", None) or "application/octet-stream"),
                    ),
                )
            )
        else:
            data[key] = str(value)

    data["model"] = _upstream_model(model, entry)
    if not files:
        raise HTTPException(400, "缺少 image 文件")

    request_user_id, billing_user_id = _resolve_proxy_user_ids_from_request(request, map_to_online_user=True)
    estimated = estimate_comfly_credits(model, data, for_user=True) or 1
    pre = _do_pre_deduct_by_user_id(
        billing_user_id,
        estimated,
        capability_id=_CAPABILITY_FOR_BILLING,
        model=model,
        endpoint="image_edit",
    )
    _audit(
        "image_edit_pre_deduct",
        user_id=billing_user_id,
        request_user_id=request_user_id,
        model=model,
        estimated=estimated,
    )

    try:
        resp = await _comfly_multipart_request(
            _comfly_url("/v1/images/edits", model),
            data,
            files,
            _comfly_auth_headers(model),
            _TIMEOUT_IMAGE,
        )
    except Exception as e:
        _do_full_refund_by_user_id(billing_user_id, pre=pre,
                        capability_id=_CAPABILITY_FOR_BILLING, model=model, endpoint="image_edit", error=str(e))
        _audit(
            "image_edit_failed",
            user_id=billing_user_id,
            request_user_id=request_user_id,
            model=model,
            error=str(e)[:300],
        )
        raise HTTPException(502, f"Comfly image edits 调用失败：{e}")

    _audit("image_edit_ok", user_id=billing_user_id, request_user_id=request_user_id, model=model, pre=credits_json_float(pre))
    return JSONResponse(resp)


@router.post("/api/comfly-proxy/v2/videos/generations", summary="Comfly Veo 视频提交 proxy（按 per_call 预扣）")
async def proxy_videos_generations_submit(
    request: Request,
):
    _check_request_authorized_for_billing(request)
    body = await request.json()
    model = (body.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "缺少 model")
    entry = _require_model_entry(model)
    upstream_body = _body_for_upstream_model(body, model, entry)

    request_user_id, billing_user_id = _resolve_proxy_user_ids_from_request(request, map_to_online_user=False)
    estimated = estimate_comfly_credits(model, body, for_user=True) or 1
    pre = _do_pre_deduct_by_user_id(
        billing_user_id,
        estimated,
        capability_id=_CAPABILITY_FOR_BILLING,
        model=model,
        endpoint="video_submit",
    )
    _audit("video_submit_pre_deduct", user_id=billing_user_id, request_user_id=request_user_id, model=model, estimated=estimated)

    try:
        if _is_grok_api_format(entry):
            resp = await _submit_comfly_grok15_video(body, model, entry)
        else:
            resp = await _comfly_request("POST", _comfly_url("/v2/videos/generations", model),
                                         upstream_body, _comfly_headers(model), _TIMEOUT_VIDEO_SUBMIT)
    except Exception as e:
        _do_full_refund_by_user_id(
            billing_user_id,
            pre=pre,
            capability_id=_CAPABILITY_FOR_BILLING,
            model=model,
            endpoint="video_submit",
            error=str(e),
        )
        _audit("video_submit_failed", user_id=billing_user_id, request_user_id=request_user_id, model=model, error=str(e)[:300])
        log_model_usage_event(
            None,
            category="video",
            event_kind="request",
            success=False,
            user_id=billing_user_id,
            requested_model=model,
            model=model,
            provider="comfly",
            channel="comfly",
            route="video_submit",
            endpoint="/api/comfly-proxy/v2/videos/generations",
            error_message=str(e)[:1000],
        )
        raise HTTPException(502, f"Comfly videos submit 调用失败：{e}")

    task_id = _task_id_from_response(resp) or (
        (resp.get("data", {}) or {}).get("task_id") if isinstance(resp.get("data"), dict) else resp.get("task_id")
    )
    api_kind = "grok_v1" if _is_grok_api_format(entry) else "veo_v2"
    _remember_proxy_video_task(task_id, api_kind, model)
    _audit("video_submit_ok", user_id=billing_user_id, request_user_id=request_user_id, model=model,
           task_id=task_id,
           api_kind=api_kind,
           pre=credits_json_float(pre))
    log_model_usage_event(
        None,
        category="video",
        event_kind="request",
        success=True,
        user_id=billing_user_id,
        requested_model=model,
        model=model,
        provider="comfly",
        channel="comfly",
        route=api_kind,
        endpoint="/api/comfly-proxy/v2/videos/generations",
        request_id=task_id or "",
        meta={"api_kind": api_kind},
    )
    return JSONResponse(resp)


@router.get("/api/comfly-proxy/v2/videos/generations/{task_id}", summary="Comfly Veo 任务轮询 proxy（不计费）")
async def proxy_videos_generations_poll(
    task_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    _check_request_authorized_for_billing(request)
    remembered_kind, remembered_model = _proxy_video_task_hint(task_id)
    try:
        resp = await _poll_comfly_video_task(task_id, remembered_model, remembered_kind)
    except Exception as e:
        raise HTTPException(502, f"Comfly videos poll 调用失败：{e}")
    return JSONResponse(resp)





def _video_provider_policy(model: str, channel: str = "") -> Dict[str, Any]:
    raw_model = (model or "").strip()
    low_model = raw_model.lower().replace("_", "-").replace(" ", "")
    low_channel = (channel or "").strip().lower()
    proxy_base = "/api/comfly-proxy"

    if low_channel in {"openmind", "grok"} or low_model in {"grok-video-3", "grok-imagine-video-1.5-preview", "grok-imagine-1.0-video", "yingmeng1.5plus"} or low_model.startswith("xai/grok-imagine-video/"):
        return {
            "ok": True,
            "model_family": "grok",
            "providers": [
                {"channel": "openmind", "model": "grok-video-3", "base_url": proxy_base},
                {"channel": "comfly", "model": "grok-video-3", "base_url": proxy_base},
                {"channel": "yunwu", "model": "grok-video-3", "base_url": proxy_base},
            ],
        }

    if low_channel in {"yunwu", "??", "??"} or low_model in {"yunwu-veo3.1-plus", "veo3.1-plus", "veo3.1", "veo31", "veo31-fast", "veo3.1-fast"}:
        return {
            "ok": True,
            "model_family": "veo31",
            "providers": [
                {"channel": "openmind", "model": "veo3.1-fast", "base_url": proxy_base},
                {"channel": "comfly", "model": "veo3.1-fast", "base_url": proxy_base},
                {"channel": "yunwu", "model": "veo3.1", "base_url": proxy_base},
            ],
        }

    if low_model in {"seedance-2-0-pro-250528", "seedance-2-0-lite-250428", "seedance-2-0-260128", "seedance-2-0-fast-260128", "doubao-seedance-2-0-260128", "doubao-seedance-2-0-fast-260128"}:
        return {
            "ok": True,
            "model_family": "seedance20",
            "providers": [
                {"channel": "openmind", "model": "doubao-seedance-2-0-260128", "base_url": proxy_base},
                {"channel": "seedance", "model": "doubao-seedance-2-0-260128", "base_url": proxy_base},
            ],
        }

    return {
        "ok": True,
        "model_family": "default",
        "providers": [
            {"channel": "seedance", "model": raw_model or "doubao-seedance-2-0-260128", "base_url": proxy_base},
        ],
    }


@router.get("/api/comfly-proxy/video/provider-policy", summary="Server-controlled video provider fallback policy")
async def proxy_video_provider_policy(
    request: Request,
    model: str = "",
    channel: str = "",
    feature: str = "",
    current_user: User = Depends(get_current_user),
):
    _check_request_authorized_for_billing(request)
    policy = _video_provider_policy(model, channel)
    _audit("video_provider_policy", user_id=current_user.id, model=model, channel=channel, feature=feature, family=policy.get("model_family"))
    return JSONResponse(policy)


@router.post("/api/comfly-proxy/openmind/v1/videos", summary="OpenMind video submit proxy")
async def proxy_openmind_video_submit(
    request: Request,
):
    _check_request_authorized_for_billing(request)
    body = await request.json()
    model = (body.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "missing model")
    entry = _require_model_entry(model)
    upstream_body = _openmind_video_body(body, model, entry)

    request_user_id, billing_user_id = _resolve_proxy_user_ids_from_request(request, map_to_online_user=True)
    estimated = estimate_comfly_credits(model, body, for_user=True) or 1
    pre = _do_pre_deduct_by_user_id(
        billing_user_id,
        estimated,
        capability_id=_CAPABILITY_FOR_BILLING,
        model=model,
        endpoint="openmind_video_submit",
        extra_meta={"upstream": "openmind", "openmind_model": upstream_body.get("model")},
    )
    _audit(
        "openmind_video_submit_pre_deduct",
        user_id=billing_user_id,
        request_user_id=request_user_id,
        model=model,
        openmind_model=upstream_body.get("model"),
        estimated=estimated,
    )

    try:
        resp = await _openmind_video_submit(body, model, entry)
    except Exception as e:
        _do_full_refund_by_user_id(
            billing_user_id,
            pre=pre,
            capability_id=_CAPABILITY_FOR_BILLING,
            model=model,
            endpoint="openmind_video_submit",
            error=str(e),
        )
        _audit(
            "openmind_video_submit_failed",
            user_id=billing_user_id,
            request_user_id=request_user_id,
            model=model,
            error=str(e)[:300],
        )
        log_model_usage_event(
            None,
            category="video",
            event_kind="request",
            success=False,
            user_id=billing_user_id,
            requested_model=model,
            model=model,
            provider="openmind",
            channel="openmind",
            route="openmind",
            endpoint="/api/comfly-proxy/openmind/v1/videos",
            error_message=str(e)[:1000],
        )
        raise HTTPException(502, f"OpenMind video submit failed: {e}")

    _audit(
        "openmind_video_submit_ok",
        user_id=billing_user_id,
        request_user_id=request_user_id,
        model=model,
        openmind_model=resp.get("_requested_model") if isinstance(resp, dict) else "",
        task_id=_task_id_from_response(resp),
        pre=credits_json_float(pre),
    )
    log_model_usage_event(
        None,
        category="video",
        event_kind="request",
        success=True,
        user_id=billing_user_id,
        requested_model=model,
        model=model,
        provider="openmind",
        channel="openmind",
        route="openmind",
        endpoint="/api/comfly-proxy/openmind/v1/videos",
        request_id=_task_id_from_response(resp) or "",
        meta={"openmind_model": resp.get("_requested_model") if isinstance(resp, dict) else ""},
    )
    return JSONResponse(resp)


@router.get("/api/comfly-proxy/openmind/v1/videos/{task_id}", summary="OpenMind video poll proxy")
async def proxy_openmind_video_poll(
    task_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    _check_request_authorized_for_billing(request)
    task_id = (task_id or "").strip()
    if not task_id:
        raise HTTPException(400, "missing task_id")
    try:
        resp = await _openmind_video_poll(task_id)
    except Exception as e:
        raise HTTPException(502, f"OpenMind video poll failed: {e}")
    return JSONResponse(resp)


@router.post("/api/comfly-proxy/v1/video/create", summary="Yunwu video create proxy")
async def proxy_yunwu_video_create(
    request: Request,
):
    _check_request_authorized_for_billing(request)
    body = await request.json()
    model = (body.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "missing model")
    entry = _require_model_entry(model)
    upstream_body = _body_for_upstream_model(body, model, entry)
    request_user_id, billing_user_id = _resolve_proxy_user_ids_from_request(request, map_to_online_user=False)

    estimated = estimate_comfly_credits(model, body, for_user=True) or 1
    pre = _do_pre_deduct_by_user_id(
        billing_user_id,
        estimated,
        capability_id=_CAPABILITY_FOR_BILLING,
        model=model,
        endpoint="yunwu_video_create",
    )
    _audit("yunwu_video_create_pre_deduct", user_id=billing_user_id, request_user_id=request_user_id, model=model, estimated=estimated)

    try:
        resp = await _yunwu_request(
            "POST",
            f"{_yunwu_base_url()}/v1/video/create",
            upstream_body,
            _yunwu_headers(),
            _TIMEOUT_VIDEO_SUBMIT,
        )
    except Exception as e:
        _do_full_refund_by_user_id(
            billing_user_id,
            pre=pre,
            capability_id=_CAPABILITY_FOR_BILLING,
            model=model,
            endpoint="yunwu_video_create",
            error=str(e),
        )
        _audit("yunwu_video_create_failed", user_id=billing_user_id, request_user_id=request_user_id, model=model, error=str(e)[:300])
        log_model_usage_event(
            None,
            category="video",
            event_kind="request",
            success=False,
            user_id=billing_user_id,
            requested_model=model,
            model=model,
            provider="yunwu",
            channel="yunwu",
            route="yunwu",
            endpoint="/api/comfly-proxy/v1/video/create",
            error_message=str(e)[:1000],
        )
        raise HTTPException(502, f"Yunwu video create failed: {e}")

    _audit("yunwu_video_create_ok", user_id=billing_user_id, request_user_id=request_user_id, model=model, task_id=resp.get("id"), pre=credits_json_float(pre))
    log_model_usage_event(
        None,
        category="video",
        event_kind="request",
        success=True,
        user_id=billing_user_id,
        requested_model=model,
        model=model,
        provider="yunwu",
        channel="yunwu",
        route="yunwu",
        endpoint="/api/comfly-proxy/v1/video/create",
        request_id=str(resp.get("id") or ""),
    )
    return JSONResponse(resp)


@router.get("/api/comfly-proxy/v1/video/query", summary="Yunwu video query proxy")
async def proxy_yunwu_video_query(
    id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    _check_request_authorized_for_billing(request)
    task_id = (id or "").strip()
    if not task_id:
        raise HTTPException(400, "missing id")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_VIDEO_POLL) as client:
            r = await client.get(
                f"{_yunwu_base_url()}/v1/video/query",
                params={"id": task_id},
                headers={"Authorization": f"Bearer {_yunwu_api_key()}", "Accept": "application/json"},
            )
        if r.status_code >= 400:
            raise RuntimeError(f"Yunwu HTTP {r.status_code}: {(r.text or '')[:500]}")
        resp = r.json() if r.content else {}
    except Exception as e:
        raise HTTPException(502, f"Yunwu video query failed: {e}")
    return JSONResponse(resp)


@router.post("/api/comfly-proxy/seedance/v3/contents/generations/tasks", summary="Comfly Seedance 视频提交 proxy（按定价表预扣）")
async def proxy_seedance_tasks_submit(
    request: Request,
):
    _check_request_authorized_for_billing(request)
    body = await request.json()
    model = (body.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "缺少 model")
    entry = _require_model_entry(model)
    upstream_body = _body_for_upstream_model(body, model, entry)

    request_user_id, billing_user_id = _resolve_proxy_user_ids_from_request(request, map_to_online_user=False)
    estimated = estimate_comfly_credits(model, body, for_user=True) or 1
    pre = _do_pre_deduct_by_user_id(
        billing_user_id,
        estimated,
        capability_id=_CAPABILITY_FOR_BILLING,
        model=model,
        endpoint="seedance_submit",
    )
    _audit("seedance_submit_pre_deduct", user_id=billing_user_id, request_user_id=request_user_id, model=model, estimated=estimated)

    try:
        resp = await _comfly_request(
            "POST",
            _comfly_url("/seedance/v3/contents/generations/tasks", model),
            upstream_body,
            _comfly_headers(model),
            _TIMEOUT_VIDEO_SUBMIT,
        )
    except Exception as e:
        _do_full_refund_by_user_id(billing_user_id, pre=pre,
                        capability_id=_CAPABILITY_FOR_BILLING, model=model, endpoint="seedance_submit", error=str(e))
        _audit("seedance_submit_failed", user_id=billing_user_id, request_user_id=request_user_id, model=model, error=str(e)[:300])
        raise HTTPException(502, f"Comfly Seedance submit 调用失败：{e}")

    data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
    _audit("seedance_submit_ok", user_id=billing_user_id, request_user_id=request_user_id, model=model,
           task_id=resp.get("id") or resp.get("task_id") or data.get("task_id") or data.get("id"),
           pre=credits_json_float(pre))
    return JSONResponse(resp)


@router.get("/api/comfly-proxy/seedance/v3/contents/generations/tasks/{task_id}", summary="Comfly Seedance 任务轮询 proxy（不计费）")
async def proxy_seedance_tasks_poll(
    task_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    _check_request_authorized_for_billing(request)
    try:
        resp = await _comfly_request(
            "GET",
            _comfly_url(f"/seedance/v3/contents/generations/tasks/{task_id}"),
            None,
            _comfly_headers(),
            _TIMEOUT_VIDEO_POLL,
        )
    except Exception as e:
        raise HTTPException(502, f"Comfly Seedance poll 调用失败：{e}")
    return JSONResponse(resp)
