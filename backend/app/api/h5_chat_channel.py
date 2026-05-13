"""Remote H5 chat channel.

The public H5 page cannot call a user's local online backend directly, so the
cloud server works as a mailbox. This local worker claims messages for the
logged-in user, runs them through the existing local chat/OpenClaw paths, and
posts progress/final events back to the cloud.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..core.config import settings
from ..db import SessionLocal
from ..models import Asset
from ..services.openclaw_channel_auth_store import read_channel_fallback
from .auth import get_current_user_for_local
from .assets import build_asset_file_url, get_asset_public_url
from .openclaw_chat_gateway import openclaw_fallback_model, try_openclaw

logger = logging.getLogger(__name__)
router = APIRouter()
_RESULT_URL_RE = re.compile(r'https?://[^\s"\'<>\)\]]+', re.IGNORECASE)
_SCHEDULED_CREATIVE_ANGLES = [
    "痛点切入",
    "场景体验",
    "结果收益",
    "工艺实力",
    "交付效率",
    "信任背书",
    "对比反差",
    "客户视角",
]
_SCHEDULED_CAPTION_STYLES = [
    "像朋友分享一次新发现",
    "突出一个明确业务结果",
    "用轻松口吻讲专业能力",
    "强调省心和交付确定性",
    "从客户常见问题切入",
    "用一句有记忆点的结论收束",
]


def _scheduled_variant(seed: str, options: List[str]) -> str:
    if not options:
        return ""
    raw = str(seed or "scheduled").encode("utf-8", "ignore")
    digest = hashlib.sha1(raw).digest()
    return options[int.from_bytes(digest[:2], "big") % len(options)]


def _enabled() -> bool:
    raw = os.environ.get("LOBSTER_H5_CHAT_CHANNEL_ENABLED", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _decode_jwt_sub(token: str) -> str:
    parts = (token or "").split(".")
    if len(parts) < 2:
        return ""
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
        return str(data.get("sub") or "").strip()
    except Exception:
        return ""


def _auth_context() -> tuple[str, str]:
    jwt_token, installation_id = read_channel_fallback()
    jwt_token = (jwt_token or getattr(settings, "openclaw_sutui_fallback_jwt", None) or "").strip()
    installation_id = (
        (installation_id or "").strip()
        or (getattr(settings, "openclaw_sutui_fallback_installation_id", None) or "").strip()
    )
    if jwt_token and not installation_id:
        sub = _decode_jwt_sub(jwt_token)
        installation_id = f"h5-local-{sub}" if sub else "h5-local"
    return jwt_token, installation_id


def _cloud_base() -> str:
    return (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")


def _headers(jwt_token: str, installation_id: str) -> Dict[str, str]:
    h = {"Authorization": f"Bearer {jwt_token}"}
    if installation_id:
        h["X-Installation-Id"] = installation_id
    return h


def _request_bearer_token(request: Request) -> str:
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[-1].strip()
    return ""


def _cloud_headers_from_request(request: Request) -> Dict[str, str]:
    fallback_jwt, fallback_installation_id = _auth_context()
    jwt_token = _request_bearer_token(request) or fallback_jwt
    installation_id = (request.headers.get("X-Installation-Id") or "").strip() or fallback_installation_id
    if jwt_token and not installation_id:
        sub = _decode_jwt_sub(jwt_token)
        installation_id = f"h5-local-{sub}" if sub else "h5-local"
    if not jwt_token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return _headers(jwt_token, installation_id)


async def _proxy_cloud_json(
    request: Request,
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout_sec: float = 20.0,
) -> Dict[str, Any]:
    base = _cloud_base()
    if not base:
        raise HTTPException(status_code=503, detail="AUTH_SERVER_BASE is not configured")
    headers = _cloud_headers_from_request(request)
    try:
        async with httpx.AsyncClient(timeout=timeout_sec, trust_env=False) as client:
            resp = await client.request(
                method,
                f"{base}{path}",
                params=params,
                json=json_body,
                headers=headers,
            )
    except httpx.RequestError as exc:
        logger.warning("[SCHEDULED-TASK] proxy request failed path=%s: %s", path, exc)
        raise HTTPException(status_code=503, detail="Cloud scheduled task service is unreachable") from exc

    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else ""
        raise HTTPException(status_code=resp.status_code, detail=detail or resp.text[:500] or f"HTTP {resp.status_code}")
    if isinstance(data, dict):
        return data
    return {"ok": True, "data": data}


@router.get("/api/h5-chat/messages", summary="Proxy cloud H5 chat messages for local online UI")
async def proxy_h5_chat_messages(
    request: Request,
    limit: int = Query(40, ge=1, le=100),
    _current_user: Any = Depends(get_current_user_for_local),
) -> Dict[str, Any]:
    base = _cloud_base()
    if not base:
        raise HTTPException(status_code=503, detail="AUTH_SERVER_BASE is not configured")

    fallback_jwt, fallback_installation_id = _auth_context()
    jwt_token = _request_bearer_token(request) or fallback_jwt
    installation_id = (request.headers.get("X-Installation-Id") or "").strip() or fallback_installation_id
    if jwt_token and not installation_id:
        sub = _decode_jwt_sub(jwt_token)
        installation_id = f"h5-local-{sub}" if sub else "h5-local"
    if not jwt_token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{base}/api/h5-chat/messages",
                params={"limit": limit},
                headers=_headers(jwt_token, installation_id),
            )
    except httpx.RequestError as exc:
        logger.warning("[H5-CHAT] proxy messages request failed: %s", exc)
        raise HTTPException(status_code=503, detail="Cloud H5 chat service is unreachable") from exc

    if resp.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="Cloud H5 chat auth failed")
    if resp.status_code >= 400:
        detail = resp.text[:500] if resp.text else f"HTTP {resp.status_code}"
        raise HTTPException(status_code=502, detail=detail)
    try:
        data = resp.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Cloud H5 chat returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Cloud H5 chat returned invalid payload")
    return data


@router.get("/api/scheduled-tasks/runs", summary="Proxy cloud scheduled task runs for local online UI")
async def proxy_scheduled_task_runs(
    request: Request,
    limit: int = Query(80, ge=1, le=200),
    _current_user: Any = Depends(get_current_user_for_local),
) -> Dict[str, Any]:
    return await _proxy_cloud_json(
        request,
        "GET",
        "/api/scheduled-tasks/runs",
        params={"limit": limit},
    )


@router.get("/api/scheduled-tasks/tasks", summary="Proxy cloud scheduled tasks for local online UI")
async def proxy_scheduled_tasks(
    request: Request,
    limit: int = Query(80, ge=1, le=200),
    _current_user: Any = Depends(get_current_user_for_local),
) -> Dict[str, Any]:
    return await _proxy_cloud_json(
        request,
        "GET",
        "/api/scheduled-tasks/tasks",
        params={"limit": limit},
    )


@router.post("/api/scheduled-tasks/tasks", summary="Proxy create scheduled task for local online UI")
async def proxy_create_scheduled_task(
    request: Request,
    _current_user: Any = Depends(get_current_user_for_local),
) -> Dict[str, Any]:
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")
    return await _proxy_cloud_json(
        request,
        "POST",
        "/api/scheduled-tasks/tasks",
        json_body=body,
        timeout_sec=30.0,
    )


@router.patch("/api/scheduled-tasks/tasks/{task_id}", summary="Proxy update scheduled task for local online UI")
async def proxy_patch_scheduled_task(
    task_id: int,
    request: Request,
    _current_user: Any = Depends(get_current_user_for_local),
) -> Dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    return await _proxy_cloud_json(
        request,
        "PATCH",
        f"/api/scheduled-tasks/tasks/{task_id}",
        json_body=body if isinstance(body, dict) else {},
    )


@router.delete("/api/scheduled-tasks/tasks/{task_id}", summary="Proxy delete scheduled task for local online UI")
async def proxy_delete_scheduled_task(
    task_id: int,
    request: Request,
    _current_user: Any = Depends(get_current_user_for_local),
) -> Dict[str, Any]:
    return await _proxy_cloud_json(
        request,
        "DELETE",
        f"/api/scheduled-tasks/tasks/{task_id}",
    )


@router.post("/api/scheduled-tasks/tasks/{task_id}/run-now", summary="Proxy run scheduled task now for local online UI")
async def proxy_run_scheduled_task_now(
    task_id: int,
    request: Request,
    _current_user: Any = Depends(get_current_user_for_local),
) -> Dict[str, Any]:
    return await _proxy_cloud_json(
        request,
        "POST",
        f"/api/scheduled-tasks/tasks/{task_id}/run-now",
        json_body={},
        timeout_sec=30.0,
    )


async def _post_cloud_event(
    client: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    message_id: str,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        await client.post(
            f"{base}/api/h5-chat/messages/{message_id}/event",
            json={"type": event_type, "payload": payload or {}},
            headers=headers,
        )
    except Exception as exc:
        logger.debug("[H5-CHAT] post event failed message_id=%s type=%s: %s", message_id, event_type, exc)


async def _complete_cloud_message(
    client: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    message_id: str,
    *,
    reply_text: str = "",
    error: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    await client.post(
        f"{base}/api/h5-chat/messages/{message_id}/complete",
        json={"reply_text": reply_text, "error": error, "payload": payload or {}},
        headers=headers,
    )


async def _post_task_event(
    client: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    run_id: str,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        await client.post(
            f"{base}/api/scheduled-tasks/runs/{run_id}/event",
            json={"type": event_type, "payload": payload or {}},
            headers=headers,
        )
    except Exception as exc:
        logger.debug("[SCHEDULED-TASK] post event failed run_id=%s type=%s: %s", run_id, event_type, exc)


async def _complete_task_run(
    client: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    run_id: str,
    *,
    result_text: str = "",
    result_payload: Optional[Dict[str, Any]] = None,
    error: str = "",
) -> None:
    await client.post(
        f"{base}/api/scheduled-tasks/runs/{run_id}/complete",
        json={"result_text": result_text, "result_payload": result_payload or {}, "error": error},
        headers=headers,
    )


def _local_chat_url() -> str:
    port = int(getattr(settings, "port", 8000) or 8000)
    return f"http://127.0.0.1:{port}/chat/stream"


def _scheduled_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    payload = item.get("payload")
    return payload if isinstance(payload, dict) else {}


def _merge_id_list(existing: Any, asset_ids: List[str]) -> List[str]:
    raw: List[Any] = []
    if isinstance(existing, list):
        raw.extend(existing)
    elif isinstance(existing, str):
        raw.extend([x for x in existing.replace("，", ",").split(",")])
    raw.extend(asset_ids)
    seen: set[str] = set()
    out: List[str] = []
    for x in raw:
        aid = str(x or "").strip().lower()
        if not aid or aid in seen:
            continue
        seen.add(aid)
        out.append(aid[:64])
        if len(out) >= 20:
            break
    return out


def _scheduled_attachment_asset_ids(item: Dict[str, Any]) -> List[str]:
    payload = _scheduled_payload(item)
    raw: List[Any] = []
    for key in ("attachment_asset_ids", "asset_ids"):
        val = payload.get(key)
        if isinstance(val, list):
            raw.extend(val)
        elif isinstance(val, str):
            raw.extend([x for x in val.replace("，", ",").split(",")])
    inner = payload.get("payload")
    if isinstance(inner, dict):
        for key in ("attachment_asset_ids", "asset_ids", "reference_asset_ids"):
            val = inner.get(key)
            if isinstance(val, list):
                raw.extend(val)
            elif isinstance(val, str):
                raw.extend([x for x in val.replace("，", ",").split(",")])
        for key in ("asset_id", "image_asset_id", "video_asset_id", "source_asset_id", "reference_asset_id"):
            val = inner.get(key)
            if isinstance(val, str):
                raw.append(val)
    seen: set[str] = set()
    out: List[str] = []
    for x in raw:
        aid = str(x or "").strip().lower()
        if not aid or aid in seen:
            continue
        seen.add(aid)
        out.append(aid[:64])
        if len(out) >= 20:
            break
    return out


def _append_scheduled_asset_context(content: str, asset_ids: List[str]) -> str:
    ids = [a for a in asset_ids if a]
    if not ids:
        return content
    if "【附加素材】" in (content or ""):
        return content
    return (content or "").rstrip() + "\n\n【附加素材】\n" + "\n".join(f"- asset_id: {aid}" for aid in ids)


def _inject_scheduled_assets_into_capability_payload(cap_payload: Dict[str, Any], asset_ids: List[str]) -> Dict[str, Any]:
    if not asset_ids:
        return cap_payload
    out = dict(cap_payload or {})
    out["attachment_asset_ids"] = _merge_id_list(out.get("attachment_asset_ids"), asset_ids)
    out["asset_ids"] = _merge_id_list(out.get("asset_ids"), asset_ids)
    return out


def _scheduled_asset_context_with_urls(asset_ids: List[str], jwt_token: str, installation_id: str) -> str:
    ids = [a for a in asset_ids if a]
    if not ids:
        return ""
    db = SessionLocal()
    try:
        uid = int(_decode_jwt_sub(jwt_token) or "0")
        if uid <= 0:
            return "\n".join(f"- asset_id: {aid}" for aid in ids)
        req = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
        lines: List[str] = []
        for aid in ids:
            row = db.query(Asset).filter(Asset.user_id == uid, Asset.asset_id == aid).first()
            if not row:
                lines.append(f"- asset_id: {aid}  状态: 本机素材库未找到")
                continue
            url = _scheduled_asset_open_url(row, aid, uid, req, db)
            mt = (row.media_type or "").strip()
            if url:
                lines.append(f"- asset_id: {aid}  media_type: {mt}  URL: {url}")
            else:
                lines.append(f"- asset_id: {aid}  media_type: {mt}  状态: 无公网 URL")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("[SCHEDULED-TASK] build asset context failed ids=%s err=%s", ids, exc)
        return "\n".join(f"- asset_id: {aid}" for aid in ids)
    finally:
        db.close()


def _scheduled_asset_open_url(row: Asset, asset_id: str, user_id: int, request: Request, db) -> str:
    url = get_asset_public_url(asset_id, user_id, request, db) or ""
    if url:
        return url
    source_url = str(getattr(row, "source_url", None) or "").strip()
    if source_url.startswith(("http://", "https://")):
        return source_url
    filename = str(getattr(row, "filename", None) or "").strip()
    if filename:
        return build_asset_file_url(request, asset_id, expiry_sec=86400) or ""
    return ""


async def _run_direct_chat(
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    item: Dict[str, Any],
    *,
    jwt_token: str = "",
) -> None:
    message_id = str(item.get("id") or "").strip()
    content = str(item.get("content") or "").strip()
    if not message_id or not content:
        return

    await _post_cloud_event(cloud, base, headers, message_id, "thinking", {"text": "本地直连链路正在处理"})
    payload = {
        "message": content,
        "history": [],
        "session_id": f"h5-{message_id}",
        "context_id": f"h5-{message_id}",
    }
    timeout = httpx.Timeout(360.0, connect=10.0, read=360.0, write=30.0, pool=10.0)
    final_reply = ""
    final_error = ""
    result_refs: Dict[str, List[str]] = {"asset_ids": [], "urls": []}

    def merge_refs(refs: Dict[str, List[str]]) -> None:
        for key, limit in (("asset_ids", 12), ("urls", 8)):
            bucket = result_refs[key]
            for value in (refs or {}).get(key) or []:
                value = str(value or "").strip()
                if value and value not in bucket:
                    bucket.append(value)
                    if len(bucket) >= limit:
                        break

    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as local:
            async with local.stream("POST", _local_chat_url(), json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    text = (await resp.aread()).decode("utf-8", errors="replace")
                    raise RuntimeError(text[:500] or f"local chat HTTP {resp.status_code}")
                async for line in resp.aiter_lines():
                    line = (line or "").strip()
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw:
                        continue
                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    et = str(ev.get("type") or "progress")
                    merge_refs(_collect_scheduled_result_refs(ev))
                    if et == "done":
                        final_reply = str(ev.get("reply") or "").strip()
                        final_error = str(ev.get("error") or "").strip()
                        merge_refs(_collect_scheduled_result_refs(final_reply))
                        break
                    await _post_cloud_event(cloud, base, headers, message_id, et[:32], ev)
        if final_error:
            await _complete_cloud_message(cloud, base, headers, message_id, error=final_error)
        else:
            refs = _scheduled_refs_with_asset_urls(result_refs, jwt_token)
            await _complete_cloud_message(
                cloud,
                base,
                headers,
                message_id,
                reply_text=final_reply or "处理完成。",
                payload={"mode": "direct", "result_refs": refs, "media_urls": refs.get("urls") or []},
            )
    except Exception as exc:
        logger.exception("[H5-CHAT] direct chat failed message_id=%s", message_id)
        await _complete_cloud_message(cloud, base, headers, message_id, error=str(exc)[:500] or "本地直连处理失败")


async def _run_openclaw_chat(
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    jwt_token: str,
    installation_id: str,
    item: Dict[str, Any],
) -> None:
    message_id = str(item.get("id") or "").strip()
    content = str(item.get("content") or "").strip()
    if not message_id or not content:
        return
    await _post_cloud_event(cloud, base, headers, message_id, "thinking", {"text": "已交给本机 OpenClaw"})
    messages = [
        {"role": "system", "content": "你是用户的手机会话助手。根据用户消息自然完成任务，使用中文回复。"},
        {"role": "user", "content": content},
    ]
    try:
        reply = await try_openclaw(
            messages,
            openclaw_fallback_model(),
            jwt_token,
            installation_id=installation_id,
            video_model_lock=(getattr(settings, "lobster_default_video_generate_model", None) or "veo3.1-fast"),
            video_model_lock_source="default",
        )
        if not reply:
            await _complete_cloud_message(
                cloud,
                base,
                headers,
                message_id,
                error="OpenClaw 无有效回复，请检查本机 OpenClaw Gateway 是否启动。",
            )
            return
        await _complete_cloud_message(
            cloud,
            base,
            headers,
            message_id,
            reply_text=reply.strip(),
            payload={"mode": "openclaw"},
        )
    except Exception as exc:
        logger.exception("[H5-CHAT] openclaw chat failed message_id=%s", message_id)
        await _complete_cloud_message(cloud, base, headers, message_id, error=str(exc)[:500] or "OpenClaw 处理失败")


async def _process_item(
    client: httpx.AsyncClient,
    base: str,
    jwt_token: str,
    installation_id: str,
    item: Dict[str, Any],
) -> None:
    headers = _headers(jwt_token, installation_id)
    await _run_direct_chat(client, base, headers, item, jwt_token=jwt_token)


async def _run_scheduled_chat_message(
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    item: Dict[str, Any],
    *,
    openclaw: bool,
    jwt_token: str,
    installation_id: str,
) -> None:
    run_id = str(item.get("id") or "").strip()
    content = str(item.get("content") or "").strip()
    attachment_asset_ids = _scheduled_attachment_asset_ids(item)
    content = _append_scheduled_asset_context(content, attachment_asset_ids)
    if not run_id or not content:
        return
    await _post_task_event(cloud, base, headers, run_id, "thinking", {"text": "local-online claimed scheduled task"})
    try:
        if openclaw:
            asset_context = _scheduled_asset_context_with_urls(attachment_asset_ids, jwt_token, installation_id)
            user_content = content
            if asset_context:
                user_content = (
                    content.rstrip()
                    + "\n\n【本机素材库上下文】\n"
                    + asset_context
                    + "\n请优先使用这些真实素材 ID/URL；不要编造素材 ID。"
                )
            messages = [
                {"role": "system", "content": "You are executing a scheduled OpenClaw task. Follow the user request and return the final result concisely."},
                {"role": "user", "content": user_content},
            ]
            reply = await try_openclaw(
                messages,
                openclaw_fallback_model(),
                jwt_token,
                installation_id=installation_id,
                video_model_lock=(getattr(settings, "lobster_default_video_generate_model", None) or "veo3.1-fast"),
                video_model_lock_source="default",
            )
            if not reply:
                raise RuntimeError("OpenClaw returned no reply")
            await _complete_task_run(
                cloud,
                base,
                headers,
                run_id,
                result_text=reply.strip(),
                result_payload={"mode": "openclaw_message"},
            )
            return

        payload = {
            "message": content,
            "history": [],
            "session_id": f"scheduled-{run_id}",
            "context_id": f"scheduled-{run_id}",
            "attachment_asset_ids": attachment_asset_ids,
        }
        timeout = httpx.Timeout(360.0, connect=10.0, read=360.0, write=30.0, pool=10.0)
        final_reply = ""
        final_error = ""
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as local:
            async with local.stream("POST", _local_chat_url(), json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    text = (await resp.aread()).decode("utf-8", errors="replace")
                    raise RuntimeError(text[:500] or f"local chat HTTP {resp.status_code}")
                async for line in resp.aiter_lines():
                    line = (line or "").strip()
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw:
                        continue
                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    et = str(ev.get("type") or "progress")
                    if et == "done":
                        final_reply = str(ev.get("reply") or "").strip()
                        final_error = str(ev.get("error") or "").strip()
                        break
                    await _post_task_event(cloud, base, headers, run_id, et[:32], ev)
        if final_error:
            await _complete_task_run(cloud, base, headers, run_id, error=final_error)
        else:
            await _complete_task_run(
                cloud,
                base,
                headers,
                run_id,
                result_text=final_reply or "done",
                result_payload={"mode": "chat_message"},
            )
    except Exception as exc:
        logger.exception("[SCHEDULED-TASK] chat task failed run_id=%s", run_id)
        await _complete_task_run(cloud, base, headers, run_id, error=str(exc)[:500] or "local execution failed")


def _compact_result_text(obj: Any) -> str:
    if isinstance(obj, str):
        return obj[:4000]
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)[:4000]
    except Exception:
        return str(obj)[:4000]


def _extract_json_object_text(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for item in candidates:
        try:
            data = json.loads(item)
            return data if isinstance(data, dict) else {}
        except Exception:
            continue
    return {}


def _scheduled_memory_context(jwt_token: str, installation_id: str, query: str) -> str:
    try:
        from .openclaw_chat_gateway import _build_openclaw_memory_context

        return _build_openclaw_memory_context(
            [{"role": "user", "content": query}],
            jwt_token,
            installation_id,
            "default",
        )
    except Exception as exc:
        logger.warning("[SCHEDULED-TASK] memory context unavailable: %s", exc)
        return ""


def _scheduled_llm_model() -> str:
    return (
        (getattr(settings, "lobster_orchestration_sutui_chat_model", None) or "").strip()
        or (getattr(settings, "lobster_default_sutui_chat_model", None) or "").strip()
        or "deepseek-chat"
    )


async def _call_scheduled_llm(
    *,
    base: str,
    headers: Dict[str, str],
    system: str,
    user_payload: Dict[str, Any],
    temperature: float = 0.2,
) -> str:
    body = {
        "model": _scheduled_llm_model(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "stream": False,
        "temperature": temperature,
    }
    async with httpx.AsyncClient(timeout=90.0, trust_env=False) as client:
        resp = await client.post(f"{base}/api/sutui-chat/completions", json=body, headers=headers)
    if resp.status_code >= 400:
        raise RuntimeError(f"sutui-chat HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    data = resp.json() if resp.content else {}
    try:
        return str(data["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return _compact_result_text(data)


def _fallback_goal(task_title: str) -> str:
    title = (task_title or "").strip()
    if title and title not in {"能力定时任务", "目标成片", "创意成片"}:
        return f"根据我的记忆和任务名称“{title}”，生成一个 8 秒抖音 9:16 中文宣传视频。"
    return "根据我的记忆，自动选择最适合推广的产品或服务，生成一个 8 秒抖音 9:16 中文宣传视频。"


def _fallback_hifly_script(task_title: str) -> str:
    title = (task_title or "").strip()
    subject = title[:12] if title and title not in {"能力定时任务", "飞影数字人", "飞鹰数字人", "必火数字人"} else "这款产品"
    return f"大家好，今天带你了解{subject}，一起看看核心亮点。"


def _hifly_script_text(text: Any) -> str:
    s = re.sub(r"\s+", "", str(text or "").strip())
    return s if len(s) <= 50 else ""


async def _generate_scheduled_content(
    *,
    base: str,
    headers: Dict[str, str],
    jwt_token: str,
    installation_id: str,
    capability_id: str,
    task_title: str,
    asset_context: str,
    run_id: str = "",
) -> Dict[str, Any]:
    ability = "必火数字人" if capability_id == "hifly.video.create_by_tts" else "创意成片"
    query = "\n".join([task_title or ability, ability, asset_context or ""]).strip()
    memory_context = _scheduled_memory_context(jwt_token, installation_id, query)
    seed = "|".join([run_id, capability_id, task_title, str(len(memory_context)), str(len(asset_context))])
    creative_angle = _scheduled_variant(seed, _SCHEDULED_CREATIVE_ANGLES)
    caption_style = _scheduled_variant(seed + "|caption", _SCHEDULED_CAPTION_STYLES)
    if capability_id == "hifly.video.create_by_tts":
        system = (
            "你是定时任务内容编排器。只输出 JSON 对象，不要 Markdown。\n"
            "根据用户记忆和可用素材，为必火数字人口播生成内容。"
            "字段：title(string), script(string), caption_hint(string)。"
            "script 是数字人口播文案，中文，一句话，必须完整通顺，最多 50 个字。"
            "不要先写长文案，不要分段，不要要求用户补充信息，不要编造素材 ID。"
        )
    else:
        system = (
            "你是定时任务内容编排器。只输出 JSON 对象，不要 Markdown。\n"
            "根据用户记忆和可用素材，为创意成片流水线生成目标。"
            "字段：title(string), goal(string), caption_hint(string), creative_angle(string), selling_points(array)。"
            "先从记忆里抽取真实卖点，再围绕指定创意角度生成本次视频目标。"
            "goal 要能直接传给创意成片能力，明确 8 秒、抖音、9:16、中文宣传视频，并写出本次成片的切入角度、画面方向和核心短文案。"
            "每次都要换表达，不要复用固定开头、固定句式或通用宣传套话；不要要求用户补充信息，不要编造素材 ID。"
        )
    user_payload = {
        "task_title": task_title,
        "ability": ability,
        "creative_angle": creative_angle,
        "caption_style": caption_style,
        "variation_rule": "本次必须围绕 creative_angle 取材和表达，避免和以往定时任务使用同一套宣传话术。",
        "memory_context": memory_context[:12000],
        "asset_context": asset_context[:4000],
    }
    try:
        text = await _call_scheduled_llm(
            base=base,
            headers=headers,
            system=system,
            user_payload=user_payload,
            temperature=0.75 if capability_id == "goal.video.pipeline" else 0.35,
        )
        data = _extract_json_object_text(text)
    except Exception as exc:
        logger.warning("[SCHEDULED-TASK] generate content failed capability_id=%s: %s", capability_id, exc)
        data = {}
    title = str(data.get("title") or task_title or ability).strip()[:120]
    if capability_id == "hifly.video.create_by_tts":
        script = _hifly_script_text(data.get("script"))
        if not script:
            script = _fallback_hifly_script(task_title)
        return {
            "title": title or "数字人口播",
            "script": script,
            "caption_hint": str(data.get("caption_hint") or "").strip()[:200],
            "creative_angle": creative_angle,
            "caption_style": caption_style,
            "memory_context_used": bool(memory_context),
        }
    goal = str(data.get("goal") or "").strip()
    if not goal:
        goal = _fallback_goal(task_title)
    return {
        "title": title or "创意成片",
        "goal": goal[:1000],
        "caption_hint": str(data.get("caption_hint") or "").strip()[:200],
        "creative_angle": str(data.get("creative_angle") or creative_angle).strip()[:40],
        "caption_style": caption_style,
        "selling_points": data.get("selling_points") if isinstance(data.get("selling_points"), list) else [],
        "memory_context_used": bool(memory_context),
    }


def _collect_scheduled_result_refs(obj: Any) -> Dict[str, List[str]]:
    asset_ids: List[str] = []
    urls: List[str] = []

    def add_asset(v: Any) -> None:
        s = str(v or "").strip()
        if s and s not in asset_ids:
            asset_ids.append(s[:128])

    def add_url(v: Any) -> None:
        s = str(v or "").strip()
        if s.startswith(("http://", "https://")) and s not in urls:
            urls.append(s[:500])

    def walk(x: Any, depth: int = 0) -> None:
        if depth > 12 or x is None:
            return
        if isinstance(x, dict):
            for k, v in x.items():
                key = str(k or "").lower()
                if key in {"asset_id", "final_asset_id", "video_asset_id", "image_asset_id"}:
                    if isinstance(v, list):
                        for item in v:
                            add_asset(item)
                    else:
                        add_asset(v)
                if key.endswith("url") or key in {"url", "src", "href"}:
                    add_url(v)
                walk(v, depth + 1)
        elif isinstance(x, list):
            for item in x:
                walk(item, depth + 1)
        elif isinstance(x, str):
            add_url(x)
            for match in _RESULT_URL_RE.finditer(x):
                add_url(match.group(0).rstrip(".,!?，。！？、；："))

    walk(obj)
    return {"asset_ids": asset_ids[:12], "urls": urls[:8]}


def _scheduled_refs_with_asset_urls(
    refs: Dict[str, List[str]],
    jwt_token: str,
) -> Dict[str, List[str]]:
    out = {
        "asset_ids": list((refs or {}).get("asset_ids") or [])[:12],
        "urls": list((refs or {}).get("urls") or [])[:8],
    }
    uid = int(_decode_jwt_sub(jwt_token) or "0")
    if uid <= 0 or not out["asset_ids"]:
        return out
    db = SessionLocal()
    try:
        req = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
        for aid in out["asset_ids"]:
            row = db.query(Asset).filter(Asset.user_id == uid, Asset.asset_id == aid).first()
            if not row:
                continue
            url = _scheduled_asset_open_url(row, aid, uid, req, db)
            if url and url not in out["urls"]:
                out["urls"].append(url[:500])
                if len(out["urls"]) >= 8:
                    break
    except Exception as exc:
        logger.warning("[SCHEDULED-TASK] build result preview urls failed asset_ids=%s err=%s", out["asset_ids"], exc)
    finally:
        db.close()
    return {"asset_ids": out["asset_ids"][:12], "urls": out["urls"][:8]}


def _scheduled_refs_asset_urls_only(
    refs: Dict[str, List[str]],
    jwt_token: str,
) -> Dict[str, List[str]]:
    return _scheduled_refs_with_asset_urls({"asset_ids": (refs or {}).get("asset_ids") or [], "urls": []}, jwt_token)


def _scheduled_hifly_result_refs(result: Any, jwt_token: str) -> Dict[str, List[str]]:
    raw = _collect_scheduled_result_refs(result)
    out = {"asset_ids": list(raw.get("asset_ids") or [])[:12], "urls": []}

    def add_url(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                add_url(item)
            return
        s = str(value or "").strip()
        if s.startswith(("http://", "https://")) and s not in out["urls"]:
            out["urls"].append(s[:500])

    if isinstance(result, dict):
        add_url(result.get("source_media_urls"))
        inner = result.get("result")
        if isinstance(inner, dict):
            add_url(inner.get("video_url"))
    if not out["urls"]:
        return _scheduled_refs_asset_urls_only(raw, jwt_token)
    return {"asset_ids": out["asset_ids"], "urls": out["urls"][:8]}


def _scheduled_result_ready(result: Any) -> bool:
    if not isinstance(result, dict):
        return True
    if result.get("error") or result.get("ok") is False:
        return False
    status = str(result.get("status") or result.get("pipeline_status") or "").strip().lower()
    if status in {"running", "processing", "pending", "queued", "waiting"}:
        return False
    if result.get("result_ready") is False:
        return False
    inner = result.get("result")
    if isinstance(inner, dict) and inner is not result:
        return _scheduled_result_ready(inner)
    return True


def _scheduled_capability_error(result: Any) -> str:
    if isinstance(result, dict):
        err = result.get("error") or result.get("detail")
        if err:
            return _compact_result_text(err)
        if result.get("ok") is False:
            return _compact_result_text(result.get("message") or result)
        inner = result.get("result")
        if isinstance(inner, dict) and inner is not result:
            return _scheduled_capability_error(inner)
    return ""


def _scheduled_caption_candidate(value: Any) -> str:
    text = " ".join(str(value or "").strip().strip('"“”`').split())
    text = re.sub(r"^(发布文案|朋友圈文案|文案)\s*[:：]\s*", "", text).strip()
    return text if 0 < len(text) <= 50 else ""


def _fallback_scheduled_caption(capability_id: str, generated: Dict[str, Any]) -> str:
    hint = _scheduled_caption_candidate(generated.get("caption_hint"))
    if hint:
        return hint
    title = str(generated.get("title") or "").strip()
    subject = title[:12] if title and title not in {"能力定时任务", "目标成片", "创意成片", "数字人口播"} else "这次内容"
    angle = str(generated.get("creative_angle") or "").strip()
    options = {
        "痛点切入": f"{subject}把难点讲清楚，选型和落地都更有底气。",
        "场景体验": f"把{subject}放进真实场景里看，价值会更直观。",
        "结果收益": f"{subject}不只好看，更要带来效率、品质和确定性。",
        "工艺实力": f"用细节呈现{subject}实力，让专业能力被一眼看见。",
        "交付效率": f"{subject}从需求到交付更顺畅，少等待，多确定。",
        "信任背书": f"靠谱的{subject}，来自持续稳定的能力和服务。",
        "对比反差": f"同样做{subject}，差别往往藏在细节和交付里。",
        "客户视角": f"站在客户角度看{subject}，省心就是最大的价值。",
    }
    if angle in options:
        return options[angle]
    if capability_id == "hifly.video.create_by_tts":
        return f"{subject}亮点已生成，适合直接分享给客户看看。"
    return f"{subject}宣传视频已生成，换个角度看看产品价值。"


async def _generate_scheduled_caption(
    *,
    base: str,
    headers: Dict[str, str],
    capability_id: str,
    generated: Dict[str, Any],
    result: Any,
) -> str:
    fallback = _fallback_scheduled_caption(capability_id, generated)
    system = (
        "你只负责写发布朋友圈文案。输出一条中文，一句完整话，35 到 50 个字，不要 Markdown，不要解释。"
        "必须根据 generated_content 里的 goal/script、caption_hint、creative_angle 和 result_refs 重新创作，"
        "不要照抄 caption_hint，不要使用固定宣传口号。"
        "同一用户多次执行时要换切入角度和句式，让每次发布看起来不是同一模板。"
    )
    refs = _collect_scheduled_result_refs(result)
    text = ""
    try:
        text = await _call_scheduled_llm(
            base=base,
            headers=headers,
            system=system,
            user_payload={
                "ability": capability_id,
                "generated_content": generated,
                "result_refs": refs,
                "creative_angle": generated.get("creative_angle"),
                "caption_style": generated.get("caption_style"),
                "prompt_sent_to_skill": generated.get("goal") or generated.get("script"),
                "length_rule": "必须是一句完整中文，最多 50 个字，不允许先生成长文再截断。",
            },
            temperature=0.85 if capability_id == "goal.video.pipeline" else 0.55,
        )
    except Exception as exc:
        logger.warning("[SCHEDULED-TASK] caption failed capability_id=%s: %s", capability_id, exc)
    caption = _scheduled_caption_candidate(text)
    if not caption and text:
        try:
            rewrite = await _call_scheduled_llm(
                base=base,
                headers=headers,
                system="把原文重写为一条完整中文朋友圈文案，最多 50 个字，不要 Markdown，不要解释。",
                user_payload={
                    "ability": capability_id,
                    "generated_content": generated,
                    "original_caption": text,
                    "length_rule": "不要截断，直接重写成一句完整话。",
                },
                temperature=0.6,
            )
            caption = _scheduled_caption_candidate(rewrite)
        except Exception as exc:
            logger.warning("[SCHEDULED-TASK] caption rewrite failed capability_id=%s: %s", capability_id, exc)
    return caption or fallback


def _scheduled_complete_text(
    result: Any,
    caption: str,
    refs: Optional[Dict[str, List[str]]] = None,
    skill_prompt: str = "",
) -> str:
    ready = _scheduled_result_ready(result)
    lines = ["生成完成。" if ready else "任务已提交，仍在生成中。", f"发布文案：{caption}"]
    if skill_prompt:
        lines.append(f"传给技能的提示词：{skill_prompt}")
    refs = refs or _collect_scheduled_result_refs(result)
    if refs["asset_ids"]:
        lines.append("素材：" + "、".join(refs["asset_ids"][:6]))
    if refs["urls"]:
        lines.append("预览链接：")
        lines.extend(refs["urls"][:6])
    return "\n".join(lines)


def _extract_mcp_payload(data: Any) -> Any:
    result = data.get("result") if isinstance(data, dict) else data
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not isinstance(text, str):
                continue
            try:
                return json.loads(text)
            except Exception:
                return text
    return result


async def _invoke_local_capability(
    *,
    headers: Dict[str, str],
    run_id: str,
    capability_id: str,
    cap_payload: Dict[str, Any],
) -> Any:
    rpc = {
        "jsonrpc": "2.0",
        "id": f"scheduled-{run_id}",
        "method": "tools/call",
        "params": {
            "name": "invoke_capability",
            "arguments": {"capability_id": capability_id, "payload": cap_payload},
        },
    }
    timeout = httpx.Timeout(7200.0, connect=10.0, read=7200.0, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as local:
        resp = await local.post("http://127.0.0.1:8001/mcp", json=rpc, headers=headers)
    if resp.status_code >= 400:
        raise RuntimeError((resp.text or f"MCP HTTP {resp.status_code}")[:500])
    data = resp.json()
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(_compact_result_text(data.get("error")))
    return _extract_mcp_payload(data)


async def _invoke_hifly_cloud_tts(
    *,
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    cap_payload: Dict[str, Any],
) -> Dict[str, Any]:
    body = {
        "title": str(cap_payload.get("title") or "数字人口播")[:128],
        "avatar": str(cap_payload.get("avatar") or "").strip(),
        "voice": str(cap_payload.get("voice") or "").strip(),
        "text": str(cap_payload.get("text") or "").strip(),
        "st_show": int(cap_payload.get("st_show") or 1),
        "aigc_flag": int(cap_payload.get("aigc_flag") or 0),
    }
    create_resp = await cloud.post(f"{base}/api/hifly/my/video/create-by-tts", json=body, headers=headers)
    create_data = create_resp.json() if create_resp.content else {}
    if create_resp.status_code >= 400 or create_data.get("ok") is False:
        raise RuntimeError(str(create_data.get("detail") or create_data.get("error") or create_data or create_resp.text)[:500])
    task_id = str(create_data.get("task_id") or "").strip()
    if not task_id:
        raise RuntimeError("HiFly 未返回 task_id")
    poll_timeout = int(cap_payload.get("poll_timeout_seconds") or 2400)
    interval = max(3, int(cap_payload.get("poll_interval_seconds") or 10))
    poll_request_timeout = httpx.Timeout(90.0, connect=10.0, read=90.0, write=30.0, pool=10.0)
    waited = 0
    last: Dict[str, Any] = {"ok": True, "task_id": task_id, "status": 2, "status_text": "生成中"}
    while waited <= poll_timeout:
        try:
            poll_resp = await cloud.post(
                f"{base}/api/hifly/my/video/task",
                json={"task_id": task_id},
                headers=headers,
                timeout=poll_request_timeout,
            )
        except httpx.TimeoutException:
            last = {"ok": True, "task_id": task_id, "status": 2, "status_text": "查询超时，继续等待生成结果"}
            await asyncio.sleep(interval)
            waited += interval
            continue
        last = poll_resp.json() if poll_resp.content else {}
        if poll_resp.status_code >= 400 or last.get("ok") is False:
            if int(last.get("status") or 0) != 4:
                raise RuntimeError(str(last.get("detail") or last.get("error") or last or poll_resp.text)[:500])
        status = int(last.get("status") or 0)
        if status == 3:
            item = last.get("item") if isinstance(last.get("item"), dict) else {}
            asset_id = str(last.get("asset_id") or item.get("asset_id") or "").strip()
            video_url = str(last.get("video_url") or item.get("video_url") or item.get("asset_video_url") or "").strip()
            saved = []
            if asset_id:
                saved.append({"asset_id": asset_id, "media_type": "video", "filename": item.get("title") or body["title"]})
            out: Dict[str, Any] = {
                "capability_id": "hifly.video.create_by_tts",
                "result": last,
                "skill_prompt": body["text"],
                "saved_assets": saved,
            }
            if video_url:
                out["source_media_urls"] = [video_url]
            return out
        if status == 4:
            raise RuntimeError(str(last.get("message") or last.get("detail") or "HiFly 任务失败")[:500])
        await asyncio.sleep(interval)
        waited += interval
    last["result_ready"] = False
    return {"capability_id": "hifly.video.create_by_tts", "result": last}


async def _run_scheduled_capability(
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    item: Dict[str, Any],
    *,
    jwt_token: str,
    installation_id: str,
) -> None:
    run_id = str(item.get("id") or "").strip()
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    capability_id = str(payload.get("capability_id") or "").strip()
    cap_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    attachment_asset_ids = _scheduled_attachment_asset_ids(item)
    if not run_id or not capability_id:
        return
    try:
        task_title = str(item.get("title") or "").strip()
        if capability_id in {"goal.video.pipeline", "hifly.video.create_by_tts"}:
            asset_context = _scheduled_asset_context_with_urls(attachment_asset_ids, jwt_token, installation_id)
            await _post_task_event(cloud, base, headers, run_id, "thinking", {"text": "正在根据记忆生成本次内容"})
            generated = await _generate_scheduled_content(
                base=base,
                headers=headers,
                jwt_token=jwt_token,
                installation_id=installation_id,
                capability_id=capability_id,
                task_title=task_title,
                asset_context=asset_context,
                run_id=run_id,
            )
            if capability_id == "goal.video.pipeline":
                cap_payload = {
                    "action": "start_pipeline",
                    "goal": generated.get("goal") or _fallback_goal(task_title),
                    "platform": "douyin",
                    "duration": 8,
                    "aspect_ratio": "9:16",
                    "language": "zh",
                    "memory_scope": "default",
                }
                if attachment_asset_ids:
                    cap_payload["reference_asset_ids"] = attachment_asset_ids[:8]
            else:
                avatar = str(cap_payload.get("avatar") or "").strip()
                voice = str(cap_payload.get("voice") or "").strip()
                if not avatar:
                    raise RuntimeError("请选择数字人")
                if not voice:
                    raise RuntimeError("请选择声音")
                skill_prompt = _hifly_script_text(generated.get("script")) or _fallback_hifly_script(task_title)
                cap_payload = {
                    "title": (generated.get("title") or task_title or "数字人口播")[:20],
                    "avatar": avatar,
                    "voice": voice,
                    "text": skill_prompt,
                    "st_show": 1,
                    "aigc_flag": 0,
                    "poll_interval_seconds": 10,
                    "poll_timeout_seconds": 2400,
                }
            cap_payload = _inject_scheduled_assets_into_capability_payload(cap_payload, attachment_asset_ids)
            await _post_task_event(cloud, base, headers, run_id, "thinking", {"text": f"正在调用 {capability_id}"})
            if capability_id == "hifly.video.create_by_tts":
                result = await _invoke_hifly_cloud_tts(
                    cloud=cloud,
                    base=base,
                    headers=headers,
                    cap_payload=cap_payload,
                )
            else:
                result = await _invoke_local_capability(
                    headers=headers,
                    run_id=run_id,
                    capability_id=capability_id,
                    cap_payload=cap_payload,
                )
            cap_error = _scheduled_capability_error(result)
            if cap_error:
                raise RuntimeError(cap_error)
            await _post_task_event(cloud, base, headers, run_id, "thinking", {"text": "正在生成发布文案"})
            caption = await _generate_scheduled_caption(
                base=base,
                headers=headers,
                capability_id=capability_id,
                generated=generated,
                result=result,
            )
            raw_refs = _collect_scheduled_result_refs(result)
            refs = (
                _scheduled_hifly_result_refs(result, jwt_token)
                if capability_id == "hifly.video.create_by_tts"
                else _scheduled_refs_with_asset_urls(raw_refs, jwt_token)
            )
            skill_prompt = str(cap_payload.get("text") or cap_payload.get("goal") or result.get("skill_prompt") or "").strip()
            await _complete_task_run(
                cloud,
                base,
                headers,
                run_id,
                result_text=_scheduled_complete_text(result, caption, refs, skill_prompt),
                result_payload={
                    "capability_id": capability_id,
                    "generated": generated,
                    "caption": caption,
                    "skill_prompt": skill_prompt,
                    "mcp_result": result,
                    "result_refs": refs,
                    "media_urls": refs["urls"],
                },
            )
            return

        cap_payload = _inject_scheduled_assets_into_capability_payload(cap_payload, attachment_asset_ids)
        await _post_task_event(cloud, base, headers, run_id, "thinking", {"text": f"invoke_capability {capability_id}"})
        result = await _invoke_local_capability(
            headers=headers,
            run_id=run_id,
            capability_id=capability_id,
            cap_payload=cap_payload,
        )
        cap_error = _scheduled_capability_error(result)
        if cap_error:
            raise RuntimeError(cap_error)
        await _complete_task_run(
            cloud,
            base,
            headers,
            run_id,
            result_text=_compact_result_text(result),
            result_payload={"capability_id": capability_id, "mcp_result": result},
        )
    except Exception as exc:
        logger.exception("[SCHEDULED-TASK] capability failed run_id=%s capability_id=%s", run_id, capability_id)
        error_text = str(exc).strip() or exc.__class__.__name__
        await _complete_task_run(cloud, base, headers, run_id, error=error_text[:500] or "capability failed")


async def _process_scheduled_task(
    client: httpx.AsyncClient,
    base: str,
    jwt_token: str,
    installation_id: str,
    item: Dict[str, Any],
) -> None:
    headers = _headers(jwt_token, installation_id)
    kind = str(item.get("task_kind") or "openclaw_message").strip().lower()
    if kind == "capability":
        await _run_scheduled_capability(
            client,
            base,
            headers,
            item,
            jwt_token=jwt_token,
            installation_id=installation_id,
        )
    elif kind == "chat_message":
        await _run_scheduled_chat_message(
            client,
            base,
            headers,
            item,
            openclaw=False,
            jwt_token=jwt_token,
            installation_id=installation_id,
        )
    else:
        await _run_scheduled_chat_message(
            client,
            base,
            headers,
            item,
            openclaw=True,
            jwt_token=jwt_token,
            installation_id=installation_id,
        )


def _channel_concurrency(name: str, default: int, upper: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or str(default))
    except (TypeError, ValueError):
        value = default
    return max(1, min(upper, value))


def _reap_channel_tasks(active: set[asyncio.Task], label: str) -> None:
    for task in list(active):
        if not task.done():
            continue
        active.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception("[%s] background task failed: %s", label, exc)


async def _scheduled_task_keepalive(
    client: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    run_id: str,
) -> None:
    interval = float(os.environ.get("LOBSTER_SCHEDULED_TASK_HEARTBEAT_SEC", "120") or "120")
    interval = max(30.0, interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await _post_task_event(
                client,
                base,
                headers,
                run_id,
                "heartbeat",
                {"text": "本地执行中", "heartbeat": True},
            )
        except Exception as exc:
            logger.debug("[SCHEDULED-TASK] heartbeat failed run_id=%s: %s", run_id, exc)


async def _process_item_detached(
    base: str,
    jwt_token: str,
    installation_id: str,
    item: Dict[str, Any],
) -> None:
    timeout = httpx.Timeout(7200.0, connect=10.0, read=7200.0, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        await _process_item(client, base, jwt_token, installation_id, item)


async def _process_scheduled_task_detached(
    base: str,
    jwt_token: str,
    installation_id: str,
    item: Dict[str, Any],
) -> None:
    timeout = httpx.Timeout(7200.0, connect=10.0, read=7200.0, write=30.0, pool=10.0)
    headers = _headers(jwt_token, installation_id)
    run_id = str(item.get("id") or "").strip()
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        keepalive: Optional[asyncio.Task] = None
        if run_id:
            keepalive = asyncio.create_task(_scheduled_task_keepalive(client, base, headers, run_id))
        try:
            await _process_scheduled_task(client, base, jwt_token, installation_id, item)
        finally:
            if keepalive:
                keepalive.cancel()
                try:
                    await keepalive
                except asyncio.CancelledError:
                    pass


async def h5_chat_poll_loop() -> None:
    if not _enabled():
        logger.info("[H5-CHAT] remote H5 chat channel disabled")
        return

    sleep_empty = float(os.environ.get("LOBSTER_H5_CHAT_POLL_INTERVAL_SEC", "2.0") or "2.0")
    sleep_missing_auth = 10.0
    logged_missing = False
    max_h5_concurrency = _channel_concurrency("LOBSTER_H5_CHAT_CONCURRENCY", 1, 5)
    max_task_concurrency = _channel_concurrency("LOBSTER_SCHEDULED_TASK_CONCURRENCY", 2, 10)
    active_items: set[asyncio.Task] = set()
    active_task_runs: set[asyncio.Task] = set()

    while True:
        _reap_channel_tasks(active_items, "H5-CHAT")
        _reap_channel_tasks(active_task_runs, "SCHEDULED-TASK")

        base = _cloud_base()
        jwt_token, installation_id = _auth_context()
        if not base or not jwt_token:
            if not logged_missing:
                logger.info("[H5-CHAT] waiting for AUTH_SERVER_BASE and logged-in channel token")
                logged_missing = True
            await asyncio.sleep(sleep_missing_auth)
            continue
        logged_missing = False

        headers = _headers(jwt_token, installation_id)
        try:
            timeout = httpx.Timeout(30.0, connect=10.0, read=30.0, write=10.0, pool=10.0)
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                await client.post(
                    f"{base}/api/h5-chat/device-heartbeat",
                    json={"display_name": "local-online"},
                    headers=headers,
                )
                items: list[Dict[str, Any]] = []
                h5_slots = max(0, max_h5_concurrency - len(active_items))
                if h5_slots > 0:
                    resp = await client.get(f"{base}/api/h5-chat/pending", params={"limit": h5_slots}, headers=headers)
                    if resp.status_code == 401:
                        logger.warning("[H5-CHAT] cloud auth rejected; waiting for next login token")
                        await asyncio.sleep(sleep_missing_auth)
                        continue
                    resp.raise_for_status()
                    items = (resp.json() or {}).get("items") or []
                task_items: list[Dict[str, Any]] = []
                task_slots = max(0, max_task_concurrency - len(active_task_runs))
                if task_slots > 0:
                    task_resp = await client.get(
                        f"{base}/api/scheduled-tasks/pending",
                        params={"limit": task_slots},
                        headers=headers,
                    )
                    if task_resp.status_code == 401:
                        logger.warning("[SCHEDULED-TASK] cloud auth rejected; waiting for next login token")
                        await asyncio.sleep(sleep_missing_auth)
                        continue
                    if task_resp.status_code < 400:
                        task_items = (task_resp.json() or {}).get("items") or []
                    elif task_resp.status_code != 404:
                        logger.debug("[SCHEDULED-TASK] pending request HTTP %s: %s", task_resp.status_code, task_resp.text[:300])
                if not items and not task_items:
                    await asyncio.sleep(sleep_empty)
                    continue
                for item in items:
                    active_items.add(asyncio.create_task(_process_item_detached(base, jwt_token, installation_id, item)))
                for item in task_items:
                    active_task_runs.add(
                        asyncio.create_task(_process_scheduled_task_detached(base, jwt_token, installation_id, item))
                    )
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[H5-CHAT] poll loop error: %s", exc)
            await asyncio.sleep(5.0)
