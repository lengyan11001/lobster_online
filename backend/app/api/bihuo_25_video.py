"""必火2.5 video workbench API.

The desktop client only handles business inputs and task state. Provider model
details and credentials stay behind the server MCP gateway.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from typing import Any, Dict, List, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import _ServerUser, get_current_user_for_local
from .chat import (
    _extract_generation_failure_reason,
    _extract_status_for_log,
    _extract_task_id_from_result,
    _extract_video_urls_from_task_result,
    _is_generation_failure_result,
    _is_task_result_in_progress,
)
from ..core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_SERVER_FALLBACK = "https://bhzn.top"
_PROVIDER_MODEL = "st-ai/super-seed2-lite"
_MODE_TO_UPSTREAM = {
    "reference": "omini",
    "first_last": "first_last_frame",
    "edit": "edit",
    "extend": "extend",
}
_ALLOWED_RATIOS = {"16:9", "21:9", "9:16", "4:3", "3:4", "1:1", "adaptive"}
_ALLOWED_RESOLUTIONS = {"480p", "720p"}
_PERMISSION_CACHE_TTL_SECONDS = 20.0
_PERMISSION_CACHE: Dict[str, tuple[float, bool]] = {}
_PRIVATE_ERROR_MARKERS = (
    "apiz",
    "seedance",
    "seed2",
    "super-seed",
    "st-ai",
    "sutui",
    "comfly",
    "yunwu",
    "openmind",
)


class Bihuo25StartBody(BaseModel):
    mode: Literal["reference", "first_last", "edit", "extend"] = "reference"
    prompt: str = Field(default="", max_length=6000)
    ratio: str = "9:16"
    resolution: str = "720p"
    duration: int = 10
    image_urls: List[str] = Field(default_factory=list)
    video_urls: List[str] = Field(default_factory=list)
    audio_urls: List[str] = Field(default_factory=list)
    first_image_url: str = ""
    last_image_url: str = ""


class Bihuo25PollBody(BaseModel):
    task_id: str = Field(min_length=4, max_length=160)


def _raw_token(request: Request) -> str:
    auth = str(request.headers.get("Authorization") or "").strip()
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


def _server_base() -> str:
    return str(settings.auth_server_base or "").strip().rstrip("/") or _SERVER_FALLBACK


def _public_service_error(raw: Any, fallback: str = "视频生成服务暂时不可用，请稍后重试") -> str:
    message = str(raw or "").strip()
    lowered = message.lower()
    if not message:
        return fallback
    if (
        len(message) > 300
        or message[:1] in {"{", "["}
        or "http://" in lowered
        or "https://" in lowered
        or "traceback" in lowered
        or "exception" in lowered
        or any(marker in lowered for marker in _PRIVATE_ERROR_MARKERS)
    ):
        return fallback
    return message


def _public_gateway_error(raw: Any) -> tuple[int, str]:
    message = str(raw or "").strip()
    if "积分不足" in message or "余额不足" in message:
        needed = re.search(r"(?:预估至少|本次需|需要)\s*([0-9]+(?:\.[0-9]+)?)\s*积分", message)
        balance = re.search(r"当前余额\s*([0-9]+(?:\.[0-9]+)?)", message)
        if needed and balance:
            detail = (
                f"当前账户积分不足，本次预计需要 {needed.group(1)} 积分，"
                f"当前余额 {balance.group(1)} 积分，请充值后重试"
            )
        else:
            detail = "当前账户积分不足，请充值后重试"
        return 402, detail
    return 502, _public_service_error(message)


def _gateway_content_text(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    texts = [
        str(item.get("text") or "")
        for item in content
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    return "\n".join(item for item in texts if item).strip()


def _gateway_headers(request: Request, token: str, user_id: int) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "x-user-authorization": f"Bearer {token}",
    }
    installation_id = str(
        request.headers.get("X-Installation-Id")
        or request.headers.get("x-installation-id")
        or f"lobster-internal-{int(user_id)}"
    ).strip()
    if installation_id:
        headers["X-Installation-Id"] = installation_id
    for header_name in ("X-Lobster-Brand", "X-Lobster-Client-Overseas"):
        value = str(request.headers.get(header_name) or "").strip()
        if value:
            headers[header_name] = value
    return headers


async def _assert_server_permission(request: Request, user: _ServerUser) -> None:
    token = _raw_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="请先登录后再使用必火2.5")
    cache_key = f"{int(user.id)}:{hashlib.sha256(token.encode('utf-8')).hexdigest()}"
    now = time.monotonic()
    cached = _PERMISSION_CACHE.get(cache_key)
    if cached and cached[0] > now:
        if not cached[1]:
            raise HTTPException(status_code=403, detail="当前账号未开通必火2.5，请联系管理员")
        return

    try:
        async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
            response = await client.get(
                f"{_server_base()}/skills/bihuo-25-video-eligible",
                headers=_gateway_headers(request, token, user.id),
            )
    except httpx.HTTPError as exc:
        logger.warning("[bihuo25] permission check failed: %s", exc)
        raise HTTPException(status_code=503, detail="暂时无法验证必火2.5使用权限，请稍后重试") from exc
    if response.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="登录状态已失效，请重新登录")
    if response.status_code != 200:
        logger.warning("[bihuo25] permission check HTTP %s: %s", response.status_code, response.text[:500])
        raise HTTPException(status_code=503, detail="暂时无法验证必火2.5使用权限，请稍后重试")
    try:
        allowed = bool((response.json() or {}).get("allowed"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="必火2.5权限返回格式异常，请稍后重试") from exc
    _PERMISSION_CACHE[cache_key] = (now + _PERMISSION_CACHE_TTL_SECONDS, allowed)
    if len(_PERMISSION_CACHE) > 2000:
        expired = [key for key, item in _PERMISSION_CACHE.items() if item[0] <= now]
        for key in expired:
            _PERMISSION_CACHE.pop(key, None)
    if not allowed:
        raise HTTPException(status_code=403, detail="当前账号未开通必火2.5，请联系管理员")


async def _call_gateway(
    request: Request,
    user: _ServerUser,
    capability_id: str,
    payload: Dict[str, Any],
    *,
    timeout_seconds: float,
) -> str:
    token = _raw_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="请先登录后再生成视频")
    rpc = {
        "jsonrpc": "2.0",
        "id": f"bihuo25-{uuid.uuid4().hex[:12]}",
        "method": "tools/call",
        "params": {
            "name": "invoke_capability",
            "arguments": {"capability_id": capability_id, "payload": payload},
        },
    }
    timeout = httpx.Timeout(connect=45.0, read=timeout_seconds, write=300.0, pool=60.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(
                f"{_server_base()}/mcp-gateway",
                json=rpc,
                headers=_gateway_headers(request, token, user.id),
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="云端视频任务响应超时，请稍后继续查询") from exc
    except httpx.HTTPError as exc:
        logger.warning("[bihuo25] gateway request failed: %s", exc)
        raise HTTPException(status_code=502, detail="视频生成服务暂时不可用，请稍后重试") from exc
    if response.status_code >= 400:
        detail = (response.text or "")[:800]
        try:
            detail = str((response.json() or {}).get("detail") or detail)
        except Exception:
            pass
        logger.warning("[bihuo25] gateway HTTP %s: %s", response.status_code, detail[:800])
        raise HTTPException(
            status_code=response.status_code,
            detail=_public_service_error(detail, "视频生成服务请求失败，请稍后重试"),
        )
    try:
        data = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="云端视频服务返回格式异常") from exc
    if isinstance(data, dict) and data.get("error"):
        message = data.get("error")
        if isinstance(message, dict):
            message = message.get("message") or message.get("data") or message
        logger.warning("[bihuo25] gateway RPC error: %s", str(message)[:800])
        raise HTTPException(status_code=502, detail=_public_service_error(message))
    result = data.get("result") if isinstance(data, dict) else data
    if isinstance(result, dict):
        joined = _gateway_content_text(result)
        if result.get("isError") is True:
            status_code, detail = _public_gateway_error(joined)
            logger.warning("[bihuo25] gateway tool error: %s", joined[:800])
            raise HTTPException(status_code=status_code, detail=detail)
        if joined:
            return joined
    return json.dumps(result, ensure_ascii=False)


def _clean_urls(values: List[str], *, limit: int) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for raw in values or []:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        if not value.lower().startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="参考素材必须先上传到素材库")
        seen.add(value)
        out.append(value)
        if len(out) > limit:
            break
    return out


def _build_generate_payload(body: Bihuo25StartBody) -> Dict[str, Any]:
    mode = body.mode
    prompt = str(body.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="请输入视频画面和动作要求")
    ratio = str(body.ratio or "").strip()
    if ratio not in _ALLOWED_RATIOS:
        raise HTTPException(status_code=400, detail="视频比例参数无效")
    resolution = str(body.resolution or "").strip().lower()
    if resolution not in _ALLOWED_RESOLUTIONS:
        raise HTTPException(status_code=400, detail="清晰度仅支持 480p 或 720p")

    images = _clean_urls(body.image_urls, limit=30)
    videos = _clean_urls(body.video_urls, limit=10)
    audios = _clean_urls(body.audio_urls, limit=10)
    if len(images) > 30 or len(videos) > 10 or len(audios) > 10:
        raise HTTPException(status_code=400, detail="参考素材超过上限：图片30个、视频10个、音频10个")

    payload: Dict[str, Any] = {
        "model": _PROVIDER_MODEL,
        "prompt": prompt,
        "functionMode": _MODE_TO_UPSTREAM[mode],
        "ratio": ratio,
        "resolution": resolution,
    }
    if mode == "reference":
        if not images and not videos and not audios:
            raise HTTPException(status_code=400, detail="请至少添加一个图片、视频或音频参考素材")
        if len(images) + len(videos) + len(audios) > 50:
            raise HTTPException(status_code=400, detail="参考素材总数不能超过50个")
        payload.update({"image_urls": images, "video_urls": videos, "audio_urls": audios})
    elif mode == "first_last":
        first_url = str(body.first_image_url or "").strip()
        last_url = str(body.last_image_url or "").strip()
        _clean_urls([first_url, last_url], limit=2)
        if not first_url or not last_url:
            raise HTTPException(status_code=400, detail="请同时选择首帧图和尾帧图")
        payload.update({"image_url": first_url, "end_image_url": last_url})
    else:
        if len(videos) != 1:
            raise HTTPException(status_code=400, detail="请且只选择一个要处理的视频")
        payload["video_urls"] = videos

    if mode != "edit":
        duration = int(body.duration or 0)
        if duration < 4 or duration > 30:
            label = "延长时长" if mode == "extend" else "视频时长"
            raise HTTPException(status_code=400, detail=f"{label}只支持 4-30 秒")
        payload["duration"] = duration
    return payload


def _public_task_result(result_text: str, task_id: str = "") -> Dict[str, Any]:
    task_id = task_id or _extract_task_id_from_result(result_text)
    videos = _extract_video_urls_from_task_result(result_text)
    failed = _is_generation_failure_result(result_text)
    pending = False if failed or videos else _is_task_result_in_progress(result_text)
    status = str(_extract_status_for_log(result_text) or "").strip()
    if failed:
        state = "failed"
    elif videos:
        state = "completed"
    elif pending:
        state = "processing"
    else:
        state = "completed"
    return {
        "ok": not failed,
        "task_id": task_id,
        "status": state,
        "upstream_status": "" if status == "?" else status,
        "video_urls": [str(item.get("url") or "") for item in videos if item.get("url")],
        "error": _public_service_error(_extract_generation_failure_reason(result_text)) if failed else "",
    }


@router.post("/api/bihuo-25-video/start")
async def start_bihuo_25_video(
    body: Bihuo25StartBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    await _assert_server_permission(request, current_user)
    payload = _build_generate_payload(body)
    logger.info(
        "[bihuo25] submitting user_id=%s mode=%s duration=%s resolution=%s",
        current_user.id,
        body.mode,
        payload.get("duration"),
        payload.get("resolution"),
    )
    result_text = await _call_gateway(
        request,
        current_user,
        "video.generate",
        payload,
        timeout_seconds=240.0,
    )
    result = _public_task_result(result_text)
    if not result["ok"]:
        raise HTTPException(status_code=502, detail=result["error"] or "视频生成任务提交失败")
    if not result["task_id"] and not result["video_urls"]:
        raise HTTPException(status_code=502, detail="视频服务未返回可查询的任务编号")
    logger.info(
        "[bihuo25] task submitted user_id=%s mode=%s task_id=%s",
        current_user.id,
        body.mode,
        result["task_id"],
    )
    return result


@router.post("/api/bihuo-25-video/poll")
async def poll_bihuo_25_video(
    body: Bihuo25PollBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    await _assert_server_permission(request, current_user)
    task_id = str(body.task_id or "").strip()
    result_text = await _call_gateway(
        request,
        current_user,
        "task.get_result",
        {"task_id": task_id, "capability_id": "video.generate"},
        timeout_seconds=180.0,
    )
    result = _public_task_result(result_text, task_id=task_id)
    if not result["ok"]:
        logger.warning(
            "[bihuo25] task failed user_id=%s task_id=%s reason=%s",
            current_user.id,
            task_id,
            result["error"][:500],
        )
    return result
