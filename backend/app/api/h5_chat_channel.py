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
from ..services.openclaw_channel_auth_store import read_channel_fallback
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
_MOBILE_UPLOAD_TITLE = "銆愭墜鏈轰笂浼犵礌鏉愩€?
_MOBILE_UPLOAD_BLOCK_RE = re.compile(r"\n*銆愭墜鏈轰笂浼犵礌鏉愩€慭n(?P<body>[\s\S]*)", re.IGNORECASE)


def _local_mcp_url() -> str:
    port = os.environ.get("MCP_PORT") or str(getattr(settings, "mcp_port", 8001))
    return f"http://127.0.0.1:{port}/mcp"
_MOBILE_UPLOAD_URL_RE = re.compile(r"\bURL:\s*(?P<url>https?://[^\s]+)", re.IGNORECASE)
_MOBILE_UPLOAD_ASSET_RE = re.compile(r"\basset_id:\s*(?P<asset_id>[A-Za-z0-9_-]{4,80})", re.IGNORECASE)
_SCHEDULED_CREATIVE_ANGLES = [
    "鐥涚偣鍒囧叆",
    "鍦烘櫙浣撻獙",
    "缁撴灉鏀剁泭",
    "宸ヨ壓瀹炲姏",
    "浜や粯鏁堢巼",
    "淇′换鑳屼功",
    "瀵规瘮鍙嶅樊",
    "瀹㈡埛瑙嗚",
]
_SCHEDULED_CAPTION_STYLES = [
    "鍍忔湅鍙嬪垎浜竴娆℃柊鍙戠幇",
    "绐佸嚭涓€涓槑纭笟鍔＄粨鏋?,
    "鐢ㄨ交鏉惧彛鍚昏涓撲笟鑳藉姏",
    "寮鸿皟鐪佸績鍜屼氦浠樼‘瀹氭€?,
    "浠庡鎴峰父瑙侀棶棰樺垏鍏?,
    "鐢ㄤ竴鍙ユ湁璁板繂鐐圭殑缁撹鏀舵潫",
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
        values = re.split(r"[,\s锛屻€?锛沒+", raw)
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
        get_online_douyin_accounts,
        load_global_config,
        douyin_stranger_message_status,
        douyin_video_comment_status,
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
                        "nickname": str((db_row.nickname if db_row else "") or f"璐﹀彿 {account_id}").strip(),
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
                    "nickname": str(db_row.nickname or f"璐﹀彿 {account_id}").strip(),
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


@router.post("/api/scheduled-tasks/debug/run-local", summary="Debug run a scheduled task locally without waiting for cloud pending")
async def debug_run_scheduled_task_local(
    request: Request,
    _current_user: Any = Depends(get_current_user_for_local),
) -> Dict[str, Any]:
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")

    kind = str(body.get("task_kind") or "").strip().lower()
    if not kind:
        raise HTTPException(status_code=400, detail="缂哄皯 task_kind")
    if kind == "capability":
        _normalize_goal_video_task_create_body(body)

    auth = str(request.headers.get("Authorization") or "").strip()
    jwt_token = auth[7:].strip() if auth.lower().startswith("bearer ") else auth
    if not jwt_token:
        raise HTTPException(status_code=401, detail="缂哄皯 Authorization Bearer")
    installation_id = str(request.headers.get("X-Installation-Id") or "").strip()
    item = {
        "id": str(body.get("id") or f"debug-{uuid.uuid4().hex[:12]}"),
        "title": str(body.get("title") or "鏈湴璋冭瘯浠诲姟").strip(),
        "content": str(body.get("content") or body.get("title") or "鏈湴璋冭瘯浠诲姟").strip(),
        "task_kind": kind,
        "payload": body.get("payload") if isinstance(body.get("payload"), dict) else {},
        "installation_id": installation_id,
    }

    cloud_base = _cloud_base()
    timeout = httpx.Timeout(7200.0, connect=10.0, read=7200.0, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        if kind == "douyin_leads":
            if cloud_base:
                await _run_scheduled_douyin_leads(
                    client,
                    cloud_base,
                    _headers(jwt_token, installation_id),
                    item,
                    jwt_token=jwt_token,
                    installation_id=installation_id,
                )
            else:
                await _run_local_debug_douyin_leads(item)
        elif kind == "capability":
            if not cloud_base:
                raise HTTPException(status_code=503, detail="鏈厤缃?AUTH_SERVER_BASE锛宑apability 璋冭瘯浠诲姟闇€瑕佷簯绔洖浼犻摼璺?)
            await _run_scheduled_capability(
                client,
                cloud_base,
                _headers(jwt_token, installation_id),
                item,
                jwt_token=jwt_token,
                installation_id=installation_id,
            )
        else:
            if not cloud_base:
                raise HTTPException(status_code=503, detail="鏈厤缃?AUTH_SERVER_BASE锛屽綋鍓嶄粎鏀寔鏈湴璋冭瘯 douyin_leads")
            await _process_scheduled_task(
                client,
                cloud_base,
                jwt_token,
                installation_id,
                item,
            )
    return {"ok": True, "debug": True, "run_id": item["id"], "task_kind": kind}


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
    payload = {"result_text": result_text, "result_payload": result_payload or {}, "error": error}
    last_error: Optional[str] = None
    for attempt in range(1, 4):
        try:
            resp = await client.post(
                f"{base}/api/scheduled-tasks/runs/{run_id}/complete",
                json=payload,
                headers=headers,
            )
            if resp.status_code in _SCHEDULED_COMPLETE_RETRY_STATUS:
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if attempt < 3:
                    await asyncio.sleep(1.5 * attempt)
                    continue
            resp.raise_for_status()
            return
        except Exception as exc:
            last_error = str(exc).strip() or exc.__class__.__name__
            if attempt < 3:
                await asyncio.sleep(1.5 * attempt)
                continue
    raise RuntimeError(f"scheduled task complete callback failed run_id={run_id}: {last_error or 'unknown error'}")


def _local_chat_url() -> str:
    port = int(getattr(settings, "port", 8000) or 8000)
    return f"http://127.0.0.1:{port}/chat/stream"


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
            url = (url_match.group("url") or "").strip().rstrip("锛屻€傦紱;)")
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
        raw.extend([x for x in existing.replace("锛?, ",").split(",")])
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
            raw.extend([x for x in val.replace("锛?, ",").split(",")])
    inner = payload.get("payload")
    if isinstance(inner, dict):
        for key in ("attachment_asset_ids", "asset_ids", "reference_asset_ids"):
            val = inner.get(key)
            if isinstance(val, list):
                raw.extend(val)
            elif isinstance(val, str):
                raw.extend([x for x in val.replace("锛?, ",").split(",")])
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
    if "銆愰檮鍔犵礌鏉愩€? in (content or ""):
        return content
    return (content or "").rstrip() + "\n\n銆愰檮鍔犵礌鏉愩€慭n" + "\n".join(f"- asset_id: {aid}" for aid in ids)


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
                lines.append(f"- asset_id: {aid}  鐘舵€? 鏈満绱犳潗搴撴湭鎵惧埌")
                continue
            url = _scheduled_asset_open_url(row, aid, uid, req, db)
            mt = (row.media_type or "").strip()
            if url:
                lines.append(f"- asset_id: {aid}  media_type: {mt}  URL: {url}")
            else:
                lines.append(f"- asset_id: {aid}  media_type: {mt}  鐘舵€? 鏃犲叕缃?URL")
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
        raise RuntimeError("璇峰厛閫夋嫨鍒涙剰鎴愮墖澶囬€夌礌鏉愮粍")
    uid = int(_decode_jwt_sub(jwt_token) or "0")
    if uid <= 0:
        raise RuntimeError("鏈瘑鍒埌褰撳墠鐢ㄦ埛锛屾棤娉曡鍙栧閫夌礌鏉愮粍")
    db = SessionLocal()
    try:
        rows = db.query(Asset).filter(Asset.user_id == uid, Asset.media_type == "image").all()
        candidates = [
            row
            for row in rows
            if name in _asset_creative_candidate_groups(getattr(row, "meta", None))
        ]
        if not candidates:
            raise RuntimeError(f"澶囬€夌粍鈥渰name}鈥濋噷娌℃湁鍥剧墖绱犳潗")
        req = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
        usable: List[tuple[Asset, str]] = []
        for candidate in candidates:
            url = get_asset_public_url(candidate.asset_id, uid, req, db) or ""
            if url:
                usable.append((candidate, url))
        if not usable:
            raise RuntimeError(f"澶囬€夌粍鈥渰name}鈥濋噷娌℃湁鍙敤浜庤棰戠敓鎴愮殑鍏綉鍥剧墖绱犳潗锛岃閲嶆柊涓婁紶鎴栦繚瀛?URL 鍚庡啀璁句负澶囬€?)
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
            raise RuntimeError(f"澶囬€夌粍鈥渰name}鈥濋€変腑鐨勫浘鐗囨病鏈夊彲鐢ㄩ摼鎺?)
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

    await _post_cloud_event(cloud, base, headers, message_id, "thinking", {"text": "鏈湴鐩磋繛閾捐矾姝ｅ湪澶勭悊"})
    payload = {
        "message": clean_content or "璇锋牴鎹笂浼犲浘鐗囩户缁鐞嗐€?,
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
                reply_text=final_reply or "澶勭悊瀹屾垚銆?,
                payload={"mode": "direct", "result_refs": refs, "media_urls": refs.get("urls") or []},
            )
    except Exception as exc:
        logger.exception("[H5-CHAT] direct chat failed message_id=%s", message_id)
        await _complete_cloud_message(cloud, base, headers, message_id, error=str(exc)[:500] or "鏈湴鐩磋繛澶勭悊澶辫触")


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
    await _post_cloud_event(cloud, base, headers, message_id, "thinking", {"text": "宸蹭氦缁欐湰鏈?OpenClaw"})
    user_content = clean_content or "璇锋牴鎹笂浼犲浘鐗囩户缁鐞嗐€?
    if attachment_urls:
        upload_lines = "\n".join(
            f"- asset_id: {attachment_asset_ids[idx] if idx < len(attachment_asset_ids) else ''}  media_type: image  URL: {url}"
            for idx, url in enumerate(attachment_urls)
        )
        user_content += f"\n\n{_MOBILE_UPLOAD_TITLE}\n{upload_lines}"
    messages = [
        {"role": "system", "content": "浣犳槸鐢ㄦ埛鐨勬墜鏈轰細璇濆姪鎵嬨€傛牴鎹敤鎴锋秷鎭嚜鐒跺畬鎴愪换鍔★紝浣跨敤涓枃鍥炲銆?},
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
                error="OpenClaw 鏃犳湁鏁堝洖澶嶏紝璇锋鏌ユ湰鏈?OpenClaw Gateway 鏄惁鍚姩銆?,
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
        await _complete_cloud_message(cloud, base, headers, message_id, error=str(exc)[:500] or "OpenClaw 澶勭悊澶辫触")


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
                    + "\n\n銆愭湰鏈虹礌鏉愬簱涓婁笅鏂囥€慭n"
                    + asset_context
                    + "\n璇蜂紭鍏堜娇鐢ㄨ繖浜涚湡瀹炵礌鏉?ID/URL锛涗笉瑕佺紪閫犵礌鏉?ID銆?
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
    if title and title not in {"鑳藉姏瀹氭椂浠诲姟", "鐩爣鎴愮墖", "鍒涙剰鎴愮墖"}:
        return f"鏍规嵁鎴戠殑璁板繂鍜屼换鍔″悕绉扳€渰title}鈥濓紝鐢熸垚涓€涓?6 绉掓姈闊?9:16 涓枃瀹ｄ紶瑙嗛銆?
    return "鏍规嵁鎴戠殑璁板繂锛岃嚜鍔ㄩ€夋嫨鏈€閫傚悎鎺ㄥ箍鐨勪骇鍝佹垨鏈嶅姟锛岀敓鎴愪竴涓?6 绉掓姈闊?9:16 涓枃瀹ｄ紶瑙嗛銆?


def _fallback_image_goal(task_title: str) -> str:
    title = (task_title or "").strip()
    if title and title not in {"鑳藉姏瀹氭椂浠诲姟", "鏂囨+鍒涙剰鍥剧墖", "鍒涙剰鍥剧墖"}:
        return f"鏍规嵁鎴戠殑璁板繂鍜屼换鍔″悕绉扳€渰title}鈥濓紝鐢熸垚涓€寮犻€傚悎鏈嬪弸鍦堟垨鐭棰戝皝闈㈢殑涓枃瀹ｄ紶鍒涙剰鍥剧墖銆?
    return "鏍规嵁鎴戠殑璁板繂锛岃嚜鍔ㄩ€夋嫨鏈€閫傚悎鎺ㄥ箍鐨勪骇鍝佹垨鏈嶅姟锛岀敓鎴愪竴寮犱腑鏂囧浼犲垱鎰忓浘鐗囥€?


def _fallback_create_video_goal(task_title: str) -> str:
    title = (task_title or "").strip()
    if title and title not in {"鑳藉姏瀹氭椂浠诲姟", "gtp鍒涙剰鎴愮墖", "GPT鍒涙剰鎴愮墖", "鍒涙剰鎴愮墖"}:
        return f"鏍规嵁鎴戠殑璁板繂鍜屼换鍔″悕绉扳€渰title}鈥濓紝鐢熸垚涓€鏉″晢涓氬箍鍛婅川鎰熺殑鍒涙剰鎴愮墖瑙嗛銆?
    return "鏍规嵁鎴戠殑璁板繂锛岃嚜鍔ㄩ€夋嫨鏈€閫傚悎鎺ㄥ箍鐨勪骇鍝佹垨鏈嶅姟锛岀敓鎴愪竴鏉″晢涓氬箍鍛婅川鎰熺殑鍒涙剰鎴愮墖瑙嗛銆?


def _fallback_ppt_goal(task_title: str) -> str:
    title = (task_title or "").strip()
    if title and title not in {"鑳藉姏瀹氭椂浠诲姟", "PPT", "鐢熸垚PPT", "鏅鸿兘PPT"}:
        return f"鏍规嵁鎴戠殑璁板繂鍜屼换鍔″悕绉扳€渰title}鈥濓紝鐢熸垚涓€浠界粨鏋勬竻鏅扮殑鍟嗗姟婕旂ずPPT銆?
    return "鏍规嵁鎴戠殑璁板繂锛岃嚜鍔ㄩ€夋嫨鏈€閫傚悎姹囨姤鐨勪骇鍝併€佹湇鍔℃垨涓氬姟涓婚锛岀敓鎴愪竴浠界粨鏋勬竻鏅扮殑鍟嗗姟婕旂ずPPT銆?


def _fallback_hifly_script(task_title: str) -> str:
    title = (task_title or "").strip()
    subject = title[:12] if title and title not in {"鑳藉姏瀹氭椂浠诲姟", "椋炲奖鏁板瓧浜?, "椋為拱鏁板瓧浜?, "蹇呯伀鏁板瓧浜?} else "杩欐浜у搧"
    return f"澶у濂斤紝浠婂ぉ甯︿綘浜嗚В{subject}锛屼竴璧风湅鐪嬫牳蹇冧寒鐐广€?


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
    if not title or title in {"鑳藉姏瀹氭椂浠诲姟", "鐩爣鎴愮墖", "鍒涙剰鎴愮墖", "鏂囨+鍒涙剰鍥剧墖", "鍒涙剰鍥剧墖", "鏅鸿兘PPT", "PPT"}:
        if capability_id == "goal.image.pipeline":
            title = "鍒涙剰鍥剧墖"
        elif capability_id == "create.video.pipeline":
            title = "gtp鍒涙剰鎴愮墖"
        elif capability_id == "create.ppt.pipeline":
            title = "鏅鸿兘PPT"
        else:
            title = "鍒涙剰鎴愮墖"
    return {
        "title": title[:120],
        "goal": prompt[:1000],
        "caption_hint": "",
        "creative_angle": "鑷畾涔夋彁绀鸿瘝",
        "caption_style": "鏍规嵁鐢ㄦ埛鎻愮ず璇嶇敓鎴愬彂甯冩枃妗?,
        "selling_points": [],
        "memory_context_used": False,
        "custom_prompt_used": True,
    }


def _scheduled_goal_video_direct_plan(prompt: str, task_title: str) -> Dict[str, Any]:
    raw = str(prompt or "").strip()
    if not raw:
        return {}
    title = (task_title or "").strip()
    if not title or title in {"鑳藉姏瀹氭椂浠诲姟", "鐩爣鎴愮墖", "鍒涙剰鎴愮墖"}:
        title = "鍒涙剰鎴愮墖"
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
        raise ValueError("璇烽€夋嫨鍒涙剰鎴愮墖澶囬€夌礌鏉愮粍")
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
        ability = "蹇呯伀鏁板瓧浜?
    elif capability_id == "goal.image.pipeline":
        ability = "鏂囨+鍒涙剰鍥剧墖"
    elif capability_id == "create.video.pipeline":
        ability = "gtp鍒涙剰鎴愮墖"
    elif capability_id == "create.ppt.pipeline":
        ability = "鏅鸿兘PPT"
    else:
        ability = "鍒涙剰鎴愮墖"
    query = "\n".join([task_title or ability, ability, asset_context or ""]).strip()
    memory_context = _scheduled_memory_context(jwt_token, installation_id, query)
    seed = "|".join([run_id, capability_id, task_title, str(len(memory_context)), str(len(asset_context))])
    creative_angle = _scheduled_variant(seed, _SCHEDULED_CREATIVE_ANGLES)
    caption_style = _scheduled_variant(seed + "|caption", _SCHEDULED_CAPTION_STYLES)
    if capability_id == "hifly.video.create_by_tts":
        system = (
            "浣犳槸瀹氭椂浠诲姟鍐呭缂栨帓鍣ㄣ€傚彧杈撳嚭 JSON 瀵硅薄锛屼笉瑕?Markdown銆俓n"
            "鏍规嵁鐢ㄦ埛璁板繂鍜屽彲鐢ㄧ礌鏉愶紝涓哄繀鐏暟瀛椾汉鍙ｆ挱鐢熸垚鍐呭銆?
            "瀛楁锛歵itle(string), script(string), caption_hint(string)銆?
            "script 鏄暟瀛椾汉鍙ｆ挱鏂囨锛屼腑鏂囷紝涓€鍙ヨ瘽锛屽繀椤诲畬鏁撮€氶『锛屾渶澶?50 涓瓧銆?
            "涓嶈鍏堝啓闀挎枃妗堬紝涓嶈鍒嗘锛屼笉瑕佽姹傜敤鎴疯ˉ鍏呬俊鎭紝涓嶈缂栭€犵礌鏉?ID銆?
        )
    elif capability_id == "goal.image.pipeline":
        system = (
            "浣犳槸瀹氭椂浠诲姟鍐呭缂栨帓鍣ㄣ€傚彧杈撳嚭 JSON 瀵硅薄锛屼笉瑕?Markdown銆俓n"
            "鏍规嵁鐢ㄦ埛璁板繂鍜屽彲鐢ㄧ礌鏉愶紝涓烘枃妗?鍒涙剰鍥剧墖浠诲姟鐢熸垚鐩爣銆?
            "瀛楁锛歵itle(string), goal(string), caption_hint(string), creative_angle(string), selling_points(array)銆?
            "goal 瑕佽兘鐩存帴浼犵粰鍒涙剰鍥剧墖鑳藉姏锛屾槑纭鐢熸垚涓€寮犱腑鏂囧浼犲垱鎰忓浘鐗囷紝骞跺啓鍑烘湰娆″浘鐗囩殑鍒囧叆瑙掑害銆佺敾闈㈡柟鍚戝拰鏍稿績鐭枃妗堛€?
            "姣忔閮借鎹㈣〃杈撅紝涓嶈澶嶇敤鍥哄畾寮€澶淬€佸浐瀹氬彞寮忔垨閫氱敤瀹ｄ紶濂楄瘽锛涗笉瑕佽姹傜敤鎴疯ˉ鍏呬俊鎭紝涓嶈缂栭€犵礌鏉?ID銆?
        )
    elif capability_id == "create.video.pipeline":
        system = (
            "浣犳槸瀹氭椂浠诲姟鍐呭缂栨帓鍣ㄣ€傚彧杈撳嚭 JSON 瀵硅薄锛屼笉瑕?Markdown銆俓n"
            "鏍规嵁鐢ㄦ埛璁板繂鍜屽彲鐢ㄧ礌鏉愶紝涓?gtp鍒涙剰鎴愮墖鐢熸垚鏍稿績瑙嗛鍒涗綔 brief銆?
            "瀛楁锛歵itle(string), goal(string), caption_hint(string), creative_angle(string), selling_points(array)銆?
            "goal 瑕佽兘鐩存帴浼犵粰 create-video 娴佹按绾匡紝鍐欐竻妤氳棰戜富棰樸€佹牳蹇冨崠鐐广€佺洰鏍囧彈浼椼€佺敾闈㈤鏍煎拰鍙欎簨鏂瑰悜銆?
            "涓嶈瑕佹眰鐢ㄦ埛琛ュ厖淇℃伅锛屼笉瑕佺紪閫犵礌鏉?ID锛涢伩鍏嶈姹傜敾闈㈠嚭鐜板瓧骞曘€佹枃瀛椼€佸瓧姣嶃€佹暟瀛椼€乴ogo銆佹按鍗般€?
        )
    elif capability_id == "create.ppt.pipeline":
        system = (
            "浣犳槸瀹氭椂浠诲姟鍐呭缂栨帓鍣ㄣ€傚彧杈撳嚭 JSON 瀵硅薄锛屼笉瑕?Markdown銆俓n"
            "鏍规嵁鐢ㄦ埛璁板繂鍜屽彲鐢ㄧ礌鏉愶紝涓烘櫤鑳絇PT鐢熸垚鏍稿績姹囨姤 brief銆?
            "瀛楁锛歵itle(string), goal(string), caption_hint(string), creative_angle(string), selling_points(array)銆?
            "goal 瑕佽兘鐩存帴浼犵粰 PPT 鐢熸垚娴佹按绾匡紝鍐欐竻妤氭眹鎶ヤ富棰樸€佺洰鏍囧彈浼椼€佹牳蹇冪粨鏋勩€佸叧閿鐐瑰拰甯屾湜鍛堢幇鐨勫晢鍔￠鏍笺€?
            "涓嶈瑕佹眰鐢ㄦ埛琛ュ厖淇℃伅锛屼笉瑕佺紪閫犵礌鏉?ID锛涙病鏈夌湡瀹炴暟鎹椂涓嶈纭€犳暟瀛椼€?
        )
    else:
        system = (
            "浣犳槸瀹氭椂浠诲姟鍐呭缂栨帓鍣ㄣ€傚彧杈撳嚭 JSON 瀵硅薄锛屼笉瑕?Markdown銆俓n"
            "鏍规嵁鐢ㄦ埛璁板繂鍜屽彲鐢ㄧ礌鏉愶紝涓哄垱鎰忔垚鐗囨祦姘寸嚎鐢熸垚鐩爣銆?
            "瀛楁锛歵itle(string), goal(string), caption_hint(string), creative_angle(string), selling_points(array)銆?
            "鍏堜粠璁板繂閲屾娊鍙栫湡瀹炲崠鐐癸紝鍐嶅洿缁曟寚瀹氬垱鎰忚搴︾敓鎴愭湰娆¤棰戠洰鏍囥€?
            "goal 瑕佽兘鐩存帴浼犵粰鍒涙剰鎴愮墖鑳藉姏锛屾槑纭?6 绉掋€佹姈闊炽€?:16銆佷腑鏂囧浼犺棰戯紝骞跺啓鍑烘湰娆℃垚鐗囩殑鍒囧叆瑙掑害銆佺敾闈㈡柟鍚戝拰鏍稿績鐭枃妗堛€?
            "姣忔閮借鎹㈣〃杈撅紝涓嶈澶嶇敤鍥哄畾寮€澶淬€佸浐瀹氬彞寮忔垨閫氱敤瀹ｄ紶濂楄瘽锛涗笉瑕佽姹傜敤鎴疯ˉ鍏呬俊鎭紝涓嶈缂栭€犵礌鏉?ID銆?
        )
    user_payload = {
        "task_title": task_title,
        "ability": ability,
        "creative_angle": creative_angle,
        "caption_style": caption_style,
        "variation_rule": "鏈蹇呴』鍥寸粫 creative_angle 鍙栨潗鍜岃〃杈撅紝閬垮厤鍜屼互寰€瀹氭椂浠诲姟浣跨敤鍚屼竴濂楀浼犺瘽鏈€?,
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
            "title": title or "鏁板瓧浜哄彛鎾?,
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
        "title": title or ("鍒涙剰鍥剧墖" if capability_id == "goal.image.pipeline" else "gtp鍒涙剰鎴愮墖" if capability_id == "create.video.pipeline" else "鍒涙剰鎴愮墖"),
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
                add_url(match.group(0).rstrip(".,!?锛屻€傦紒锛熴€侊紱锛?))

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
        "鏄?,
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
        raw = re.split(r"[,锛孿s#銆乚+", str(value or ""))
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
        return "灏忕孩涔︼細鏍囬 12-20 瀛楋紝鏈夌鑽夋劅锛涙鏂?80-180 瀛楋紝鍒嗘鑷劧锛岀粨灏惧甫 3-6 涓瘽棰樻爣绛俱€?
    if p == "toutiao":
        return "浠婃棩澶存潯锛氭爣棰?18-30 瀛楋紝淇℃伅鏄庣‘锛涙鏂?120-300 瀛楋紝閫傚悎鍥炬枃璧勮鍙ｅ惢锛屽皯鐢ㄥじ寮犵鍙枫€?
    if p == "kuaishou":
        return "蹇墜锛氭爣棰樼煭鐩存帴锛涙鏂?40-100 瀛楋紝鐢熸椿鍖栥€佹帴鍦版皵锛屽甫 2-4 涓爣绛俱€?
    if p == "bilibili":
        return "B绔欙細鏍囬 16-32 瀛楋紱绠€浠嬭鏄庝寒鐐瑰拰鐪嬬偣锛屽甫 2-5 涓爣绛俱€?
    return "鎶栭煶锛氭爣棰?10-24 瀛楋紝姝ｆ枃 40-90 瀛楋紝寮€澶存湁鍚稿紩鍔涳紝甯?2-5 涓瘽棰樻爣绛俱€?


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
    fallback_title = (str(generated.get("title") or task_title or "AI 鍒涙剰鍐呭").strip() or "AI 鍒涙剰鍐呭")[:30]
    fallback_desc = caption or _fallback_scheduled_caption(capability_id, generated)
    fallback_tags = _clean_publish_tags(generated.get("tags") or generated.get("keywords") or "")
    system = (
        "浣犳槸涓枃绀句氦骞冲彴杩愯惀銆傚彧杈撳嚭 JSON 瀵硅薄锛屽瓧娈靛繀椤绘槸 title銆乨escription銆乼ags銆?
        "涓嶈 Markdown锛屼笉瑕佽В閲娿€?
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
                "requirements": "鏍囬銆佹鏂囥€佹爣绛捐閫傚悎鎵€閫夊钩鍙帮紱涓嶈缂栭€犱笉瀛樺湪鐨勪紭鎯犮€佷环鏍兼垨鍦板潃銆?,
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
        raise RuntimeError("鍙戝竷鑽夌缂哄皯绱犳潗 asset_id")
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
    """鍒涙剰鎴愮墖鍙繑鍥炴渶缁堣棰戠礌鏉愶紝閬垮厤鎶婂閫夊浘/涓婃父涓存椂閾炬帴涓€璧峰缁?H5銆?""
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
    """gtp鍒涙剰鎴愮墖涔熷彧杩斿洖鏈€缁堣棰戠礌鏉愶紝閬垮厤鎶婁腑闂撮甯у浘娣疯繘 H5 缁撴灉銆?""
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
                    return f"鍒涙剰鎴愮墖瑙嗛浠嶅湪鐢熸垚涓瓄('锛宼ask_id=' + task_id) if task_id else ''}"
                final = video.get("final_result")
                if isinstance(final, dict):
                    final_status = str(final.get("status") or (final.get("result") or {}).get("status") or "").strip().lower()
                    if final_status in {"running", "processing", "pending", "queued", "waiting"}:
                        task_id = str(video.get("task_id") or (final.get("result") or {}).get("task_id") or "").strip()
                        return f"鍒涙剰鎴愮墖瑙嗛浠嶅湪鐢熸垚涓瓄('锛宼ask_id=' + task_id) if task_id else ''}"
            for value in cur.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(cur, list):
            stack.extend(v for v in cur if isinstance(v, (dict, list)))
    if any(s in {"running", "processing", "pending", "queued", "waiting"} for s in statuses):
        return "鍒涙剰鎴愮墖瑙嗛浠嶅湪鐢熸垚涓?
    return ""


def _create_video_pipeline_pending_reason(result: Any) -> str:
    reason = _goal_video_pipeline_pending_reason(result)
    return reason.replace("鍒涙剰鎴愮墖", "gtp鍒涙剰鎴愮墖") if reason else ""


def _scheduled_caption_candidate(value: Any) -> str:
    text = " ".join(str(value or "").strip().strip('"鈥溾€漙').split())
    text = re.sub(r"^(鍙戝竷鏂囨|鏈嬪弸鍦堟枃妗坾鏂囨)\s*[:锛歖\s*", "", text).strip()
    return text if 0 < len(text) <= 50 else ""


def _fallback_scheduled_caption(capability_id: str, generated: Dict[str, Any]) -> str:
    hint = _scheduled_caption_candidate(generated.get("caption_hint"))
    if hint:
        return hint
    title = str(generated.get("title") or "").strip()
    subject = title[:12] if title and title not in {"鑳藉姏瀹氭椂浠诲姟", "鐩爣鎴愮墖", "鍒涙剰鎴愮墖", "鏅鸿兘PPT", "PPT", "鏁板瓧浜哄彛鎾?} else "杩欐鍐呭"
    angle = str(generated.get("creative_angle") or "").strip()
    options = {
        "鐥涚偣鍒囧叆": f"{subject}鎶婇毦鐐硅娓呮锛岄€夊瀷鍜岃惤鍦伴兘鏇存湁搴曟皵銆?,
        "鍦烘櫙浣撻獙": f"鎶妠subject}鏀捐繘鐪熷疄鍦烘櫙閲岀湅锛屼环鍊间細鏇寸洿瑙傘€?,
        "缁撴灉鏀剁泭": f"{subject}涓嶅彧濂界湅锛屾洿瑕佸甫鏉ユ晥鐜囥€佸搧璐ㄥ拰纭畾鎬с€?,
        "宸ヨ壓瀹炲姏": f"鐢ㄧ粏鑺傚憟鐜皗subject}瀹炲姏锛岃涓撲笟鑳藉姏琚竴鐪肩湅瑙併€?,
        "浜や粯鏁堢巼": f"{subject}浠庨渶姹傚埌浜や粯鏇撮『鐣咃紝灏戠瓑寰咃紝澶氱‘瀹氥€?,
        "淇′换鑳屼功": f"闈犺氨鐨剓subject}锛屾潵鑷寔缁ǔ瀹氱殑鑳藉姏鍜屾湇鍔°€?,
        "瀵规瘮鍙嶅樊": f"鍚屾牱鍋歿subject}锛屽樊鍒線寰€钘忓湪缁嗚妭鍜屼氦浠橀噷銆?,
        "瀹㈡埛瑙嗚": f"绔欏湪瀹㈡埛瑙掑害鐪媨subject}锛岀渷蹇冨氨鏄渶澶х殑浠峰€笺€?,
    }
    if angle in options:
        return options[angle]
    if capability_id == "hifly.video.create_by_tts":
        return f"{subject}浜偣宸茬敓鎴愶紝閫傚悎鐩存帴鍒嗕韩缁欏鎴风湅鐪嬨€?
    if capability_id == "create.ppt.pipeline":
        return f"{subject}PPT宸茬敓鎴愶紝閫傚悎鐩存帴鐢ㄤ簬姹囨姤鍜屾矡閫氥€?
    return f"{subject}瀹ｄ紶瑙嗛宸茬敓鎴愶紝鎹釜瑙掑害鐪嬬湅浜у搧浠峰€笺€?


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
        "浣犲彧璐熻矗鍐欏彂甯冩湅鍙嬪湀鏂囨銆傝緭鍑轰竴鏉′腑鏂囷紝涓€鍙ュ畬鏁磋瘽锛?5 鍒?50 涓瓧锛屼笉瑕?Markdown锛屼笉瑕佽В閲娿€?
        "蹇呴』鏍规嵁 generated_content 閲岀殑 goal/script銆乧aption_hint銆乧reative_angle 鍜?result_refs 閲嶆柊鍒涗綔锛?
        "涓嶈鐓ф妱 caption_hint锛屼笉瑕佷娇鐢ㄥ浐瀹氬浼犲彛鍙枫€?
        "鍚屼竴鐢ㄦ埛澶氭鎵ц鏃惰鎹㈠垏鍏ヨ搴﹀拰鍙ュ紡锛岃姣忔鍙戝竷鐪嬭捣鏉ヤ笉鏄悓涓€妯℃澘銆?
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
                "length_rule": "蹇呴』鏄竴鍙ュ畬鏁翠腑鏂囷紝鏈€澶?50 涓瓧锛屼笉鍏佽鍏堢敓鎴愰暱鏂囧啀鎴柇銆?,
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
                system="鎶婂師鏂囬噸鍐欎负涓€鏉″畬鏁翠腑鏂囨湅鍙嬪湀鏂囨锛屾渶澶?50 涓瓧锛屼笉瑕?Markdown锛屼笉瑕佽В閲娿€?,
                user_payload={
                    "ability": capability_id,
                    "generated_content": generated,
                    "original_caption": text,
                    "length_rule": "涓嶈鎴柇锛岀洿鎺ラ噸鍐欐垚涓€鍙ュ畬鏁磋瘽銆?,
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
    lines = ["鐢熸垚瀹屾垚銆? if ready else "浠诲姟宸叉彁浜わ紝浠嶅湪鐢熸垚涓€?, f"鍙戝竷鏂囨锛歿caption}"]
    if skill_prompt:
        lines.append(f"浼犵粰鎶€鑳界殑鎻愮ず璇嶏細{skill_prompt}")
    if input_refs:
        source_mode = str(input_refs.get("source_mode") or "").strip()
        image_model = str(input_refs.get("image_model") or "").strip()
        group = str(input_refs.get("candidate_group") or "").strip()
        ref_asset = str(input_refs.get("reference_asset_id") or "").strip()
        if source_mode == _SCHEDULED_VIDEO_SOURCE_AI_IMAGE:
            lines.append(f"棣栧抚鏉ユ簮锛欰I 鐢熸垚鍥剧墖{('锛? + image_model + '锛?) if image_model else ''}")
        elif source_mode == _SCHEDULED_VIDEO_SOURCE_ASSET_RANDOM:
            lines.append("棣栧抚鏉ユ簮锛氱礌鏉愬簱澶囬€夌粍杞崲鍥剧墖")
        elif source_mode == "create_video_pipeline":
            video_model = str(input_refs.get("video_model") or "").strip()
            planning_model = str(input_refs.get("planning_model") or "").strip()
            if image_model:
                lines.append(f"棣栧抚妯″瀷锛歿image_model}")
            if video_model:
                lines.append(f"瑙嗛妯″瀷锛歿video_model}")
            if planning_model:
                lines.append(f"瑙勫垝妯″瀷锛歿planning_model}")
        elif source_mode == "create_ppt_pipeline":
            planning_model = str(input_refs.get("planning_model") or "").strip()
            theme = str(input_refs.get("theme") or "").strip()
            slide_count = str(input_refs.get("slide_count") or "").strip()
            if planning_model:
                lines.append(f"PPT瑙勫垝妯″瀷锛歿planning_model}")
            if theme:
                lines.append(f"PPT涓婚鏍峰紡锛歿theme}")
            if slide_count:
                lines.append(f"PPT椤垫暟锛歿slide_count}")
        if group:
            lines.append(f"澶囬€夌粍锛歿group}")
        if ref_asset:
            lines.append(f"浣跨敤澶囬€夌礌鏉愶細{ref_asset}")
    refs = refs or _collect_scheduled_result_refs(result)
    if refs["asset_ids"]:
        lines.append("鐢熸垚绱犳潗锛? + "銆?.join(refs["asset_ids"][:6]))
    if refs["urls"]:
        lines.append("棰勮閾炬帴锛?)
        lines.extend(refs["urls"][:6])
    if publish_draft:
        status = str(publish_draft.get("status") or "ready").strip()
        platform = str(publish_draft.get("platform_name") or publish_draft.get("platform") or "").strip()
        acct = str(publish_draft.get("account_nickname") or publish_draft.get("account_id") or "").strip()
        label = {
            "ready": "寰呭彂甯?,
            "pending": "绛夊緟鍙戝竷",
            "processing": "鍙戝竷涓?,
            "published": "宸插彂甯?,
            "failed": "鍙戝竷澶辫触",
        }.get(status, status or "寰呭彂甯?)
        lines.append("鍙戝竷鐘舵€侊細" + label + (f"锛坽platform} 路 {acct}锛? if platform or acct else ""))
        if publish_draft.get("error"):
            lines.append("鍙戝竷閿欒锛? + str(publish_draft.get("error"))[:200])
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


def _compact_douyin_message(value: Any, fallback: str) -> str:
    text = _compact_result_text(value)
    return text or fallback


def _douyin_result_error(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    code = result.get("code")
    try:
        if code is not None and int(code) != 200:
            return _compact_douyin_message(
                result.get("msg") or result.get("message") or result.get("detail") or result,
                "鎶栭煶浠诲姟鎵ц澶辫触",
            )
    except Exception:
        pass
    return _scheduled_capability_error(result)


def _douyin_stats_from_tasks(tasks: List[Dict[str, Any]]) -> Dict[str, int]:
    total = len(tasks)
    completed = 0
    failed = 0
    high_intent_users = 0
    comments_collected = 0
    for task in tasks:
        if not isinstance(task, dict):
            continue
        status = str(task.get("status") or "").strip().lower()
        if status == "completed":
            completed += 1
        elif status == "failed":
            failed += 1
        high_intent_users += max(0, int(task.get("high_intent_count", 0) or 0))
        comments_collected += max(
            int(task.get("comment_count", 0) or 0),
            len(task.get("all_comments") or []) if isinstance(task.get("all_comments"), list) else 0,
        )
    return {
        "tasks_total": total,
        "tasks_completed": completed,
        "tasks_failed": failed,
        "high_intent_users": high_intent_users,
        "comments_collected": comments_collected,
    }


def _douyin_stats_from_users(users: List[Dict[str, Any]], status_field: str, success_statuses: set[str]) -> Dict[str, int]:
    total = len(users)
    success = 0
    failed = 0
    for row in users:
        if not isinstance(row, dict):
            continue
        status = str(row.get(status_field) or "").strip().lower()
        if status in success_statuses:
            success += 1
        elif status == "failed":
            failed += 1
    return {
        "users_total": total,
        "users_success": success,
        "users_failed": failed,
    }


async def _run_local_debug_douyin_leads(item: Dict[str, Any]) -> None:
    _install_douyin_origin_import_path()
    from douyin_api import (  # type: ignore
        douyin_collect_stranger_messages,
        douyin_interaction_status,
        douyin_search_collect,
        douyin_send_stranger_messages,
        douyin_start_interaction,
        douyin_start_tasks,
        douyin_stranger_message_status,
        douyin_tasks_from_search,
        douyin_get_tasks_lite,
    )

    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    action = str(payload.get("action") or "").strip().lower()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    if not action:
        raise RuntimeError("缂哄皯 douyin_leads.action")

    async def poll_until_finished(
        *,
        status_func,
        done_when,
        interval_seconds: float = 5.0,
        max_seconds: float = 7200.0,
    ) -> Dict[str, Any]:
        waited = 0.0
        last_payload: Dict[str, Any] = {}
        while waited <= max_seconds:
            current = await status_func()
            if not isinstance(current, dict):
                current = {"code": 500, "msg": "鐘舵€佹帴鍙ｈ繑鍥炲紓甯?}
            last_payload = current
            err = _douyin_result_error(current)
            if err:
                raise RuntimeError(err)
            if done_when(current):
                return current
            await asyncio.sleep(interval_seconds)
            waited += interval_seconds
        raise RuntimeError(f"鏈湴璋冭瘯浠诲姟瓒呮椂锛歿action}")

    if action == "search_collect":
        search_request = dict(params or {})
        search_request["mode"] = "script"
        result = await douyin_search_collect(search_request)
        err = _douyin_result_error(result)
        if err:
            raise RuntimeError(err)
        rows = result.get("data") if isinstance(result.get("data"), list) else []
        if not rows:
            logger.info("[debug.douyin_leads] search_collect no rows result=%s", _json_trunc(result, 1200))
            return
        first_item = dict(rows[0] if isinstance(rows[0], dict) else {})
        sync_result = await douyin_tasks_from_search({"data": [first_item]})
        err = _douyin_result_error(sync_result)
        if err:
            raise RuntimeError(err)
        final_tasks = await douyin_get_tasks_lite()
        lite_tasks = final_tasks.get("tasks") if isinstance(final_tasks.get("tasks"), list) else []
        matched_task = None
        first_url = str(first_item.get("url", "") or "").strip()
        first_title = str(first_item.get("title", "") or "").strip()
        for task in lite_tasks:
            if not isinstance(task, dict):
                continue
            if str(task.get("url", "") or "").strip() == first_url and first_url:
                matched_task = task
                break
            if str(task.get("title", "") or "").strip() == first_title and first_title:
                matched_task = task
        if not matched_task:
            raise RuntimeError("鎼滅储瀹屾垚鍚庢湭鑳藉畾浣嶅埌棣栦釜瑙嗛浠诲姟")
        task_id = int(matched_task.get("id", 0) or 0)
        if task_id <= 0:
            raise RuntimeError("鎼滅储瀹屾垚鍚庨涓棰戜换鍔＄己灏戞湁鏁?ID")
        start_result = await douyin_start_tasks(request={"selected_task_ids": [task_id], "collection_mode": "script"})
        err = _douyin_result_error(start_result)
        if err:
            raise RuntimeError(err)
        final = await poll_until_finished(
            status_func=douyin_get_tasks_lite,
            done_when=lambda current: not bool(current.get("running")),
        )
        logger.info(
            "[debug.douyin_leads] search_collect search=%s sync=%s start=%s final=%s",
            _json_trunc(result, 800),
            _json_trunc(sync_result, 800),
            _json_trunc(start_result, 800),
            _json_trunc(final, 1600),
        )
        return

    if action == "tasks_from_search":
        result = await douyin_tasks_from_search(dict(params or {}))
        err = _douyin_result_error(result)
        if err:
            raise RuntimeError(err)
        logger.info("[debug.douyin_leads] tasks_from_search result=%s", _json_trunc(result, 1200))
        return

    if action == "comment_collect":
        start_result = await douyin_start_tasks(request=dict(params or {}))
        err = _douyin_result_error(start_result)
        if err:
            raise RuntimeError(err)
        final = await poll_until_finished(
            status_func=douyin_get_tasks_lite,
            done_when=lambda current: not bool(current.get("running")),
        )
        logger.info("[debug.douyin_leads] comment_collect start=%s final=%s", _json_trunc(start_result, 800), _json_trunc(final, 1600))
        return

    if action == "interaction":
        start_result = await douyin_start_interaction(request=dict(params or {}))
        err = _douyin_result_error(start_result)
        if err:
            raise RuntimeError(err)
        final = await poll_until_finished(
            status_func=lambda: douyin_interaction_status(lite=True, include_users=True),
            done_when=lambda current: not bool(current.get("running")),
        )
        logger.info("[debug.douyin_leads] interaction start=%s final=%s", _json_trunc(start_result, 800), _json_trunc(final, 1600))
        return

    if action == "stranger_collect":
        start_result = await douyin_collect_stranger_messages(request=dict(params or {}))
        err = _douyin_result_error(start_result)
        if err:
            raise RuntimeError(err)
        final = await poll_until_finished(
            status_func=douyin_stranger_message_status,
            done_when=lambda current: not bool(current.get("running")),
        )
        logger.info("[debug.douyin_leads] stranger_collect start=%s final=%s", _json_trunc(start_result, 800), _json_trunc(final, 1600))
        return

    if action == "stranger_send":
        start_result = await douyin_send_stranger_messages(request=dict(params or {}))
        err = _douyin_result_error(start_result)
        if err:
            raise RuntimeError(err)
        final = await poll_until_finished(
            status_func=douyin_stranger_message_status,
            done_when=lambda current: not bool(current.get("running")),
        )
        logger.info("[debug.douyin_leads] stranger_send start=%s final=%s", _json_trunc(start_result, 800), _json_trunc(final, 1600))
        return

    raise RuntimeError(f"鏆備笉鏀寔鐨?douyin_leads.action: {action}")


async def _run_scheduled_douyin_leads(
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    item: Dict[str, Any],
    *,
    jwt_token: str,
    installation_id: str,
) -> None:
    _install_douyin_origin_import_path()
    from douyin_api import (  # type: ignore
        douyin_collect_stranger_messages,
        douyin_interaction_status,
        douyin_search_collect,
        douyin_send_stranger_messages,
        douyin_start_interaction,
        douyin_start_tasks,
        douyin_stranger_message_status,
        douyin_tasks_from_search,
        douyin_video_comment_status,
        douyin_get_tasks_lite,
    )

    run_id = str(item.get("id") or "").strip()
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    action = str(payload.get("action") or "").strip().lower()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    title = str(item.get("title") or item.get("content") or run_id or "").strip()
    if not run_id:
        return
    if not action:
        raise RuntimeError("缂哄皯 douyin_leads.action")

    async def emit(stage: str, text: str, *, progress: Optional[int] = None, stats: Optional[Dict[str, Any]] = None) -> None:
        body: Dict[str, Any] = {"text": text, "stage": stage, "action": action}
        if progress is not None:
            body["progress"] = max(0, min(100, int(progress)))
        if stats:
            body["stats"] = stats
        await _post_task_event(cloud, base, headers, run_id, stage, body)

    def progress(done: int, total: int) -> int:
        if total <= 0:
            return 0
        return max(0, min(99, int(done * 100 / total)))

    def pick_task_from_lite_rows(tasks: List[Dict[str, Any]], item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        target_url = str(item.get("url", "") or "").strip()
        target_title = str(item.get("title", "") or "").strip()
        for task in tasks:
            if not isinstance(task, dict):
                continue
            if target_url and str(task.get("url", "") or "").strip() == target_url:
                return task
        for task in tasks:
            if not isinstance(task, dict):
                continue
            if target_title and str(task.get("title", "") or "").strip() == target_title:
                return task
        return None

    async def poll_until_finished(
        *,
        status_func,
        state_key: str,
        done_when,
        stats_builder,
        interval_seconds: float = 5.0,
        max_seconds: float = 7200.0,
    ) -> Dict[str, Any]:
        waited = 0.0
        last_payload: Dict[str, Any] = {}
        last_digest = ""
        while waited <= max_seconds:
            current = await status_func()
            if not isinstance(current, dict):
                current = {"code": 500, "msg": "鐘舵€佹帴鍙ｈ繑鍥炲紓甯?}
            last_payload = current
            err = _scheduled_capability_error(current)
            if err:
                raise RuntimeError(err)
            state = current.get(state_key) if isinstance(current.get(state_key), dict) else {}
            running = bool(current.get("running"))
            stats = stats_builder(current)
            state_message = _compact_douyin_message(
                state.get("message") or state.get("last_message") or current.get("msg"),
                f"{title or action} 姝ｅ湪鎵ц",
            )
            digest = json.dumps({"running": running, "stats": stats, "message": state_message}, ensure_ascii=False, sort_keys=True)
            if digest != last_digest:
                total = int(stats.get("total") or stats.get("tasks_total") or stats.get("users_total") or 0)
                done = int(
                    stats.get("completed")
                    or stats.get("tasks_completed")
                    or stats.get("users_success")
                    or stats.get("processed")
                    or 0
                )
                await emit("progress", state_message, progress=progress(done, total), stats=stats)
                last_digest = digest
            if done_when(current):
                return current
            await asyncio.sleep(interval_seconds)
            waited += interval_seconds
        raise RuntimeError(f"{title or action} 瓒呮椂锛岃秴杩?{int(max_seconds)} 绉掍粛鏈粨鏉?)

    try:
        await emit("claimed", f"宸查鍙栨姈闊宠幏瀹换鍔★細{action}", progress=1)
        if action == "search_collect":
            await emit("searching", "姝ｅ湪鎵ц鎶栭煶鎼滅储閲囬泦", progress=5)
            search_request = dict(params or {})
            search_request["mode"] = "script"
            result = await douyin_search_collect(search_request)
            err = _douyin_result_error(result)
            if err:
                raise RuntimeError(err)
            rows = result.get("data") if isinstance(result.get("data"), list) else []
            total_rows = int(result.get("total", len(rows)) or len(rows))
            await emit(
                "search_done",
                _compact_douyin_message(result.get("msg"), f"鎼滅储鍏抽敭璇嶅畬鎴愶紝鎵惧埌浜?{total_rows} 涓棰?),
                progress=20,
                stats={"videos_found": total_rows},
            )
            if not rows:
                result_payload = {
                    "task_kind": "douyin_leads",
                    "action": action,
                    "search_mode": result.get("search_mode"),
                    "account_id": result.get("account_id"),
                    "total": total_rows,
                    "items": [],
                    "search_result": result,
                    "raw_result": result,
                }
                await _complete_task_run(
                    cloud,
                    base,
                    headers,
                    run_id,
                    result_text=_compact_douyin_message(result.get("msg"), "鎼滅储瀹屾垚锛屼絾娌℃湁鎵惧埌鍙噰闆嗙殑瑙嗛"),
                    result_payload=result_payload,
                )
                return
            first_item = dict(rows[0] if isinstance(rows[0], dict) else {})
            await emit(
                "collect_prepare",
                "鎼滅储瀹屾垚锛屾鍦ㄥ绗?1 涓棰戦噰闆嗗鎴?,
                progress=28,
                stats={"videos_found": total_rows},
            )
            sync_result = await douyin_tasks_from_search({"data": [first_item]})
            err = _douyin_result_error(sync_result)
            if err:
                raise RuntimeError(err)
            current_tasks = await douyin_get_tasks_lite()
            err = _douyin_result_error(current_tasks)
            if err:
                raise RuntimeError(err)
            lite_tasks = current_tasks.get("tasks") if isinstance(current_tasks.get("tasks"), list) else []
            matched_task = pick_task_from_lite_rows(lite_tasks, first_item)
            if not matched_task:
                raise RuntimeError("鎼滅储瀹屾垚鍚庢湭鑳藉畾浣嶅埌棣栦釜瑙嗛浠诲姟")
            task_id = int(matched_task.get("id", 0) or 0)
            if task_id <= 0:
                raise RuntimeError("鎼滅储瀹屾垚鍚庨涓棰戜换鍔＄己灏戞湁鏁?ID")
            await emit(
                "collect_start",
                f"姝ｅ湪閲囬泦銆妠str(matched_task.get('title') or first_item.get('title') or '绗?1 涓棰?)}銆嬬殑瀹㈡埛",
                progress=35,
                stats={"videos_found": total_rows, "selected_task_id": task_id},
            )
            start_result = await douyin_start_tasks(request={"selected_task_ids": [task_id], "collection_mode": "script"})
            err = _douyin_result_error(start_result)
            if err:
                raise RuntimeError(err)
            final = await poll_until_finished(
                status_func=douyin_get_tasks_lite,
                state_key="state",
                done_when=lambda current: not bool(current.get("running")),
                stats_builder=lambda current: {
                    **_douyin_stats_from_tasks(current.get("tasks") if isinstance(current.get("tasks"), list) else []),
                    "total": int(current.get("total", 0) or 0),
                },
                interval_seconds=5.0,
                max_seconds=7200.0,
            )
            final_tasks = final.get("tasks") if isinstance(final.get("tasks"), list) else []
            final_task = pick_task_from_lite_rows(final_tasks, first_item) or matched_task
            comments_collected = max(
                int((final_task or {}).get("comment_count", 0) or 0),
                len((final_task or {}).get("all_comments") or []) if isinstance((final_task or {}).get("all_comments"), list) else 0,
            )
            precise_count = max(
                int((final_task or {}).get("high_intent_count", 0) or 0),
                len((final_task or {}).get("high_intent_users") or []) if isinstance((final_task or {}).get("high_intent_users"), list) else 0,
            )
            result_payload = {
                "task_kind": "douyin_leads",
                "action": action,
                "search_mode": result.get("search_mode"),
                "account_id": result.get("account_id"),
                "total": total_rows,
                "items": rows[:20],
                "search_result": result,
                "search_videos_total": total_rows,
                "selected_video": {
                    "task_id": task_id,
                    "title": str((final_task or {}).get("title") or first_item.get("title") or "").strip(),
                    "url": str((final_task or {}).get("url") or first_item.get("url") or "").strip(),
                    "author": str((final_task or {}).get("author") or first_item.get("author") or "").strip(),
                },
                "stats": {
                    "tasks_total": 1,
                    "tasks_completed": 1 if str((final_task or {}).get("status", "") or "").strip().lower() == "completed" else 0,
                    "comments_collected": comments_collected,
                    "high_intent_users": precise_count,
                },
                "tasks": [final_task] if isinstance(final_task, dict) else [],
                "start_result": start_result,
                "final_status": final,
                "sync_result": sync_result,
                "raw_result": {
                    "search": result,
                    "sync": sync_result,
                    "start": start_result,
                    "final": final,
                },
            }
            await _complete_task_run(
                cloud,
                base,
                headers,
                run_id,
                result_text=f"鎼滅储瀹屾垚锛屾壘鍒?{total_rows} 涓棰戯紱宸查噰闆嗙 1 涓棰戠殑瀹㈡埛 {comments_collected} 浜猴紝绮惧噯瀹㈡埛 {precise_count} 浜恒€?,
                result_payload=result_payload,
            )
            return

        if action == "tasks_from_search":
            await emit("preparing", "姝ｅ湪鎶婃悳绱㈢粨鏋滃啓鍏ユ姈闊充换鍔℃睜", progress=5)
            result = await douyin_tasks_from_search(dict(params or {}))
            err = _douyin_result_error(result)
            if err:
                raise RuntimeError(err)
            await _complete_task_run(
                cloud,
                base,
                headers,
                run_id,
                result_text=_compact_douyin_message(result.get("msg"), "宸插悓姝ユ悳绱㈢粨鏋滃埌浠诲姟姹?),
                result_payload={
                    "task_kind": "douyin_leads",
                    "action": action,
                    "selected_total": int(result.get("selected_total", 0) or 0),
                    "total": int(result.get("total", 0) or 0),
                    "raw_result": result,
                },
            )
            return

        if action == "comment_collect":
            await emit("collect_start", "姝ｅ湪鍚姩璇勮閲囬泦", progress=5)
            start_result = await douyin_start_tasks(request=dict(params or {}))
            err = _douyin_result_error(start_result)
            if err:
                raise RuntimeError(err)
            final = await poll_until_finished(
                status_func=douyin_get_tasks_lite,
                state_key="state",
                done_when=lambda current: not bool(current.get("running")),
                stats_builder=lambda current: {
                    **_douyin_stats_from_tasks(current.get("tasks") if isinstance(current.get("tasks"), list) else []),
                    "total": int(current.get("total", 0) or 0),
                },
            )
            tasks = final.get("tasks") if isinstance(final.get("tasks"), list) else []
            stats = _douyin_stats_from_tasks(tasks)
            result_text = (
                f"璇勮閲囬泦瀹屾垚锛屽叡 {stats['tasks_total']} 鏉′换鍔★紝"
                f"瀹屾垚 {stats['tasks_completed']} 鏉★紝"
                f"楂樻剰鍚戝鎴?{stats['high_intent_users']} 浜恒€?
            )
            await _complete_task_run(
                cloud,
                base,
                headers,
                run_id,
                result_text=result_text,
                result_payload={
                    "task_kind": "douyin_leads",
                    "action": action,
                    "start_result": start_result,
                    "final_status": final,
                    "stats": stats,
                    "tasks": tasks[:50],
                },
            )
            return

        if action == "interaction":
            await emit("interaction_start", "姝ｅ湪鍚姩绮惧噯瀹㈡埛绉佷俊", progress=5)
            start_result = await douyin_start_interaction(request=dict(params or {}))
            err = _douyin_result_error(start_result)
            if err:
                raise RuntimeError(err)
            final = await poll_until_finished(
                status_func=lambda: douyin_interaction_status(lite=True, include_users=True),
                state_key="state",
                done_when=lambda current: not bool(current.get("running")),
                stats_builder=lambda current: {
                    **_douyin_stats_from_users(
                        current.get("users") if isinstance(current.get("users"), list) else [],
                        "interaction_status",
                        {"sent", "completed"},
                    ),
                    "processed": int(((current.get("state") if isinstance(current.get("state"), dict) else {}).get("processed", 0)) or 0),
                    "total": int(((current.get("state") if isinstance(current.get("state"), dict) else {}).get("total", 0)) or 0),
                },
            )
            users = final.get("users") if isinstance(final.get("users"), list) else []
            stats = _douyin_stats_from_users(users, "interaction_status", {"sent", "completed"})
            result_text = f"绉佷俊浜掑姩瀹屾垚锛屽叡 {stats['users_total']} 浜猴紝鎴愬姛 {stats['users_success']} 浜恒€?
            await _complete_task_run(
                cloud,
                base,
                headers,
                run_id,
                result_text=result_text,
                result_payload={
                    "task_kind": "douyin_leads",
                    "action": action,
                    "start_result": start_result,
                    "final_status": final,
                    "stats": stats,
                    "users": users[:100],
                },
            )
            return

        if action == "stranger_collect":
            await emit("stranger_collect_start", "姝ｅ湪閲囬泦闄岀敓浜虹淇?, progress=5)
            start_result = await douyin_collect_stranger_messages(request=dict(params or {}))
            err = _douyin_result_error(start_result)
            if err:
                raise RuntimeError(err)
            final = await poll_until_finished(
                status_func=douyin_stranger_message_status,
                state_key="state",
                done_when=lambda current: not bool(current.get("running")),
                stats_builder=lambda current: {
                    "processed": int(((current.get("state") if isinstance(current.get("state"), dict) else {}).get("processed", 0)) or 0),
                    "success": int(((current.get("state") if isinstance(current.get("state"), dict) else {}).get("success", 0)) or 0),
                    "failed": int(((current.get("state") if isinstance(current.get("state"), dict) else {}).get("failed", 0)) or 0),
                    "total": int(((current.get("state") if isinstance(current.get("state"), dict) else {}).get("total", 0)) or 0),
                },
            )
            results = final.get("results") if isinstance(final.get("results"), list) else []
            stats = {
                "total": len(results),
                "users_success": sum(1 for row in results if str((row or {}).get("reply_status") or "").strip().lower() in {"collected", "completed", "success"}),
                "users_failed": sum(1 for row in results if str((row or {}).get("reply_status") or "").strip().lower() == "failed"),
            }
            await _complete_task_run(
                cloud,
                base,
                headers,
                run_id,
                result_text=f"闄岀敓浜虹淇￠噰闆嗗畬鎴愶紝鍏?{len(results)} 鏉°€?,
                result_payload={
                    "task_kind": "douyin_leads",
                    "action": action,
                    "start_result": start_result,
                    "final_status": final,
                    "stats": stats,
                    "rows": results[:100],
                },
            )
            return

        if action == "stranger_send":
            await emit("stranger_send_start", "姝ｅ湪鍙戦€侀檶鐢熶汉绉佷俊", progress=5)
            start_result = await douyin_send_stranger_messages(request=dict(params or {}))
            err = _douyin_result_error(start_result)
            if err:
                raise RuntimeError(err)
            final = await poll_until_finished(
                status_func=douyin_stranger_message_status,
                state_key="state",
                done_when=lambda current: not bool(current.get("running")),
                stats_builder=lambda current: {
                    "processed": int(((current.get("state") if isinstance(current.get("state"), dict) else {}).get("processed", 0)) or 0),
                    "success": int(((current.get("state") if isinstance(current.get("state"), dict) else {}).get("success", 0)) or 0),
                    "failed": int(((current.get("state") if isinstance(current.get("state"), dict) else {}).get("failed", 0)) or 0),
                    "total": int(((current.get("state") if isinstance(current.get("state"), dict) else {}).get("total", 0)) or 0),
                },
            )
            rows = final.get("results") if isinstance(final.get("results"), list) else []
            stats = _douyin_stats_from_users(rows, "reply_status", {"sent", "completed", "success"})
            result_text = f"闄岀敓浜虹淇″彂閫佸畬鎴愶紝鍏?{stats['users_total']} 浜猴紝鎴愬姛 {stats['users_success']} 浜恒€?
            await _complete_task_run(
                cloud,
                base,
                headers,
                run_id,
                result_text=result_text,
                result_payload={
                    "task_kind": "douyin_leads",
                    "action": action,
                    "start_result": start_result,
                    "final_status": final,
                    "stats": stats,
                    "rows": rows[:100],
                },
            )
            return

        raise RuntimeError(f"鏆備笉鏀寔鐨?douyin_leads.action: {action}")
    except Exception as exc:
        logger.exception("[SCHEDULED-TASK] douyin leads failed run_id=%s action=%s", run_id, action)
        await _complete_task_run(
            cloud,
            base,
            headers,
            run_id,
            error=(str(exc).strip() or exc.__class__.__name__)[:500],
            result_payload={"task_kind": "douyin_leads", "action": action},
        )


async def _invoke_hifly_cloud_tts(
    *,
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    cap_payload: Dict[str, Any],
) -> Dict[str, Any]:
    body = {
        "title": str(cap_payload.get("title") or "鏁板瓧浜哄彛鎾?)[:128],
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
        raise RuntimeError("HiFly 鏈繑鍥?task_id")
    poll_timeout = int(cap_payload.get("poll_timeout_seconds") or 2400)
    interval = max(3, int(cap_payload.get("poll_interval_seconds") or 10))
    poll_request_timeout = httpx.Timeout(90.0, connect=10.0, read=90.0, write=30.0, pool=10.0)
    waited = 0
    last: Dict[str, Any] = {"ok": True, "task_id": task_id, "status": 2, "status_text": "鐢熸垚涓?}
    while waited <= poll_timeout:
        try:
            poll_resp = await cloud.post(
                f"{base}/api/hifly/my/video/task",
                json={"task_id": task_id},
                headers=headers,
                timeout=poll_request_timeout,
            )
        except httpx.TimeoutException:
            last = {"ok": True, "task_id": task_id, "status": 2, "status_text": "鏌ヨ瓒呮椂锛岀户缁瓑寰呯敓鎴愮粨鏋?}
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
            raise RuntimeError(str(last.get("message") or last.get("detail") or "HiFly 浠诲姟澶辫触")[:500])
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
            {"text": "灏嗗厛鐢?AI 鐢熸垚棣栧抚鍥剧墖锛屽啀鐢ㄨ鍥剧墖鐢熸垚瑙嗛"},
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
            raise RuntimeError("琛ュ彂瑙嗛缂哄皯鍙敤鐨勯甯у浘鐗?)
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
        {"text": f"宸蹭粠澶囬€夌粍鈥渰picked['group_name']}鈥濊疆鎹㈤€夋嫨鍥剧墖绱犳潗 {picked['asset_id']}"},
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
        {"text": "姝ｅ湪鎵ц gtp鍒涙剰鎴愮墖锛氳剼鏈鍒掋€侀甯х敓鎴愩€佽棰戠敓鎴?},
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
        raise RuntimeError("鏈瘑鍒埌褰撳墠鐢ㄦ埛锛屾棤娉曚繚瀛?PPT 绱犳潗")
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
        {"text": "姝ｅ湪鎵ц鏅鸿兘PPT锛氬ぇ绾茶鍒掋€丳PTX娓叉煋銆佷繚瀛樼礌鏉?},
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
                await _post_task_event(cloud, base, headers, run_id, "thinking", {"text": "姝ｅ湪浣跨敤鑷畾涔夋彁绀鸿瘝鐢熸垚鏈鍐呭"})
                generated = _generated_from_scheduled_prompt(capability_id, task_title, custom_prompt)
            else:
                await _post_task_event(cloud, base, headers, run_id, "thinking", {"text": "姝ｅ湪鏍规嵁璁板繂鐢熸垚鏈鍐呭"})
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
                    raise RuntimeError("璇烽€夋嫨鏁板瓧浜?)
                if not voice:
                    raise RuntimeError("璇烽€夋嫨澹伴煶")
                skill_prompt = _hifly_script_text(generated.get("script")) or _fallback_hifly_script(task_title)
                cap_payload = {
                    "title": (generated.get("title") or task_title or "鏁板瓧浜哄彛鎾?)[:20],
                    "avatar": avatar,
                    "voice": voice,
                    "text": skill_prompt,
                    "st_show": 1,
                    "aigc_flag": 0,
                    "poll_interval_seconds": 10,
                    "poll_timeout_seconds": 2400,
                }
            cap_payload = _inject_scheduled_assets_into_capability_payload(cap_payload, attachment_asset_ids)
            await _post_task_event(cloud, base, headers, run_id, "thinking", {"text": f"姝ｅ湪璋冪敤 {capability_id}"})
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
                raise RuntimeError(_goal_video_pipeline_pending_reason(result) or "鍒涙剰鎴愮墖瑙嗛浠嶆湭瀹屾垚锛屾湭鍙栧緱瑙嗛绱犳潗鎴栬棰戦摼鎺?)
            if capability_id == "create.video.pipeline" and not _create_video_pipeline_has_video_result(result):
                raise RuntimeError(_create_video_pipeline_pending_reason(result) or "gtp鍒涙剰鎴愮墖瑙嗛浠嶆湭瀹屾垚锛屾湭鍙栧緱瑙嗛绱犳潗鎴栬棰戦摼鎺?)
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
            await _post_task_event(cloud, base, headers, run_id, "thinking", {"text": "姝ｅ湪鐢熸垚鍙戝竷鏂囨"})
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
                    "platform_name": publish_cfg.get("platform_name") or _platform_publish_rules(str(publish_cfg.get("platform") or "")).split("锛?, 1)[0],
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
                    publish_draft["error"] = "鏈彇寰楀彲鍙戝竷绱犳潗 asset_id"
                elif publish_draft["auto_publish"]:
                    await _post_task_event(
                        cloud,
                        base,
                        headers,
                        run_id,
                        "thinking",
                        {"text": "姝ｅ湪鎸夋墍閫夊钩鍙拌嚜鍔ㄥ彂甯?},
                    )
                    try:
                        publish_result = await _submit_local_publish_draft(draft=publish_draft, headers=headers)
                        publish_draft["status"] = "published"
                        publish_draft["publish_result"] = publish_result
                    except Exception as exc:
                        logger.exception("[SCHEDULED-TASK] auto publish failed run_id=%s", run_id)
                        publish_draft["status"] = "failed"
                        publish_draft["error"] = str(exc)[:500] or "鑷姩鍙戝竷澶辫触"
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
                {"text": "鏈湴鎵ц涓?, "heartbeat": True},
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
                    json={"error": str(exc)[:500] or "鍙戝竷澶辫触", "publish_result": {}},
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
                    await client.post(
                        f"{base}/api/h5-chat/device-heartbeat",
                        json={"display_name": "local-online"},
                        headers=headers,
                    )
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
                    last_h5_poll_at = now_loop                    resp = await client.get(f"{base}/api/h5-chat/pending", params={"limit": h5_slots}, headers=headers)
                    if resp.status_code == 401:
                        logger.warning("[H5-CHAT] cloud auth rejected; waiting for next login token")
                        await asyncio.sleep(sleep_missing_auth)
                        continue
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

