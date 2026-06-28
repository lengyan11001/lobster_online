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
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func

from ..core.config import settings
from ..db import SessionLocal
from ..models import Asset, PublishAccount
from ..services.openclaw_channel_auth_store import clear_channel_fallback, read_channel_fallback
from .auth import get_current_user_for_local
from .assets import build_asset_file_url, get_asset_public_url
from .chat import _get_default_image_generate_model
from .create_ppt_pipeline import CreatePptPipelinePayload, run_create_ppt_pipeline
from .create_video_pipeline import CreateVideoPipelinePayload, run_create_video_pipeline_with_total_billing
from .goal_video_pipeline import (
    GoalVideoPipelinePayload,
    PipelinePartialResultError,
    _with_video_no_text_constraint,
    run_goal_image_pipeline,
    run_goal_video_pipeline_with_total_billing,
)
from .openclaw_chat_gateway import openclaw_fallback_model, try_openclaw

logger = logging.getLogger(__name__)
router = APIRouter()
_BASE_DIR = Path(__file__).resolve().parents[3]
_DOUYIN_ORIGIN_DIR = _BASE_DIR / "backend" / "douyin_origin"
_RESULT_URL_RE = re.compile(r'https?://[^\s"\'<>\)\]]+', re.IGNORECASE)
_active_scheduled_run_ids: set[str] = set()
_SCHEDULED_COMPLETE_RETRY_STATUS = {500, 502, 503, 504}
_MOBILE_UPLOAD_TITLE = "【手机上传素材】"
_MOBILE_UPLOAD_BLOCK_RE = re.compile(r"\n*【手机上传素材】\n(?P<body>[\s\S]*)", re.IGNORECASE)


def _local_mcp_url() -> str:
    port = os.environ.get("MCP_PORT") or str(getattr(settings, "mcp_port", 8001))
    return f"http://127.0.0.1:{port}/mcp"
_MOBILE_UPLOAD_URL_RE = re.compile(r"\bURL:\s*(?P<url>https?://[^\s]+)", re.IGNORECASE)
_MOBILE_UPLOAD_ASSET_RE = re.compile(r"\basset_id:\s*(?P<asset_id>[A-Za-z0-9_-]{4,80})", re.IGNORECASE)
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
_SCHEDULED_VIDEO_SOURCE_ASSET_RANDOM = "asset_random"
_SCHEDULED_VIDEO_SOURCE_AI_IMAGE = "ai_image"
_SCHEDULED_VIDEO_SOURCE_REFERENCE_IMAGE = "reference_image"
_CREATIVE_CANDIDATE_USAGE_META_KEY = "creative_candidate_usage"
_CREATIVE_CANDIDATE_RESERVATION_META_KEY = "creative_candidate_reservations"
_IMAGE_MODEL_ALIASES = {
    "openai/gpt-image2": "openai/gpt-image-2",
    "openai/gptimage2": "openai/gpt-image-2",
    "openai/gpt-image": "openai/gpt-image-2",
    "gpt-image2": "openai/gpt-image-2",
    "gpt-image-2": "openai/gpt-image-2",
    "gpt-image": "openai/gpt-image-2",
    "gptimage2": "openai/gpt-image-2",
}


def _install_douyin_origin_import_path() -> None:
    origin_path = str(_DOUYIN_ORIGIN_DIR)
    if origin_path not in sys.path:
        sys.path.insert(0, origin_path)


def _scheduled_variant(seed: str, options: List[str]) -> str:
    if not options:
        return ""
    raw = str(seed or "scheduled").encode("utf-8", "ignore")
    digest = hashlib.sha1(raw).digest()
    return options[int.from_bytes(digest[:2], "big") % len(options)]


def _asset_creative_candidate_groups(meta: Any) -> List[str]:
    if not isinstance(meta, dict):
        return []
    current = str(meta.get("creative_candidate_group") or "").strip()
    if current:
        return [current]
    raw = meta.get("creative_candidate_groups")
    if isinstance(raw, str):
        values = re.split(r"[,\s，、;；]+", raw)
    elif isinstance(raw, list):
        values = raw
    else:
        values = []
    for item in values:
        name = str(item or "").strip()
        if name:
            return [name]
    return []


def _creative_candidate_usage(meta: Any, group_name: str) -> Dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    usage = meta.get(_CREATIVE_CANDIDATE_USAGE_META_KEY)
    if not isinstance(usage, dict):
        usage = meta.get("creative_candidate_use_stats")
    if not isinstance(usage, dict):
        return {}
    current = usage.get(group_name)
    return current if isinstance(current, dict) else {}


def _creative_candidate_use_count(meta: Any, group_name: str) -> int:
    data = _creative_candidate_usage(meta, group_name)
    try:
        return max(int(data.get("count") or data.get("use_count") or 0), 0)
    except Exception:
        return 0


def _creative_candidate_last_used_at(meta: Any, group_name: str) -> str:
    data = _creative_candidate_usage(meta, group_name)
    return str(data.get("last_used_at") or data.get("last_used") or "")


def _creative_candidate_group_reservations(meta: Any, group_name: str) -> Dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    reservations = meta.get(_CREATIVE_CANDIDATE_RESERVATION_META_KEY)
    if not isinstance(reservations, dict):
        return {}
    current = reservations.get(group_name)
    return current if isinstance(current, dict) else {}


def _creative_candidate_reservation_count(meta: Any, group_name: str) -> int:
    return len(_creative_candidate_group_reservations(meta, group_name))


def _creative_candidate_last_reserved_at(meta: Any, group_name: str) -> str:
    latest = ""
    for item in _creative_candidate_group_reservations(meta, group_name).values():
        if not isinstance(item, dict):
            continue
        reserved_at = str(item.get("reserved_at") or "")
        if reserved_at > latest:
            latest = reserved_at
    return latest


def _remove_creative_candidate_reservation(meta: Dict[str, Any], group_name: str, reservation_id: str) -> None:
    rid = str(reservation_id or "").strip()
    if not rid:
        return
    reservations = meta.get(_CREATIVE_CANDIDATE_RESERVATION_META_KEY)
    if not isinstance(reservations, dict):
        return
    current = reservations.get(group_name)
    if not isinstance(current, dict):
        return
    current.pop(rid, None)
    if current:
        reservations[group_name] = current
    else:
        reservations.pop(group_name, None)
    if reservations:
        meta[_CREATIVE_CANDIDATE_RESERVATION_META_KEY] = reservations
    else:
        meta.pop(_CREATIVE_CANDIDATE_RESERVATION_META_KEY, None)


def _mark_creative_candidate_asset_used(
    asset_id: str,
    group_name: str,
    jwt_token: str,
    reservation_id: str = "",
) -> None:
    aid = str(asset_id or "").strip()
    name = str(group_name or "").strip()
    if not aid or not name:
        return
    uid = int(_decode_jwt_sub(jwt_token) or "0")
    if uid <= 0:
        return
    db = SessionLocal()
    try:
        row = db.query(Asset).filter(Asset.user_id == uid, Asset.asset_id == aid).first()
        if not row:
            return
        meta = dict(row.meta or {})
        usage = meta.get(_CREATIVE_CANDIDATE_USAGE_META_KEY)
        if not isinstance(usage, dict):
            usage = {}
        current = usage.get(name)
        if not isinstance(current, dict):
            current = {}
        try:
            count = max(int(current.get("count") or current.get("use_count") or 0), 0)
        except Exception:
            count = 0
        _remove_creative_candidate_reservation(meta, name, reservation_id)
        current["count"] = count + 1
        current["last_used_at"] = datetime.now(timezone.utc).isoformat()
        usage[name] = current
        meta[_CREATIVE_CANDIDATE_USAGE_META_KEY] = usage
        row.meta = meta
        db.add(row)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning(
            "[SCHEDULED-TASK] mark creative candidate used failed asset_id=%s group=%s err=%s",
            aid,
            name,
            exc,
        )
    finally:
        db.close()


def _release_creative_candidate_asset_reservation(
    asset_id: str,
    group_name: str,
    jwt_token: str,
    reservation_id: str,
) -> None:
    aid = str(asset_id or "").strip()
    name = str(group_name or "").strip()
    rid = str(reservation_id or "").strip()
    if not aid or not name or not rid:
        return
    uid = int(_decode_jwt_sub(jwt_token) or "0")
    if uid <= 0:
        return
    db = SessionLocal()
    try:
        row = db.query(Asset).filter(Asset.user_id == uid, Asset.asset_id == aid).first()
        if not row:
            return
        meta = dict(row.meta or {})
        _remove_creative_candidate_reservation(meta, name, rid)
        row.meta = meta
        db.add(row)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning(
            "[SCHEDULED-TASK] release creative candidate reservation failed asset_id=%s group=%s rid=%s err=%s",
            aid,
            name,
            rid,
            exc,
        )
    finally:
        db.close()


def _normalize_image_model_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return _IMAGE_MODEL_ALIASES.get(raw.lower(), raw)


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
    h["X-Lobster-Chat-Turn-Billing"] = "pre_deduct_v1"
    return h


def _local_chat_headers(headers: Dict[str, str]) -> Dict[str, str]:
    out = dict(headers or {})
    billing_key = (getattr(settings, "lobster_mcp_billing_internal_key", None) or "").strip()
    if billing_key:
        out["X-Lobster-Mcp-Billing"] = billing_key
    return out


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _today_date_text() -> str:
    return datetime.now().strftime("%Y-%m-%d")


async def _build_douyin_dashboard_snapshot(jwt_token: str, installation_id: str) -> Dict[str, Any]:
    _install_douyin_origin_import_path()
    from douyin_api import (  # type: ignore
        douyin_get_customer_pools,
        douyin_get_tasks_lite,
        douyin_interaction_status,
        douyin_stranger_message_status,
        douyin_video_comment_status,
        get_online_douyin_accounts,
        load_global_config,
    )

    user_id = int(_decode_jwt_sub(jwt_token) or "0")
    accounts: List[Dict[str, Any]] = []
    today_task_runs = 0
    config = load_global_config()
    online_accounts = get_online_douyin_accounts(config)
    db = SessionLocal()
    try:
        db_rows: Dict[int, PublishAccount] = {}
        if user_id > 0:
            rows = (
                db.query(PublishAccount)
                .filter(PublishAccount.user_id == user_id, PublishAccount.platform == "douyin")
                .order_by(PublishAccount.last_login.desc().nullslast(), PublishAccount.created_at.desc())
                .all()
            )
            db_rows = {int(row.id): row for row in rows}
        seen_ids: set[int] = set()
        config_accounts = config.get("douyin_accounts") if isinstance(config, dict) else []
        if isinstance(config_accounts, list):
            for item in config_accounts:
                if not isinstance(item, dict):
                    continue
                account_id = _safe_int(item.get("id"))
                if account_id <= 0 or account_id in seen_ids:
                    continue
                seen_ids.add(account_id)
                db_row = db_rows.get(account_id)
                online = any(_safe_int(row.get("id")) == account_id for row in online_accounts)
                accounts.append(
                    {
                        "account_id": account_id,
                        "nickname": str((db_row.nickname if db_row else "") or f"账号 {account_id}").strip(),
                        "status": "active" if online else str(item.get("status") or (db_row.status if db_row else "offline")).strip(),
                        "online": online,
                        "installation_id": installation_id,
                        "last_login": db_row.last_login.isoformat() if db_row and db_row.last_login else "",
                    }
                )
        for account_id, db_row in db_rows.items():
            if account_id in seen_ids:
                continue
            online = any(_safe_int(row.get("id")) == account_id for row in online_accounts)
            accounts.append(
                {
                    "account_id": account_id,
                    "nickname": str(db_row.nickname or f"账号 {account_id}").strip(),
                    "status": "active" if online else str(db_row.status or "offline").strip(),
                    "online": online,
                    "installation_id": installation_id,
                    "last_login": db_row.last_login.isoformat() if db_row.last_login else "",
                }
            )
    finally:
        db.close()

    tasks_data = await douyin_get_tasks_lite()
    pool_data = await douyin_get_customer_pools()
    interaction_data = await douyin_interaction_status(lite=True, include_users=True)
    stranger_data = await douyin_stranger_message_status()
    video_comment_data = await douyin_video_comment_status()

    task_rows = tasks_data.get("tasks") if isinstance(tasks_data, dict) and isinstance(tasks_data.get("tasks"), list) else []
    all_customers = pool_data.get("all_customers") if isinstance(pool_data, dict) and isinstance(pool_data.get("all_customers"), list) else []
    precise_customers = pool_data.get("precise_customers") if isinstance(pool_data, dict) and isinstance(pool_data.get("precise_customers"), list) else []
    interaction_users = interaction_data.get("users") if isinstance(interaction_data, dict) and isinstance(interaction_data.get("users"), list) else []

    commented_videos = 0
    for task in task_rows:
        if not isinstance(task, dict):
            continue
        status = str(task.get("comment_status") or task.get("video_comment_status") or task.get("status") or "").strip().lower()
        if status in {"commented", "completed", "success"}:
            commented_videos += 1

    private_messages_sent = 0
    for row in interaction_users:
        if not isinstance(row, dict):
            continue
        status = str(row.get("interaction_status") or "").strip().lower()
        if status in {"sent", "completed", "success"}:
            private_messages_sent += 1

    today = _today_date_text()
    for row in precise_customers:
        if not isinstance(row, dict):
            continue
        created = str(row.get("created_at") or row.get("updated_at") or "").strip()
        if created.startswith(today):
            today_task_runs += 1

    runtime_comment = ""
    if isinstance(video_comment_data, dict):
        state = video_comment_data.get("state") if isinstance(video_comment_data.get("state"), dict) else {}
        runtime_comment = str(state.get("message") or state.get("last_message") or "").strip()
    runtime_interaction = ""
    if isinstance(interaction_data, dict):
        state = interaction_data.get("state") if isinstance(interaction_data.get("state"), dict) else {}
        runtime_interaction = str(state.get("message") or state.get("last_message") or interaction_data.get("msg") or "").strip()
    runtime_monitor = ""
    if isinstance(stranger_data, dict):
        state = stranger_data.get("state") if isinstance(stranger_data.get("state"), dict) else {}
        runtime_monitor = str(state.get("message") or state.get("last_message") or stranger_data.get("msg") or "").strip()

    return {
        "accounts": accounts,
        "runtime": {
            "comment_message": runtime_comment,
            "interaction_message": runtime_interaction,
            "monitor_message": runtime_monitor,
        },
        "metrics": {
            "collected_videos": _safe_int(tasks_data.get("total") if isinstance(tasks_data, dict) else 0),
            "all_customers": len(all_customers),
            "precise_customers": len(precise_customers),
            "commented_videos": commented_videos,
            "private_messages_sent": private_messages_sent,
            "monitor_tasks": 1 if runtime_monitor else 0,
            "today_new_customers": sum(
                1
                for row in precise_customers
                if isinstance(row, dict) and str(row.get("created_at") or row.get("updated_at") or "").startswith(today)
            ),
            "today_task_runs": today_task_runs,
        },
        "updated_at": datetime.utcnow().isoformat(),
    }


async def _report_douyin_dashboard_status(
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    *,
    jwt_token: str,
    installation_id: str,
) -> None:
    snapshot = await _build_douyin_dashboard_snapshot(jwt_token, installation_id)
    await cloud.post(
        f"{base}/api/douyin/dashboard-status/report",
        json={"payload": snapshot},
        headers=headers,
    )


def _chat_turn_payload_fields(item: Dict[str, Any], fallback_prefix: str) -> Dict[str, Any]:
    charged = bool(item.get("chat_turn_charged"))
    turn_id = str(item.get("chat_turn_id") or "").strip()
    if not charged:
        return {}
    if not turn_id:
        item_id = str(item.get("id") or "").strip()
        turn_id = f"{fallback_prefix}:{item_id}" if item_id else ""
    if not turn_id:
        return {}
    return {"chat_turn_charged": True, "chat_turn_id": turn_id[:128]}


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


@router.get("/api/scheduled-tasks/runs/{run_id}", summary="Proxy cloud scheduled task run detail for local online UI")
async def proxy_scheduled_task_run_detail(
    run_id: str,
    request: Request,
    _current_user: Any = Depends(get_current_user_for_local),
) -> Dict[str, Any]:
    return await _proxy_cloud_json(
        request,
        "GET",
        f"/api/scheduled-tasks/runs/{run_id}",
    )


@router.delete("/api/scheduled-tasks/runs/{run_id}", summary="Proxy delete scheduled task run for local online UI")
async def proxy_delete_scheduled_task_run(
    run_id: str,
    request: Request,
    _current_user: Any = Depends(get_current_user_for_local),
) -> Dict[str, Any]:
    return await _proxy_cloud_json(
        request,
        "DELETE",
        f"/api/scheduled-tasks/runs/{run_id}",
    )


@router.post("/api/scheduled-tasks/runs/{run_id}/publish-request", summary="Proxy scheduled task publish request")
async def proxy_request_scheduled_task_publish(
    run_id: str,
    request: Request,
    _current_user: Any = Depends(get_current_user_for_local),
) -> Dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    return await _proxy_cloud_json(
        request,
        "POST",
        f"/api/scheduled-tasks/runs/{run_id}/publish-request",
        json_body=body if isinstance(body, dict) else {},
        timeout_sec=30.0,
    )


@router.post("/api/scheduled-tasks/runs/{run_id}/resume-video", summary="Proxy scheduled task video resume request")
async def proxy_resume_scheduled_task_video(
    run_id: str,
    request: Request,
    _current_user: Any = Depends(get_current_user_for_local),
) -> Dict[str, Any]:
    return await _proxy_cloud_json(
        request,
        "POST",
        f"/api/scheduled-tasks/runs/{run_id}/resume-video",
        json_body={},
        timeout_sec=30.0,
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


def _normalize_goal_video_task_create_body(body: Dict[str, Any]) -> None:
    payload = body.get("payload")
    if not isinstance(payload, dict):
        return
    if str(payload.get("capability_id") or "").strip() != "goal.video.pipeline":
        return
    cap_payload = payload.get("payload")
    if not isinstance(cap_payload, dict):
        cap_payload = {}
        payload["payload"] = cap_payload
    try:
        source_mode, candidate_group = _goal_video_source_config_from_payload(cap_payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cap_payload["source_mode"] = source_mode
    cap_payload["candidate_group"] = candidate_group
    custom_prompt = _scheduled_custom_prompt(cap_payload)
    existing_plan = cap_payload.get("precomputed_plan")
    if custom_prompt and not (isinstance(existing_plan, dict) and existing_plan.get("video_prompt")):
        cap_payload["precomputed_plan"] = _scheduled_goal_video_direct_plan(
            custom_prompt,
            str(body.get("title") or ""),
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
    _normalize_goal_video_task_create_body(body)
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


def _local_api_url(path: str) -> str:
    port = int(getattr(settings, "port", 8000) or 8000)
    suffix = str(path or "").strip()
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    return f"http://127.0.0.1:{port}{suffix}"


def _extract_mobile_upload_attachments(content: str) -> tuple[str, List[str], List[str]]:
    raw = str(content or "")
    match = _MOBILE_UPLOAD_BLOCK_RE.search(raw)
    if not match:
        return raw.strip(), [], []
    clean = raw[: match.start()].strip()
    body = match.group("body") or ""
    asset_ids: List[str] = []
    urls: List[str] = []
    for line in body.splitlines():
        aid_match = _MOBILE_UPLOAD_ASSET_RE.search(line or "")
        if aid_match:
            aid = (aid_match.group("asset_id") or "").strip()
            if aid and aid not in asset_ids:
                asset_ids.append(aid)
        url_match = _MOBILE_UPLOAD_URL_RE.search(line or "")
        if url_match:
            url = (url_match.group("url") or "").strip().rstrip("，。；;)")
            if url and url not in urls:
                urls.append(url)
    return clean, asset_ids[:8], urls[:8]


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


def _pick_creative_candidate_asset(
    group_name: str,
    jwt_token: str,
    run_id: str = "",
) -> Dict[str, str]:
    name = str(group_name or "").strip()
    if not name:
        raise RuntimeError("请先选择创意成片备选素材组")
    uid = int(_decode_jwt_sub(jwt_token) or "0")
    if uid <= 0:
        raise RuntimeError("未识别到当前用户，无法读取备选素材组")
    db = SessionLocal()
    try:
        rows = db.query(Asset).filter(Asset.user_id == uid, Asset.media_type == "image").all()
        candidates = [
            row
            for row in rows
            if name in _asset_creative_candidate_groups(getattr(row, "meta", None))
        ]
        if not candidates:
            raise RuntimeError(f"备选组“{name}”里没有图片素材")
        req = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
        usable: List[tuple[Asset, str]] = []
        for candidate in candidates:
            url = get_asset_public_url(candidate.asset_id, uid, req, db) or ""
            if url:
                usable.append((candidate, url))
        if not usable:
            raise RuntimeError(f"备选组“{name}”里没有可用于视频生成的公网图片素材，请重新上传或保存 URL 后再设为备选")
        usable.sort(
            key=lambda item: (
                _creative_candidate_use_count(getattr(item[0], "meta", None), name)
                + _creative_candidate_reservation_count(getattr(item[0], "meta", None), name),
                _creative_candidate_last_used_at(getattr(item[0], "meta", None), name)
                or _creative_candidate_last_reserved_at(getattr(item[0], "meta", None), name),
                item[0].created_at.isoformat() if getattr(item[0], "created_at", None) else "",
                item[0].asset_id or "",
            )
        )
        row, url = usable[0]
        if not url:
            raise RuntimeError(f"备选组“{name}”选中的图片没有可用链接")
        reservation_id = str(run_id or "").strip() or uuid.uuid4().hex
        meta = dict(row.meta or {})
        reservations = meta.get(_CREATIVE_CANDIDATE_RESERVATION_META_KEY)
        if not isinstance(reservations, dict):
            reservations = {}
        current_reservations = reservations.get(name)
        if not isinstance(current_reservations, dict):
            current_reservations = {}
        current_reservations[reservation_id] = {
            "run_id": str(run_id or "").strip(),
            "reserved_at": datetime.now(timezone.utc).isoformat(),
        }
        reservations[name] = current_reservations
        meta[_CREATIVE_CANDIDATE_RESERVATION_META_KEY] = reservations
        row.meta = meta
        db.add(row)
        db.commit()
        return {
            "asset_id": row.asset_id,
            "url": url,
            "group_name": name,
            "filename": row.filename,
            "usage_count": str(_creative_candidate_use_count(getattr(row, "meta", None), name)),
            "reservation_id": reservation_id,
        }
    finally:
        db.close()


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
    clean_content, attachment_asset_ids, attachment_urls = _extract_mobile_upload_attachments(content)
    if not message_id or not (clean_content or attachment_urls):
        return

    await _post_cloud_event(cloud, base, headers, message_id, "thinking", {"text": "本地直连链路正在处理"})
    payload = {
        "message": clean_content or "请根据上传图片继续处理。",
        "history": [],
        "session_id": f"h5-{message_id}",
        "context_id": f"h5-{message_id}",
    }
    if attachment_urls:
        payload["attachment_image_urls"] = attachment_urls
        logger.info(
            "[H5-CHAT] mobile upload attachments injected message_id=%s asset_ids=%s urls=%d",
            message_id,
            attachment_asset_ids,
            len(attachment_urls),
        )
    payload.update(_chat_turn_payload_fields(item, "h5"))
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
            async with local.stream("POST", _local_chat_url(), json=payload, headers=_local_chat_headers(headers)) as resp:
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
    clean_content, attachment_asset_ids, attachment_urls = _extract_mobile_upload_attachments(content)
    if not message_id or not (clean_content or attachment_urls):
        return
    await _post_cloud_event(cloud, base, headers, message_id, "thinking", {"text": "已交给本机 OpenClaw"})
    user_content = clean_content or "请根据上传图片继续处理。"
    if attachment_urls:
        upload_lines = "\n".join(
            f"- asset_id: {attachment_asset_ids[idx] if idx < len(attachment_asset_ids) else ''}  media_type: image  URL: {url}"
            for idx, url in enumerate(attachment_urls)
        )
        user_content += f"\n\n{_MOBILE_UPLOAD_TITLE}\n{upload_lines}"
    messages = [
        {"role": "system", "content": "你是用户的手机会话助手。根据用户消息自然完成任务，使用中文回复。"},
        {"role": "user", "content": user_content},
    ]
    try:
        reply = await try_openclaw(
            messages,
            openclaw_fallback_model(),
            jwt_token,
            installation_id=installation_id,
            video_model_lock=(getattr(settings, "lobster_default_video_generate_model", None) or "xai/grok-imagine-video/text-to-video"),
            video_model_lock_source="default",
            chat_turn_id=str(item.get("chat_turn_id") or f"h5:{message_id}")[:128],
            chat_turn_precharged=bool(item.get("chat_turn_charged")),
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
                video_model_lock=(getattr(settings, "lobster_default_video_generate_model", None) or "xai/grok-imagine-video/text-to-video"),
                video_model_lock_source="default",
                chat_turn_id=str(item.get("chat_turn_id") or f"scheduled:{run_id}")[:128],
                chat_turn_precharged=bool(item.get("chat_turn_charged")),
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
        payload.update(_chat_turn_payload_fields(item, "scheduled"))
        timeout = httpx.Timeout(360.0, connect=10.0, read=360.0, write=30.0, pool=10.0)
        final_reply = ""
        final_error = ""
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as local:
            async with local.stream("POST", _local_chat_url(), json=payload, headers=_local_chat_headers(headers)) as resp:
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
        return f"根据我的记忆和任务名称“{title}”，生成一个 6 秒抖音 9:16 中文宣传视频。"
    return "根据我的记忆，自动选择最适合推广的产品或服务，生成一个 6 秒抖音 9:16 中文宣传视频。"


def _fallback_image_goal(task_title: str) -> str:
    title = (task_title or "").strip()
    if title and title not in {"能力定时任务", "文案+创意图片", "创意图片"}:
        return f"根据我的记忆和任务名称“{title}”，生成一张适合朋友圈或短视频封面的中文宣传创意图片。"
    return "根据我的记忆，自动选择最适合推广的产品或服务，生成一张中文宣传创意图片。"


def _fallback_create_video_goal(task_title: str) -> str:
    title = (task_title or "").strip()
    if title and title not in {"能力定时任务", "gtp创意成片", "GPT创意成片", "创意成片"}:
        return f"根据我的记忆和任务名称“{title}”，生成一条商业广告质感的创意成片视频。"
    return "根据我的记忆，自动选择最适合推广的产品或服务，生成一条商业广告质感的创意成片视频。"


def _fallback_ppt_goal(task_title: str) -> str:
    title = (task_title or "").strip()
    if title and title not in {"能力定时任务", "PPT", "生成PPT", "智能PPT"}:
        return f"根据我的记忆和任务名称“{title}”，生成一份结构清晰的商务演示PPT。"
    return "根据我的记忆，自动选择最适合汇报的产品、服务或业务主题，生成一份结构清晰的商务演示PPT。"


def _fallback_hifly_script(task_title: str) -> str:
    title = (task_title or "").strip()
    subject = title[:12] if title and title not in {"能力定时任务", "飞影数字人", "飞鹰数字人", "必火数字人"} else "这款产品"
    return f"大家好，今天带你了解{subject}，一起看看核心亮点。"


def _hifly_script_text(text: Any) -> str:
    s = re.sub(r"\s+", "", str(text or "").strip())
    return s if len(s) <= 50 else ""


def _scheduled_custom_prompt(cap_payload: Dict[str, Any]) -> str:
    for key in ("prompt", "creative_prompt", "goal", "description"):
        value = str((cap_payload or {}).get(key) or "").strip()
        if value:
            return value[:1000]
    return ""


def _generated_from_scheduled_prompt(capability_id: str, task_title: str, prompt: str) -> Dict[str, Any]:
    title = (task_title or "").strip()
    if not title or title in {"能力定时任务", "目标成片", "创意成片", "文案+创意图片", "创意图片", "智能PPT", "PPT"}:
        if capability_id == "goal.image.pipeline":
            title = "创意图片"
        elif capability_id == "create.video.pipeline":
            title = "gtp创意成片"
        elif capability_id == "create.ppt.pipeline":
            title = "智能PPT"
        else:
            title = "创意成片"
    return {
        "title": title[:120],
        "goal": prompt[:1000],
        "caption_hint": "",
        "creative_angle": "自定义提示词",
        "caption_style": "根据用户提示词生成发布文案",
        "selling_points": [],
        "memory_context_used": False,
        "custom_prompt_used": True,
    }


def _scheduled_goal_video_direct_plan(prompt: str, task_title: str) -> Dict[str, Any]:
    raw = str(prompt or "").strip()
    if not raw:
        return {}
    title = (task_title or "").strip()
    if not title or title in {"能力定时任务", "目标成片", "创意成片"}:
        title = "创意成片"
    video_prompt = _with_video_no_text_constraint(raw, 2500)
    return {
        "title": title[:120],
        "copy": raw[:2000],
        "selling_points": [],
        "image_prompt": video_prompt,
        "video_prompt": video_prompt,
        "user_prompt": raw[:2500],
        "direct_user_prompt": True,
    }


def _scheduled_goal_video_precomputed_plan(
    cap_payload: Dict[str, Any],
    generated: Dict[str, Any],
    task_title: str,
) -> Dict[str, Any]:
    existing = cap_payload.get("precomputed_plan") if isinstance(cap_payload, dict) else {}
    if isinstance(existing, dict) and existing.get("video_prompt"):
        return existing
    if not generated.get("custom_prompt_used"):
        return {}
    prompt = str(generated.get("goal") or cap_payload.get("prompt") or cap_payload.get("goal") or "").strip()
    return _scheduled_goal_video_direct_plan(prompt, task_title)


def _normalize_goal_video_source_mode(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"ai_image", "ai", "generated_image", "image_generate", "generate_image"}:
        return _SCHEDULED_VIDEO_SOURCE_AI_IMAGE
    if raw in {"reference_image", "reference", "resume_image", "resume_from_image", "existing_image"}:
        return _SCHEDULED_VIDEO_SOURCE_REFERENCE_IMAGE
    return _SCHEDULED_VIDEO_SOURCE_ASSET_RANDOM


def _goal_video_source_config_from_payload(payload: Dict[str, Any]) -> tuple[str, str]:
    payload = payload if isinstance(payload, dict) else {}
    raw_source_mode = (
        payload.get("source_mode")
        or payload.get("video_source_mode")
        or payload.get("image_source")
        or payload.get("first_frame_source")
    )
    source_mode = _normalize_goal_video_source_mode(raw_source_mode)
    if source_mode in {_SCHEDULED_VIDEO_SOURCE_AI_IMAGE, _SCHEDULED_VIDEO_SOURCE_REFERENCE_IMAGE}:
        return source_mode, ""
    candidate_group = str(payload.get("candidate_group") or payload.get("candidate_group_name") or "").strip()
    if not candidate_group:
        raise ValueError("请选择创意成片备选素材组")
    return source_mode, candidate_group


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
    if capability_id == "hifly.video.create_by_tts":
        ability = "必火数字人"
    elif capability_id == "goal.image.pipeline":
        ability = "文案+创意图片"
    elif capability_id == "create.video.pipeline":
        ability = "gtp创意成片"
    elif capability_id == "create.ppt.pipeline":
        ability = "智能PPT"
    else:
        ability = "创意成片"
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
    elif capability_id == "goal.image.pipeline":
        system = (
            "你是定时任务内容编排器。只输出 JSON 对象，不要 Markdown。\n"
            "根据用户记忆和可用素材，为文案+创意图片任务生成目标。"
            "字段：title(string), goal(string), caption_hint(string), creative_angle(string), selling_points(array)。"
            "goal 要能直接传给创意图片能力，明确要生成一张中文宣传创意图片，并写出本次图片的切入角度、画面方向和核心短文案。"
            "每次都要换表达，不要复用固定开头、固定句式或通用宣传套话；不要要求用户补充信息，不要编造素材 ID。"
        )
    elif capability_id == "create.video.pipeline":
        system = (
            "你是定时任务内容编排器。只输出 JSON 对象，不要 Markdown。\n"
            "根据用户记忆和可用素材，为 gtp创意成片生成核心视频创作 brief。"
            "字段：title(string), goal(string), caption_hint(string), creative_angle(string), selling_points(array)。"
            "goal 要能直接传给 create-video 流水线，写清楚视频主题、核心卖点、目标受众、画面风格和叙事方向。"
            "不要要求用户补充信息，不要编造素材 ID；避免要求画面出现字幕、文字、字母、数字、logo、水印。"
        )
    elif capability_id == "create.ppt.pipeline":
        system = (
            "你是定时任务内容编排器。只输出 JSON 对象，不要 Markdown。\n"
            "根据用户记忆和可用素材，为智能PPT生成核心汇报 brief。"
            "字段：title(string), goal(string), caption_hint(string), creative_angle(string), selling_points(array)。"
            "goal 要能直接传给 PPT 生成流水线，写清楚汇报主题、目标受众、核心结构、关键观点和希望呈现的商务风格。"
            "不要要求用户补充信息，不要编造素材 ID；没有真实数据时不要硬造数字。"
        )
    else:
        system = (
            "你是定时任务内容编排器。只输出 JSON 对象，不要 Markdown。\n"
            "根据用户记忆和可用素材，为创意成片流水线生成目标。"
            "字段：title(string), goal(string), caption_hint(string), creative_angle(string), selling_points(array)。"
            "先从记忆里抽取真实卖点，再围绕指定创意角度生成本次视频目标。"
            "goal 要能直接传给创意成片能力，明确 6 秒、抖音、9:16、中文宣传视频，并写出本次成片的切入角度、画面方向和核心短文案。"
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
            temperature=0.75 if capability_id in {"goal.video.pipeline", "goal.image.pipeline", "create.video.pipeline", "create.ppt.pipeline"} else 0.35,
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
        goal = _fallback_image_goal(task_title) if capability_id == "goal.image.pipeline" else _fallback_create_video_goal(task_title) if capability_id == "create.video.pipeline" else _fallback_ppt_goal(task_title) if capability_id == "create.ppt.pipeline" else _fallback_goal(task_title)
    return {
        "title": title or ("创意图片" if capability_id == "goal.image.pipeline" else "gtp创意成片" if capability_id == "create.video.pipeline" else "创意成片"),
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


def _scheduled_publish_config(cap_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = cap_payload if isinstance(cap_payload, dict) else {}
    platform = str(payload.get("publish_platform") or payload.get("platform") or "").strip()
    account_id = payload.get("publish_account_id") or payload.get("account_id")
    try:
        account_id_int = int(account_id) if account_id not in (None, "") else None
    except (TypeError, ValueError):
        account_id_int = None
    account_nickname = str(payload.get("publish_account_nickname") or payload.get("account_nickname") or "").strip()
    auto_publish = str(payload.get("publish_auto") or payload.get("auto_publish") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "是",
    } or payload.get("publish_auto") is True or payload.get("auto_publish") is True
    if not (platform or account_id_int or account_nickname or auto_publish):
        return {}
    return {
        "platform": platform,
        "platform_name": str(payload.get("publish_platform_name") or "").strip(),
        "account_id": account_id_int,
        "account_nickname": account_nickname,
        "auto_publish": auto_publish,
    }


def _scheduled_publish_asset_id(result: Any, refs: Dict[str, List[str]]) -> str:
    keys = ("final_asset_id", "image_asset_id", "asset_id", "video_asset_id")
    stack: List[Any] = [result]
    seen: set[int] = set()
    while stack:
        cur = stack.pop()
        oid = id(cur)
        if oid in seen:
            continue
        seen.add(oid)
        if isinstance(cur, dict):
            for key in keys:
                value = str(cur.get(key) or "").strip()
                if value:
                    return value[:128]
            for item in cur.get("saved_assets") or []:
                if isinstance(item, dict):
                    aid = str(item.get("asset_id") or item.get("id") or "").strip()
                    media_type = str(item.get("media_type") or item.get("type") or "").strip().lower()
                    if aid and (not media_type or media_type in {"image", "video"}):
                        return aid[:128]
            for value in cur.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(cur, list):
            stack.extend(v for v in cur if isinstance(v, (dict, list)))
    for aid in (refs or {}).get("asset_ids") or []:
        s = str(aid or "").strip()
        if s:
            return s[:128]
    return ""


def _clean_publish_tags(value: Any) -> str:
    raw: List[str] = []
    if isinstance(value, list):
        raw = [str(x or "").strip() for x in value]
    else:
        raw = re.split(r"[,，\s#、]+", str(value or ""))
    out: List[str] = []
    seen: set[str] = set()
    for item in raw:
        tag = re.sub(r"^#+", "", item.strip())
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag[:20])
        if len(out) >= 8:
            break
    return " ".join(f"#{tag}" for tag in out)


def _platform_publish_rules(platform: str) -> str:
    p = (platform or "").strip().lower()
    if p == "xiaohongshu":
        return "小红书：标题 12-20 字，有种草感；正文 80-180 字，分段自然，结尾带 3-6 个话题标签。"
    if p == "toutiao":
        return "今日头条：标题 18-30 字，信息明确；正文 120-300 字，适合图文资讯口吻，少用夸张符号。"
    if p == "kuaishou":
        return "快手：标题短直接；正文 40-100 字，生活化、接地气，带 2-4 个标签。"
    if p == "bilibili":
        return "B站：标题 16-32 字；简介说明亮点和看点，带 2-5 个标签。"
    return "抖音：标题 10-24 字，正文 40-90 字，开头有吸引力，带 2-5 个话题标签。"


async def _generate_scheduled_publish_copy(
    *,
    base: str,
    headers: Dict[str, str],
    capability_id: str,
    generated: Dict[str, Any],
    result: Any,
    refs: Dict[str, List[str]],
    platform: str,
    task_title: str,
    caption: str,
) -> Dict[str, str]:
    fallback_title = (str(generated.get("title") or task_title or "AI 创意内容").strip() or "AI 创意内容")[:30]
    fallback_desc = caption or _fallback_scheduled_caption(capability_id, generated)
    fallback_tags = _clean_publish_tags(generated.get("tags") or generated.get("keywords") or "")
    system = (
        "你是中文社交平台运营。只输出 JSON 对象，字段必须是 title、description、tags。"
        "不要 Markdown，不要解释。"
        + _platform_publish_rules(platform)
    )
    try:
        text = await _call_scheduled_llm(
            base=base,
            headers=headers,
            system=system,
            user_payload={
                "platform": platform,
                "task_title": task_title,
                "generated_content": generated,
                "caption": caption,
                "skill_result_summary": _compact_result_text(result)[:1200],
                "result_refs": refs,
                "requirements": "标题、正文、标签要适合所选平台；不要编造不存在的优惠、价格或地址。",
            },
            temperature=0.55,
        )
        data = _extract_json_object_text(text)
        title = " ".join(str(data.get("title") or fallback_title).split())[:60]
        desc = str(data.get("description") or data.get("desc") or fallback_desc).strip()[:1200]
        tags = _clean_publish_tags(data.get("tags") or fallback_tags)
        return {"title": title or fallback_title, "description": desc or fallback_desc, "tags": tags}
    except Exception as exc:
        logger.warning("[SCHEDULED-TASK] publish copy failed platform=%s: %s", platform, exc)
        return {"title": fallback_title, "description": fallback_desc, "tags": fallback_tags}


async def _submit_local_publish_draft(
    *,
    draft: Dict[str, Any],
    headers: Dict[str, str],
) -> Dict[str, Any]:
    asset_id = str(draft.get("asset_id") or "").strip()
    if not asset_id:
        raise RuntimeError("发布草稿缺少素材 asset_id")
    body = {
        "asset_id": asset_id,
        "account_id": draft.get("account_id"),
        "account_nickname": str(draft.get("account_nickname") or "").strip() or None,
        "title": str(draft.get("title") or "").strip(),
        "description": str(draft.get("description") or "").strip(),
        "tags": str(draft.get("tags") or "").strip(),
        "ai_publish_copy": False,
        "options": draft.get("options") if isinstance(draft.get("options"), dict) else {},
    }
    timeout = httpx.Timeout(2400.0, connect=10.0, read=2400.0, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resp = await client.post(
            f"http://127.0.0.1:{int(getattr(settings, 'port', 8000) or 8000)}/api/publish",
            json=body,
            headers=headers,
        )
    try:
        data = resp.json() if resp.content else {}
    except ValueError:
        data = {"text": resp.text[:500]}
    if resp.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else ""
        raise RuntimeError(str(detail or resp.text or f"publish HTTP {resp.status_code}")[:500])
    if isinstance(data, dict) and data.get("code") not in (None, 0, 200):
        raise RuntimeError(str(data.get("msg") or data.get("message") or data)[:500])
    return data if isinstance(data, dict) else {"result": data}


def _scheduled_refs_asset_urls_only(
    refs: Dict[str, List[str]],
    jwt_token: str,
) -> Dict[str, List[str]]:
    return _scheduled_refs_with_asset_urls({"asset_ids": (refs or {}).get("asset_ids") or [], "urls": []}, jwt_token)


def _scheduled_goal_video_result_refs(result: Any, jwt_token: str) -> Dict[str, List[str]]:
    """创意成片只返回最终视频素材，避免把备选图/上游临时链接一起塞给 H5。"""
    if not isinstance(result, dict):
        return _scheduled_refs_with_asset_urls(_collect_scheduled_result_refs(result), jwt_token)
    video_asset_id = str(result.get("video_asset_id") or result.get("final_asset_id") or "").strip()
    if video_asset_id:
        return _scheduled_refs_asset_urls_only({"asset_ids": [video_asset_id]}, jwt_token)

    video_urls: List[str] = []
    media_urls = result.get("media_urls")
    if isinstance(media_urls, dict):
        raw_urls = media_urls.get("video")
        if isinstance(raw_urls, list):
            video_urls = [str(u or "").strip() for u in raw_urls if str(u or "").strip()]
        elif isinstance(raw_urls, str) and raw_urls.strip():
            video_urls = [raw_urls.strip()]
    if not video_urls:
        raw_refs = _collect_scheduled_result_refs(result.get("video") if isinstance(result.get("video"), dict) else result)
        video_urls = [
            u for u in (raw_refs.get("urls") or [])
            if str(u or "").lower().split("?", 1)[0].split("#", 1)[0].endswith((".mp4", ".webm", ".mov", ".m4v", ".avi"))
        ]
    return {"asset_ids": [], "urls": video_urls[:1]}


def _scheduled_create_video_result_refs(result: Any, jwt_token: str) -> Dict[str, List[str]]:
    """gtp创意成片也只返回最终视频素材，避免把中间首帧图混进 H5 结果。"""
    return _scheduled_goal_video_result_refs(result, jwt_token)


def _scheduled_ppt_result_refs(result: Any, jwt_token: str) -> Dict[str, List[str]]:
    if not isinstance(result, dict):
        return _scheduled_refs_with_asset_urls(_collect_scheduled_result_refs(result), jwt_token)
    aid = str(result.get("ppt_asset_id") or result.get("asset_id") or "").strip()
    if aid:
        return _scheduled_refs_asset_urls_only({"asset_ids": [aid]}, jwt_token)
    return _scheduled_refs_with_asset_urls(_collect_scheduled_result_refs(result), jwt_token)


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


_SCHEDULED_DOUYIN_SKIP_MARKERS = (
    "没有可执行的采集任务",
    "已完成任务会自动跳过",
)


def _scheduled_is_douyin_skip_result(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    code = result.get("code")
    if code not in (400, "400"):
        return False
    msg = " ".join(
        str(result.get(key) or "").strip()
        for key in ("msg", "message", "detail", "error")
    )
    return any(marker in msg for marker in _SCHEDULED_DOUYIN_SKIP_MARKERS)


def _scheduled_is_douyin_skip_error_text(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    return any(marker in raw for marker in _SCHEDULED_DOUYIN_SKIP_MARKERS)


def _scheduled_douyin_selected_task_ids(payload: Optional[Dict[str, Any]]) -> List[int]:
    source = payload if isinstance(payload, dict) else {}
    selected_ids: List[int] = []
    seen: set[int] = set()
    for task_id in source.get("selected_task_ids") or []:
        value = _safe_int(task_id)
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        selected_ids.append(value)
    return selected_ids


def _scheduled_douyin_task_snapshot(selected_ids: List[int]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    normalized_ids = [task_id for task_id in selected_ids if _safe_int(task_id) > 0]
    if not normalized_ids:
        return payload
    try:
        _install_douyin_origin_import_path()
        from douyin_api import ensure_douyin_task_shape  # type: ignore
        from douyin_api import douyin_tasks as raw_douyin_tasks  # type: ignore

        selected_id_set = {_safe_int(task_id) for task_id in normalized_ids if _safe_int(task_id) > 0}
        matched_tasks: List[Dict[str, Any]] = []
        for task in raw_douyin_tasks if isinstance(raw_douyin_tasks, list) else []:
            if not isinstance(task, dict):
                continue
            task_id = _safe_int(task.get("id"))
            if task_id <= 0 or task_id not in selected_id_set:
                continue
            matched_tasks.append(ensure_douyin_task_shape(dict(task)))
        if not matched_tasks:
            return payload
        selected_video = matched_tasks[0]
        precise_customers: List[Dict[str, Any]] = []
        for task in matched_tasks:
            for row in task.get("high_intent_users", []) or []:
                if isinstance(row, dict):
                    precise_customers.append(dict(row))
        payload.update(
            {
                "selected_task_ids": sorted(selected_id_set),
                "selected_videos_total": len(matched_tasks),
                "selected_video": {
                    "task_id": _safe_int(selected_video.get("id")),
                    "title": str(selected_video.get("title") or "").strip(),
                    "url": str(selected_video.get("url") or "").strip(),
                    "author": str(selected_video.get("author") or "").strip(),
                    "cover_image": str(selected_video.get("cover_image") or "").strip(),
                    "comments_collected": max(
                        _safe_int(selected_video.get("comment_count")),
                        len(selected_video.get("all_comments", []) or []),
                    ),
                    "high_intent_users": precise_customers,
                    "precise_customers": precise_customers,
                },
                "precise_customers": precise_customers,
                "high_intent_users": precise_customers,
                "total_customers": sum(
                    max(_safe_int(task.get("comment_count")), len(task.get("all_comments", []) or []))
                    for task in matched_tasks
                ),
                "total_high_intent": len(precise_customers),
            }
        )
    except Exception as exc:
        logger.warning("[SCHEDULED-TASK] build douyin task snapshot failed: %s", exc)
    return payload


def _scheduled_douyin_regions(params: Optional[Dict[str, Any]]) -> List[str]:
    source = params if isinstance(params, dict) else {}
    raw_values: List[Any] = []
    for key in ("regions", "region_list", "area_list", "region_values"):
        value = source.get(key)
        if isinstance(value, list):
            raw_values.extend(value)
        elif isinstance(value, str):
            raw_values.extend([part.strip() for part in re.split(r"[,\s，]+", value) if part.strip()])
    normalized: List[str] = []
    seen: set[str] = set()
    for item in raw_values:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized or ["全国"]


def _scheduled_douyin_skip_payload(
    capability_id: str,
    result: Any,
    cap_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "capability_id": capability_id,
        "skipped": True,
        "skip_reason": "no_executable_collect_task",
    }
    if isinstance(result, dict):
        payload["mcp_result"] = result
    cap_payload = cap_payload if isinstance(cap_payload, dict) else {}
    selected_ids = _scheduled_douyin_selected_task_ids(cap_payload)
    if selected_ids:
        payload["action"] = str(cap_payload.get("action") or "search_collect").strip() or "search_collect"
        payload.update(_scheduled_douyin_task_snapshot(selected_ids))
        payload["skipped_completed"] = int(payload.get("selected_videos_total") or 0)
    return payload


def _scheduled_douyin_result_payload(
    action: str,
    result: Any,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    source = params if isinstance(params, dict) else {}
    payload: Dict[str, Any] = {
        "task_kind": "douyin_leads",
        "action": str(action or "").strip() or "search_collect",
    }
    if isinstance(result, dict):
        payload["mcp_result"] = result
    selected_ids = _scheduled_douyin_selected_task_ids(source)
    if isinstance(result, dict) and not selected_ids:
        selected_ids = _scheduled_douyin_selected_task_ids(result)
    if selected_ids:
        payload.update(_scheduled_douyin_task_snapshot(selected_ids))
    if isinstance(result, dict):
        for key in ("search_total", "session_id", "keyword", "search_mode"):
            value = result.get(key)
            if value not in (None, "", []):
                payload[key] = value
        regions = result.get("regions")
        if isinstance(regions, list) and regions:
            payload["regions"] = [str(item or "").strip() for item in regions if str(item or "").strip()]
    if "regions" not in payload:
        payload["regions"] = _scheduled_douyin_regions(source)
    return payload


async def _wait_for_douyin_collect_completion(
    selected_task_ids: List[int],
    *,
    timeout_seconds: float = 900.0,
    poll_interval_seconds: float = 3.0,
) -> Dict[str, Any]:
    selected_ids = [task_id for task_id in selected_task_ids if _safe_int(task_id) > 0]
    if not selected_ids:
        return {"status": "empty", "tasks": [], "selected_video": None}

    deadline = asyncio.get_running_loop().time() + max(timeout_seconds, poll_interval_seconds)
    last_snapshot: List[Dict[str, Any]] = []
    while True:
        _install_douyin_origin_import_path()
        from douyin_api import ensure_douyin_task_shape  # type: ignore
        from douyin_api import douyin_tasks as raw_douyin_tasks  # type: ignore

        task_map: Dict[int, Dict[str, Any]] = {}
        for task in raw_douyin_tasks if isinstance(raw_douyin_tasks, list) else []:
            if not isinstance(task, dict):
                continue
            task_id = _safe_int(task.get("id"))
            if task_id <= 0 or task_id not in selected_ids:
                continue
            task_map[task_id] = ensure_douyin_task_shape(dict(task))
        snapshot = [task_map[task_id] for task_id in selected_ids if task_id in task_map]
        if snapshot:
            last_snapshot = snapshot
            statuses = [str(task.get("status") or "").strip().lower() for task in snapshot]
            if statuses and all(status in {"completed", "failed"} for status in statuses):
                return {
                    "status": "done",
                    "tasks": snapshot,
                    "selected_video": snapshot[0] if snapshot else None,
                }
        if asyncio.get_running_loop().time() >= deadline:
            return {
                "status": "timeout",
                "tasks": last_snapshot,
                "selected_video": last_snapshot[0] if last_snapshot else None,
            }
        await asyncio.sleep(max(1.0, poll_interval_seconds))


def _scheduled_douyin_collect_result_payload(
    action: str,
    start_result: Dict[str, Any],
    final_state: Dict[str, Any],
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _scheduled_douyin_result_payload(action, start_result, params)
    tasks = final_state.get("tasks") if isinstance(final_state, dict) else []
    selected_video = final_state.get("selected_video") if isinstance(final_state, dict) else None
    normalized_tasks = [task for task in tasks if isinstance(task, dict)]
    if normalized_tasks:
        precise_customers: List[Dict[str, Any]] = []
        total_customers = 0
        total_high_intent = 0
        for task in normalized_tasks:
            comment_count = max(_safe_int(task.get("comment_count")), len(task.get("all_comments", []) or []))
            total_customers += comment_count
            users = [dict(row) for row in (task.get("high_intent_users", []) or []) if isinstance(row, dict)]
            total_high_intent += len(users)
            precise_customers.extend(users)
        payload.update(
            {
                "selected_videos_total": len(normalized_tasks),
                "total_customers": total_customers,
                "total_high_intent": total_high_intent,
                "precise_customers": precise_customers,
                "high_intent_users": precise_customers,
            }
        )
    if isinstance(selected_video, dict):
        users = [dict(row) for row in (selected_video.get("high_intent_users", []) or []) if isinstance(row, dict)]
        payload["selected_video"] = {
            "task_id": _safe_int(selected_video.get("id")),
            "title": str(selected_video.get("title") or "").strip(),
            "url": str(selected_video.get("url") or "").strip(),
            "author": str(selected_video.get("author") or "").strip(),
            "cover_image": str(selected_video.get("cover_image") or "").strip(),
            "comments_collected": max(
                _safe_int(selected_video.get("comment_count")),
                len(selected_video.get("all_comments", []) or []),
            ),
            "high_intent_users": users,
            "precise_customers": users,
        }
    payload["final_state"] = {
        "status": str(final_state.get("status") or "").strip(),
        "tasks": normalized_tasks,
    }
    return payload


async def _run_scheduled_douyin_search_collect_action(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    source = params if isinstance(params, dict) else {}
    keyword = str(
        source.get("keyword")
        or source.get("query")
        or source.get("search_keyword")
        or source.get("prompt")
        or ""
    ).strip()
    if not keyword:
        return {"code": 400, "msg": "缺少采集关键词"}

    max_results = max(10, min(_safe_int(source.get("max_results") or 50) or 50, 100))
    max_videos = max(1, min(_safe_int(source.get("max_videos_per_run") or source.get("max_videos") or 1) or 1, 50))
    comment_scroll_rounds = max(
        20,
        min(_safe_int(source.get("comment_scroll_rounds") or 300) or 300, 300),
    )
    comment_max_comments = max(
        20,
        min(_safe_int(source.get("comment_max_comments") or 500) or 500, 500),
    )
    search_mode = str(source.get("mode") or "script").strip().lower()
    if search_mode not in {"api", "script"}:
        search_mode = "script"
    regions = _scheduled_douyin_regions(source)

    _install_douyin_origin_import_path()
    from douyin_api import douyin_search_collect  # type: ignore
    from douyin_api import douyin_start_tasks  # type: ignore
    from douyin_api import match_douyin_tasks_for_rows  # type: ignore
    from douyin_api import normalize_douyin_search_session_result  # type: ignore
    from douyin_api import set_tasks_from_rows  # type: ignore
    from douyin_api import upsert_douyin_search_session_state  # type: ignore

    search_result = await douyin_search_collect(
        {
            "keyword": keyword,
            "max_results": max_results,
            "mode": search_mode,
        }
    )
    if _safe_int(search_result.get("code")) != 200:
        return search_result if isinstance(search_result, dict) else {"code": 500, "msg": "抖音搜索失败"}

    raw_results = search_result.get("data", []) if isinstance(search_result, dict) else []
    normalized_results = [
        normalize_douyin_search_session_result(item)
        for item in (raw_results if isinstance(raw_results, list) else [])
        if isinstance(item, dict)
    ]
    selected_item_keys: List[str] = [
        str(item.get("source_item_key", "") or "").strip()
        for item in normalized_results
        if bool(item.get("export_selected", True))
    ][:max_videos]
    if not selected_item_keys:
        selected_item_keys = [
            str(item.get("source_item_key", "") or "").strip()
            for item in normalized_results
            if str(item.get("source_item_key", "") or "").strip()
        ][:max_videos]
        if selected_item_keys:
            logger.warning(
                "[SCHEDULED-TASK] douyin search_collect fallback to first search results keyword=%s keys=%s",
                keyword,
                selected_item_keys,
            )
    if not selected_item_keys:
        return {"code": 400, "msg": f"关键词“{keyword}”本次没有可用视频。"}

    selected_item_key_set = {key for key in selected_item_keys if key}
    session = upsert_douyin_search_session_state(
        keyword=keyword,
        account_id=search_result.get("account_id", "") if isinstance(search_result, dict) else "",
        results=normalized_results,
        capture_state={
            "enabled": True,
            "status": "running",
            "region_values": regions,
            "account_id": "auto",
            "task_ids": [],
            "selected_item_keys": selected_item_keys,
            "matched_users": 0,
            "precise_users": 0,
            "last_message": f"已完成关键词“{keyword}”搜索，正在准备采集第 1 个视频的客户。",
            "updated_at": int(datetime.now().timestamp() * 1000),
        },
    )
    rows = [
        {
            **item,
            "source_session_id": str(session.get("id", "") or "").strip(),
            "source_item_key": str(item.get("source_item_key", "") or "").strip(),
            "source_keyword": keyword,
        }
        for item in (session.get("results", []) if isinstance(session.get("results", []), list) else [])
        if str(item.get("source_item_key", "") or "").strip() in selected_item_key_set
    ]
    if not rows:
        rows = [
            {
                **item,
                "source_session_id": str(session.get("id", "") or "").strip(),
                "source_item_key": str(item.get("source_item_key", "") or "").strip(),
                "source_keyword": keyword,
                "export_selected": True,
            }
            for item in normalized_results
            if str(item.get("source_item_key", "") or "").strip() in selected_item_key_set
        ]
        if rows:
            logger.warning(
                "[SCHEDULED-TASK] douyin search_collect rebuilt rows from normalized results keyword=%s keys=%s",
                keyword,
                selected_item_keys,
            )
    if not rows:
        return {"code": 400, "msg": f"关键词“{keyword}”本次没有可用视频。"}

    set_tasks_from_rows(rows)
    matched_tasks = match_douyin_tasks_for_rows(rows)
    task_ids = [int(task.get("id", 0) or 0) for task in matched_tasks if int(task.get("id", 0) or 0) > 0]
    if not task_ids:
        return {"code": 400, "msg": f"关键词“{keyword}”本次没有匹配到可执行任务。"}

    upsert_douyin_search_session_state(
        keyword=keyword,
        account_id=search_result.get("account_id", "") if isinstance(search_result, dict) else "",
        results=session.get("results", []),
        session_id=str(session.get("id", "") or "").strip(),
        capture_state={
            "enabled": True,
            "status": "running",
            "region_values": regions,
            "account_id": "auto",
            "task_ids": task_ids,
            "selected_item_keys": selected_item_keys,
            "matched_users": 0,
            "precise_users": 0,
            "last_message": "搜索完成，正在对第 1 个视频采集客户。",
            "updated_at": int(datetime.now().timestamp() * 1000),
        },
    )

    start_result = await douyin_start_tasks(
        request={
            "selected_task_ids": task_ids,
            "comment_scroll_rounds": comment_scroll_rounds,
            "comment_max_comments": comment_max_comments,
            "collection_mode": "script",
        }
    )
    result = dict(start_result) if isinstance(start_result, dict) else {"code": 500, "msg": "抖音采集启动失败"}
    result.update(
        {
            "keyword": keyword,
            "search_mode": search_mode,
            "regions": regions,
            "session_id": str(session.get("id", "") or "").strip(),
            "search_total": len(normalized_results),
            "selected_task_ids": task_ids,
            "selected_videos_total": len(task_ids),
            "selected_item_keys": selected_item_keys,
        }
    )
    if _safe_int(result.get("code")) == 200:
        actual_started = max(0, _safe_int(result.get("selected_count") or len(task_ids)))
        skipped_existing = max(0, len(task_ids) - actual_started)
        result["msg"] = (
            f"搜索完成，找到 {len(normalized_results)} 个视频；已开始采集第 1 个视频的客户。"
            + (f" 已跳过 {skipped_existing} 个已完成任务。" if skipped_existing else "")
        )
    return result


async def _run_scheduled_douyin_leads(
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    item: Dict[str, Any],
    *,
    jwt_token: str,
    installation_id: str,
) -> None:
    _ = (jwt_token, installation_id)
    run_id = str(item.get("id") or "").strip()
    payload = _scheduled_payload(item)
    action = str(payload.get("action") or "").strip().lower()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    if not run_id or not action:
        return
    await _post_task_event(cloud, base, headers, run_id, "thinking", {"text": f"正在执行抖音获客任务：{action}"})
    try:
        if action == "search_collect":
            result = await _run_scheduled_douyin_search_collect_action(params)
        else:
            raise RuntimeError(f"暂不支持的抖音获客任务类型：{action}")

        if _scheduled_is_douyin_skip_result(result):
            await _complete_task_run(
                cloud,
                base,
                headers,
                run_id,
                result_text="本次没有新的可执行采集任务，已自动跳过重复或已完成的任务。",
                result_payload=_scheduled_douyin_skip_payload("douyin_leads", result, {"action": action, **params}),
            )
            return

        code = _safe_int(result.get("code") if isinstance(result, dict) else 0)
        error_text = ""
        if isinstance(result, dict):
            error_text = str(result.get("msg") or result.get("detail") or "").strip()
        if code and code != 200:
            if _scheduled_is_douyin_skip_error_text(error_text):
                await _complete_task_run(
                    cloud,
                    base,
                    headers,
                    run_id,
                    result_text="本次没有新的可执行采集任务，已自动跳过重复或已完成的任务。",
                    result_payload=_scheduled_douyin_skip_payload("douyin_leads", result, {"action": action, **params}),
                )
                return
            raise RuntimeError(error_text or f"douyin_leads {action} failed")

        if action == "search_collect" and isinstance(result, dict):
            selected_task_ids = _scheduled_douyin_selected_task_ids(result)
            if selected_task_ids:
                await _post_task_event(
                    cloud,
                    base,
                    headers,
                    run_id,
                    "progress",
                    {
                        "text": "已启动采集任务，正在等待最终结果。",
                        "action": action,
                        "progress": 65,
                        "stats": {
                            "videos_found": _safe_int(result.get("search_total") or 0),
                            "selected_task_id": selected_task_ids[0] if selected_task_ids else 0,
                            "selected_tasks_total": len(selected_task_ids),
                        },
                    },
                )
                final_state = await _wait_for_douyin_collect_completion(selected_task_ids)
                result_payload = _scheduled_douyin_collect_result_payload(action, result, final_state, params)
                selected_video = result_payload.get("selected_video") if isinstance(result_payload.get("selected_video"), dict) else {}
                comments_collected = max(
                    _safe_int(selected_video.get("comments_collected")),
                    _safe_int(result_payload.get("total_customers")),
                )
                precise_total = max(
                    len(result_payload.get("precise_customers") or []),
                    _safe_int(result_payload.get("total_high_intent")),
                )
                search_total = _safe_int(result.get("search_total") or result_payload.get("search_total"))
                final_status = str((final_state or {}).get("status") or "").strip().lower()
                if final_status == "done":
                    result_text = (
                        f"搜索完成，找到 {search_total} 个视频；"
                        f"已采集第 1 个视频的客户 {comments_collected} 人，精准客户 {precise_total} 人。"
                    )
                elif final_status == "timeout":
                    result_text = (
                        f"搜索完成，找到 {search_total} 个视频；"
                        "采集任务仍在执行，结果会继续同步。"
                    )
                else:
                    result_text = (
                        str(result.get("msg") or "").strip()
                        or _compact_result_text(result)
                    )
                await _complete_task_run(
                    cloud,
                    base,
                    headers,
                    run_id,
                    result_text=result_text,
                    result_payload=result_payload,
                )
                return

        result_text = (
            str(result.get("msg") or "").strip()
            if isinstance(result, dict)
            else ""
        ) or _compact_result_text(result)
        await _complete_task_run(
            cloud,
            base,
            headers,
            run_id,
            result_text=result_text,
            result_payload=_scheduled_douyin_result_payload(action, result, params),
        )
    except Exception as exc:
        logger.exception("[SCHEDULED-TASK] douyin leads failed run_id=%s action=%s", run_id, action)
        error_text = str(exc).strip() or exc.__class__.__name__
        if _scheduled_is_douyin_skip_error_text(error_text):
            await _complete_task_run(
                cloud,
                base,
                headers,
                run_id,
                result_text="本次没有新的可执行采集任务，已自动跳过重复或已完成的任务。",
                result_payload=_scheduled_douyin_skip_payload("douyin_leads", {"msg": error_text}, {"action": action, **(params or {})}),
            )
            return
        await _complete_task_run(cloud, base, headers, run_id, error=error_text[:500] or "douyin leads failed")


def _goal_video_pipeline_has_video_result(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    raw_refs = _collect_scheduled_result_refs(result)
    if any(str(u or "").lower().split("?", 1)[0].split("#", 1)[0].endswith((".mp4", ".webm", ".mov", ".m4v", ".avi")) for u in raw_refs.get("urls") or []):
        return True
    stack = [result]
    seen: set[int] = set()
    while stack:
        cur = stack.pop()
        oid = id(cur)
        if oid in seen:
            continue
        seen.add(oid)
        if isinstance(cur, dict):
            for key in ("video_asset_id", "final_asset_id"):
                if str(cur.get(key) or "").strip():
                    return True
            for item in cur.get("saved_assets") or []:
                if isinstance(item, dict) and str(item.get("media_type") or "").strip().lower() == "video":
                    if str(item.get("asset_id") or item.get("id") or "").strip():
                        return True
            for value in cur.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(cur, list):
            stack.extend(v for v in cur if isinstance(v, (dict, list)))
    return False


def _create_video_pipeline_has_video_result(result: Any) -> bool:
    return _goal_video_pipeline_has_video_result(result)


def _goal_video_pipeline_pending_reason(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    statuses: List[str] = []
    stack = [result]
    seen: set[int] = set()
    while stack:
        cur = stack.pop()
        oid = id(cur)
        if oid in seen:
            continue
        seen.add(oid)
        if isinstance(cur, dict):
            status = str(cur.get("status") or cur.get("state") or cur.get("task_status") or cur.get("taskStatus") or "").strip().lower()
            if status:
                statuses.append(status)
            video = cur.get("video")
            if isinstance(video, dict):
                video_status = str(video.get("status") or "").strip().lower()
                if video_status in {"running", "processing", "pending", "queued", "waiting"}:
                    task_id = str(video.get("task_id") or "").strip()
                    return f"创意成片视频仍在生成中{('，task_id=' + task_id) if task_id else ''}"
                final = video.get("final_result")
                if isinstance(final, dict):
                    final_status = str(final.get("status") or (final.get("result") or {}).get("status") or "").strip().lower()
                    if final_status in {"running", "processing", "pending", "queued", "waiting"}:
                        task_id = str(video.get("task_id") or (final.get("result") or {}).get("task_id") or "").strip()
                        return f"创意成片视频仍在生成中{('，task_id=' + task_id) if task_id else ''}"
            for value in cur.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(cur, list):
            stack.extend(v for v in cur if isinstance(v, (dict, list)))
    if any(s in {"running", "processing", "pending", "queued", "waiting"} for s in statuses):
        return "创意成片视频仍在生成中"
    return ""


def _create_video_pipeline_pending_reason(result: Any) -> str:
    reason = _goal_video_pipeline_pending_reason(result)
    return reason.replace("创意成片", "gtp创意成片") if reason else ""


def _scheduled_caption_candidate(value: Any) -> str:
    text = " ".join(str(value or "").strip().strip('"“”`').split())
    text = re.sub(r"^(发布文案|朋友圈文案|文案)\s*[:：]\s*", "", text).strip()
    return text if 0 < len(text) <= 50 else ""


def _fallback_scheduled_caption(capability_id: str, generated: Dict[str, Any]) -> str:
    hint = _scheduled_caption_candidate(generated.get("caption_hint"))
    if hint:
        return hint
    title = str(generated.get("title") or "").strip()
    subject = title[:12] if title and title not in {"能力定时任务", "目标成片", "创意成片", "智能PPT", "PPT", "数字人口播"} else "这次内容"
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
    if capability_id == "create.ppt.pipeline":
        return f"{subject}PPT已生成，适合直接用于汇报和沟通。"
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
            temperature=0.85 if capability_id in {"goal.video.pipeline", "goal.image.pipeline", "create.video.pipeline", "create.ppt.pipeline"} else 0.55,
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
    input_refs: Optional[Dict[str, Any]] = None,
    publish_draft: Optional[Dict[str, Any]] = None,
) -> str:
    ready = _scheduled_result_ready(result)
    lines = ["生成完成。" if ready else "任务已提交，仍在生成中。", f"发布文案：{caption}"]
    if skill_prompt:
        lines.append(f"传给技能的提示词：{skill_prompt}")
    if input_refs:
        source_mode = str(input_refs.get("source_mode") or "").strip()
        image_model = str(input_refs.get("image_model") or "").strip()
        group = str(input_refs.get("candidate_group") or "").strip()
        ref_asset = str(input_refs.get("reference_asset_id") or "").strip()
        if source_mode == _SCHEDULED_VIDEO_SOURCE_AI_IMAGE:
            lines.append(f"首帧来源：AI 生成图片{('（' + image_model + '）') if image_model else ''}")
        elif source_mode == _SCHEDULED_VIDEO_SOURCE_ASSET_RANDOM:
            lines.append("首帧来源：素材库备选组轮换图片")
        elif source_mode == "create_video_pipeline":
            video_model = str(input_refs.get("video_model") or "").strip()
            planning_model = str(input_refs.get("planning_model") or "").strip()
            if image_model:
                lines.append(f"首帧模型：{image_model}")
            if video_model:
                lines.append(f"视频模型：{video_model}")
            if planning_model:
                lines.append(f"规划模型：{planning_model}")
        elif source_mode == "create_ppt_pipeline":
            planning_model = str(input_refs.get("planning_model") or "").strip()
            theme = str(input_refs.get("theme") or "").strip()
            slide_count = str(input_refs.get("slide_count") or "").strip()
            if planning_model:
                lines.append(f"PPT规划模型：{planning_model}")
            if theme:
                lines.append(f"PPT主题样式：{theme}")
            if slide_count:
                lines.append(f"PPT页数：{slide_count}")
        if group:
            lines.append(f"备选组：{group}")
        if ref_asset:
            lines.append(f"使用备选素材：{ref_asset}")
    refs = refs or _collect_scheduled_result_refs(result)
    if refs["asset_ids"]:
        lines.append("生成素材：" + "、".join(refs["asset_ids"][:6]))
    if refs["urls"]:
        lines.append("预览链接：")
        lines.extend(refs["urls"][:6])
    if publish_draft:
        status = str(publish_draft.get("status") or "ready").strip()
        platform = str(publish_draft.get("platform_name") or publish_draft.get("platform") or "").strip()
        acct = str(publish_draft.get("account_nickname") or publish_draft.get("account_id") or "").strip()
        label = {
            "ready": "待发布",
            "pending": "等待发布",
            "processing": "发布中",
            "published": "已发布",
            "failed": "发布失败",
        }.get(status, status or "待发布")
        lines.append("发布状态：" + label + (f"（{platform} · {acct}）" if platform or acct else ""))
        if publish_draft.get("error"):
            lines.append("发布错误：" + str(publish_draft.get("error"))[:200])
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
        resp = await local.post(_local_mcp_url(), json=rpc, headers=headers)
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


async def _run_goal_image_scheduled_pipeline(
    *,
    jwt_token: str,
    installation_id: str,
    generated: Dict[str, Any],
    task_title: str,
    attachment_asset_ids: List[str],
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    run_id: str,
) -> Dict[str, Any]:
    pl = GoalVideoPipelinePayload(
        action="run_pipeline",
        goal=generated.get("goal") or _fallback_image_goal(task_title),
        platform="douyin",
        duration=6,
        aspect_ratio="9:16",
        language="zh",
        memory_scope="none" if generated.get("custom_prompt_used") else "default",
        reference_asset_ids=attachment_asset_ids[:8],
    )

    def progress(stage: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        if not run_id:
            return
        asyncio.create_task(
            _post_task_event(cloud, base, headers, run_id, stage[:32], {"text": message, **(extra or {})})
        )

    return await run_goal_image_pipeline(
        pl=pl,
        token=jwt_token,
        installation_id=installation_id,
        progress=progress,
    )


async def _run_goal_video_scheduled_pipeline(
    *,
    jwt_token: str,
    installation_id: str,
    generated: Dict[str, Any],
    task_title: str,
    source_mode: str,
    candidate_group: str,
    cap_payload: Dict[str, Any],
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    run_id: str,
) -> Dict[str, Any]:
    pl = GoalVideoPipelinePayload(
        action="run_pipeline",
        goal=generated.get("goal") or _fallback_goal(task_title),
        platform="douyin",
        duration=6,
        aspect_ratio="9:16",
        language="zh",
        memory_scope="none" if generated.get("custom_prompt_used") else "default",
        precomputed_plan=cap_payload.get("precomputed_plan") if isinstance(cap_payload.get("precomputed_plan"), dict) else {},
    )

    def progress(stage: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        if not run_id:
            return
        asyncio.create_task(
            _post_task_event(cloud, base, headers, run_id, stage[:32], {"text": message, **(extra or {})})
        )

    if source_mode == _SCHEDULED_VIDEO_SOURCE_AI_IMAGE:
        pl.image_model = _normalize_image_model_id(_get_default_image_generate_model())
        if pl.image_model and not pl.image_model.startswith("openai/") and "/" not in pl.image_model:
            pl.image_model = f"openai/{pl.image_model}"
        pl.image_model = _normalize_image_model_id(pl.image_model)
        await _post_task_event(
            cloud,
            base,
            headers,
            run_id,
            "thinking",
            {"text": "将先用 AI 生成首帧图片，再用该图片生成视频"},
        )
        result = await run_goal_video_pipeline_with_total_billing(
            pl=pl,
            token=jwt_token,
            installation_id=installation_id,
            progress=progress,
            source_mode=_SCHEDULED_VIDEO_SOURCE_AI_IMAGE,
        )
        result["source_mode"] = _SCHEDULED_VIDEO_SOURCE_AI_IMAGE
        result["image_model"] = pl.image_model
        return result

    if source_mode == _SCHEDULED_VIDEO_SOURCE_REFERENCE_IMAGE:
        payload_refs = cap_payload if isinstance(cap_payload, dict) else {}
        ref_asset_ids = [str(x).strip() for x in (payload_refs.get("reference_asset_ids") or []) if str(x).strip()]
        ref_urls = [str(x).strip() for x in (payload_refs.get("reference_image_urls") or []) if str(x).strip()]
        if not ref_asset_ids:
            ref_asset_ids = [str(x).strip() for x in (generated.get("reference_asset_ids") or []) if str(x).strip()]
        if not ref_urls:
            ref_urls = [str(x).strip() for x in (generated.get("reference_image_urls") or []) if str(x).strip()]
        if not ref_asset_ids:
            ref_asset_ids = [str(x).strip() for x in (generated.get("resume_reference_asset_ids") or []) if str(x).strip()]
        if not ref_urls:
            ref_urls = [str(x).strip() for x in (generated.get("resume_reference_image_urls") or []) if str(x).strip()]
        if not ref_asset_ids and not ref_urls:
            ref_asset_ids = [str(x).strip() for x in (generated.get("attachment_asset_ids") or []) if str(x).strip()]
        if not ref_asset_ids and not ref_urls:
            raise RuntimeError("补发视频缺少可用的首帧图片")
        pl.reference_asset_ids = ref_asset_ids[:1]
        pl.reference_image_urls = ref_urls[:1]
        await _post_task_event(
            cloud,
            base,
            headers,
            run_id,
            "thinking",
            {"text": "resume video from generated image"},
        )
        result = await run_goal_video_pipeline_with_total_billing(
            pl=pl,
            token=jwt_token,
            installation_id=installation_id,
            progress=progress,
            source_mode=_SCHEDULED_VIDEO_SOURCE_REFERENCE_IMAGE,
        )
        result["source_mode"] = _SCHEDULED_VIDEO_SOURCE_REFERENCE_IMAGE
        result["reference_asset_id"] = pl.reference_asset_ids[0] if pl.reference_asset_ids else ""
        return result

    picked = _pick_creative_candidate_asset(candidate_group, jwt_token, run_id=run_id)
    await _post_task_event(
        cloud,
        base,
        headers,
        run_id,
        "thinking",
        {"text": f"已从备选组“{picked['group_name']}”轮换选择图片素材 {picked['asset_id']}"},
    )
    pl.reference_asset_ids = [picked["asset_id"]] if picked.get("asset_id") else []
    pl.reference_image_urls = [picked["url"]] if picked.get("url") else []
    try:
        result = await run_goal_video_pipeline_with_total_billing(
            pl=pl,
            token=jwt_token,
            installation_id=installation_id,
            progress=progress,
            source_mode=_SCHEDULED_VIDEO_SOURCE_ASSET_RANDOM,
        )
    except Exception:
        _release_creative_candidate_asset_reservation(
            picked.get("asset_id") or "",
            picked.get("group_name") or "",
            jwt_token,
            picked.get("reservation_id") or "",
        )
        raise
    result["source_mode"] = _SCHEDULED_VIDEO_SOURCE_ASSET_RANDOM
    result["candidate_group"] = picked["group_name"]
    result["reference_asset_id"] = picked["asset_id"]
    result["reference_asset_reservation_id"] = picked.get("reservation_id") or ""
    result["reference_asset_usage_count_before"] = picked.get("usage_count") or "0"
    return result


async def _run_create_video_scheduled_pipeline(
    *,
    jwt_token: str,
    installation_id: str,
    generated: Dict[str, Any],
    task_title: str,
    cap_payload: Dict[str, Any],
    attachment_asset_ids: List[str],
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    run_id: str,
) -> Dict[str, Any]:
    payload = cap_payload if isinstance(cap_payload, dict) else {}
    goal = str(generated.get("goal") or payload.get("prompt") or payload.get("topic") or "").strip()
    if not goal:
        goal = _fallback_create_video_goal(task_title)
    pl = CreateVideoPipelinePayload(
        action="run_pipeline",
        prompt=goal,
        topic=str(payload.get("topic") or "").strip(),
        video_type=str(payload.get("video_type") or "brand_promo").strip() or "brand_promo",
        target_audience=str(payload.get("target_audience") or "general_audience").strip() or "general_audience",
        style=str(payload.get("style") or "premium commercial, realistic, cinematic lighting").strip()
        or "premium commercial, realistic, cinematic lighting",
        duration=int(payload.get("duration") or 8),
        scene_count=int(payload.get("scene_count") or 1),
        aspect_ratio=str(payload.get("aspect_ratio") or "9:16").strip() or "9:16",
        language=str(payload.get("language") or "Chinese").strip() or "Chinese",
        planning_model=str(payload.get("planning_model") or "gpt-5.4").strip() or None,
        image_model=str(payload.get("image_model") or "openai/gpt-image-2").strip() or None,
        video_model=str(payload.get("video_model") or "fal-ai/veo3.1/image-to-video").strip() or None,
        precomputed_plan=payload.get("precomputed_plan") if isinstance(payload.get("precomputed_plan"), dict) else {},
        reference_asset_ids=[str(x).strip() for x in (payload.get("reference_asset_ids") or []) if str(x).strip()],
        reference_image_urls=[str(x).strip() for x in (payload.get("reference_image_urls") or []) if str(x).strip()],
    )

    def progress(stage: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        if not run_id:
            return
        asyncio.create_task(
            _post_task_event(cloud, base, headers, run_id, stage[:32], {"text": message, **(extra or {})})
        )

    await _post_task_event(
        cloud,
        base,
        headers,
        run_id,
        "thinking",
        {"text": "正在执行 gtp创意成片：脚本规划、首帧生成、视频生成"},
    )
    result = await run_create_video_pipeline_with_total_billing(
        pl=pl,
        token=jwt_token,
        installation_id=installation_id,
        progress=progress,
    )
    result["source_mode"] = "create_video_pipeline"
    return result


async def _run_create_ppt_scheduled_pipeline(
    *,
    jwt_token: str,
    installation_id: str,
    generated: Dict[str, Any],
    task_title: str,
    cap_payload: Dict[str, Any],
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    run_id: str,
) -> Dict[str, Any]:
    payload = cap_payload if isinstance(cap_payload, dict) else {}
    goal = str(generated.get("goal") or payload.get("prompt") or payload.get("topic") or "").strip()
    if not goal:
        goal = _fallback_ppt_goal(task_title)
    user_id = int(_decode_jwt_sub(jwt_token) or "0")
    if user_id <= 0:
        raise RuntimeError("未识别到当前用户，无法保存 PPT 素材")
    pl = CreatePptPipelinePayload(
        action="run_pipeline",
        prompt=goal,
        topic=str(payload.get("topic") or "").strip(),
        slide_count=int(payload.get("slide_count") or 10),
        theme=str(payload.get("theme") or "business").strip() or "business",
        language=str(payload.get("language") or "zh-CN").strip() or "zh-CN",
        audience=str(payload.get("audience") or "business").strip() or "business",
        style=str(payload.get("style") or "professional, clear, modern business presentation").strip()
        or "professional, clear, modern business presentation",
        planning_model=str(payload.get("planning_model") or "gpt-5.4").strip() or None,
    )

    def progress(stage: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        if not run_id:
            return
        asyncio.create_task(
            _post_task_event(cloud, base, headers, run_id, stage[:32], {"text": message, **(extra or {})})
        )

    await _post_task_event(
        cloud,
        base,
        headers,
        run_id,
        "thinking",
        {"text": "正在执行智能PPT：大纲规划、PPTX渲染、保存素材"},
    )
    result = await run_create_ppt_pipeline(
        pl=pl,
        token=jwt_token,
        installation_id=installation_id,
        user_id=user_id,
        progress=progress,
    )
    result["source_mode"] = "create_ppt_pipeline"
    return result


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
    original_cap_payload = dict(cap_payload or {})
    attachment_asset_ids = _scheduled_attachment_asset_ids(item)
    if not run_id or not capability_id:
        return
    try:
        task_title = str(item.get("title") or "").strip()
        if capability_id in {"goal.video.pipeline", "goal.image.pipeline", "hifly.video.create_by_tts", "create.video.pipeline", "create.ppt.pipeline"}:
            asset_context = _scheduled_asset_context_with_urls(attachment_asset_ids, jwt_token, installation_id)
            custom_prompt = _scheduled_custom_prompt(cap_payload)
            resume_from_image = bool(cap_payload.get("resume_from_image"))
            if resume_from_image and capability_id in {"goal.video.pipeline", "create.video.pipeline"}:
                generated = {
                    "goal": str(cap_payload.get("goal") or cap_payload.get("prompt") or task_title or "").strip(),
                    "custom_prompt_used": True,
                    "reference_asset_ids": cap_payload.get("reference_asset_ids") or [],
                    "reference_image_urls": cap_payload.get("reference_image_urls") or [],
                }
            elif custom_prompt and capability_id in {"goal.video.pipeline", "goal.image.pipeline", "create.video.pipeline", "create.ppt.pipeline"}:
                await _post_task_event(cloud, base, headers, run_id, "thinking", {"text": "正在使用自定义提示词生成本次内容"})
                generated = _generated_from_scheduled_prompt(capability_id, task_title, custom_prompt)
            else:
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
                source_mode, candidate_group = _goal_video_source_config_from_payload(cap_payload)
                goal = generated.get("goal") or _fallback_goal(task_title)
                precomputed_plan = _scheduled_goal_video_precomputed_plan(original_cap_payload, generated, task_title)
                cap_payload = {
                    "source_mode": source_mode,
                    "candidate_group": candidate_group,
                    "goal": goal,
                    "prompt": goal,
                    "reference_asset_ids": original_cap_payload.get("reference_asset_ids") or [],
                    "reference_image_urls": original_cap_payload.get("reference_image_urls") or [],
                    "resume_from_image": bool(original_cap_payload.get("resume_from_image")),
                }
                if precomputed_plan:
                    cap_payload["precomputed_plan"] = precomputed_plan
            elif capability_id == "create.video.pipeline":
                cap_payload = dict(original_cap_payload or {})
                cap_payload["prompt"] = generated.get("goal") or cap_payload.get("prompt") or _fallback_create_video_goal(task_title)
                cap_payload.setdefault("action", "run_pipeline")
            elif capability_id == "create.ppt.pipeline":
                cap_payload = dict(original_cap_payload or {})
                cap_payload["prompt"] = generated.get("goal") or cap_payload.get("prompt") or _fallback_ppt_goal(task_title)
                cap_payload.setdefault("action", "run_pipeline")
            elif capability_id == "goal.image.pipeline":
                publish_cfg = _scheduled_publish_config(original_cap_payload)
                cap_payload = {
                    "goal": generated.get("goal") or _fallback_image_goal(task_title),
                }
            else:
                publish_cfg = {}
                avatar = str(cap_payload.get("avatar") or "").strip()
                voice = str(cap_payload.get("voice") or "").strip()
                if not avatar:
                    raise RuntimeError("请选择数字人")
                if not voice:
                    raise RuntimeError("请选择声音")
                skill_prompt = (
                    _hifly_script_text(cap_payload.get("script"))
                    or _hifly_script_text(cap_payload.get("text"))
                    or _hifly_script_text(generated.get("script"))
                    or _fallback_hifly_script(task_title)
                )
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
            elif capability_id == "goal.image.pipeline":
                result = await _run_goal_image_scheduled_pipeline(
                    jwt_token=jwt_token,
                    installation_id=installation_id,
                    generated=generated,
                    task_title=task_title,
                    attachment_asset_ids=attachment_asset_ids,
                    cloud=cloud,
                    base=base,
                    headers=headers,
                    run_id=run_id,
                )
            elif capability_id == "goal.video.pipeline":
                result = await _run_goal_video_scheduled_pipeline(
                    jwt_token=jwt_token,
                    installation_id=installation_id,
                    generated=generated,
                    task_title=task_title,
                    source_mode=str(cap_payload.get("source_mode") or _SCHEDULED_VIDEO_SOURCE_ASSET_RANDOM),
                    candidate_group=str(cap_payload.get("candidate_group") or "").strip(),
                    cap_payload=cap_payload,
                    cloud=cloud,
                    base=base,
                    headers=headers,
                    run_id=run_id,
                )
            elif capability_id == "create.video.pipeline":
                result = await _run_create_video_scheduled_pipeline(
                    jwt_token=jwt_token,
                    installation_id=installation_id,
                    generated=generated,
                    task_title=task_title,
                    cap_payload=cap_payload,
                    attachment_asset_ids=attachment_asset_ids,
                    cloud=cloud,
                    base=base,
                    headers=headers,
                    run_id=run_id,
                )
            elif capability_id == "create.ppt.pipeline":
                result = await _run_create_ppt_scheduled_pipeline(
                    jwt_token=jwt_token,
                    installation_id=installation_id,
                    generated=generated,
                    task_title=task_title,
                    cap_payload=cap_payload,
                    cloud=cloud,
                    base=base,
                    headers=headers,
                    run_id=run_id,
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
            if capability_id == "goal.video.pipeline" and not _goal_video_pipeline_has_video_result(result):
                raise RuntimeError(_goal_video_pipeline_pending_reason(result) or "创意成片视频仍未完成，未取得视频素材或视频链接")
            if capability_id == "create.video.pipeline" and not _create_video_pipeline_has_video_result(result):
                raise RuntimeError(_create_video_pipeline_pending_reason(result) or "gtp创意成片视频仍未完成，未取得视频素材或视频链接")
            if (
                capability_id == "goal.video.pipeline"
                and str(result.get("source_mode") or "") == _SCHEDULED_VIDEO_SOURCE_ASSET_RANDOM
                and str(result.get("reference_asset_id") or "").strip()
            ):
                _mark_creative_candidate_asset_used(
                    str(result.get("reference_asset_id") or ""),
                    str(result.get("candidate_group") or cap_payload.get("candidate_group") or ""),
                    jwt_token,
                    str(result.get("reference_asset_reservation_id") or ""),
                )
            await _post_task_event(cloud, base, headers, run_id, "thinking", {"text": "正在生成发布文案"})
            caption = await _generate_scheduled_caption(
                base=base,
                headers=headers,
                capability_id=capability_id,
                generated=generated,
                result=result,
            )
            raw_refs = _collect_scheduled_result_refs(result)
            if capability_id == "hifly.video.create_by_tts":
                refs = _scheduled_hifly_result_refs(result, jwt_token)
            elif capability_id == "goal.video.pipeline":
                refs = _scheduled_goal_video_result_refs(result, jwt_token)
            elif capability_id == "create.video.pipeline":
                refs = _scheduled_create_video_result_refs(result, jwt_token)
            elif capability_id == "create.ppt.pipeline":
                refs = _scheduled_ppt_result_refs(result, jwt_token)
            else:
                refs = _scheduled_refs_with_asset_urls(raw_refs, jwt_token)
            skill_prompt = str(cap_payload.get("text") or cap_payload.get("goal") or result.get("skill_prompt") or "").strip()
            if capability_id == "goal.video.pipeline":
                plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
                skill_prompt = str(plan.get("user_prompt") or plan.get("video_prompt") or skill_prompt).strip()
            elif capability_id == "create.video.pipeline":
                skill_prompt = str((result.get("plan") or {}).get("summary") or cap_payload.get("prompt") or skill_prompt).strip()
            elif capability_id == "create.ppt.pipeline":
                skill_prompt = str(result.get("title") or cap_payload.get("prompt") or skill_prompt).strip()
            elif capability_id == "goal.image.pipeline":
                skill_prompt = str((result.get("plan") or {}).get("image_prompt") or skill_prompt).strip()
            input_refs = {}
            publish_draft: Optional[Dict[str, Any]] = None
            if capability_id == "goal.video.pipeline":
                input_refs = {
                    "source_mode": result.get("source_mode") or cap_payload.get("source_mode"),
                    "image_model": result.get("image_model"),
                    "candidate_group": result.get("candidate_group"),
                    "reference_asset_id": result.get("reference_asset_id"),
                    "reference_asset_reservation_id": result.get("reference_asset_reservation_id"),
                }
            elif capability_id == "create.video.pipeline":
                models = result.get("models") if isinstance(result.get("models"), dict) else {}
                input_refs = {
                    "source_mode": "create_video_pipeline",
                    "image_model": models.get("image"),
                    "video_model": models.get("video"),
                    "planning_model": models.get("planning"),
                }
            elif capability_id == "create.ppt.pipeline":
                models = result.get("models") if isinstance(result.get("models"), dict) else {}
                input_refs = {
                    "source_mode": "create_ppt_pipeline",
                    "planning_model": models.get("planning"),
                    "theme": cap_payload.get("theme"),
                    "slide_count": result.get("slide_count"),
                }
            elif capability_id == "goal.image.pipeline" and publish_cfg:
                asset_id = _scheduled_publish_asset_id(result, refs)
                copy = await _generate_scheduled_publish_copy(
                    base=base,
                    headers=headers,
                    capability_id=capability_id,
                    generated=generated,
                    result=result,
                    refs=refs,
                    platform=str(publish_cfg.get("platform") or ""),
                    task_title=task_title,
                    caption=caption,
                )
                publish_draft = {
                    "run_id": run_id,
                    "status": "ready",
                    "auto_publish": bool(publish_cfg.get("auto_publish")),
                    "platform": publish_cfg.get("platform"),
                    "platform_name": publish_cfg.get("platform_name") or _platform_publish_rules(str(publish_cfg.get("platform") or "")).split("：", 1)[0],
                    "account_id": publish_cfg.get("account_id"),
                    "account_nickname": publish_cfg.get("account_nickname"),
                    "asset_id": asset_id,
                    "title": copy.get("title") or "",
                    "description": copy.get("description") or "",
                    "tags": copy.get("tags") or "",
                    "options": {"scheduled_publish": True, "source_run_id": run_id},
                }
                if not asset_id:
                    publish_draft["status"] = "failed"
                    publish_draft["error"] = "未取得可发布素材 asset_id"
                elif publish_draft["auto_publish"]:
                    await _post_task_event(
                        cloud,
                        base,
                        headers,
                        run_id,
                        "thinking",
                        {"text": "正在按所选平台自动发布"},
                    )
                    try:
                        publish_result = await _submit_local_publish_draft(draft=publish_draft, headers=headers)
                        publish_draft["status"] = "published"
                        publish_draft["publish_result"] = publish_result
                    except Exception as exc:
                        logger.exception("[SCHEDULED-TASK] auto publish failed run_id=%s", run_id)
                        publish_draft["status"] = "failed"
                        publish_draft["error"] = str(exc)[:500] or "自动发布失败"
            result_payload = {
                "capability_id": capability_id,
                "generated": generated,
                "caption": caption,
                "skill_prompt": skill_prompt,
                "mcp_result": result,
                "input_refs": input_refs,
                "result_refs": refs,
                "media_urls": refs["urls"],
            }
            if publish_draft:
                result_payload["publish_draft"] = publish_draft
            await _complete_task_run(
                cloud,
                base,
                headers,
                run_id,
                result_text=_scheduled_complete_text(result, caption, refs, skill_prompt, input_refs, publish_draft),
                result_payload=result_payload,
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
        if _scheduled_is_douyin_skip_result(result):
            await _complete_task_run(
                cloud,
                base,
                headers,
                run_id,
                result_text="本次没有新的可执行采集任务，已自动跳过重复或已完成的任务。",
                result_payload=_scheduled_douyin_skip_payload(capability_id, result, cap_payload),
            )
            return
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
        if _scheduled_is_douyin_skip_error_text(error_text):
            await _complete_task_run(
                cloud,
                base,
                headers,
                run_id,
                result_text="本次没有新的可执行采集任务，已自动跳过重复或已完成的任务。",
                result_payload=_scheduled_douyin_skip_payload(capability_id, locals().get("result"), cap_payload),
            )
            return
        partial = (
            exc.partial_result
            if isinstance(exc, PipelinePartialResultError) and isinstance(exc.partial_result, dict)
            else None
        )
        reservation_source = (
            partial.get("resume_payload")
            if isinstance(partial, dict) and isinstance(partial.get("resume_payload"), dict)
            else locals().get("result")
        )
        if isinstance(reservation_source, dict):
            _release_creative_candidate_asset_reservation(
                str(reservation_source.get("reference_asset_id") or ""),
                str(reservation_source.get("candidate_group") or cap_payload.get("candidate_group") or ""),
                jwt_token,
                str(reservation_source.get("reference_asset_reservation_id") or ""),
            )
        if isinstance(exc, PipelinePartialResultError) and isinstance(exc.partial_result, dict) and exc.partial_result:
            partial_result = dict(exc.partial_result)
            refs = _collect_scheduled_result_refs(partial_result)
            result_payload = {
                "capability_id": capability_id,
                "generated": locals().get("generated") if isinstance(locals().get("generated"), dict) else {},
                "caption": "",
                "skill_prompt": str((partial_result.get("plan") or {}).get("video_prompt") or (partial_result.get("plan") or {}).get("summary") or "").strip(),
                "mcp_result": partial_result,
                "input_refs": partial_result.get("resume_payload") if isinstance(partial_result.get("resume_payload"), dict) else {},
                "result_refs": refs,
                "media_urls": refs["urls"],
                "resume_available": bool(partial_result.get("resume_available")),
                "resume_payload": partial_result.get("resume_payload") if isinstance(partial_result.get("resume_payload"), dict) else {},
            }
            await _complete_task_run(
                cloud,
                base,
                headers,
                run_id,
                result_text="image generated; video step failed and total pre-deduct was refunded; use resume video to continue.",
                result_payload=result_payload,
                error=error_text[:500] or "video generation failed after image",
            )
            return
        await _complete_task_run(cloud, base, headers, run_id, error=error_text[:500] or "capability failed")


async def _post_local_api_json(
    path: str,
    body: Dict[str, Any],
    *,
    headers: Dict[str, str],
    timeout_seconds: float = 7200.0,
) -> Dict[str, Any]:
    timeout = httpx.Timeout(timeout_seconds, connect=10.0, read=timeout_seconds, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as local:
        resp = await local.post(_local_api_url(path), json=body or {}, headers=_local_chat_headers(headers))
    try:
        data = resp.json() if resp.content else {}
    except Exception:
        data = {"detail": (resp.text or "")[:1000]}
    if resp.status_code >= 400:
        raise RuntimeError(str(data.get("detail") or data.get("message") or data or resp.text)[:500])
    if isinstance(data, dict) and data.get("ok") is False:
        raise RuntimeError(str(data.get("detail") or data.get("error") or data.get("message") or data)[:500])
    return data if isinstance(data, dict) else {"result": data}


async def _run_client_workflow_action(
    action: str,
    params: Dict[str, Any],
    *,
    headers: Dict[str, str],
    run_id: str,
) -> Dict[str, Any]:
    source = params if isinstance(params, dict) else {}
    if action == "local_bestseller_plan":
        return await _post_local_api_json(
            "/api/local-bestseller/plan",
            {
                "profile": source.get("profile") if isinstance(source.get("profile"), dict) else {},
                "days": max(1, min(_safe_int(source.get("days") or 30), 30)),
            },
            headers=headers,
        )
    if action == "local_bestseller_scene_batch":
        return await _post_local_api_json(
            "/api/local-bestseller/scene/batch",
            {
                "profile": source.get("profile") if isinstance(source.get("profile"), dict) else {},
                "days": max(1, min(_safe_int(source.get("days") or 30), 30)),
                "model": str(source.get("model") or "gpt-image-2").strip() or "gpt-image-2",
                "quality": str(source.get("quality") or "high").strip() or "high",
            },
            headers=headers,
        )
    if action == "viral_video_remix_start":
        body = {
            "original_video_url": str(source.get("original_video_url") or "").strip(),
            "character_image_url": str(source.get("character_image_url") or "").strip(),
            "product_image_url": str(source.get("product_image_url") or "").strip(),
            "prompt": str(source.get("prompt") or "").strip(),
            "audio_prompt": str(source.get("audio_prompt") or "").strip(),
            "narration_script": str(source.get("narration_script") or "").strip(),
            "model": str(source.get("model") or "grok-imagine-video-1.5-preview").strip() or "grok-imagine-video-1.5-preview",
            "ratio": str(source.get("ratio") or "9:16").strip() or "9:16",
            "resolution": str(source.get("resolution") or "720p").strip() or "720p",
            "duration": max(5, min(_safe_int(source.get("duration") or 10), 10)),
            "generate_audio": bool(source.get("generate_audio", True)),
            "watermark": bool(source.get("watermark", False)),
            "use_character_reference": bool(source.get("use_character_reference") or source.get("character_image_url")),
            "billing_confirmed": bool(source.get("billing_confirmed", True)),
        }
        if not body["original_video_url"]:
            raise RuntimeError("爆款复刻缺少参考视频链接")
        if not (body["character_image_url"] or body["product_image_url"]):
            raise RuntimeError("爆款复刻需要人物图或产品图")
        return await _post_local_api_json("/api/viral-video-remix/seedance/start", body, headers=headers)
    if action == "wecom_poll_reply":
        return await _post_local_api_json("/api/wecom/poll-and-reply", {}, headers=headers, timeout_seconds=300.0)
    if action == "publish_content":
        material = str(source.get("asset_id") or "").strip()
        source_url = str(source.get("url") or "").strip()
        save_result: Dict[str, Any] = {}
        if not material and source_url:
            save_result = await _post_local_api_json(
                "/api/assets/save-url",
                {
                    "url": source_url,
                    "media_type": str(source.get("media_type") or "video").strip() or "video",
                    "name": str(source.get("name") or source.get("title") or "H5安排工作素材").strip(),
                    "tags": str(source.get("tags") or "H5安排工作").strip(),
                    "prompt": str(source.get("description") or source.get("title") or "").strip(),
                },
                headers=headers,
                timeout_seconds=1200.0,
            )
            material = str(save_result.get("asset_id") or "").strip()
        if not material:
            raise RuntimeError("发布中心入库缺少素材 ID 或公网链接")
        account_nickname = str(source.get("account_nickname") or "").strip()
        if not account_nickname:
            raise RuntimeError("发布中心入库缺少发布账号昵称")
        publish_result = await _post_local_api_json(
            "/api/publish",
            {
                "asset_id": material,
                "account_nickname": account_nickname,
                "title": str(source.get("title") or "").strip() or None,
                "description": str(source.get("description") or "").strip() or None,
                "tags": str(source.get("tags") or "").strip() or None,
                "ai_publish_copy": bool(source.get("ai_publish_copy", True)),
                "options": source.get("options") if isinstance(source.get("options"), dict) else {},
            },
            headers=headers,
            timeout_seconds=7200.0,
        )
        return {
            "ok": True,
            "asset_id": material,
            "save_result": save_result,
            "publish_result": publish_result,
        }
    raise RuntimeError(f"暂不支持的客户端工作流：{action}")


def _client_workflow_result_text(action: str, result: Dict[str, Any]) -> str:
    if action.startswith("local_bestseller"):
        items = result.get("items") if isinstance(result.get("items"), list) else []
        return f"同城爆款任务完成，已生成 {len(items)} 条内容。" if items else "同城爆款任务已完成。"
    if action == "viral_video_remix_start":
        task_id = str(result.get("task_id") or result.get("job_id") or "").strip()
        return "爆款复刻任务已提交到客户端。" + (f" 任务ID：{task_id}" if task_id else "")
    if action == "wecom_poll_reply":
        return "企业微信客服已执行一次拉取与自动回复检查。"
    if action == "publish_content":
        asset_id = str(result.get("asset_id") or "").strip()
        publish_result = result.get("publish_result") if isinstance(result.get("publish_result"), dict) else {}
        status = str(publish_result.get("status") or publish_result.get("state") or "").strip()
        return "发布中心任务已提交。" + (f" 素材：{asset_id}" if asset_id else "") + (f" 状态：{status}" if status else "")
    return _compact_result_text(result)


async def _run_client_workflow(
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    item: Dict[str, Any],
) -> None:
    run_id = str(item.get("id") or "").strip()
    payload = _scheduled_payload(item)
    action = str(payload.get("action") or "").strip()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    if not run_id or not action:
        return
    await _post_task_event(cloud, base, headers, run_id, "thinking", {"text": f"正在执行客户端工作流：{action}"})
    try:
        result = await _run_client_workflow_action(action, params, headers=headers, run_id=run_id)
        await _complete_task_run(
            cloud,
            base,
            headers,
            run_id,
            result_text=_client_workflow_result_text(action, result),
            result_payload={
                "task_kind": "client_workflow",
                "action": action,
                "params": params,
                "local_result": result,
            },
        )
    except Exception as exc:
        logger.exception("[SCHEDULED-TASK] client workflow failed run_id=%s action=%s", run_id, action)
        await _complete_task_run(cloud, base, headers, run_id, error=(str(exc).strip() or "client workflow failed")[:500])


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
    elif kind == "client_workflow":
        await _run_client_workflow(client, base, headers, item)
    elif kind == "douyin_leads":
        await _run_scheduled_douyin_leads(
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


def _channel_interval(name: str, default: float, minimum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)) or str(default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _try_mark_scheduled_run_active(run_id: str) -> bool:
    run_id = str(run_id or "").strip()
    if not run_id:
        return True
    if run_id in _active_scheduled_run_ids:
        return False
    _active_scheduled_run_ids.add(run_id)
    return True


def _unmark_scheduled_run_active(run_id: str) -> None:
    run_id = str(run_id or "").strip()
    if run_id:
        _active_scheduled_run_ids.discard(run_id)


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
    if run_id and not _try_mark_scheduled_run_active(run_id):
        logger.info("[SCHEDULED-TASK] skip duplicate in-flight run_id=%s", run_id)
        return
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
            _unmark_scheduled_run_active(run_id)


async def _process_publish_request_detached(
    base: str,
    jwt_token: str,
    installation_id: str,
    item: Dict[str, Any],
) -> None:
    run_id = str(item.get("id") or "").strip()
    payload = item.get("result_payload") if isinstance(item.get("result_payload"), dict) else {}
    draft = payload.get("publish_draft") if isinstance(payload.get("publish_draft"), dict) else {}
    if not run_id or not draft:
        return
    headers = _headers(jwt_token, installation_id)
    timeout = httpx.Timeout(2400.0, connect=10.0, read=2400.0, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        try:
            await _post_task_event(
                client,
                base,
                headers,
                run_id,
                "publish_claimed",
                {"run_id": run_id, "publish_draft": draft},
            )
            result = await _submit_local_publish_draft(draft=draft, headers=headers)
            await client.post(
                f"{base}/api/scheduled-tasks/runs/{run_id}/publish-complete",
                json={"publish_result": result},
                headers=headers,
            )
        except Exception as exc:
            logger.exception("[SCHEDULED-TASK] publish request failed run_id=%s", run_id)
            try:
                await client.post(
                    f"{base}/api/scheduled-tasks/runs/{run_id}/publish-complete",
                    json={"error": str(exc)[:500] or "发布失败", "publish_result": {}},
                    headers=headers,
                )
            except Exception as post_exc:
                logger.warning("[SCHEDULED-TASK] publish failure callback failed run_id=%s: %s", run_id, post_exc)


async def h5_chat_poll_loop() -> None:
    if not _enabled():
        logger.info("[H5-CHAT] remote H5 chat channel disabled")
        return

    h5_poll_interval = _channel_interval("LOBSTER_H5_CHAT_POLL_INTERVAL_SEC", 5.0, 3.0)
    task_poll_interval = _channel_interval("LOBSTER_SCHEDULED_TASK_POLL_INTERVAL_SEC", 30.0, 10.0)
    publish_poll_interval = _channel_interval("LOBSTER_SCHEDULED_PUBLISH_POLL_INTERVAL_SEC", 30.0, 10.0)
    heartbeat_interval = _channel_interval("LOBSTER_H5_CHAT_HEARTBEAT_INTERVAL_SEC", 60.0, 30.0)
    sleep_missing_auth = 10.0
    logged_missing = False
    last_heartbeat_at = 0.0
    last_h5_poll_at = 0.0
    last_task_poll_at = 0.0
    last_publish_poll_at = 0.0
    max_h5_concurrency = _channel_concurrency("LOBSTER_H5_CHAT_CONCURRENCY", 1, 5)
    max_task_concurrency = _channel_concurrency("LOBSTER_SCHEDULED_TASK_CONCURRENCY", 2, 10)
    max_publish_concurrency = _channel_concurrency("LOBSTER_SCHEDULED_PUBLISH_CONCURRENCY", 1, 3)
    active_items: set[asyncio.Task] = set()
    active_task_runs: set[asyncio.Task] = set()
    active_publish_runs: set[asyncio.Task] = set()
    logger.info(
        "[H5-CHAT] poll intervals h5=%ss scheduled=%ss publish=%ss heartbeat=%ss",
        h5_poll_interval,
        task_poll_interval,
        publish_poll_interval,
        heartbeat_interval,
    )

    while True:
        _reap_channel_tasks(active_items, "H5-CHAT")
        _reap_channel_tasks(active_task_runs, "SCHEDULED-TASK")
        _reap_channel_tasks(active_publish_runs, "SCHEDULED-PUBLISH")

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
                now_loop = asyncio.get_event_loop().time()
                if now_loop - last_heartbeat_at >= heartbeat_interval:
                    heartbeat_resp = await client.post(
                        f"{base}/api/h5-chat/device-heartbeat",
                        json={"display_name": "local-online"},
                        headers=headers,
                    )
                    if heartbeat_resp.status_code == 401:
                        logger.warning("[H5-CHAT] heartbeat auth rejected; clearing stale channel token")
                        clear_channel_fallback("h5_heartbeat_401")
                        last_heartbeat_at = 0.0
                        await asyncio.sleep(sleep_missing_auth)
                        continue
                    heartbeat_resp.raise_for_status()
                    last_heartbeat_at = now_loop
                    try:
                        await _report_douyin_dashboard_status(
                            client,
                            base,
                            headers,
                            jwt_token=jwt_token,
                            installation_id=installation_id,
                        )
                    except Exception as exc:
                        logger.debug("[DOUYIN-DASHBOARD] report failed: %s", exc)
                items: list[Dict[str, Any]] = []
                h5_slots = max(0, max_h5_concurrency - len(active_items))
                if h5_slots > 0 and now_loop - last_h5_poll_at >= h5_poll_interval:
                    last_h5_poll_at = now_loop
                    resp = await client.get(f"{base}/api/h5-chat/pending", params={"limit": h5_slots}, headers=headers)
                    if resp.status_code == 401:
                        logger.warning("[H5-CHAT] cloud auth rejected; waiting for next login token")
                        clear_channel_fallback("h5_pending_401")
                        last_heartbeat_at = 0.0
                        await asyncio.sleep(sleep_missing_auth)
                        continue
                    else:
                        resp.raise_for_status()
                        items = (resp.json() or {}).get("items") or []
                task_items: list[Dict[str, Any]] = []
                task_slots = max(0, max_task_concurrency - len(active_task_runs))
                if task_slots > 0 and now_loop - last_task_poll_at >= task_poll_interval:
                    last_task_poll_at = now_loop
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
                publish_items: list[Dict[str, Any]] = []
                publish_slots = max(0, max_publish_concurrency - len(active_publish_runs))
                if publish_slots > 0 and now_loop - last_publish_poll_at >= publish_poll_interval:
                    last_publish_poll_at = now_loop
                    publish_resp = await client.get(
                        f"{base}/api/scheduled-tasks/publish/pending",
                        params={"limit": publish_slots},
                        headers=headers,
                    )
                    if publish_resp.status_code == 401:
                        logger.warning("[SCHEDULED-PUBLISH] cloud auth rejected; waiting for next login token")
                        await asyncio.sleep(sleep_missing_auth)
                        continue
                    if publish_resp.status_code < 400:
                        publish_items = (publish_resp.json() or {}).get("items") or []
                    elif publish_resp.status_code != 404:
                        logger.debug("[SCHEDULED-PUBLISH] pending request HTTP %s: %s", publish_resp.status_code, publish_resp.text[:300])
                if not items and not task_items and not publish_items:
                    next_due = min(
                        last_h5_poll_at + h5_poll_interval if h5_slots > 0 else now_loop + h5_poll_interval,
                        last_task_poll_at + task_poll_interval if task_slots > 0 else now_loop + task_poll_interval,
                        last_publish_poll_at + publish_poll_interval if publish_slots > 0 else now_loop + publish_poll_interval,
                        last_heartbeat_at + heartbeat_interval,
                    )
                    await asyncio.sleep(max(0.5, min(5.0, next_due - asyncio.get_event_loop().time())))
                    continue
                for item in items:
                    active_items.add(asyncio.create_task(_process_item_detached(base, jwt_token, installation_id, item)))
                for item in task_items:
                    active_task_runs.add(
                        asyncio.create_task(_process_scheduled_task_detached(base, jwt_token, installation_id, item))
                    )
                for item in publish_items:
                    active_publish_runs.add(
                        asyncio.create_task(_process_publish_request_detached(base, jwt_token, installation_id, item))
                    )
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[H5-CHAT] poll loop error: %s", exc)
            await asyncio.sleep(5.0)
