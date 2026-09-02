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
import mimetypes
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func

from ..core.config import settings
from ..services.oem_brand_context import configured_brand_mark, with_oem_brand_header
from ..services.media_edit_exec import find_ffmpeg
from ..services.document_text_extractor import (
    SUPPORTED_SUFFIXES,
    document_parser_runtime_status,
    extract_document_text,
    require_document_parser_runtime,
)
from ..db import SessionLocal
from ..models import Asset, PublishAccount
from ..services import native_wechat_engine
from ..services.openclaw_channel_auth_store import clear_channel_fallback, read_channel_fallback
from .auth import _ServerUser, get_current_user_for_local
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


_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

logger = logging.getLogger(__name__)
router = APIRouter()
_BASE_DIR = Path(__file__).resolve().parents[3]
_DOUYIN_ORIGIN_DIR = _BASE_DIR / "backend" / "douyin_origin"
_RESULT_URL_RE = re.compile(r'https?://[^\s"\'<>\)\]]+', re.IGNORECASE)
_H5_CLIENT_COMMAND_PREFIX = "__LOBSTER_H5_CLIENT_COMMAND__"
_H5_CLIENT_BASE_CAPABILITIES = ("asset_video_split_v1",)
_CLIENT_PROCESS_ID = f"{os.getpid()}-{uuid.uuid4().hex}"
_SEEDANCE_TVC_CAPABILITY_ID = "comfly.seedance.tvc.pipeline"
_SEEDANCE_TVC_DEFAULT_MODEL = "grok-imagine-video-1.5-preview"
_SEEDANCE_TVC_DEFAULT_CHANNEL = "openmind"
_active_scheduled_run_ids: set[str] = set()
_active_scheduled_douyin_actions: Dict[str, str] = {}
_active_client_workflow_actions: Dict[str, str] = {}
_scheduled_douyin_precise_touch_claim_lock = asyncio.Lock()
_SCHEDULED_DOUYIN_IDLE_POLL_SECONDS = 2.0
_SCHEDULED_DOUYIN_STOP_SETTLE_TIMEOUT_SECONDS = 20.0
_WORKFLOW_NODE_STOP_TIMEOUT_SECONDS = 8.0
_WORKFLOW_NODE_CANCEL_GRACE_SECONDS = 8.0
_NATIVE_WECHAT_BUSY_RETRY_SECONDS = 45.0
_NATIVE_WECHAT_BUSY_RETRY_INTERVAL_SECONDS = 1.0
_SCHEDULED_COMPLETE_RETRY_STATUS = {500, 502, 503, 504}
_SCHEDULED_TASK_TRANSIENT_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_SCHEDULED_TASK_EVENT_TIMEOUT_SECONDS = 12.0
_SCHEDULED_TASK_EVENT_ATTEMPTS = 3
_SCHEDULED_TASK_COMPLETION_ATTEMPTS = 5
_SCHEDULED_TASK_COMPLETION_RETRY_DELAY_SECONDS = 2.0
_SCHEDULED_TASK_COMPLETION_RETRY_SECONDS = 6 * 60 * 60
_pending_task_completion_run_ids: set[str] = set()
_pending_task_completion_tasks: set[asyncio.Task] = set()
_MOBILE_UPLOAD_TITLE = "【手机上传素材】"
_MOBILE_UPLOAD_BLOCK_RE = re.compile(r"\n*【手机上传素材】\n(?P<body>[\s\S]*)", re.IGNORECASE)


def _h5_client_capabilities() -> list[str]:
    capabilities = list(_H5_CLIENT_BASE_CAPABILITIES)
    ready, _missing = document_parser_runtime_status()
    if ready:
        capabilities.append("memory_document_parse_v1")
        capabilities.append("memory_document_generate_v1")
    return capabilities


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
_IMAGE_STUDIO_REFERENCE_HINTS = {
    "person": "参考图{n}是唯一目标人物身份参考。生成结果中的主要人物必须替换为参考图{n}里的人物，优先保持脸型、五官比例、发型、气质、肤色和服装核心特征；不要沿用提示词里的默认人物长相。",
    "product": "参考图{n}是目标产品，请将画面中的主体产品替换为参考图{n}的产品，保持外观、包装、颜色、材质、标签布局和品牌识别特征。",
    "style": "参考图{n}只作为风格参考，请学习它的色彩、光线、构图和质感，不要复制其中的具体人物或产品。",
    "background": "参考图{n}是背景参考，请使用类似场景、空间氛围、光线和环境结构。",
    "local_edit": "参考图{n}用于局部修改，请优先保持原图主体一致，只修改提示词明确要求的区域。",
    "auto": "参考图{n}是普通参考图，请结合它的主体、风格或构图进行生成。",
}
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
    h = with_oem_brand_header({"Authorization": f"Bearer {jwt_token}"})
    if installation_id:
        h["X-Installation-Id"] = installation_id
    h["X-Client-Process-Id"] = _CLIENT_PROCESS_ID
    h["X-Lobster-Chat-Turn-Billing"] = "pre_deduct_v1"
    return h


async def _sync_openclaw_memory_for_context(jwt_token: str, installation_id: str, reason: str = "") -> None:
    uid = int(_decode_jwt_sub(jwt_token) or "0")
    if uid <= 0 or not installation_id:
        return
    try:
        from .openclaw_memory import sync_openclaw_memory_from_cloud

        raw_headers = [
            (b"authorization", f"Bearer {jwt_token}".encode("utf-8")),
            (b"x-installation-id", installation_id.encode("utf-8")),
            (b"x-lobster-brand", configured_brand_mark().encode("utf-8")),
        ]
        req = Request({"type": "http", "method": "POST", "path": "/api/openclaw/memory/sync-cloud", "headers": raw_headers})
        result = await sync_openclaw_memory_from_cloud(req, _ServerUser(id=uid), raise_errors=False)
        if result.get("ok"):
            logger.debug(
                "[OPENCLAW-MEMORY] synced before %s applied=%s deleted=%s remote=%s",
                reason or "task",
                result.get("applied_count"),
                result.get("deleted_count"),
                result.get("remote_count"),
            )
        elif result.get("error") or result.get("skipped"):
            logger.debug("[OPENCLAW-MEMORY] sync skipped before %s: %s", reason or "task", result)
    except Exception as exc:
        logger.warning("[OPENCLAW-MEMORY] sync failed before %s: %s", reason or "task", exc)


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


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    num = _safe_int(value)
    if num <= 0:
        num = default
    return max(minimum, min(num, maximum))


def _workflow_flag(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _parse_utc_datetime(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _workflow_minutes_between(start: Any, end: Any, *, default: int = 30) -> int:
    start_text = str(start or "").strip()
    end_text = str(end or "").strip()
    start_match = _TIME_RE.match(start_text)
    end_match = _TIME_RE.match(end_text)
    if not start_match or not end_match:
        return max(1, int(default))
    start_total = int(start_match.group(1)) * 60 + int(start_match.group(2))
    end_total = int(end_match.group(1)) * 60 + int(end_match.group(2))
    if end_total < start_total:
        end_total += 24 * 60
    return max(1, end_total - start_total)


_WORKFLOW_NODE_DEADLINE_KEYS = (
    "workflow_node_deadline_at",
    "workflow_node_deadline_utc",
    "node_deadline_at",
    "deadline_at",
)


def _workflow_node_deadline_utc(item: Any, *, now: Optional[datetime] = None) -> Optional[datetime]:
    """Return a workflow node's absolute UTC end time from a claimed run.

    New servers may provide an absolute deadline. Older queued runs only carry
    a local start/end pair, so derive the same absolute deadline from the run's
    creation date and workflow timezone. Using ``created_at`` is important:
    a run claimed late must keep the original node window rather than receiving
    a new full window from its late claim time.
    """
    source = item if isinstance(item, dict) else {}
    payload = source.get("payload") if isinstance(source.get("payload"), dict) else {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    h5_context = payload.get("h5_context") if isinstance(payload.get("h5_context"), dict) else {}
    param_context = params.get("h5_context") if isinstance(params.get("h5_context"), dict) else {}
    schedule_config = payload.get("schedule_config") if isinstance(payload.get("schedule_config"), dict) else {}
    param_schedule_config = params.get("schedule_config") if isinstance(params.get("schedule_config"), dict) else {}

    if not str(
        h5_context.get("workflow_node_id")
        or param_context.get("workflow_node_id")
        or source.get("workflow_node_id")
        or ""
    ).strip():
        return None

    # Prefer a server-calculated absolute cutoff when it is available. It
    # avoids timezone ambiguity for runs created by a different client build.
    for candidate in (source, payload, h5_context, params, param_context, schedule_config, param_schedule_config):
        for key in _WORKFLOW_NODE_DEADLINE_KEYS:
            deadline = _parse_utc_datetime(candidate.get(key))
            if deadline is not None:
                return deadline

    def _first_value(*keys: str) -> str:
        for candidate in (h5_context, param_context, params, payload):
            for key in keys:
                value = str(candidate.get(key) or "").strip()
                if value:
                    return value
        return ""

    start_text = _first_value("workflow_node_time", "sales_schedule_start", "start_time", "time")
    end_text = _first_value("workflow_node_end_time", "sales_schedule_end", "end_time")
    start_match = _TIME_RE.match(start_text)
    end_match = _TIME_RE.match(end_text)
    if not start_match or not end_match:
        return None

    timezone_offset_minutes = 480
    for candidate in (schedule_config, param_schedule_config, h5_context, param_context, params, payload):
        if candidate.get("timezone_offset_minutes") is None:
            continue
        try:
            timezone_offset_minutes = int(candidate.get("timezone_offset_minutes"))
        except (TypeError, ValueError):
            pass
        break
    timezone_offset_minutes = max(-720, min(840, timezone_offset_minutes))

    reference: Optional[datetime] = None
    for value in (
        source.get("created_at"),
        payload.get("created_at"),
        source.get("claimed_at"),
        source.get("started_at"),
    ):
        reference = _parse_utc_datetime(value)
        if reference is not None:
            break
    if reference is None:
        reference = now or datetime.now(timezone.utc)
    else:
        reference = reference.astimezone(timezone.utc)

    local_reference = reference + timedelta(minutes=timezone_offset_minutes)
    start_hour, start_minute = int(start_match.group(1)), int(start_match.group(2))
    end_hour, end_minute = int(end_match.group(1)), int(end_match.group(2))
    local_end = datetime(
        local_reference.year,
        local_reference.month,
        local_reference.day,
        end_hour,
        end_minute,
    )
    if (end_hour, end_minute) < (start_hour, start_minute):
        local_end += timedelta(days=1)
    return (local_end - timedelta(minutes=timezone_offset_minutes)).replace(tzinfo=timezone.utc)


def _workflow_node_remaining_seconds(item: Any, *, now: Optional[datetime] = None) -> Optional[float]:
    deadline = _workflow_node_deadline_utc(item, now=now)
    if deadline is None:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (deadline - current.astimezone(timezone.utc)).total_seconds()


def _local_bestseller_workflow_day(
    source: Dict[str, Any],
    days: int,
    *,
    now: Optional[datetime] = None,
) -> int:
    days = max(1, days)
    mode = str(source.get("day_mode") or "").strip().lower()
    explicit = _safe_int(source.get("day"))
    legacy_elapsed = mode == "activation_selected" and explicit > 0
    if mode != "workflow_elapsed" and not legacy_elapsed and explicit > 0:
        return max(1, min(explicit, days))
    start_day = _safe_int(source.get("start_day"))
    if start_day <= 0 and legacy_elapsed:
        # Older employee activations stored the selected starting day in
        # ``day``. Treat it as the start only for that legacy workflow mode;
        # one-off demo tasks with an explicit day remain fixed above.
        start_day = explicit
    start_day = max(1, min(start_day or 1, days))
    ctx = source.get("h5_context") if isinstance(source.get("h5_context"), dict) else {}
    schedule = source.get("schedule_config") if isinstance(source.get("schedule_config"), dict) else {}
    tz_offset = _safe_int(schedule.get("timezone_offset_minutes") or ctx.get("timezone_offset_minutes") or 480)
    start = _parse_utc_datetime(ctx.get("workflow_day_start") or ctx.get("workflow_started_at") or source.get("workflow_started_at"))
    if not start:
        return start_day
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    today_local = (current.astimezone(timezone.utc) + timedelta(minutes=tz_offset)).date()
    start_local = (start + timedelta(minutes=tz_offset)).date()
    elapsed = max(0, (today_local - start_local).days)
    return ((start_day - 1 + elapsed) % days) + 1


def _local_bestseller_missing_fields(source: Dict[str, Any], profile: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    raw = source.get("missing_profile_fields")
    if isinstance(raw, list):
        out.extend([str(item or "").strip() for item in raw if str(item or "").strip()])
    checks = [
        ("gender", "性别"),
        ("identity", "你是做什么的"),
        ("industry", "业务/产品或主要分享内容"),
        ("province", "现居省份"),
        ("city", "现居城市"),
        ("hometown", "籍贯"),
        ("age_label", "出生年代"),
        ("target_age", "想卖给谁/目标客户"),
        ("style", "视频风格"),
    ]
    for key, label in checks:
        if not str(profile.get(key) or "").strip():
            out.append(label)
    if not (str(profile.get("photo_asset_id") or "").strip() or str(profile.get("photo_url") or "").strip()):
        out.append("人物照片")
    seen: set[str] = set()
    unique: List[str] = []
    for item in out:
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _today_date_text() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _build_publish_account_snapshot(jwt_token: str) -> List[Dict[str, Any]]:
    user_id = int(_decode_jwt_sub(jwt_token) or "0")
    if user_id <= 0:
        return []
    try:
        from .publish import SUPPORTED_PLATFORMS, _douyin_origin_publish_accounts, _is_douyin_origin_publish_account
    except Exception as exc:
        logger.debug("[H5-CHAT] publish account snapshot import failed: %s", exc)
        return []
    db = SessionLocal()
    try:
        rows = (
            db.query(PublishAccount)
            .filter(PublishAccount.user_id == user_id)
            .order_by(PublishAccount.created_at.desc())
            .all()
        )
        display_rows = _douyin_origin_publish_accounts(user_id)
        display_rows.extend([row for row in rows if not _is_douyin_origin_publish_account(row)])
        accounts: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for row in display_rows:
            account_id = str(getattr(row, "id", "") or "").strip()
            platform = str(getattr(row, "platform", "") or "").strip()
            nickname = str(getattr(row, "nickname", "") or "").strip()
            if not account_id or not platform or not nickname:
                continue
            key = f"{platform}:{account_id}"
            if key in seen:
                continue
            seen.add(key)
            accounts.append(
                {
                    "id": account_id,
                    "account_id": account_id,
                    "platform": platform,
                    "platform_name": SUPPORTED_PLATFORMS.get(platform, {}).get("name", platform),
                    "nickname": nickname,
                    "status": str(getattr(row, "status", "") or "").strip(),
                    "online": str(getattr(row, "status", "") or "").strip().lower() in {"active", "online", "logged_in"},
                    "managed_by": "douyin_origin" if platform == "douyin" and _is_douyin_origin_publish_account(row) else "",
                    "is_origin_slot": bool(platform == "douyin" and _is_douyin_origin_publish_account(row)),
                }
            )
            if len(accounts) >= 200:
                break
        return accounts
    except Exception as exc:
        logger.debug("[H5-CHAT] build publish account snapshot failed: %s", exc)
        return []
    finally:
        db.close()


def _build_native_wechat_contact_snapshot() -> List[Dict[str, str]]:
    try:
        rows = native_wechat_engine.list_contacts(
            native_wechat_engine.LOCAL_DEFAULT_ACCOUNT_ID,
            limit=500,
            offset=0,
        ).get("items") or []
    except Exception as exc:
        logger.debug("[H5-CHAT] native WeChat contact snapshot failed: %s", exc)
        return []
    contacts: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        display_name = str(item.get("display_name") or item.get("remark") or item.get("wx_no") or "").strip()
        target = str(item.get("wx_no") or item.get("remark") or item.get("display_name") or item.get("contact_key") or "").strip()
        contact_key = str(item.get("contact_key") or item.get("wx_no") or target).strip()
        if not target or target.lower() in seen:
            continue
        seen.add(target.lower())
        contacts.append(
            {
                "value": target[:240],
                "name": (display_name or target)[:240],
                "contact_key": contact_key[:240],
                "remark": str(item.get("remark") or "").strip()[:240],
                "wx_no": str(item.get("wx_no") or "").strip()[:240],
            }
        )
    return contacts


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


@router.get("/api/h5-chat/devices/status", summary="Proxy cloud Online device status for local UI")
async def proxy_h5_chat_devices_status(
    request: Request,
    _current_user: Any = Depends(get_current_user_for_local),
) -> Dict[str, Any]:
    return await _proxy_cloud_json(
        request,
        "GET",
        "/api/h5-chat/devices/status",
    )


@router.post("/api/h5-chat/device-heartbeat/refresh", summary="Immediately refresh local Online device presence")
async def refresh_h5_chat_device_heartbeat(
    request: Request,
    _current_user: Any = Depends(get_current_user_for_local),
) -> Dict[str, Any]:
    """Send a fresh presence snapshot before UI actions that need a device.

    The background channel normally sends this every 30 seconds.  A direct
    refresh closes the small race where the UI asks for status between two
    heartbeats (or immediately after the process resumes from a busy task).
    """
    jwt_token, installation_id = _auth_context()
    request_token = _request_bearer_token(request)
    if request_token:
        jwt_token = request_token
    request_installation = (request.headers.get("X-Installation-Id") or "").strip()
    if request_installation:
        installation_id = request_installation
    if not jwt_token or not installation_id:
        raise HTTPException(status_code=401, detail="Missing bearer token or installation id")
    payload = {
        "display_name": "local-online",
        "publish_accounts": _build_publish_account_snapshot(jwt_token),
        "wechat_contacts": _build_native_wechat_contact_snapshot(),
        "capabilities": _h5_client_capabilities(),
    }
    return await _proxy_cloud_json(
        request,
        "POST",
        "/api/h5-chat/device-heartbeat",
        json_body=payload,
        timeout_sec=10.0,
    )


@router.get("/api/scheduled-tasks/runs", summary="Proxy cloud scheduled task runs for local online UI")
async def proxy_scheduled_task_runs(
    request: Request,
    limit: int = Query(80, ge=1, le=200),
    _current_user: Any = Depends(get_current_user_for_local),
) -> Dict[str, Any]:
    # Preserve the filters used by the Online task badge.  Dropping
    # ``active_only``/``installation_id`` makes the cloud return historical
    # rows, so a currently-running task can fall outside the first page and
    # disappear from the local UI.
    params: Dict[str, Any] = {"limit": limit}
    for key in (
        "offset",
        "compact",
        "date",
        "timezone_offset_minutes",
        "installation_id",
        "active_only",
    ):
        value = request.query_params.get(key)
        if value is not None and str(value).strip() != "":
            params[key] = value
    return await _proxy_cloud_json(
        request,
        "GET",
        "/api/scheduled-tasks/runs",
        params=params,
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


@router.post("/api/scheduled-tasks/runs/{run_id}/cancel", summary="Stop a scheduled task run and its local worker")
async def proxy_cancel_scheduled_task_run(
    run_id: str,
    request: Request,
    _current_user: Any = Depends(get_current_user_for_local),
) -> Dict[str, Any]:
    detail = await _proxy_cloud_json(
        request,
        "GET",
        f"/api/scheduled-tasks/runs/{run_id}",
    )
    run = detail.get("run") if isinstance(detail.get("run"), dict) else {}
    result = await _proxy_cloud_json(
        request,
        "POST",
        f"/api/scheduled-tasks/runs/{run_id}/cancel",
    )
    status_before_cancel = str(run.get("status") or "").strip().lower()
    if run and status_before_cancel not in {"success", "completed", "failed", "cancelled", "canceled"}:
        try:
            result["local_stop"] = await _stop_workflow_node_with_timeout(
                run,
                headers=_cloud_headers_from_request(request),
            )
        except Exception as exc:
            logger.warning("[SCHEDULED-TASK] local stop failed run_id=%s: %s", run_id, exc)
            result["local_stop"] = {
                "stop_requested": False,
                "reason": "local_stop_failed",
                "error": str(exc)[:300],
            }
    return result


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
    if custom_prompt and not _goal_video_memory_doc_ids(cap_payload) and not (isinstance(existing_plan, dict) and existing_plan.get("video_prompt")):
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
) -> int:
    try:
        response = await client.post(
            f"{base}/api/h5-chat/messages/{message_id}/event",
            json={"type": event_type, "payload": payload or {}},
            headers=headers,
        )
        return int(response.status_code)
    except Exception as exc:
        logger.debug("[H5-CHAT] post event failed message_id=%s type=%s: %s", message_id, event_type, exc)
        return 0


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
    response = await client.post(
        f"{base}/api/h5-chat/messages/{message_id}/complete",
        json={"reply_text": reply_text, "error": error, "payload": payload or {}},
        headers=headers,
    )
    response.raise_for_status()


async def _post_task_event(
    client: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    run_id: str,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
) -> int:
    body = {"type": event_type, "payload": payload or {}}
    return await _post_task_control_request(
        client,
        f"{base}/api/scheduled-tasks/runs/{run_id}/event",
        body,
        headers,
        label=f"event:{event_type}",
    )


async def _post_task_control_request(
    client: httpx.AsyncClient,
    url: str,
    body: Dict[str, Any],
    headers: Dict[str, str],
    *,
    label: str,
    attempts: int = _SCHEDULED_TASK_EVENT_ATTEMPTS,
) -> int:
    """Post task control data with a short timeout.

    The client HTTP session is also used by long-running local jobs and can
    have a multi-hour read timeout. Task events must never inherit that timeout:
    a DNS outage should make one event unavailable, not suspend the worker's
    heartbeat coroutine for the entire watchdog window.
    """
    attempts = max(1, int(attempts or 1))
    timeout = httpx.Timeout(
        _SCHEDULED_TASK_EVENT_TIMEOUT_SECONDS,
        connect=3.0,
        read=_SCHEDULED_TASK_EVENT_TIMEOUT_SECONDS,
        write=3.0,
        pool=3.0,
    )
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            response = await client.post(url, json=body, headers=headers, timeout=timeout)
            status = int(response.status_code)
            if status in _SCHEDULED_TASK_TRANSIENT_STATUS and attempt < attempts:
                await asyncio.sleep(0.25 * (2 ** (attempt - 1)))
                continue
            if status in _SCHEDULED_TASK_TRANSIENT_STATUS:
                logger.warning("[SCHEDULED-TASK] %s unavailable status=%s attempts=%s", label, status, attempts)
            return status
        except httpx.RequestError as exc:
            last_error = exc
            if attempt < attempts:
                await asyncio.sleep(0.25 * (2 ** (attempt - 1)))
                continue
        except Exception as exc:
            last_error = exc
            break
    logger.warning(
        "[SCHEDULED-TASK] %s unavailable after %s attempts error=%s",
        label,
        attempts,
        str(last_error)[:240] if last_error else "unknown",
    )
    return 0


def _task_event_rejects_local_work(status_code: Any) -> bool:
    try:
        return int(status_code or 0) in {401, 403, 404, 409}
    except (TypeError, ValueError):
        return False


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
    body = {"result_text": result_text, "result_payload": result_payload or {}, "error": error}
    status = await _post_task_control_request(
        client,
        f"{base}/api/scheduled-tasks/runs/{run_id}/complete",
        body,
        headers,
        label="completion",
        attempts=_SCHEDULED_TASK_COMPLETION_ATTEMPTS,
    )
    if status in {200, 201, 202, 204, 409}:
        return
    if status == 0 or status in _SCHEDULED_TASK_TRANSIENT_STATUS:
        # Completion is the one event that cannot be reconstructed from a
        # later heartbeat. Keep retrying in a separate task after the local
        # workflow returns, so a remote outage cannot abort the local worker.
        _queue_task_completion_retry(base, headers, run_id, body)
        return
    raise RuntimeError(f"scheduled task completion rejected HTTP {status}")


def _queue_task_completion_retry(
    base: str,
    headers: Dict[str, str],
    run_id: str,
    body: Dict[str, Any],
) -> None:
    clean_run_id = str(run_id or "").strip()
    if not clean_run_id or clean_run_id in _pending_task_completion_run_ids:
        return
    _pending_task_completion_run_ids.add(clean_run_id)
    task = asyncio.create_task(_retry_task_completion(base, headers, clean_run_id, body))
    _pending_task_completion_tasks.add(task)

    def _done(completed: asyncio.Task) -> None:
        _pending_task_completion_tasks.discard(completed)
        _pending_task_completion_run_ids.discard(clean_run_id)
        try:
            completed.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("[SCHEDULED-TASK] completion retry stopped run_id=%s error=%s", clean_run_id, str(exc)[:240])

    task.add_done_callback(_done)


async def _retry_task_completion(
    base: str,
    headers: Dict[str, str],
    run_id: str,
    body: Dict[str, Any],
) -> None:
    deadline = asyncio.get_running_loop().time() + _SCHEDULED_TASK_COMPLETION_RETRY_SECONDS
    delay = _SCHEDULED_TASK_COMPLETION_RETRY_DELAY_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        timeout = httpx.Timeout(
            _SCHEDULED_TASK_EVENT_TIMEOUT_SECONDS,
            connect=3.0,
            read=_SCHEDULED_TASK_EVENT_TIMEOUT_SECONDS,
            write=3.0,
            pool=3.0,
        )
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as retry_client:
                status = await _post_task_control_request(
                    retry_client,
                    f"{base}/api/scheduled-tasks/runs/{run_id}/complete",
                    body,
                    headers,
                    label="completion-retry",
                    attempts=2,
                )
            if status in {200, 201, 202, 204, 409}:
                logger.info("[SCHEDULED-TASK] completion retry succeeded run_id=%s", run_id)
                return
            if status not in _SCHEDULED_TASK_TRANSIENT_STATUS and status != 0:
                logger.warning("[SCHEDULED-TASK] completion retry rejected run_id=%s status=%s", run_id, status)
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("[SCHEDULED-TASK] completion retry error run_id=%s: %s", run_id, str(exc)[:240])
        await asyncio.sleep(min(delay, max(0.5, deadline - asyncio.get_running_loop().time())))
        delay = min(60.0, delay * 1.8)


def _local_chat_url() -> str:
    port = int(getattr(settings, "port", 8000) or 8000)
    return f"http://127.0.0.1:{port}/chat/stream"


def _local_api_url(path: str) -> str:
    port = int(getattr(settings, "port", 8000) or 8000)
    suffix = str(path or "").strip()
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    return f"http://127.0.0.1:{port}{suffix}"


def _local_api_unavailable_message(path: str, exc: Exception) -> str:
    port = int(getattr(settings, "port", 8000) or 8000)
    return f"本机微信接管服务不可达（127.0.0.1:{port}），无法访问 {path}：{exc}"


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


def _h5_client_command_payload(content: str) -> Optional[Dict[str, Any]]:
    raw = str(content or "").strip()
    if not raw.startswith(_H5_CLIENT_COMMAND_PREFIX):
        return None
    body = raw[len(_H5_CLIENT_COMMAND_PREFIX) :].strip()
    if not body:
        return {}
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _split_online_video_file(
    source_path: Path,
    output_dir: Path,
    *,
    segment_seconds: int,
    max_segments: int,
) -> List[Path]:
    ffmpeg = find_ffmpeg()
    seconds = max(2, min(int(segment_seconds or 3), 10))
    segment_limit = max(1, min(int(max_segments or 120), 120))
    output_dir.mkdir(parents=True, exist_ok=True)
    output_pattern = output_dir / "segment_%03d.mp4"
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-t",
        str(seconds * segment_limit),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-force_key_frames",
        f"expr:gte(t,n_forced*{seconds})",
        "-f",
        "segment",
        "-segment_time",
        str(seconds),
        "-reset_timestamps",
        "1",
        str(output_pattern),
    ]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=3600,
            check=False,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("本机视频切片超过 60 分钟，已停止处理") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "ffmpeg segment failed").strip()[-1000:]
        raise RuntimeError(f"本机视频切片失败：{detail}")
    segments = sorted(output_dir.glob("segment_*.mp4"))[:segment_limit]
    segments = [path for path in segments if path.is_file() and path.stat().st_size > 0]
    if not segments:
        raise RuntimeError("本机视频切片失败：没有生成可用片段")
    return segments


async def _download_online_split_source(source_url: str, target: Path) -> int:
    if not source_url.startswith(("http://", "https://")):
        raise RuntimeError("切片原视频缺少可下载的公网地址")
    max_bytes = 4 * 1024 * 1024 * 1024
    timeout = httpx.Timeout(1800.0, connect=20.0, read=180.0, write=30.0, pool=20.0)
    total = 0
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
        async with client.stream("GET", source_url) as response:
            response.raise_for_status()
            content_length = int(response.headers.get("content-length") or 0)
            if content_length > max_bytes:
                raise RuntimeError("原视频超过 4GB，无法在本机执行切片")
            with target.open("wb") as output:
                async for chunk in response.aiter_bytes(1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise RuntimeError("原视频超过 4GB，无法在本机执行切片")
                    output.write(chunk)
    if total <= 0:
        raise RuntimeError("下载到的原视频为空")
    return total


async def _upload_online_split_segment(
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    path: Path,
    *,
    source_filename: str,
    split_job_id: str,
    segment_index: int,
) -> Dict[str, Any]:
    with path.open("rb") as stream:
        response = await cloud.post(
            f"{base}/api/assets/upload",
            headers=headers,
            data={
                "split_video": "false",
                "source_upload_filename": source_filename,
                "video_segment": "true",
                "segment_index": str(segment_index),
                "split_job_id": split_job_id,
            },
            files={"file": (path.name, stream, "video/mp4")},
        )
    if response.status_code >= 400:
        detail = (response.text or "")[:500]
        raise RuntimeError(f"第 {segment_index} 段回传素材库失败：HTTP {response.status_code} {detail}")
    payload = response.json() if response.content else {}
    if not isinstance(payload, dict) or not payload.get("asset_id"):
        raise RuntimeError(f"第 {segment_index} 段回传后未返回 asset_id")
    return payload


async def _run_online_video_split_command(
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    message_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    source_asset_id = str(payload.get("source_asset_id") or "").strip()
    source_url = str(payload.get("source_url") or "").strip()
    source_filename = Path(str(payload.get("source_filename") or "source.mp4")).name
    segment_seconds = max(2, min(int(payload.get("segment_seconds") or 3), 10))
    max_segments = max(1, min(int(payload.get("max_segments") or 120), 120))
    if not source_asset_id or not source_url:
        raise RuntimeError("Online 切片指令缺少原视频信息")

    suffix = Path(source_filename).suffix.lower()
    if suffix not in {".mp4", ".webm", ".mov", ".avi", ".mkv", ".flv", ".wmv"}:
        suffix = ".mp4"
    uploaded: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="lobster_h5_video_split_") as temp_name:
        work_dir = Path(temp_name)
        source_path = work_dir / f"source{suffix}"
        await _post_cloud_event(
            cloud,
            base,
            headers,
            message_id,
            "progress",
            {"text": "Online 正在下载原视频", "stage": "download"},
        )
        download_task = asyncio.create_task(_download_online_split_source(source_url, source_path))
        while not download_task.done():
            done, _pending = await asyncio.wait({download_task}, timeout=120.0)
            if done:
                break
            await _post_cloud_event(
                cloud,
                base,
                headers,
                message_id,
                "progress",
                {"text": "Online 仍在下载原视频", "stage": "download", "heartbeat": True},
            )
        source_size = await download_task
        await _post_cloud_event(
            cloud,
            base,
            headers,
            message_id,
            "progress",
            {"text": "Online 正在本机切片", "stage": "split", "source_size": source_size},
        )
        split_task = asyncio.create_task(
            asyncio.to_thread(
                _split_online_video_file,
                source_path,
                work_dir / "segments",
                segment_seconds=segment_seconds,
                max_segments=max_segments,
            )
        )
        while not split_task.done():
            done, _pending = await asyncio.wait({split_task}, timeout=120.0)
            if done:
                break
            await _post_cloud_event(
                cloud,
                base,
                headers,
                message_id,
                "progress",
                {"text": "Online 仍在本机切片", "stage": "split", "heartbeat": True},
            )
        segments = await split_task
        try:
            for index, segment_path in enumerate(segments, start=1):
                uploaded.append(
                    await _upload_online_split_segment(
                        cloud,
                        base,
                        headers,
                        segment_path,
                        source_filename=source_filename,
                        split_job_id=message_id,
                        segment_index=index,
                    )
                )
                await _post_cloud_event(
                    cloud,
                    base,
                    headers,
                    message_id,
                    "progress",
                    {
                        "text": f"正在回传切片 {index}/{len(segments)}",
                        "stage": "upload",
                        "current": index,
                        "total": len(segments),
                    },
                )
        except Exception:
            for item in uploaded:
                if item.get("deduplicated"):
                    continue
                asset_id = str(item.get("asset_id") or "").strip()
                if asset_id:
                    try:
                        await cloud.delete(
                            f"{base}/api/assets/{asset_id}/online-split-segment",
                            headers=headers,
                        )
                    except Exception:
                        pass
            raise

    return {
        "mode": "client_command",
        "action": "split_uploaded_video_asset",
        "source_asset_id": source_asset_id,
        "segment_seconds": segment_seconds,
        "total": len(uploaded),
        "assets": uploaded,
    }


async def _cleanup_online_split_source(
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    source_asset_id: str,
) -> None:
    try:
        cleanup = await cloud.delete(
            f"{base}/api/assets/{source_asset_id}/online-split-source",
            headers=headers,
        )
        if cleanup.status_code >= 400:
            logger.warning(
                "[H5-CHAT] online split source cleanup failed asset_id=%s HTTP=%s body=%s",
                source_asset_id,
                cleanup.status_code,
                (cleanup.text or "")[:300],
            )
    except Exception as exc:
        logger.warning("[H5-CHAT] online split source cleanup failed asset_id=%s: %s", source_asset_id, exc)


async def _download_online_memory_source(source_url: str, target: Path) -> bytes:
    if not source_url.startswith(("http://", "https://")):
        raise RuntimeError("资料解析原文件缺少可下载的公网地址")
    max_bytes = 30 * 1024 * 1024
    timeout = httpx.Timeout(300.0, connect=20.0, read=120.0, write=30.0, pool=20.0)
    chunks: List[bytes] = []
    total = 0
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
        async with client.stream("GET", source_url) as response:
            response.raise_for_status()
            content_length = int(response.headers.get("content-length") or 0)
            if content_length > max_bytes:
                raise RuntimeError("资料文件超过 30MB，请压缩或拆分后上传")
            async for chunk in response.aiter_bytes(1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError("资料文件超过 30MB，请压缩或拆分后上传")
                chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise RuntimeError("下载到的资料文件为空")
    target.write_bytes(data)
    return data


async def _cleanup_online_memory_source(
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    source_asset_id: str,
) -> None:
    if not source_asset_id:
        return
    try:
        response = await cloud.delete(
            f"{base}/api/personal-settings/memory-documents/online-upload-source/{source_asset_id}",
            headers=headers,
        )
        if response.status_code >= 400:
            logger.warning(
                "[H5-CHAT] online memory source cleanup failed asset_id=%s HTTP=%s body=%s",
                source_asset_id,
                response.status_code,
                (response.text or "")[:300],
            )
    except Exception as exc:
        logger.warning("[H5-CHAT] online memory source cleanup failed asset_id=%s: %s", source_asset_id, exc)


async def _run_online_memory_parse_command(
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    message_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    source_asset_id = str(payload.get("source_asset_id") or "").strip()
    source_url = str(payload.get("source_url") or "").strip()
    source_filename = Path(str(payload.get("source_filename") or "document.txt")).name
    suffix = Path(source_filename).suffix.lower()
    if not source_asset_id or not source_url:
        raise RuntimeError("Online 资料解析指令缺少原文件信息")
    if suffix not in SUPPORTED_SUFFIXES:
        raise RuntimeError(f"Online 不支持解析 {suffix or '该'} 格式")
    with tempfile.TemporaryDirectory(prefix="lobster_h5_memory_parse_") as temp_name:
        source_path = Path(temp_name) / f"source{suffix}"
        await _post_cloud_event(
            cloud,
            base,
            headers,
            message_id,
            "progress",
            {"text": "Online 正在下载资料", "stage": "download"},
        )
        data = await _download_online_memory_source(source_url, source_path)
        await _post_cloud_event(
            cloud,
            base,
            headers,
            message_id,
            "progress",
            {"text": "Online 正在本机解析资料", "stage": "parse", "file_size": len(data)},
        )
        parse_task = asyncio.create_task(asyncio.to_thread(extract_document_text, data, source_filename))
        while not parse_task.done():
            done, _pending = await asyncio.wait({parse_task}, timeout=120.0)
            if done:
                break
            await _post_cloud_event(
                cloud,
                base,
                headers,
                message_id,
                "progress",
                {"text": "Online 仍在本机解析资料", "stage": "parse", "heartbeat": True},
            )
        content_text = await parse_task
    callback = await cloud.post(
        f"{base}/api/personal-settings/memory-documents/complete-online-upload",
        headers=headers,
        json={
            "message_id": message_id,
            "source_asset_id": source_asset_id,
            "filename": source_filename,
            "title": str(payload.get("title") or source_filename),
            "notes": str(payload.get("notes") or "IP人设定位上传资料"),
            "content_text": content_text,
            "sha256": hashlib.sha256(data).hexdigest(),
        },
    )
    if callback.status_code >= 400:
        raise RuntimeError(f"资料解析结果回写失败：HTTP {callback.status_code} {(callback.text or '')[:300]}")
    result = callback.json() if callback.content else {}
    return {
        "mode": "client_command",
        "action": "parse_uploaded_memory_document",
        "source_asset_id": source_asset_id,
        "filename": source_filename,
        "document": result.get("document") if isinstance(result, dict) else None,
    }


async def _run_online_memory_generation_command(
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    message_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    raw_sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    sources = [item for item in raw_sources[:12] if isinstance(item, dict)]
    if not sources:
        raise RuntimeError("Online 批量资料理解指令没有原文件")
    parsed_sources: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="lobster_h5_memory_generate_") as temp_name:
        work_dir = Path(temp_name)
        for index, item in enumerate(sources, start=1):
            asset_id = str(item.get("source_asset_id") or "").strip()
            source_url = str(item.get("source_url") or "").strip()
            filename = Path(str(item.get("source_filename") or f"document-{index}.txt")).name
            suffix = Path(filename).suffix.lower()
            if not asset_id or not source_url:
                raise RuntimeError(f"第 {index} 份资料缺少原文件信息")
            if suffix not in SUPPORTED_SUFFIXES:
                raise RuntimeError(f"Online 不支持解析 {filename}")
            source_path = work_dir / f"source_{index:02d}{suffix}"
            await _post_cloud_event(
                cloud,
                base,
                headers,
                message_id,
                "progress",
                {
                    "text": f"Online 正在处理资料 {index}/{len(sources)}：{filename}",
                    "stage": "parse",
                    "current": index,
                    "total": len(sources),
                },
            )
            data = await _download_online_memory_source(source_url, source_path)
            content_text = await asyncio.to_thread(extract_document_text, data, filename)
            parsed_sources.append(
                {
                    "source_asset_id": asset_id,
                    "filename": filename,
                    "content_text": content_text,
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
    await _post_cloud_event(
        cloud,
        base,
        headers,
        message_id,
        "progress",
        {"text": "资料解析完成，正在生成可审核内容", "stage": "generate"},
    )
    callback = await cloud.post(
        f"{base}/api/personal-settings/memory-documents/complete-online-generation-upload",
        headers=headers,
        json={"message_id": message_id, "sources": parsed_sources},
    )
    if callback.status_code >= 400:
        raise RuntimeError(f"资料理解结果回写失败：HTTP {callback.status_code} {(callback.text or '')[:300]}")
    result = callback.json() if callback.content else {}
    return {
        "mode": "client_command",
        "action": "generate_memory_documents_from_upload",
        "source_asset_ids": [item["source_asset_id"] for item in parsed_sources],
        "documents": result.get("documents") if isinstance(result, dict) else {},
        "doc_types": result.get("doc_types") if isinstance(result, dict) else [],
        "source_images": result.get("source_images") if isinstance(result, dict) else [],
        "file_results": result.get("file_results") if isinstance(result, dict) else [],
    }


async def _run_client_command(
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    jwt_token: str,
    installation_id: str,
    item: Dict[str, Any],
) -> None:
    message_id = str(item.get("id") or "").strip()
    payload = _h5_client_command_payload(str(item.get("content") or ""))
    action = str((payload or {}).get("action") or "").strip()
    if not message_id:
        return
    if not payload:
        await _complete_cloud_message(cloud, base, headers, message_id, error="invalid client command")
        return
    event_status = await _post_cloud_event(
        cloud,
        base,
        headers,
        message_id,
        "thinking",
        {"text": f"正在执行客户端命令：{action}"},
    )
    if _task_event_rejects_local_work(event_status):
        return
    split_result_ready = False
    memory_result_ready = False
    try:
        if action == "native_wechat_auto_reply_config":
            account_id = str(payload.get("account_id") or "pc-wechat-default").strip() or "pc-wechat-default"
            interval_seconds = 15
            user_id = int(_decode_jwt_sub(jwt_token) or "0") or None
            cfg = native_wechat_engine.save_auto_reply_config(
                account_id,
                enabled=(bool(payload.get("enabled")) if "enabled" in payload else None),
                interval_seconds=interval_seconds,
                user_id=user_id,
                language=(
                    str(payload.get("language") or payload.get("target_language") or "").strip()
                    if ("language" in payload or "target_language" in payload)
                    else None
                ),
                memory_doc_ids=(
                    [str(item or "").strip() for item in payload.get("memory_doc_ids") if str(item or "").strip()]
                    if isinstance(payload.get("memory_doc_ids"), list)
                    else None
                ),
                group_invite_enabled=(
                    bool(payload.get("group_invite_enabled"))
                    if "group_invite_enabled" in payload
                    else None
                ),
                group_invite_memory_doc_id=(
                    str(payload.get("group_invite_memory_doc_id") or "").strip()
                    if "group_invite_memory_doc_id" in payload
                    else None
                ),
                group_invite_keywords=(
                    str(payload.get("group_invite_keywords") or "").strip()
                    if "group_invite_keywords" in payload
                    else None
                ),
                group_invite_contacts=(
                    [str(item or "").strip() for item in payload.get("group_invite_contacts") if str(item or "").strip()]
                    if isinstance(payload.get("group_invite_contacts"), list)
                    else None
                ),
                group_invite_primary_contact=(
                    str(payload.get("group_invite_primary_contact") or "").strip()
                    if "group_invite_primary_contact" in payload
                    else None
                ),
                group_invite_primary_contact_name=(
                    str(payload.get("group_invite_primary_contact_name") or "").strip()
                    if "group_invite_primary_contact_name" in payload
                    else None
                ),
                group_invite_welcome_message=(
                    str(payload.get("group_invite_welcome_message") or "").strip()
                    if "group_invite_welcome_message" in payload
                    else None
                ),
                auth_context={
                    "token": jwt_token,
                    "user_id": user_id,
                    "installation_id": installation_id,
                },
            )
            await _complete_cloud_message(
                cloud,
                base,
                headers,
                message_id,
                reply_text="personal WeChat auto-reply config updated",
                payload={"mode": "client_command", "action": action, "config": cfg},
            )
            return
        if action == "split_uploaded_video_asset":
            result = await _run_online_video_split_command(cloud, base, headers, message_id, payload)
            split_result_ready = True
            await _complete_cloud_message(
                cloud,
                base,
                headers,
                message_id,
                reply_text=f"视频切片完成，共生成 {result['total']} 段",
                payload=result,
            )
            await _cleanup_online_split_source(
                cloud,
                base,
                headers,
                str(result.get("source_asset_id") or ""),
            )
            return
        if action == "parse_uploaded_memory_document":
            require_document_parser_runtime(refresh=True)
            result = await _run_online_memory_parse_command(cloud, base, headers, message_id, payload)
            memory_result_ready = True
            await _complete_cloud_message(
                cloud,
                base,
                headers,
                message_id,
                reply_text=f"资料解析完成：{result['filename']}",
                payload=result,
            )
            await _cleanup_online_memory_source(
                cloud,
                base,
                headers,
                str(result.get("source_asset_id") or ""),
            )
            return
        if action == "generate_memory_documents_from_upload":
            require_document_parser_runtime(refresh=True)
            result = await _run_online_memory_generation_command(cloud, base, headers, message_id, payload)
            memory_result_ready = True
            await _complete_cloud_message(
                cloud,
                base,
                headers,
                message_id,
                reply_text="资料理解完成，请审核生成内容",
                payload=result,
            )
            for source_asset_id in result.get("source_asset_ids") or []:
                await _cleanup_online_memory_source(cloud, base, headers, str(source_asset_id or ""))
            return
        raise RuntimeError(f"unsupported client command: {action or '-'}")
    except Exception as exc:
        if action == "split_uploaded_video_asset" and not split_result_ready:
            source_asset_id = str((payload or {}).get("source_asset_id") or "").strip()
            if source_asset_id:
                await _cleanup_online_split_source(cloud, base, headers, source_asset_id)
        if action in {"parse_uploaded_memory_document", "generate_memory_documents_from_upload"} and not memory_result_ready:
            source_asset_ids = [str((payload or {}).get("source_asset_id") or "").strip()]
            if action == "generate_memory_documents_from_upload":
                source_asset_ids = [
                    str(item.get("source_asset_id") or "").strip()
                    for item in ((payload or {}).get("sources") or [])
                    if isinstance(item, dict)
                ]
            for source_asset_id in source_asset_ids:
                await _cleanup_online_memory_source(cloud, base, headers, source_asset_id)
        logger.exception("[H5-CHAT] client command failed message_id=%s action=%s", message_id, action)
        await _complete_cloud_message(cloud, base, headers, message_id, error=str(exc)[:500] or "client command failed")


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

    event_status = await _post_cloud_event(
        cloud,
        base,
        headers,
        message_id,
        "thinking",
        {"text": "本地直连链路正在处理"},
    )
    if _task_event_rejects_local_work(event_status):
        return
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
                    event_status = await _post_cloud_event(cloud, base, headers, message_id, et[:32], ev)
                    if _task_event_rejects_local_work(event_status):
                        raise RuntimeError("message cancelled because this installation logged into another account")
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
    if str(item.get("mode") or "").strip() == "client_command" or _h5_client_command_payload(str(item.get("content") or "")):
        await _run_client_command(client, base, headers, jwt_token, installation_id, item)
        return
    await _sync_openclaw_memory_for_context(jwt_token, installation_id, "h5-message")
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
    event_status = await _post_task_event(
        cloud,
        base,
        headers,
        run_id,
        "thinking",
        {"text": "local-online claimed scheduled task"},
    )
    if _task_event_rejects_local_work(event_status):
        return
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
                result_payload={"mode": "chat_message"},
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
                    event_status = await _post_task_event(cloud, base, headers, run_id, et[:32], ev)
                    if _task_event_rejects_local_work(event_status):
                        raise RuntimeError("task cancelled because this installation logged into another account")
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


def _scheduled_tvc_completion(result: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    job = result.get("result") if isinstance(result.get("result"), dict) else result
    if not isinstance(job, dict):
        job = {}
    pipeline_result = job.get("result") if isinstance(job.get("result"), dict) else {}
    final_video = pipeline_result.get("final_video") if isinstance(pipeline_result.get("final_video"), dict) else {}
    saved_assets = job.get("saved_assets") if isinstance(job.get("saved_assets"), list) else []
    urls: List[str] = []
    asset_ids: List[str] = []

    def add_url(value: Any) -> None:
        url = str(value or "").strip()
        if url.startswith(("http://", "https://")) and url not in urls:
            urls.append(url)

    add_url(final_video.get("url"))
    for item in saved_assets:
        if not isinstance(item, dict):
            continue
        asset = item.get("asset") if isinstance(item.get("asset"), dict) else {}
        add_url(
            item.get("source_url")
            or item.get("url")
            or item.get("public_url")
            or asset.get("source_url")
            or asset.get("url")
            or asset.get("public_url")
        )
        asset_id = str(item.get("asset_id") or asset.get("asset_id") or asset.get("id") or "").strip()
        if asset_id and asset_id not in asset_ids:
            asset_ids.append(asset_id)

    if urls:
        result_text = "爆款TVC已生成，点击查看成片。"
    elif asset_ids:
        result_text = "爆款TVC已生成，成片正在同步，请稍后刷新查看。"
    else:
        result_text = "爆款TVC已生成。"
    payload = {
        "capability_id": "comfly.daihuo.pipeline",
        "mcp_result": result,
        "saved_assets": saved_assets,
        "media_urls": urls,
        "result_refs": {
            "urls": urls,
            "asset_ids": asset_ids,
            "saved_assets": saved_assets,
        },
    }
    return result_text, payload


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


def _goal_video_memory_doc_ids(payload: Dict[str, Any], limit: int = 8) -> List[str]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("memory_doc_ids") or payload.get("memoryDocIds") or []
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        text = re.sub(r"[^A-Za-z0-9_-]", "", str(item or "").strip())[:80]
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _scheduled_goal_video_precomputed_plan(
    cap_payload: Dict[str, Any],
    generated: Dict[str, Any],
    task_title: str,
) -> Dict[str, Any]:
    existing = cap_payload.get("precomputed_plan") if isinstance(cap_payload, dict) else {}
    if isinstance(existing, dict) and existing.get("video_prompt"):
        return existing
    if _goal_video_memory_doc_ids(cap_payload):
        return {}
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
            "goal 要能直接传给创意成片能力，写明抖音、9:16、中文宣传视频及本次成片的切入角度、画面方向和核心短文案。"
            "不要自行写死视频时长；时长由任务配置原样传给生成管线。"
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
    account_id_raw = payload.get("publish_account_id") or payload.get("account_id")
    if account_id_raw in (None, ""):
        account_id_value: Any = None
    else:
        try:
            account_id_value = int(account_id_raw)
        except (TypeError, ValueError):
            account_id_value = str(account_id_raw).strip() or None
    account_nickname = str(payload.get("publish_account_nickname") or payload.get("account_nickname") or "").strip()
    installation_id = str(payload.get("publish_installation_id") or payload.get("installation_id") or "").strip()
    auto_publish = str(payload.get("publish_auto") or payload.get("auto_publish") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "是",
    } or payload.get("publish_auto") is True or payload.get("auto_publish") is True
    if not (platform or account_id_value or account_nickname or auto_publish):
        return {}
    return {
        "platform": platform,
        "platform_name": str(payload.get("publish_platform_name") or "").strip()
        or ("朋友圈图文" if _is_wechat_moments_platform(platform) else ""),
        "account_id": account_id_value,
        "account_nickname": account_nickname,
        "installation_id": installation_id,
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
    if p in {"wechat_moments", "wechat", "moments"}:
        return "朋友圈图文：标题可以留空，正文 30-120 字，更像朋友动态的口吻，少营销少口号；有图片就按图文发布，视频只传 1 条；标签 0-5 个即可。"
    if p in {"wechat_channels", "channels", "sph"}:
        return "视频号：短标题 10-16 字，只能使用中英文和数字，不要标点、特殊符号或 Emoji；描述 40-120 字，带 2-5 个话题标签。"
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
    source_script: str = "",
) -> Dict[str, str]:
    source_script = " ".join(str(source_script or "").split())[:6000]
    fallback_title = (str(generated.get("title") or task_title or "AI 创意内容").strip() or "AI 创意内容")[:30]
    fallback_desc = source_script or caption or _fallback_scheduled_caption(capability_id, generated)
    fallback_tags = _clean_publish_tags(generated.get("tags") or generated.get("keywords") or "")
    system = (
        "你是社交平台运营。只输出 JSON 对象，字段必须是 title、description、tags。"
        "不要 Markdown，不要解释。"
        + _platform_publish_rules(platform)
    )
    if source_script:
        system += (
            "\nThe source_oral_script is the primary factual source. Create platform copy from its actual topic and claims; "
            "do not infer facts from the filename or task title. Keep the output language consistent with the script. "
            "Do not invent prices, results, credentials, addresses, guarantees, or statistics."
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
                "source_oral_script": source_script,
                "skill_result_summary": _compact_result_text(result)[:1200],
                "result_refs": refs,
                "requirements": (
                    "标题、正文、标签要适合所选平台；不要编造不存在的优惠、价格或地址。"
                    " When source_oral_script is present, derive every field from that script."
                ),
            },
            temperature=0.55,
        )
        data = _extract_json_object_text(text)
        title = " ".join(str(data.get("title") or fallback_title).split())[:60]
        desc = str(data.get("description") or data.get("desc") or fallback_desc).strip()[:1200]
        tags = _clean_publish_tags(data.get("tags") or fallback_tags)
        final_title = "" if _is_wechat_moments_platform(platform) else (title or fallback_title)
        return {"title": final_title, "description": desc or fallback_desc, "tags": tags}
    except Exception as exc:
        logger.warning("[SCHEDULED-TASK] publish copy failed platform=%s: %s", platform, exc)
        final_title = "" if _is_wechat_moments_platform(platform) else fallback_title
        return {"title": final_title, "description": fallback_desc, "tags": fallback_tags}


def _extract_parent_publish_context(result_payload: Any) -> Dict[str, str]:
    if not isinstance(result_payload, dict):
        return {}
    generated = result_payload.get("generated") if isinstance(result_payload.get("generated"), dict) else {}
    local_result = result_payload.get("local_result") if isinstance(result_payload.get("local_result"), dict) else {}
    local_item = local_result.get("item") if isinstance(local_result.get("item"), dict) else {}
    video_result = local_result.get("video_result") if isinstance(local_result.get("video_result"), dict) else {}
    video_item = video_result.get("item") if isinstance(video_result.get("item"), dict) else {}
    record = generated.get("ip_daily_record") if isinstance(generated.get("ip_daily_record"), dict) else {}
    if not record and isinstance(local_result.get("ip_daily_record"), dict):
        record = local_result["ip_daily_record"]
    mcp_result = result_payload.get("mcp_result") if isinstance(result_payload.get("mcp_result"), dict) else {}

    def first_text(*values: Any, limit: int = 6000) -> str:
        for value in values:
            if isinstance(value, (dict, list)):
                continue
            text = " ".join(str(value or "").split())
            if text:
                return text[:limit]
        return ""

    script = first_text(
        generated.get("script"),
        generated.get("oral_script"),
        local_result.get("script"),
        local_result.get("oral_script"),
        local_item.get("subtitle_text"),
        video_item.get("subtitle_text"),
        record.get("body"),
        record.get("content"),
        record.get("script"),
        record.get("text"),
        result_payload.get("skill_prompt"),
        mcp_result.get("script"),
    )
    title = first_text(
        record.get("title"),
        generated.get("title"),
        local_result.get("title"),
        local_item.get("title"),
        video_item.get("title"),
        result_payload.get("title"),
        limit=160,
    )
    caption = first_text(
        result_payload.get("caption"),
        generated.get("caption_hint"),
        local_result.get("caption_hint"),
        limit=1200,
    )
    tags = _clean_publish_tags(
        record.get("tags")
        or record.get("keywords")
        or generated.get("tags")
        or generated.get("keywords")
        or ""
    )
    language = first_text(generated.get("language"), local_result.get("language"), record.get("language"), limit=64)
    capability_id = first_text(result_payload.get("capability_id"), limit=128)
    if not capability_id and str(local_result.get("action") or "").strip() == "shanjian_digital_human_video":
        capability_id = "hifly.video.create_by_tts"
    if not capability_id and str(local_result.get("mode") or "").strip() == "daily_video":
        capability_id = "local_bestseller_daily_video"
    return {
        "source_script": script,
        "source_title": title,
        "source_caption": caption,
        "source_tags": tags,
        "source_language": language,
        "source_capability_id": capability_id,
    }


def _is_wechat_moments_platform(value: Any) -> bool:
    return str(value or "").strip().lower() in {"wechat_moments", "wechat", "moments"}


def _is_wechat_channels_platform(value: Any) -> bool:
    return str(value or "").strip().lower() in {"wechat_channels", "channels", "sph"}


def _wechat_channels_short_title(value: Any, fallback: Any = "") -> str:
    raw = str(value or fallback or "").strip()
    chars: List[str] = []
    for char in raw:
        if char.isalnum():
            chars.append(char)
        elif char.isspace() and chars and chars[-1] != " ":
            chars.append(" ")
    return ("".join(chars).strip() or "作品分享")[:16]


def _should_forward_auth_for_download_url(url: str) -> bool:
    try:
        host = (urlparse(str(url or "")).hostname or "").strip().lower()
    except Exception:
        return False
    return host in {"127.0.0.1", "localhost", "::1"}


def _publish_moments_content_from_draft(draft: Dict[str, Any]) -> str:
    content = str(draft.get("content") or draft.get("text") or "").strip()
    title = str(draft.get("title") or "").strip()
    description = str(draft.get("description") or "").strip()
    tags = str(draft.get("tags") or "").strip()
    parts: List[str] = []
    if content:
        parts.append(content)
    else:
        if title and (not description or not description.startswith(title)):
            parts.append(title)
        if description:
            parts.append(description)
    if tags and tags not in "\n".join(parts):
        parts.append(tags)
    return "\n".join(part for part in parts if part).strip()


def _filename_from_url_for_moments(url: str, media_type: str = "") -> str:
    parsed = urlparse(str(url or ""))
    name = unquote((parsed.path or "").rsplit("/", 1)[-1]).strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if name and Path(name).suffix:
        return name[:140]
    suffix = {
        "image": ".jpg",
        "image_text": ".jpg",
        "video": ".mp4",
    }.get(str(media_type or "").lower(), "")
    return f"moments_asset_{uuid.uuid4().hex[:8]}{suffix or '.bin'}"


def _local_asset_to_native_wechat_attachment(asset_id: str) -> Optional[Dict[str, Any]]:
    aid = str(asset_id or "").strip()
    if not aid:
        return None
    db = SessionLocal()
    try:
        row = db.query(Asset).filter(Asset.asset_id == aid).first()
        if not row or not row.filename:
            return None
        source = (_BASE_DIR / "assets" / row.filename).resolve()
        if not source.exists() or not source.is_file():
            return None
        target = native_wechat_engine.make_native_wechat_upload_path(row.filename)
        shutil.copy2(source, target)
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        return {
            "local_path": str(target.resolve()),
            "filename": row.filename,
            "size": target.stat().st_size,
            "content_type": content_type,
            "kind": native_wechat_engine.native_wechat_file_kind(target, content_type),
            "asset_id": aid,
        }
    finally:
        db.close()


async def _download_url_to_native_wechat_attachment(
    url: str,
    *,
    filename: str = "",
    media_type: str = "",
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    clean_url = str(url or "").strip()
    if not clean_url:
        raise RuntimeError("朋友圈发布缺少素材 URL")
    target = native_wechat_engine.make_native_wechat_upload_path(filename or _filename_from_url_for_moments(clean_url, media_type))
    timeout = httpx.Timeout(600.0, connect=10.0, read=600.0, write=30.0, pool=10.0)
    req_headers = {"User-Agent": "Lobster-H5-Moments/1.0", "Accept": "*/*"}
    if _should_forward_auth_for_download_url(clean_url):
        auth = str((headers or {}).get("Authorization") or "").strip()
        xi = str((headers or {}).get("X-Installation-Id") or (headers or {}).get("x-installation-id") or "").strip()
        if auth:
            req_headers["Authorization"] = auth
        if xi:
            req_headers["X-Installation-Id"] = xi
    total = 0
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
            async with client.stream("GET", clean_url, headers=req_headers) as resp:
                if resp.status_code >= 400:
                    text = await resp.aread()
                    raise RuntimeError(f"朋友圈素材下载失败 HTTP {resp.status_code}: {text[:200]!r}")
                content_type = str(resp.headers.get("content-type") or mimetypes.guess_type(str(target))[0] or "application/octet-stream").split(";", 1)[0]
                with target.open("wb") as out:
                    async for chunk in resp.aiter_bytes(1024 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > native_wechat_engine.NATIVE_WECHAT_MAX_UPLOAD_BYTES:
                            raise RuntimeError("朋友圈素材文件过大")
                        out.write(chunk)
    except Exception:
        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    if total <= 0:
        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass
        raise RuntimeError("朋友圈素材下载为空")
    return {
        "local_path": str(target.resolve()),
        "filename": target.name,
        "size": total,
        "content_type": content_type,
        "kind": native_wechat_engine.native_wechat_file_kind(target, content_type),
        "source_url": clean_url,
    }


async def _wechat_moments_attachments_from_draft(
    draft: Dict[str, Any],
    headers: Dict[str, str],
) -> List[Dict[str, Any]]:
    files: List[Dict[str, Any]] = []
    raw_attachments = draft.get("attachments") if isinstance(draft.get("attachments"), list) else []
    for item in raw_attachments:
        if not isinstance(item, dict):
            continue
        local_path = str(item.get("local_path") or item.get("path") or "").strip()
        if local_path:
            files.append(item)
            continue
        attachment_asset_id = str(item.get("asset_id") or "").strip()
        attachment_url = str(item.get("source_url") or item.get("url") or "").strip()
        attachment_media_type = str(item.get("media_type") or item.get("kind") or draft.get("media_type") or "").strip()
        if attachment_asset_id:
            local = _local_asset_to_native_wechat_attachment(attachment_asset_id)
            if local:
                files.append(local)
                continue
        if attachment_url:
            files.append(
                await _download_url_to_native_wechat_attachment(
                    attachment_url,
                    filename=str(item.get("filename") or "").strip(),
                    media_type=attachment_media_type,
                    headers=headers,
                )
            )
    asset_id = str(draft.get("asset_id") or "").strip()
    media_type = str(draft.get("media_type") or "").strip()
    source_url = str(draft.get("source_url") or draft.get("url") or "").strip()
    if asset_id and not files:
        local = _local_asset_to_native_wechat_attachment(asset_id)
        if local:
            files.append(local)
    if source_url and not files:
        files.append(
            await _download_url_to_native_wechat_attachment(
                source_url,
                filename=str(draft.get("filename") or "").strip(),
                media_type=media_type,
                headers=headers,
            )
        )
    # Content-library drafts may carry several generated images without a legacy
    # attachments array. Resolve those references in order and keep the first
    # nine, matching the native WeChat image limit.
    def list_value(value: Any) -> List[Any]:
        if isinstance(value, list):
            return value
        return [value] if value else []

    existing_urls = {
        str(item.get("source_url") or "").strip()
        for item in files
        if isinstance(item, dict) and str(item.get("source_url") or "").strip()
    }
    existing_assets = {
        str(item.get("asset_id") or "").strip()
        for item in files
        if isinstance(item, dict) and str(item.get("asset_id") or "").strip()
    }
    image_urls = [str(value or "").strip() for value in list_value(draft.get("image_urls"))]
    image_asset_ids = [str(value or "").strip() for value in list_value(draft.get("image_asset_ids"))]
    for index in range(max(len(image_urls), len(image_asset_ids))):
        if len(files) >= 9:
            break
        asset_id = image_asset_ids[index] if index < len(image_asset_ids) else ""
        url = image_urls[index] if index < len(image_urls) else ""
        if asset_id and asset_id not in existing_assets:
            local = _local_asset_to_native_wechat_attachment(asset_id)
            if local:
                files.append(local)
                existing_assets.add(asset_id)
                continue
        if url and url not in existing_urls:
            files.append(
                await _download_url_to_native_wechat_attachment(
                    url,
                    filename=f"moments-{index + 1}.jpg",
                    media_type="image",
                    headers=headers,
                )
            )
            existing_urls.add(url)
    return files[:9]


async def _submit_local_publish_draft(
    *,
    draft: Dict[str, Any],
    headers: Dict[str, str],
) -> Dict[str, Any]:
    if _is_wechat_moments_platform(draft.get("platform")):
        content = _publish_moments_content_from_draft(draft)
        attachments = await _wechat_moments_attachments_from_draft(draft, headers)
        if not content and not attachments:
            raise RuntimeError("朋友圈发布缺少正文或素材")
        body = {
            "account_id": str(draft.get("account_id") or native_wechat_engine.LOCAL_DEFAULT_ACCOUNT_ID).strip() or native_wechat_engine.LOCAL_DEFAULT_ACCOUNT_ID,
            "content": content,
            "attachments": attachments,
            "media_type": str(draft.get("media_type") or ("video" if any(item.get("kind") == "video" for item in attachments) else "image_text")).strip() or "image_text",
            "visibility": str(draft.get("visibility") or "public").strip() or "public",
        }
        submitted = await _post_local_api_json(
            "/api/native-wechat/moments/publish",
            body,
            headers=headers,
            timeout_seconds=1200.0,
        )
        return await _wait_for_local_native_wechat_task_result(
            submitted,
            headers=headers,
            timeout_seconds=1200.0,
        )

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


def _local_native_wechat_publish_status(result: Dict[str, Any]) -> str:
    task = result.get("task") if isinstance(result.get("task"), dict) else {}
    return str(result.get("status") or result.get("state") or task.get("status") or "").strip().lower()


async def _wait_for_local_native_wechat_task_result(
    result: Dict[str, Any],
    *,
    headers: Dict[str, str],
    timeout_seconds: float = 1200.0,
    poll_interval_seconds: float = 2.0,
) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {"ok": False, "status": "failed", "error": "本机发布任务返回格式异常"}
    task = result.get("task") if isinstance(result.get("task"), dict) else {}
    task_id = str(task.get("id") or result.get("task_id") or "").strip()
    status = _local_native_wechat_publish_status(result)
    running_statuses = {"pending", "running", "processing", "queued", "waiting"}
    final_statuses = {"success", "failed", "partial_failed", "cancelled", "canceled", "timeout", "need_login", "login_required"}
    if not task_id or (status and status not in running_statuses):
        return result
    deadline = asyncio.get_running_loop().time() + max(timeout_seconds, poll_interval_seconds)
    latest = dict(result)
    while True:
        if status in final_statuses:
            latest["queued"] = False
            latest["status"] = status
            latest.setdefault("task_id", task_id)
            latest.setdefault("ok", status == "success")
            if latest.get("ok") is False and not str(latest.get("error") or "").strip():
                latest["error"] = str(task.get("error_message") or latest.get("message") or "本机发布任务执行失败").strip()
            return latest
        if asyncio.get_running_loop().time() >= deadline:
            latest["ok"] = False
            latest["queued"] = True
            latest["status"] = status or "timeout"
            latest["task_id"] = task_id
            latest["error"] = "发布仍在本机执行队列中，尚未返回最终成功结果"
            return latest
        await asyncio.sleep(max(0.5, poll_interval_seconds))
        try:
            detail = await _get_local_api_json(
                f"/api/native-wechat/tasks/{task_id}",
                headers=headers,
                timeout_seconds=max(10.0, min(30.0, poll_interval_seconds + 8.0)),
            )
        except Exception as exc:
            latest["ok"] = False
            latest["queued"] = True
            latest["status"] = status or "pending"
            latest["task_id"] = task_id
            latest["error"] = f"查询本机发布任务结果失败：{exc}"
            return latest
        task = detail.get("task") if isinstance(detail.get("task"), dict) else {}
        if task:
            latest = dict(task)
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            publish_result = payload.get("publish_result") if isinstance(payload.get("publish_result"), dict) else {}
            if publish_result:
                latest.update(publish_result)
            latest["task"] = task
            latest.setdefault("task_id", task_id)
        else:
            latest = dict(detail)
            latest.setdefault("task_id", task_id)
        status = _local_native_wechat_publish_status(latest)


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


_DOUYIN_WORKFLOW_TITLE_PATTERNS = [
    re.compile(pattern)
    for pattern in (
        r"(抖音|微信|视频号).*(接管|养号|自动加好友|自动拉群|朋友圈点赞|朋友圈发布)",
        r"(评论并@|@.*精准客户|回复精准客户评论|评论区接管)",
        r"(主动私信|私信接管|关注\d*个?精准客户|抓取精准客户)",
        r"同城爆款视频发布|数字人口播视频|创作一条",
    )
]


def _scheduled_douyin_keyword_values(value: Any) -> List[str]:
    raw_values = value if isinstance(value, list) else [value]
    out: List[str] = []
    seen: set[str] = set()
    for item in raw_values:
        for part in re.split(r"[，,、;；\r\n]+", str(item or "")):
            text = re.sub(r"\s+", " ", part).strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(text[:120])
    return out


def _scheduled_douyin_keyword_looks_like_workflow_title(value: Any) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in _DOUYIN_WORKFLOW_TITLE_PATTERNS)


def _scheduled_douyin_search_keywords(source: Dict[str, Any]) -> List[str]:
    # Node titles can leak into old keyword fields. Keep only real search terms.
    candidates: List[str] = []
    candidates.extend(_scheduled_douyin_keyword_values(source.get("keywords")))
    for key in ("keyword", "search_keyword", "query"):
        candidates.extend(_scheduled_douyin_keyword_values(source.get(key)))
    candidates.extend(_scheduled_douyin_keyword_values(source.get("prompt")))

    keywords: List[str] = []
    seen: set[str] = set()
    for keyword in candidates:
        if _scheduled_douyin_keyword_looks_like_workflow_title(keyword):
            continue
        normalized = keyword.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        keywords.append(keyword)
    return keywords


def _scheduled_douyin_search_keyword(source: Dict[str, Any]) -> str:
    keywords = _scheduled_douyin_search_keywords(source)
    return keywords[0] if keywords else ""


def _scheduled_douyin_sales_action_from_text(value: Any) -> str:
    text = str(value or "").strip()
    if "我的评论区" in text or "抖音我的评论区" in text:
        return "self_comment_monitor"
    if "精准用户触达" in text or "精准触达" in text:
        return "precise_touch"
    if "养号" in text:
        return "account_nurture"
    if "发布后采集" in text or "关键词抓取" in text:
        return "search_collect"
    if "回复" in text and "评论" in text:
        return "reply_comments"
    if "@精准" in text or "评论并@" in text or "自己评论区接管" in text:
        return "mention_comment"
    if "关注" in text and "评论" in text:
        return "follow_comment"
    if "主动私信" in text or "私信10" in text:
        return "direct_message"
    if "私信接管" in text or "私信引流" in text:
        return "stranger_message"
    return ""


def _scheduled_douyin_sales_action_from_context(context: Any) -> str:
    source = context if isinstance(context, dict) else {}
    for key in ("ability_label", "workflow_node_label", "sales_node_label", "title", "note"):
        action = _scheduled_douyin_sales_action_from_text(source.get(key))
        if action:
            return action
    return ""


def _scheduled_douyin_sales_action_from_payload(payload: Dict[str, Any], item: Dict[str, Any]) -> str:
    """Recover the sales action from saved task labels when an old node polluted keyword fields."""
    source = payload if isinstance(payload, dict) else {}
    params = source.get("params") if isinstance(source.get("params"), dict) else {}
    h5_context = source.get("h5_context") if isinstance(source.get("h5_context"), dict) else {}
    candidates: List[Any] = [
        h5_context.get("ability_label"),
        h5_context.get("workflow_node_label"),
        h5_context.get("sales_node_label"),
        source.get("title"),
        source.get("content"),
        item.get("title"),
        item.get("content"),
        item.get("name"),
        params.get("sales_node_label"),
        params.get("node_label"),
        params.get("note"),
        params.get("prompt"),
    ]
    for key in ("keyword", "search_keyword", "query", "keywords"):
        for keyword in _scheduled_douyin_keyword_values(params.get(key)):
            if _scheduled_douyin_keyword_looks_like_workflow_title(keyword):
                candidates.append(keyword)
    for candidate in candidates:
        action = _scheduled_douyin_sales_action_from_text(candidate)
        if action:
            return action
    return ""


def _scheduled_douyin_latest_config(rows: Any, *, row_type: str = "") -> Dict[str, Any]:
    candidates = [
        dict(row)
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict) and (not row_type or str(row.get("type") or "").strip() == row_type)
    ]
    if not candidates:
        return {}
    candidates.sort(
        key=lambda row: str(
            row.get("updated_at")
            or row.get("saved_at")
            or row.get("created_at")
            or row.get("id")
            or ""
        )
    )
    return candidates[-1]


def _scheduled_douyin_online_config_params(
    action: str,
    *,
    config: Optional[Dict[str, Any]] = None,
    plans: Any = None,
    search_sessions: Any = None,
    stranger_monitors: Any = None,
    self_comment_monitors: Any = None,
) -> Dict[str, Any]:
    """Build task parameters exclusively from Online's persisted Douyin settings."""
    action_key = str(action or "").strip().lower()
    local_config = config if isinstance(config, dict) else {}
    params: Dict[str, Any] = {}
    default_account_id = _safe_int(local_config.get("douyin_default_account_id"))
    if default_account_id > 0:
        params["account_id"] = default_account_id

    if action_key == "search_collect":
        plan = _scheduled_douyin_latest_config(plans, row_type="collect_precise")
        session = _scheduled_douyin_latest_config(search_sessions)
        keyword_sources: List[Any] = []
        collect_plans = [
            row for row in (plans if isinstance(plans, list) else [])
            if isinstance(row, dict)
            and str(row.get("type") or "").strip() == "collect_precise"
            and bool(row.get("enabled", True))
        ]
        collect_plans.sort(
            key=lambda row: str(row.get("updated_at") or row.get("created_at") or row.get("id") or ""),
            reverse=True,
        )
        sessions = [row for row in (search_sessions if isinstance(search_sessions, list) else []) if isinstance(row, dict)]
        sessions.sort(
            key=lambda row: str(row.get("updated_at") or row.get("created_at") or row.get("id") or ""),
            reverse=True,
        )
        keyword_sources.extend(row.get("keyword") for row in collect_plans)
        keyword_sources.extend(row.get("keyword") for row in sessions)
        keywords = _scheduled_douyin_search_keywords({"keywords": keyword_sources})
        if keywords:
            params["keywords"] = keywords
            params["keyword"] = keywords[0]
        else:
            keyword = str(plan.get("keyword") or session.get("keyword") or "").strip()
            if keyword:
                params["keyword"] = keyword
        params["max_results"] = _safe_int(plan.get("max_results") or 50) or 50
        # Keep H5 and Online aligned. Older plans may omit this field; that
        # must mean the normal per-run limit, not "only the first video".
        params["max_videos_per_run"] = _safe_int(plan.get("max_videos_per_run") or 50) or 50
        params["comment_scroll_rounds"] = _safe_int(
            plan.get("comment_scroll_rounds") or local_config.get("comment_scroll_rounds") or 300
        ) or 300
        params["comment_max_comments"] = _safe_int(
            plan.get("comment_max_comments") or local_config.get("comment_max_comments") or 500
        ) or 500
        params["mode"] = str(plan.get("mode") or "script").strip() or "script"
        has_reply_config = (
            _workflow_flag(plan.get("reply_precise_comments"), False)
            or str(plan.get("reply_comment_text") or "").strip()
            or str(plan.get("reply_comment_prompt") or "").strip()
            or str(plan.get("reply_comment_seed_text") or "").strip()
            or str(plan.get("reply_comment_mode") or "fixed").strip().lower() not in {"", "fixed"}
        )
        if has_reply_config:
            params["reply_precise_comments"] = _workflow_flag(plan.get("reply_precise_comments"), False)
            params["reply_comment_mode"] = str(plan.get("reply_comment_mode") or plan.get("comment_mode") or "fixed").strip().lower() or "fixed"
            if params["reply_comment_mode"] not in {"fixed", "ai", "rewrite"}:
                params["reply_comment_mode"] = "fixed"
            params["reply_comment_text"] = str(plan.get("reply_comment_text") or plan.get("comment_text") or "").strip()
            params["reply_comment_prompt"] = str(plan.get("reply_comment_prompt") or plan.get("comment_prompt") or "").strip()
            params["reply_comment_seed_text"] = str(plan.get("reply_comment_seed_text") or plan.get("comment_seed_text") or "").strip()
        return params

    if action_key in {"reply_comments", "mention_comment", "follow_comment"}:
        plan = _scheduled_douyin_latest_config(plans, row_type="follow_comment")
        if plan:
            params.update(
                {
                    "max_users": _safe_int(plan.get("max_users_per_run") or 10) or 10,
                    "comment_mode": str(plan.get("comment_mode") or "fixed").strip() or "fixed",
                    "comment_text": str(plan.get("comment_text") or "").strip(),
                    "comment_prompt": str(plan.get("comment_prompt") or "").strip(),
                    "comment_seed_text": str(plan.get("comment_seed_text") or "").strip(),
                    "interval_minutes_min": plan.get("follow_interval_minutes_min", 4),
                    "interval_minutes_max": plan.get("follow_interval_minutes_max", 6),
                }
            )
        return params

    if action_key == "direct_message":
        plan = _scheduled_douyin_latest_config(plans, row_type="interaction")
        if plan:
            message = str(plan.get("message") or "").strip()
            params.update(
                {
                    "max_users": _safe_int(plan.get("max_users_per_run") or 10) or 10,
                    "message_mode": str(plan.get("message_mode") or "fixed").strip() or "fixed",
                    "message": message,
                    "messages": [message] if message else [],
                    "message_prompt": str(plan.get("message_prompt") or "").strip(),
                    "message_seed_text": str(plan.get("message_seed_text") or "").strip(),
                    "interval_minutes_min": plan.get("interaction_interval_minutes_min", 4),
                    "interval_minutes_max": plan.get("interaction_interval_minutes_max", 6),
                }
            )
        return params

    if action_key == "stranger_message":
        monitors = [dict(row) for row in (stranger_monitors if isinstance(stranger_monitors, list) else []) if isinstance(row, dict)]
        monitor = next(
            (row for row in monitors if default_account_id > 0 and _safe_int(row.get("account_id")) == default_account_id),
            next((row for row in monitors if bool(row.get("enabled"))), monitors[0] if monitors else {}),
        )
        if monitor:
            params.update(
                {
                    "account_id": _safe_int(monitor.get("account_id") or default_account_id),
                    "interval_minutes": _safe_int(monitor.get("interval_minutes") or 30) or 30,
                    "max_users": _safe_int(monitor.get("max_conversations") or 100) or 100,
                    "auto_reply_enabled": bool(monitor.get("auto_reply_enabled", True)),
                    # H5 supports only its explicit fixed/AI-lead switch.
                    # Online's legacy ``ai_auto`` mode must not leak into a
                    # one-shot workflow and change its fixed-script behavior.
                    "reply_mode": (
                        "ai_lead"
                        if str(monitor.get("reply_mode") or "").strip().lower() == "ai_lead"
                        else "fixed"
                    ),
                    "message": str(monitor.get("reply_message") or "").strip(),
                    "reply_prompt": str(monitor.get("reply_prompt") or "").strip(),
                    "contact_value": str(monitor.get("contact_value") or "").strip(),
                    "wechat_add_friend_enabled": bool(monitor.get("wechat_add_friend_enabled", False)),
                }
            )
        return params

    if action_key == "self_comment_monitor":
        states = [
            dict(row)
            for row in (self_comment_monitors if isinstance(self_comment_monitors, list) else [])
            if isinstance(row, dict)
        ]
        target = next(
            (
                row
                for row in states
                if default_account_id > 0 and _safe_int(row.get("account_id")) == default_account_id
            ),
            next((row for row in states if bool(row.get("enabled"))), states[0] if states else {}),
        )
        if target:
            params.update(
                {
                    "account_id": _safe_int(target.get("account_id") or default_account_id),
                    "max_videos": _safe_int(target.get("max_videos") or 20) or 20,
                    "max_comments_per_video": _safe_int(target.get("max_comments_per_video") or 80) or 80,
                    "auto_reply_enabled": bool(target.get("auto_reply_enabled", False)),
                    "reply_mode": str(target.get("reply_mode") or "fixed").strip().lower() or "fixed",
                    "reply_message": str(target.get("reply_message") or "").strip(),
                    "reply_prompt": str(target.get("reply_prompt") or "").strip(),
                    "contact_value": str(target.get("contact_value") or "").strip(),
                    "reply_image_path": str(target.get("reply_image_path") or target.get("comment_image_path") or "").strip(),
                }
            )
        return params

    if action_key == "account_nurture":
        duration_min = max(5, _safe_int(local_config.get("douyin_nurture_session_min_minutes") or 20) or 20)
        duration_max = max(duration_min, _safe_int(local_config.get("douyin_nurture_session_max_minutes") or 40) or 40)
        params["nurture_duration_min"] = duration_min
        params["nurture_duration_max"] = duration_max
    return params


def _load_scheduled_douyin_online_config_params(action: str) -> Dict[str, Any]:
    try:
        _install_douyin_origin_import_path()
        from douyin_api import (  # type: ignore
            douyin_schedule_plans,
            list_douyin_self_comment_monitor_states,
            list_douyin_stranger_message_monitor_states,
            load_douyin_search_sessions_state,
            load_global_config,
            restore_douyin_stranger_message_monitor_config,
            restore_douyin_self_comment_monitor_config,
        )

        if str(action or "").strip().lower() == "stranger_message":
            # The Online process can import this module after the startup
            # restore hook has already run. Reload the persisted config before
            # a one-shot H5 task so the fixed reply is never lost from memory.
            restore_douyin_stranger_message_monitor_config()
        if str(action or "").strip().lower() == "self_comment_monitor":
            restore_douyin_self_comment_monitor_config()

        return _scheduled_douyin_online_config_params(
            action,
            config=load_global_config(),
            plans=douyin_schedule_plans,
            search_sessions=load_douyin_search_sessions_state(),
            stranger_monitors=list_douyin_stranger_message_monitor_states(),
            self_comment_monitors=list_douyin_self_comment_monitor_states(),
        )
    except Exception as exc:
        logger.warning("[SCHEDULED-TASK] load Online Douyin config failed action=%s error=%s", action, exc)
        return {}


def _merge_scheduled_douyin_stranger_params(
    online_params: Any,
    task_params: Any,
) -> Dict[str, Any]:
    """Use Online's saved stranger-message config for every one-shot task.

    H5 sends a small task payload and may include only the add-friend switch.
    Do not mistake that partial payload for a complete reply configuration.
    """
    online = dict(online_params) if isinstance(online_params, dict) else {}
    task = dict(task_params) if isinstance(task_params, dict) else {}
    merged = dict(online)
    for key in (
        "account_id",
        "max_conversations",
        "auto_reply_enabled",
        "reply_mode",
        "message",
        "reply_prompt",
        "contact_value",
        "wechat_add_friend_enabled",
    ):
        if key not in task:
            continue
        value = task.get(key)
        if key == "reply_mode":
            value = "ai_lead" if str(value or "").strip().lower() == "ai_lead" else "fixed"
        if isinstance(value, bool):
            merged[key] = value
        elif value not in (None, "", []):
            merged[key] = value
    return merged


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
        for key in (
            "summary",
            "message",
            "status",
            "account_id",
            "account_ids",
            "total",
            "stats",
            "final_state",
            "items",
            "tasks",
            "users",
            "conversations",
            "conversation_scope",
            "mode",
            "total_conversations",
            "processed_user_last",
            "skipped_last_message_not_user",
            "skipped_duplicate_reply",
            "normal_conversations",
            "stranger_conversations",
            "detail_read_failed",
            "detail_read_failed_count",
            "detail_failures",
            "reply",
            "extracted_phone_numbers",
            "wechat_add_targets",
            "phone_contacts_skipped",
            "wechat_add_friend",
        ):
            value = result.get(key)
            if value not in (None, "", []):
                payload[key] = value
    selected_ids = _scheduled_douyin_selected_task_ids(source)
    if isinstance(result, dict) and not selected_ids:
        selected_ids = _scheduled_douyin_selected_task_ids(result)
    if selected_ids:
        payload.update(_scheduled_douyin_task_snapshot(selected_ids))
    if isinstance(result, dict):
        for key in (
            "search_total",
            "session_id",
            "session_ids",
            "keyword",
            "keywords",
            "keyword_summaries",
            "search_mode",
        ):
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
    payload["stats"] = {
        "videos_found": _safe_int(payload.get("search_total")),
        "comments_collected": _safe_int(payload.get("total_customers")),
        "high_intent_users": _safe_int(payload.get("total_high_intent")),
    }
    payload["status"] = str(final_state.get("status") or "").strip()
    payload["final_state"] = {
        "status": str(final_state.get("status") or "").strip(),
        "tasks": normalized_tasks,
    }
    return payload


async def _run_scheduled_douyin_single_search_collect_action(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    source = params if isinstance(params, dict) else {}
    keyword = _scheduled_douyin_search_keyword(source)
    if not keyword:
        return {"code": 400, "msg": "缺少采集关键词：请先在 Online 的抖音获客排期或搜索工作台配置关键词。"}

    max_results = max(10, min(_safe_int(source.get("max_results") or 50) or 50, 100))
    # Collect every selected/new video up to the configured per-run limit.
    max_videos = max(
        1,
        min(_safe_int(source.get("max_videos_per_run") or source.get("max_videos") or 50) or 50, 50),
    )
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
    from douyin_api import configure_douyin_collection_tasks  # type: ignore
    from douyin_api import match_douyin_tasks_for_rows  # type: ignore
    from douyin_api import normalize_douyin_search_session_result  # type: ignore
    from douyin_api import select_douyin_search_result_keys  # type: ignore
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
    selected_item_keys = select_douyin_search_result_keys(normalized_results, max_videos)
    if selected_item_keys and not any(
        bool(item.get("export_selected", True))
        and str(item.get("source_item_key", "") or "").strip() in set(selected_item_keys)
        for item in normalized_results
        if isinstance(item, dict)
    ):
        logger.warning(
            "[SCHEDULED-TASK] douyin search_collect fallback to unfiltered search results keyword=%s keys=%s",
            keyword,
            selected_item_keys,
        )
    if not selected_item_keys:
        return {"code": 400, "msg": f"关键词“{keyword}”本次没有可用视频。"}

    selected_item_key_set = {key for key in selected_item_keys if key}
    session = await asyncio.to_thread(
        upsert_douyin_search_session_state,
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
            "last_message": f"已完成关键词“{keyword}”搜索，正在准备依次采集 {len(selected_item_keys)} 个视频的客户。",
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

    await asyncio.to_thread(set_tasks_from_rows, rows)
    matched_tasks = await asyncio.to_thread(match_douyin_tasks_for_rows, rows)
    await asyncio.to_thread(configure_douyin_collection_tasks, matched_tasks, source)
    task_ids = [int(task.get("id", 0) or 0) for task in matched_tasks if int(task.get("id", 0) or 0) > 0]
    if not task_ids:
        return {"code": 400, "msg": f"关键词“{keyword}”本次没有匹配到可执行任务。"}

    await asyncio.to_thread(
        upsert_douyin_search_session_state,
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
            "last_message": f"搜索完成，正在依次采集 {len(task_ids)} 个视频的客户。",
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
            "account_id": search_result.get("account_id", "") if isinstance(search_result, dict) else "",
            "selected_task_ids": task_ids,
            "selected_videos_total": len(task_ids),
            "selected_item_keys": selected_item_keys,
            "items": [dict(row) for row in normalized_results[:20]],
        }
    )
    if _safe_int(result.get("code")) == 200:
        actual_started = max(0, _safe_int(result.get("selected_count") or len(task_ids)))
        skipped_existing = max(0, len(task_ids) - actual_started)
        result["msg"] = (
            f"搜索完成，找到 {len(normalized_results)} 个视频；已开始依次采集 {actual_started} 个视频的客户。"
            + (f" 已跳过 {skipped_existing} 个已完成任务。" if skipped_existing else "")
        )
    return result


async def _run_scheduled_douyin_search_collect_action(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    source = dict(params) if isinstance(params, dict) else {}
    keywords = _scheduled_douyin_search_keywords(source)
    if len(keywords) <= 1:
        return await _run_scheduled_douyin_single_search_collect_action(source)

    keyword_summaries: List[Dict[str, Any]] = []
    successful_results: List[Dict[str, Any]] = []
    selected_task_ids: List[int] = []
    selected_item_keys: List[str] = []
    items: List[Dict[str, Any]] = []
    session_ids: List[str] = []
    account_ids: List[int] = []

    for index, keyword in enumerate(keywords, start=1):
        logger.info(
            "[SCHEDULED-TASK] douyin search_collect keyword %s/%s: %s",
            index,
            len(keywords),
            keyword,
        )
        keyword_params = dict(source)
        keyword_params["keywords"] = [keyword]
        keyword_params["keyword"] = keyword
        result = await _run_scheduled_douyin_single_search_collect_action(keyword_params)
        code = _safe_int(result.get("code") if isinstance(result, dict) else 0)
        summary = {
            "keyword": keyword,
            "code": code,
            "message": str((result or {}).get("msg") or (result or {}).get("message") or "").strip(),
            "search_total": _safe_int((result or {}).get("search_total")),
            "selected_videos_total": _safe_int((result or {}).get("selected_videos_total")),
        }
        keyword_summaries.append(summary)
        if code != 200:
            continue

        successful_results.append(result)
        keyword_task_ids = _scheduled_douyin_selected_task_ids(result)
        for task_id in keyword_task_ids:
            if task_id not in selected_task_ids:
                selected_task_ids.append(task_id)
        for item_key in result.get("selected_item_keys") or []:
            text_value = str(item_key or "").strip()
            if text_value and text_value not in selected_item_keys:
                selected_item_keys.append(text_value)
        for row in result.get("items") or []:
            if isinstance(row, dict):
                items.append(dict(row))
        session_id = str(result.get("session_id") or "").strip()
        if session_id and session_id not in session_ids:
            session_ids.append(session_id)
        for account_id in result.get("account_ids") or [result.get("account_id")]:
            normalized_account_id = _safe_int(account_id)
            if normalized_account_id > 0 and normalized_account_id not in account_ids:
                account_ids.append(normalized_account_id)

        # The collector is single-flight. Finish this keyword before starting
        # the next one, then return the complete batch to the workflow runner.
        if keyword_task_ids:
            final_state = await _wait_for_douyin_collect_completion(keyword_task_ids)
            summary["collection_status"] = str(final_state.get("status") or "").strip()

    if not successful_results:
        first = keyword_summaries[0] if keyword_summaries else {}
        return {
            "code": _safe_int(first.get("code")) or 400,
            "msg": str(first.get("message") or "No configured keyword produced usable videos."),
            "keywords": keywords,
            "keyword_summaries": keyword_summaries,
        }

    combined = dict(successful_results[0])
    combined.update(
        {
            "code": 200,
            "msg": (
                f"Completed {len(successful_results)}/{len(keywords)} configured keywords; "
                f"found {sum(_safe_int(row.get('search_total')) for row in successful_results)} videos "
                f"and started {len(selected_task_ids)} collection tasks."
            ),
            "keyword": keywords[0],
            "keywords": keywords,
            "keyword_summaries": keyword_summaries,
            "search_total": sum(_safe_int(row.get("search_total")) for row in successful_results),
            "selected_task_ids": selected_task_ids,
            "selected_videos_total": len(selected_task_ids),
            "selected_item_keys": selected_item_keys,
            "items": items[:100],
            "session_ids": session_ids,
            "session_id": session_ids[0] if session_ids else "",
            "account_ids": account_ids,
            "account_id": account_ids[0] if account_ids else combined.get("account_id", ""),
        }
    )
    return combined


def _scheduled_douyin_account_id(params: Optional[Dict[str, Any]]) -> int:
    source = params if isinstance(params, dict) else {}
    for key in ("douyin_account_id", "account_id", "account_key"):
        raw = str(source.get(key) or "").strip()
        if not raw:
            continue
        matches = re.findall(r"\d+", raw)
        if matches:
            value = _safe_int(matches[-1])
            if value > 0:
                return value
    return 0


def _scheduled_douyin_fixed_text(params: Optional[Dict[str, Any]], kind: str) -> str:
    source = params if isinstance(params, dict) else {}
    if kind == "message":
        return str(source.get("message") or source.get("direct_message") or "你好，看到你的内容挺有启发，想交流一下。").strip()
    return str(source.get("comment_text") or source.get("comment") or "内容很有参考价值，学习了。").strip()


def _scheduled_douyin_interaction_users(
    limit: int = 10,
    selected_task_ids: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    _install_douyin_origin_import_path()
    from douyin_api import collect_douyin_interaction_users  # type: ignore

    wanted_task_ids = {
        _safe_int(task_id) for task_id in (selected_task_ids or []) if _safe_int(task_id) > 0
    }
    rows = collect_douyin_interaction_users(wanted_task_ids or None)
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        if wanted_task_ids and _safe_int(row.get("task_id")) not in wanted_task_ids:
            continue
        key = str(row.get("profile_url") or row.get("sec_uid") or row.get("user_id") or row.get("username") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(dict(row))
        if len(out) >= limit:
            break
    return out


_SCHEDULED_DOUYIN_ACTION_LABELS = {
    "account_nurture": "自动养号",
    "search_collect": "关键词采集精准客户",
    "self_comment_monitor": "我的评论区",
    "precise_touch": "精准用户触达",
    "reply_comments": "回复视频评论",
    "mention_comment": "评论并@精准客户",
    "follow_comment": "关注并评论精准客户",
    "direct_message": "主动私信精准客户",
    "stranger_message": "私信接管",
}

_SCHEDULED_DOUYIN_FOLLOWUP_ACTIONS = (
    "follow_comment",
    "mention_comment",
    "direct_message",
)


def _scheduled_douyin_followup_actions(value: Any, *, default_all: bool = False) -> List[str]:
    if not isinstance(value, list):
        return list(_SCHEDULED_DOUYIN_FOLLOWUP_ACTIONS) if default_all else []
    selected = {str(item or "").strip().lower() for item in value}
    return [action for action in _SCHEDULED_DOUYIN_FOLLOWUP_ACTIONS if action in selected]


def _merge_scheduled_douyin_collection_params(
    online_params: Any,
    workflow_params: Any,
) -> Dict[str, Any]:
    merged = dict(online_params) if isinstance(online_params, dict) else {}
    workflow = dict(workflow_params) if isinstance(workflow_params, dict) else {}
    for key in (
        "regions",
        "max_results",
        "max_videos_per_run",
        "mode",
        "reply_precise_comments",
        "reply_comment_mode",
        "reply_comment_text",
        "reply_comment_prompt",
        "reply_comment_seed_text",
        "reply_comment_interval_minutes_min",
        "reply_comment_interval_minutes_max",
    ):
        if key in workflow and workflow.get(key) not in (None, "", []):
            merged[key] = workflow.get(key)
    explicit_keywords = _scheduled_douyin_search_keywords(
        {key: workflow.get(key) for key in ("keywords", "keyword", "search_keyword", "query")}
    )
    if explicit_keywords:
        merged["keywords"] = explicit_keywords
        merged["keyword"] = explicit_keywords[0]
    # Collection writes candidates into the global precise-user pool and may
    # reply to the just-filtered comments immediately. Other touch actions
    # remain owned by the standalone precise-touch node.
    legacy_actions = []
    for key in ("followup_actions", "touch_actions"):
        values = workflow.get(key)
        if isinstance(values, list):
            legacy_actions.extend(values)
    legacy_reply = "reply_comments" in {
        str(item or "").strip().lower() for item in (legacy_actions if isinstance(legacy_actions, list) else [])
    }
    merged["reply_precise_comments"] = _workflow_flag(
        workflow.get("reply_precise_comments", merged.get("reply_precise_comments")),
        legacy_reply or _workflow_flag(merged.get("reply_precise_comments"), False),
    )
    for target, aliases in (
        ("reply_comment_mode", ("reply_comment_mode", "comment_mode")),
        ("reply_comment_text", ("reply_comment_text", "comment_text")),
        ("reply_comment_prompt", ("reply_comment_prompt", "comment_prompt")),
        ("reply_comment_seed_text", ("reply_comment_seed_text", "comment_seed_text")),
    ):
        for alias in aliases:
            if workflow.get(alias) not in (None, "", []):
                merged[target] = workflow.get(alias)
                break
    merged["reply_comment_mode"] = str(merged.get("reply_comment_mode") or "fixed").strip().lower() or "fixed"
    if merged["reply_comment_mode"] not in {"fixed", "ai", "rewrite"}:
        merged["reply_comment_mode"] = "fixed"
    merged["reply_comment_text"] = str(merged.get("reply_comment_text") or "").strip()
    merged["reply_comment_prompt"] = str(merged.get("reply_comment_prompt") or "").strip()
    merged["reply_comment_seed_text"] = str(merged.get("reply_comment_seed_text") or "").strip()
    merged["followup_actions"] = []
    merged.pop("touch_actions", None)
    merged["customer_scope"] = "current_collection_batch"
    return merged


def _merge_scheduled_douyin_precise_touch_params(
    online_params: Any,
    workflow_params: Any,
) -> Dict[str, Any]:
    merged = dict(online_params) if isinstance(online_params, dict) else {}
    workflow = dict(workflow_params) if isinstance(workflow_params, dict) else {}
    for key in (
        "touch_actions",
        "followup_actions",
        "max_users",
        "max_results",
        "interval_minutes_min",
        "interval_minutes_max",
        "sales_schedule_start",
        "sales_schedule_end",
        "sales_schedule_duration_minutes",
        "sales_node_label",
    ):
        if key in workflow and workflow.get(key) not in (None, "", []):
            merged[key] = (
                _scheduled_douyin_followup_actions(workflow.get(key))
                if key in {"touch_actions", "followup_actions"}
                else workflow.get(key)
            )
    if "touch_actions" in merged:
        merged["touch_actions"] = _scheduled_douyin_followup_actions(merged.get("touch_actions"))
        merged.pop("followup_actions", None)
    elif "followup_actions" in merged:
        merged["touch_actions"] = _scheduled_douyin_followup_actions(merged.get("followup_actions"))
        merged.pop("followup_actions", None)
    merged["customer_scope"] = "precise_pool"
    return merged


def _scheduled_douyin_action_timeout(start_result: Dict[str, Any]) -> float:
    total = max(1, _safe_int(start_result.get("total") or 1))
    interval_seconds = max(
        0,
        _safe_int(start_result.get("interval_seconds_max") or start_result.get("interval_seconds") or 0),
    )
    return float(max(300, min(7200, interval_seconds * max(0, total - 1) + 300)))


async def _wait_for_douyin_sales_action_completion(
    action: str,
    *,
    timeout_seconds: float,
    account_id: int = 0,
    poll_interval_seconds: float = 2.0,
) -> Dict[str, Any]:
    _install_douyin_origin_import_path()
    from douyin_api import (  # type: ignore
        douyin_follow_comment_status,
        douyin_interaction_status,
        douyin_mention_comment_status,
        douyin_video_comment_status,
    )

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(timeout_seconds, poll_interval_seconds)
    last_snapshot: Dict[str, Any] = {}

    async def read_status() -> Dict[str, Any]:
        if action == "reply_comments":
            return await douyin_video_comment_status()
        if action == "mention_comment":
            return await douyin_mention_comment_status()
        if action == "follow_comment":
            return await douyin_follow_comment_status(lite=False, include_users=False)
        if action == "direct_message":
            return await douyin_interaction_status(lite=False, include_users=False)
        return {"running": False, "state": {}}

    while True:
        raw = await read_status()

        state = raw.get("state") if isinstance(raw, dict) and isinstance(raw.get("state"), dict) else {}
        last_snapshot = dict(state)
        running = bool(raw.get("running")) if isinstance(raw, dict) else bool(state.get("running"))
        if not running:
            return {"status": "done", "state": last_snapshot, "runtime_idle": True}
        if loop.time() >= deadline:
            stop_result = await _stop_douyin_action(action, account_id=account_id)
            # A stop request is asynchronous. Do not let the next workflow
            # action start while the old local worker still owns the browser.
            settle_deadline = loop.time() + _SCHEDULED_DOUYIN_STOP_SETTLE_TIMEOUT_SECONDS
            while loop.time() < settle_deadline:
                try:
                    settled = await read_status()
                except Exception as exc:
                    logger.debug("[SCHEDULED-TASK] Douyin stop settle poll failed action=%s: %s", action, exc)
                    settled = {}
                settled_state = (
                    settled.get("state")
                    if isinstance(settled, dict) and isinstance(settled.get("state"), dict)
                    else {}
                )
                if isinstance(settled_state, dict):
                    last_snapshot = dict(settled_state)
                if not bool(settled.get("running")):
                    return {
                        "status": "timeout_stopped",
                        "state": last_snapshot,
                        "stop_result": stop_result,
                        "runtime_idle": True,
                    }
                await asyncio.sleep(min(0.5, max(0.1, poll_interval_seconds)))
            logger.warning(
                "[SCHEDULED-TASK] Douyin action did not settle after stop action=%s account_id=%s",
                action,
                account_id,
            )
            return {
                "status": "timeout_stop_pending",
                "state": last_snapshot,
                "stop_result": stop_result,
                "runtime_idle": False,
            }
        await asyncio.sleep(max(0.5, poll_interval_seconds))


async def _wait_for_local_douyin_runtime_idle(run_id: str) -> None:
    """Queue a claimed server task behind any active local Douyin worker."""
    _install_douyin_origin_import_path()
    from douyin_api import get_douyin_schedule_busy_reason  # type: ignore

    last_reason = ""
    while True:
        reason = str(get_douyin_schedule_busy_reason(include_external=False) or "").strip()
        if not reason:
            if last_reason:
                logger.info(
                    "[SCHEDULED-TASK] local Douyin worker released; continuing run_id=%s",
                    run_id,
                )
            return
        if reason != last_reason:
            logger.info(
                "[SCHEDULED-TASK] waiting for local Douyin worker run_id=%s holder=%s",
                run_id,
                reason,
            )
            last_reason = reason
        await asyncio.sleep(_SCHEDULED_DOUYIN_IDLE_POLL_SECONDS)


def _scheduled_item_uses_douyin_runtime(item: Dict[str, Any]) -> bool:
    """Whether a claimed cloud run shares the local Douyin browser."""
    if not isinstance(item, dict):
        return False
    kind = str(item.get("task_kind") or item.get("kind") or "").strip().lower()
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    action = str(payload.get("action") or item.get("action") or "").strip().lower()
    if kind == "douyin_leads":
        return True
    return action in {
        "collect_precise",
        "precise_touch",
        "self_comment_monitor",
        "follow_comment",
        "interaction",
        "comment_collect",
        "video_comment",
        "mention_comment",
        "direct_message",
    } or action.startswith("douyin_")


def _local_douyin_busy_reason() -> str:
    """Read local worker occupancy before claiming another cloud task."""
    try:
        _install_douyin_origin_import_path()
        from douyin_api import get_douyin_schedule_busy_reason  # type: ignore

        return str(get_douyin_schedule_busy_reason(include_external=False) or "").strip()
    except Exception as exc:
        logger.debug("[SCHEDULED-TASK] local Douyin busy check failed: %s", exc)
        return ""


def _scheduled_douyin_slim_user(row: Dict[str, Any], action: str) -> Dict[str, Any]:
    status_prefix = {
        "reply_comments": "reply_comments",
        "mention_comment": "mention_comment",
        "follow_comment": "follow_comment",
        "direct_message": "interaction",
    }.get(action, "interaction")
    result: Dict[str, Any] = {}
    field_map = {
        "username": ("username", "nickname", "name"),
        "profile_url": ("profile_url",),
        "comment": ("comment", "content"),
        "region": ("region", "location", "ip_location"),
        "source_video": ("task_title", "video_title"),
        "source_video_url": ("task_url", "video_url"),
        "status": (f"{status_prefix}_status",),
        "error": (f"{status_prefix}_error",),
        "sent_text": (
            f"{status_prefix}_message",
            f"{status_prefix}_text",
            f"{status_prefix}_result",
        ),
        "account_id": (f"{status_prefix}_account_id",),
        "started_at": (f"{status_prefix}_started_at",),
        "finished_at": (f"{status_prefix}_finished_at",),
    }
    for target, candidates in field_map.items():
        for key in candidates:
            value = row.get(key)
            if value not in (None, "", []):
                result[target] = value
                break
    return result


def _scheduled_douyin_action_users(
    action: str,
    selected_users: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not selected_users:
        return []
    _install_douyin_origin_import_path()
    from douyin_api import collect_douyin_interaction_users, user_choice_key  # type: ignore

    selected_task_ids = {
        _safe_int(row.get("task_id"))
        for row in selected_users
        if isinstance(row, dict) and _safe_int(row.get("task_id")) > 0
    }
    current_rows = collect_douyin_interaction_users(selected_task_ids or None)
    current_map = {
        user_choice_key(row): row
        for row in (current_rows if isinstance(current_rows, list) else [])
        if isinstance(row, dict) and user_choice_key(row)
    }
    output: List[Dict[str, Any]] = []
    for selected in selected_users:
        key = user_choice_key(selected)
        row = current_map.get(key, selected)
        output.append(_scheduled_douyin_slim_user(row, action))
    return output


def _scheduled_douyin_precise_touch_user_status(action: str, row: Dict[str, Any]) -> str:
    raw_status = str((row or {}).get("status") or "").strip().lower()
    success_statuses = {
        "reply_comments": {"completed", "success"},
        "mention_comment": {"completed", "success"},
        "follow_comment": {"completed", "success"},
        "direct_message": {"sent", "completed", "success"},
    }
    return "completed" if raw_status in success_statuses.get(action, {"completed", "success"}) else "failed"


def _scheduled_douyin_precise_touch_status_prefix(action: str) -> str:
    return {
        "direct_message": "interaction",
        "follow_comment": "follow_comment",
        "mention_comment": "mention_comment",
    }.get(action, action)


async def _run_scheduled_douyin_batch_followups(
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    run_id: str,
    followup_actions: List[str],
    selected_task_ids: List[int],
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for followup_action in followup_actions:
        await _post_task_event(
            cloud,
            base,
            headers,
            run_id,
            "progress",
            {
                "text": f"正在对本次采集的精准客户执行：{_SCHEDULED_DOUYIN_ACTION_LABELS.get(followup_action, followup_action)}",
                "action": followup_action,
            },
        )
        _active_scheduled_douyin_actions[run_id] = followup_action
        followup_params = _load_scheduled_douyin_online_config_params(followup_action)
        followup_params.update(
            {
                "selected_task_ids": list(selected_task_ids),
                "customer_scope": "current_collection_batch",
            }
        )
        try:
            followup_result = await _run_scheduled_douyin_sales_action(followup_action, followup_params)
        except Exception as exc:
            logger.exception(
                "[SCHEDULED-TASK] Douyin batch followup failed run_id=%s action=%s",
                run_id,
                followup_action,
            )
            followup_result = {"code": 500, "msg": str(exc).strip() or exc.__class__.__name__}
        results.append(
            {
                "action": followup_action,
                "label": _SCHEDULED_DOUYIN_ACTION_LABELS.get(followup_action, followup_action),
                "result": followup_result,
            }
        )
    return results


def _scheduled_douyin_video_comment_tasks(selected_task_ids: List[int]) -> List[Dict[str, Any]]:
    if not selected_task_ids:
        return []
    _install_douyin_origin_import_path()
    from douyin_api import douyin_tasks, ensure_douyin_task_shape  # type: ignore

    wanted = {_safe_int(task_id) for task_id in selected_task_ids if _safe_int(task_id) > 0}
    output: List[Dict[str, Any]] = []
    for raw in douyin_tasks if isinstance(douyin_tasks, list) else []:
        if not isinstance(raw, dict) or _safe_int(raw.get("id")) not in wanted:
            continue
        task = ensure_douyin_task_shape(dict(raw))
        output.append(
            {
                "task_id": _safe_int(task.get("id")),
                "title": str(task.get("title") or "").strip(),
                "url": str(task.get("url") or "").strip(),
                "author": str(task.get("author") or "").strip(),
                "status": str(task.get("video_comment_status") or "pending").strip(),
                "sent_text": str(task.get("video_comment_text") or "").strip(),
                "error": str(task.get("video_comment_error") or "").strip(),
                "account_id": str(task.get("video_comment_account_id") or "").strip(),
                "started_at": str(task.get("video_comment_started_at") or "").strip(),
                "finished_at": str(task.get("video_comment_finished_at") or "").strip(),
            }
        )
    return output


def _scheduled_douyin_conversation_key(row: Dict[str, Any]) -> str:
    return str(
        row.get("conversation_key")
        or row.get("conversation_id")
        or row.get("profile_url")
        or row.get("sec_user_id")
        or row.get("username")
        or ""
    ).strip()


def _scheduled_douyin_conversation_signature(row: Dict[str, Any]) -> tuple:
    return tuple(
        str(row.get(key) if row.get(key) is not None else "").strip()
        for key in (
            "incoming_message",
            "preview_text",
            "unread_count",
            "is_unread",
            "time_text",
            "reply_status",
            "reply_message",
            "reply_error",
            "reply_updated_at",
        )
    )


def _scheduled_douyin_slim_conversation(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "conversation_key",
            "username",
            "avatar",
            "incoming_message",
            "preview_text",
            "unread_count",
            "is_unread",
            "time_text",
            "profile_url",
            "account_id",
            "reply_status",
            "reply_message",
            "reply_error",
            "reply_started_at",
            "reply_finished_at",
            "reply_updated_at",
            "collected_at",
        )
        if row.get(key) not in (None, "")
    }


def _scheduled_douyin_changed_conversations(
    before: List[Dict[str, Any]],
    after: List[Dict[str, Any]],
    *,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    before_map = {
        _scheduled_douyin_conversation_key(row): _scheduled_douyin_conversation_signature(row)
        for row in before
        if isinstance(row, dict) and _scheduled_douyin_conversation_key(row)
    }
    changed = [
        row
        for row in after
        if isinstance(row, dict)
        and _scheduled_douyin_conversation_key(row)
        and before_map.get(_scheduled_douyin_conversation_key(row)) != _scheduled_douyin_conversation_signature(row)
    ]
    changed.sort(
        key=lambda row: str(
            row.get("reply_updated_at")
            or row.get("collected_at")
            or row.get("time_text")
            or ""
        ),
        reverse=True,
    )
    return [_scheduled_douyin_slim_conversation(row) for row in changed[: max(1, limit)]]


def _scheduled_douyin_completed_result(
    action: str,
    start_result: Dict[str, Any],
    completion: Dict[str, Any],
    *,
    users: Optional[List[Dict[str, Any]]] = None,
    tasks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    result = dict(start_result)
    state = completion.get("state") if isinstance(completion.get("state"), dict) else {}
    completion_status = str(completion.get("status") or "done").strip().lower()
    stats = {
        key: _safe_int(state.get(key) if state.get(key) is not None else start_result.get(key))
        for key in ("total", "processed", "success", "failed", "commented", "skipped_no_posts")
    }
    stats = {key: value for key, value in stats.items() if value or key in {"total", "processed", "success", "failed"}}
    label = _SCHEDULED_DOUYIN_ACTION_LABELS.get(action, action)
    if completion_status in {"timeout", "timeout_stopped", "timeout_stop_pending"}:
        summary = (
            f"{label}超过安全等待时间，已请求停止；当前已处理 {stats.get('processed', 0)}/{stats.get('total', 0)}，"
            f"成功 {stats.get('success', 0)}，失败 {stats.get('failed', 0)}。"
        )
    else:
        summary = (
            f"{label}执行完成：共 {stats.get('total', 0)}，已处理 {stats.get('processed', 0)}，"
            f"成功 {stats.get('success', 0)}，失败 {stats.get('failed', 0)}。"
        )
    result.update(
        {
            "msg": summary,
            "summary": summary,
            "status": completion_status,
            "stats": stats,
            "final_state": dict(state),
            "runtime_idle": bool(completion.get("runtime_idle", True)),
        }
    )
    if users is not None:
        result["users"] = users
    if tasks is not None:
        result["tasks"] = tasks
    if isinstance(completion.get("stop_result"), dict):
        result["stop_result"] = completion.get("stop_result")
    return result


async def _run_scheduled_douyin_sales_action(action: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    source = params if isinstance(params, dict) else {}
    account_id = _scheduled_douyin_account_id(source)
    max_users = max(1, min(_safe_int(source.get("max_users") or source.get("max_results") or 10) or 10, 200))
    interval_minutes_min = max(1, min(_safe_int(source.get("interval_minutes_min") or 4) or 4, 60))
    interval_minutes_max = max(interval_minutes_min, min(_safe_int(source.get("interval_minutes_max") or 6) or 6, 120))
    raw_source_users = source.get("users") if isinstance(source.get("users"), list) else source.get("selected_users") if isinstance(source.get("selected_users"), list) else []
    source_users = [dict(row) for row in raw_source_users if isinstance(row, dict)]

    _install_douyin_origin_import_path()
    if action == "account_nurture":
        from douyin_api import douyin_run_account_nurture_once  # type: ignore

        duration_min = max(5, _safe_int(source.get("nurture_duration_min") or 20) or 20)
        duration_max = max(duration_min, _safe_int(source.get("nurture_duration_max") or 40) or 40)
        duration_minutes = _safe_int(source.get("duration_minutes") or source.get("sales_schedule_duration_minutes"))
        payload = {"duration_minutes": duration_minutes or random.randint(duration_min, duration_max)}
        if account_id > 0:
            payload["account_ids"] = [account_id]
        result = await douyin_run_account_nurture_once(payload)
        if not isinstance(result, dict):
            return {"code": 500, "msg": "抖音养号启动失败"}
        normalized = dict(result)
        status = normalized.get("status") if isinstance(normalized.get("status"), dict) else {}
        all_accounts = [dict(row) for row in (status.get("accounts") or []) if isinstance(row, dict)]
        accounts = [
            row
            for row in all_accounts
            if bool(row.get("is_enabled"))
            or _safe_int(row.get("completed_sessions")) > 0
            or bool(str(row.get("last_started_at") or "").strip())
        ] or all_accounts
        completed = _safe_int(status.get("total_sessions"))
        failed = len([row for row in accounts if str(row.get("last_error") or "").strip()])
        normalized.update(
            {
                "summary": str(normalized.get("msg") or "抖音自动养号执行完成。").strip(),
                "final_state": dict(status),
                "items": accounts,
                "stats": {
                    "total": len(accounts),
                    "processed": completed + failed,
                    "success": completed,
                    "failed": failed,
                },
            }
        )
        return normalized

    if action == "self_comment_monitor":
        from douyin_api import get_douyin_self_comment_monitor_state, run_douyin_self_comment_monitor_cycle  # type: ignore

        if account_id <= 0:
            return {"code": 400, "msg": "Online 未配置可执行的抖音我的评论区账号。"}
        result = await run_douyin_self_comment_monitor_cycle(
            account_id,
            trigger_type="workflow",
            one_shot=True,
        )
        if not isinstance(result, dict):
            return {"code": 500, "msg": "抖音我的评论区执行没有返回结果。"}
        monitor_state = get_douyin_self_comment_monitor_state(account_id)
        status = str(result.get("status") or monitor_state.get("last_cycle_status") or "failed").strip().lower()
        code = 200 if status in {"completed", "skipped", "disabled"} else 500
        return {
            **result,
            "code": code,
            "action": action,
            "stats": {
                "total": _safe_int(monitor_state.get("last_video_count") or 0),
                "processed": _safe_int(monitor_state.get("last_comment_count") or 0),
                "success": _safe_int(monitor_state.get("last_auto_reply_success") or 0),
                "failed": _safe_int(monitor_state.get("last_auto_reply_failed") or 0),
                "new_comments": _safe_int(monitor_state.get("last_new_comment_count") or 0),
                "precise": _safe_int(monitor_state.get("last_precise_count") or 0),
                "failed_videos": _safe_int(monitor_state.get("last_failed_video_count") or 0),
            },
            "final_state": monitor_state,
        }

    if action == "precise_touch":
        from douyin_api import (  # type: ignore
            collect_douyin_precise_touch_users,
            precise_customer_touch_identity_key,
            update_douyin_precise_touch_users,
        )

        touch_actions = _scheduled_douyin_followup_actions(
            source.get("touch_actions") if isinstance(source.get("touch_actions"), list) else source.get("followup_actions"),
            default_all=True,
        )
        if not touch_actions:
            touch_actions = list(_SCHEDULED_DOUYIN_FOLLOWUP_ACTIONS)
        if not touch_actions:
            return {"code": 400, "msg": "当前没有可执行的精准触达动作。"}

        results: List[Dict[str, Any]] = []
        success_count = 0
        action_stats: List[Dict[str, Any]] = []
        # A user can be selected once for each enabled action.  The summary is
        # about people touched, so count stable customer identities rather than
        # adding the same person once per action.
        touched_user_keys: set[str] = set()
        blocked_by_unsettled_action = ""
        for touch_action in touch_actions:
            if blocked_by_unsettled_action:
                # Do not claim or mark users for actions that were never
                # started. They remain available to the next touch round.
                action_stat = {
                    "action": touch_action,
                    "label": _SCHEDULED_DOUYIN_ACTION_LABELS.get(touch_action, touch_action),
                    "selected": 0,
                    "processed": 0,
                    "success": 0,
                    "failed": 0,
                    "not_started": 0,
                    "started": False,
                    "result_code": 423,
                    "error": blocked_by_unsettled_action,
                }
                action_stats.append(action_stat)
                results.append(
                    {
                        "action": touch_action,
                        "label": action_stat["label"],
                        "result": {"code": 423, "msg": blocked_by_unsettled_action},
                        "users": [],
                        "stats": action_stat,
                    }
                )
                continue
            # Claim the bounded slice atomically so two workflow triggers
            # cannot select the same user/action before either one is queued.
            async with _scheduled_douyin_precise_touch_claim_lock:
                touch_users = await asyncio.to_thread(
                    collect_douyin_precise_touch_users,
                    touch_action,
                    max_users,
                )
                if touch_action == "reply_comments":
                    touch_users = [
                        row
                        for row in touch_users
                        if isinstance(row, dict) and _safe_int(row.get("task_id")) > 0
                    ]
                started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if touch_users:
                    await asyncio.to_thread(
                        update_douyin_precise_touch_users,
                        touch_users,
                        action=touch_action,
                        status="queued",
                        account_id=account_id or None,
                        started_at=started_at,
                    )
            if not touch_users:
                action_stat = {
                    "action": touch_action,
                    "label": _SCHEDULED_DOUYIN_ACTION_LABELS.get(touch_action, touch_action),
                    "selected": 0,
                    "processed": 0,
                    "success": 0,
                    "failed": 0,
                    "not_started": 0,
                    "started": False,
                    "result_code": 204,
                    "error": "",
                }
                action_stats.append(action_stat)
                results.append(
                    {
                        "action": touch_action,
                        "label": _SCHEDULED_DOUYIN_ACTION_LABELS.get(touch_action, touch_action),
                        "result": {"code": 204, "msg": "当前动作没有可触达的精准用户"},
                        "users": [],
                        "stats": action_stat,
                    }
                )
                continue

            for touch_user in touch_users:
                if not isinstance(touch_user, dict):
                    continue
                identity_key = precise_customer_touch_identity_key(touch_user)
                if identity_key:
                    touched_user_keys.add(identity_key)

            sub_params = dict(source)
            sub_params = _load_scheduled_douyin_online_config_params(touch_action)
            for key in ("interval_minutes_min", "interval_minutes_max"):
                if key in source and source.get(key) not in (None, "", []):
                    sub_params[key] = source.get(key)
            sub_params["users"] = touch_users
            sub_params["selected_users"] = touch_users
            sub_params["customer_scope"] = "precise_pool"
            if touch_action == "reply_comments":
                sub_params["selected_task_ids"] = sorted(
                    {
                        _safe_int(row.get("task_id"))
                        for row in touch_users
                        if isinstance(row, dict) and _safe_int(row.get("task_id")) > 0
                    }
                )
            try:
                touch_result = await _run_scheduled_douyin_sales_action(touch_action, sub_params)
            except Exception as exc:
                logger.exception(
                    "[SCHEDULED-TASK] Douyin precise touch failed action=%s",
                    touch_action,
                )
                touch_result = {"code": 500, "msg": str(exc).strip() or exc.__class__.__name__}

            user_results = touch_result.get("user_results") if isinstance(touch_result, dict) else None
            result_code = _safe_int(touch_result.get("code")) if isinstance(touch_result, dict) else 500
            action_users = await asyncio.to_thread(_scheduled_douyin_action_users, touch_action, touch_users)
            # A rejected start (busy/conflict/offline) did not process this
            # round. Do not reuse stale completed flags from the global pool.
            if result_code != 200 and not (touch_action == "reply_comments" and isinstance(user_results, list)):
                action_users = []
            if touch_action == "reply_comments" and isinstance(user_results, list):
                # Reply results are per customer. Persist each result instead
                # of applying one aggregate status to the whole selected set.
                for user_result in user_results:
                    if not isinstance(user_result, dict) or not isinstance(user_result.get("user"), dict):
                        continue
                    user_row = user_result["user"]
                    user_status = "completed" if str(user_result.get("status") or "").strip().lower() == "completed" else "failed"
                    await asyncio.to_thread(
                        update_douyin_precise_touch_users,
                        [user_row],
                        action=touch_action,
                        status=user_status,
                        error="" if user_status == "completed" else str(user_result.get("error") or "").strip(),
                        result=str(user_result.get("reply_text") or "").strip(),
                        account_id=user_result.get("account_id") or account_id or None,
                        started_at=str(user_result.get("started_at") or started_at).strip() or None,
                        finished_at=str(user_result.get("finished_at") or "").strip() or None,
                    )
                failed_users = [
                    item
                    for item in user_results
                    if isinstance(item, dict) and str(item.get("status") or "").strip().lower() != "completed"
                ]
                mark_status = "failed" if failed_users else "completed"
            else:
                completed_users = 0
                aggregate_error = str(
                    (touch_result or {}).get("msg")
                    or (touch_result or {}).get("message")
                    or ""
                ).strip()
                aggregate_result = str(
                    (touch_result or {}).get("summary")
                    or (touch_result or {}).get("msg")
                    or ""
                ).strip()
                for index, selected_user in enumerate(touch_users):
                    user_result = action_users[index] if index < len(action_users) else {}
                    user_status = (
                        "failed"
                        if result_code != 200
                        else _scheduled_douyin_precise_touch_user_status(touch_action, user_result)
                    )
                    if index >= len(action_users):
                        fallback_row = dict(selected_user)
                        status_prefix = _scheduled_douyin_precise_touch_status_prefix(touch_action)
                        fallback_row[f"{status_prefix}_status"] = user_status
                        fallback_row[f"{status_prefix}_error"] = aggregate_error or "本轮未返回该用户的成功结果"
                        action_users.append(_scheduled_douyin_slim_user(fallback_row, touch_action))
                    if user_status == "completed":
                        completed_users += 1
                    await asyncio.to_thread(
                        update_douyin_precise_touch_users,
                        [selected_user],
                        action=touch_action,
                        status=user_status,
                        error="" if user_status == "completed" else str(user_result.get("error") or aggregate_error or "本轮未返回该用户的成功结果").strip(),
                        result=str(user_result.get("sent_text") or aggregate_result).strip(),
                        account_id=user_result.get("account_id") or account_id or None,
                        started_at=str(user_result.get("started_at") or started_at).strip() or None,
                        finished_at=str(user_result.get("finished_at") or "").strip() or None,
                    )
                mark_status = "completed" if completed_users == len(touch_users) else "failed"
            if mark_status == "completed":
                success_count += 1
            if touch_action == "reply_comments" and isinstance(user_results, list):
                action_users = []
                for user_result in user_results:
                    if not isinstance(user_result, dict) or not isinstance(user_result.get("user"), dict):
                        continue
                    reply_user = dict(user_result["user"])
                    reply_user["reply_comments_status"] = str(user_result.get("status") or "failed").strip().lower()
                    reply_user["reply_comments_error"] = str(user_result.get("error") or "").strip()
                    reply_user["reply_comments_text"] = str(user_result.get("reply_text") or "").strip()
                    reply_user["reply_comments_account_id"] = user_result.get("account_id") or ""
                    reply_user["reply_comments_started_at"] = user_result.get("started_at") or ""
                    reply_user["reply_comments_finished_at"] = user_result.get("finished_at") or ""
                    action_users.append(_scheduled_douyin_slim_user(reply_user, touch_action))
            selected_count = len(touch_users)
            status_values = [
                str((row or {}).get("status") or "").strip().lower()
                for row in action_users
                if isinstance(row, dict)
            ]
            if result_code != 200:
                # A rejected launch leaves this pool slice retryable. It did
                # not process or fail any person in the current round.
                action_success_users = 0
                action_failed_users = 0
                action_processed_users = 0
                action_not_started_users = selected_count
            else:
                action_success_users = sum(status in {"completed", "success", "sent"} for status in status_values)
                action_failed_users = sum(status in {"failed", "error", "cancelled", "skipped"} for status in status_values)
                action_processed_users = min(selected_count, action_success_users + action_failed_users)
                action_not_started_users = max(0, selected_count - action_processed_users)
            action_stat = {
                "action": touch_action,
                "label": _SCHEDULED_DOUYIN_ACTION_LABELS.get(touch_action, touch_action),
                "selected": selected_count,
                "processed": action_processed_users,
                "success": min(selected_count, action_success_users),
                "failed": min(selected_count, action_failed_users),
                "not_started": action_not_started_users,
                "started": result_code == 200,
                "result_code": result_code,
                "error": str((touch_result or {}).get("msg") or "").strip() if isinstance(touch_result, dict) else "",
            }
            action_stats.append(action_stat)
            results.append(
                {
                    "action": touch_action,
                    "label": _SCHEDULED_DOUYIN_ACTION_LABELS.get(touch_action, touch_action),
                    "result": touch_result,
                    "users": action_users,
                    "stats": action_stat,
                }
            )
            if isinstance(touch_result, dict) and touch_result.get("runtime_idle") is False:
                # The local action was stopped but did not release its worker
                # within the bounded settle window. Keep later actions queued
                # instead of starting them against the same browser session.
                blocked_by_unsettled_action = (
                    f"前置动作“{action_stat['label']}”停止后仍未释放本地抖音浏览器；"
                    "本轮后续动作未启动，将在下一轮重新领取。"
                )
                logger.warning(
                    "[SCHEDULED-TASK] Douyin precise touch halted before next action action=%s",
                    touch_action,
                )
        touched_total = len(touched_user_keys)
        started_action_count = sum(bool(item.get("started")) for item in action_stats)
        not_started_action_count = len(action_stats) - started_action_count
        failed_action_count = max(0, started_action_count - success_count)
        detail_summary = "；".join(
            f"{item['label']}：选取 {item['selected']}，处理 {item['processed']}，成功 {item['success']}，失败 {item['failed']}，未启动 {item['not_started']}"
            for item in action_stats
        )
        summary = (
            f"精准用户触达完成：配置 {len(results)} 个动作，已启动 {started_action_count} 个，"
            f"全部完成 {success_count} 个，启动后未全部完成 {failed_action_count} 个，"
            f"未启动 {not_started_action_count} 个，触达候选 {touched_total} 人。"
        )
        if detail_summary:
            summary = f"{summary} {detail_summary}。"
        return {
            "code": 200,
            "msg": summary,
            "summary": summary,
            "action": action,
            "results": results,
            "action_stats": action_stats,
            "stats": {
                "total": len(results),
                "processed": started_action_count,
                "success": success_count,
                "failed": failed_action_count,
                "not_started": not_started_action_count,
                "users": touched_total,
                "selected_users": sum(item["selected"] for item in action_stats),
                "processed_users": sum(item["processed"] for item in action_stats),
                "success_users": sum(item["success"] for item in action_stats),
                "failed_users": sum(item["failed"] for item in action_stats),
                "not_started_users": sum(item["not_started"] for item in action_stats),
            },
        }

    if action == "reply_comments":
        from douyin_api import run_douyin_precise_customer_replies  # type: ignore

        # Precise touch replies are customer-level actions. Do not route them
        # through the legacy video-comment endpoint, which would process every
        # comment in a selected video instead of the bounded pool slice.
        if source_users:
            comment_mode = str(source.get("comment_mode") or "fixed").strip() or "fixed"
            reply_result = await run_douyin_precise_customer_replies(
                source_users,
                comment_mode=comment_mode,
                comment_text=_scheduled_douyin_fixed_text(source, "comment") if comment_mode == "fixed" else "",
                comment_prompt=str(source.get("comment_prompt") or "").strip(),
                comment_seed_text=str(source.get("comment_seed_text") or "").strip(),
                account_id=account_id,
                interval_minutes_min=interval_minutes_min,
                interval_minutes_max=interval_minutes_max,
            )
            return dict(reply_result) if isinstance(reply_result, dict) else {"code": 500, "msg": "精准客户评论回复没有返回结果。"}

        from douyin_api import douyin_start_video_comment, get_commentable_douyin_tasks  # type: ignore

        comment_mode = str(source.get("comment_mode") or "fixed").strip() or "fixed"
        selected_task_ids = _scheduled_douyin_selected_task_ids(source)
        if not selected_task_ids:
            if source_users:
                selected_task_ids = [
                    _safe_int(row.get("task_id"))
                    for row in source_users
                    if isinstance(row, dict) and _safe_int(row.get("task_id")) > 0
                ]
            if not selected_task_ids:
                selected_task_ids = [
                    _safe_int(row.get("id"))
                    for row in get_commentable_douyin_tasks()
                    if isinstance(row, dict) and _safe_int(row.get("id")) > 0
                ][:max_users]
        result = await douyin_start_video_comment(request={
            "selected_task_ids": selected_task_ids,
            "comment_mode": comment_mode,
            "comment_text": _scheduled_douyin_fixed_text(source, "comment") if comment_mode == "fixed" else "",
            "comment_prompt": str(source.get("comment_prompt") or "").strip(),
            "comment_seed_text": str(source.get("comment_seed_text") or "").strip(),
            "interval_minutes_min": interval_minutes_min,
            "interval_minutes_max": interval_minutes_max,
        })
        if not isinstance(result, dict):
            return {"code": 500, "msg": "抖音视频评论启动失败"}
        if _safe_int(result.get("code")) != 200:
            return dict(result)
        completion = await _wait_for_douyin_sales_action_completion(
            action,
            timeout_seconds=_scheduled_douyin_action_timeout(result),
            account_id=account_id,
        )
        return _scheduled_douyin_completed_result(
            action,
            result,
            completion,
            tasks=_scheduled_douyin_video_comment_tasks(selected_task_ids),
        )

    if action == "follow_comment":
        from douyin_api import douyin_start_follow_comment  # type: ignore

        users = source_users or await asyncio.to_thread(
            _scheduled_douyin_interaction_users,
            max_users,
            _scheduled_douyin_selected_task_ids(source),
        )
        if not users:
            return {"code": 400, "msg": "当前没有可执行的精准客户，请先完成关键词采集。"}
        comment_mode = str(source.get("comment_mode") or "fixed").strip() or "fixed"
        result = await douyin_start_follow_comment(request={
            "users": users,
            "comment_mode": comment_mode,
            "comment_text": _scheduled_douyin_fixed_text(source, "comment") if comment_mode == "fixed" else "",
            "comment_prompt": str(source.get("comment_prompt") or "").strip(),
            "comment_seed_text": str(source.get("comment_seed_text") or "").strip(),
            "interval_minutes_min": interval_minutes_min,
            "interval_minutes_max": interval_minutes_max,
        })
        if not isinstance(result, dict):
            return {"code": 500, "msg": "抖音关注评论启动失败"}
        if _safe_int(result.get("code")) != 200:
            return dict(result)
        completion = await _wait_for_douyin_sales_action_completion(
            action,
            timeout_seconds=_scheduled_douyin_action_timeout(result),
            account_id=account_id,
        )
        return _scheduled_douyin_completed_result(
            action,
            result,
            completion,
            users=await asyncio.to_thread(_scheduled_douyin_action_users, action, users),
        )

    if action == "direct_message":
        from douyin_api import douyin_start_interaction  # type: ignore

        users = source_users or await asyncio.to_thread(
            _scheduled_douyin_interaction_users,
            max_users,
            _scheduled_douyin_selected_task_ids(source),
        )
        if not users:
            return {"code": 400, "msg": "当前没有可私信的精准客户，请先完成关键词采集。"}
        message_mode = str(source.get("message_mode") or "fixed").strip() or "fixed"
        raw_messages = source.get("messages") if isinstance(source.get("messages"), list) else []
        messages = [str(value or "").strip() for value in raw_messages if str(value or "").strip()]
        message = messages[0] if messages else (_scheduled_douyin_fixed_text(source, "message") if message_mode == "fixed" else "")
        if message and not messages:
            messages = [message]
        result = await douyin_start_interaction(
            request={
                "users": users,
                "message_mode": message_mode,
                "messages": messages,
                "message": message,
                "message_prompt": str(source.get("message_prompt") or "").strip(),
                "message_seed_text": str(source.get("message_seed_text") or "").strip(),
                "interval_minutes_min": interval_minutes_min,
                "interval_minutes_max": interval_minutes_max,
            }
        )
        if not isinstance(result, dict):
            return {"code": 500, "msg": "抖音私信启动失败"}
        if _safe_int(result.get("code")) != 200:
            return dict(result)
        completion = await _wait_for_douyin_sales_action_completion(
            action,
            timeout_seconds=_scheduled_douyin_action_timeout(result),
            account_id=account_id,
        )
        return _scheduled_douyin_completed_result(
            action,
            result,
            completion,
            users=await asyncio.to_thread(_scheduled_douyin_action_users, action, users),
        )

    if action == "mention_comment":
        from douyin_api import douyin_get_self_videos  # type: ignore
        from douyin_api import douyin_start_mention_comment  # type: ignore

        users = source_users or await asyncio.to_thread(
            _scheduled_douyin_interaction_users,
            max_users,
            _scheduled_douyin_selected_task_ids(source),
        )
        if not users:
            return {"code": 400, "msg": "当前没有可@的精准客户，请先完成关键词采集。"}
        videos_result = await douyin_get_self_videos(account_id=account_id, max_videos=6)
        if _safe_int(videos_result.get("code") if isinstance(videos_result, dict) else 0) != 200:
            return dict(videos_result) if isinstance(videos_result, dict) else {"code": 500, "msg": "读取自己的抖音视频失败"}
        videos = videos_result.get("videos") if isinstance(videos_result, dict) else []
        video = next((row for row in videos if isinstance(row, dict) and str(row.get("url") or "").strip()), None)
        if not video:
            return {"code": 400, "msg": "当前账号没有可用于@评论的公开视频。"}
        result = await douyin_start_mention_comment(
            request={
                "account_id": account_id,
                "users": users,
                "video_url": str(video.get("url") or "").strip(),
                "video_title": str(video.get("title") or "").strip(),
                "video_cover_image": str(video.get("cover_image") or "").strip(),
                "max_mentions": max_users,
            }
        )
        if not isinstance(result, dict):
            return {"code": 500, "msg": "抖音@评论启动失败"}
        if _safe_int(result.get("code")) != 200:
            return dict(result)
        completion = await _wait_for_douyin_sales_action_completion(
            action,
            timeout_seconds=_scheduled_douyin_action_timeout(result),
            account_id=account_id,
        )
        return _scheduled_douyin_completed_result(
            action,
            result,
            completion,
            users=await asyncio.to_thread(_scheduled_douyin_action_users, action, users),
        )

    if action == "stranger_message":
        from douyin_api import run_douyin_h5_stranger_message_task_once  # type: ignore

        result = await run_douyin_h5_stranger_message_task_once(
            account_id=account_id,
            max_conversations=100,
            fixed_message=str(source.get("message") or "").strip(),
            auto_reply_enabled=bool(source.get("auto_reply_enabled", True)),
            wechat_add_friend_enabled=bool(source.get("wechat_add_friend_enabled", False)),
            reply_mode=(
                "ai_lead"
                if str(source.get("reply_mode") or "").strip().lower() == "ai_lead"
                else "fixed"
            ),
            reply_prompt=str(source.get("reply_prompt") or "").strip(),
            contact_value=str(source.get("contact_value") or "").strip(),
        )
        return dict(result) if isinstance(result, dict) else {"code": 500, "msg": "抖音私信一次性任务执行失败"}

    return {"code": 400, "msg": f"暂不支持的销售抖音动作：{action}"}


def _is_h5_douyin_one_shot(payload: Any, h5_context: Any = None) -> bool:
    payload_obj = payload if isinstance(payload, dict) else {}
    context_obj = h5_context if isinstance(h5_context, dict) else {}
    if bool(payload_obj.get("h5_one_shot")) or bool(context_obj.get("h5_one_shot")):
        return True
    return str(
        payload_obj.get("douyin_execution_mode")
        or context_obj.get("douyin_execution_mode")
        or ""
    ).strip().lower() == "one_shot"


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
    h5_context = payload.get("h5_context") if isinstance(payload.get("h5_context"), dict) else {}
    h5_one_shot = _is_h5_douyin_one_shot(payload, h5_context)
    is_sales_workflow = (
        str(h5_context.get("department_id") or "").strip().lower() == "sales"
        or str(h5_context.get("workflow_template_key") or "").strip().lower() == "system_sales"
    )
    inferred_sales_action = _scheduled_douyin_sales_action_from_payload(payload, item)
    sales_action = str((params if isinstance(params, dict) else {}).get("sales_action") or "").strip().lower()
    if action == "search_collect" and sales_action and sales_action != "search_collect":
        action = sales_action
    if action == "search_collect" and inferred_sales_action and inferred_sales_action != "search_collect":
        action = inferred_sales_action
    use_online_sales_config = is_sales_workflow or bool(inferred_sales_action and inferred_sales_action != "search_collect")
    workflow_params = dict(params)
    if action == "stranger_message":
        # H5 sends a partial one-shot payload. The saved reply lives in the
        # Online runtime config and must be loaded even without sales metadata.
        params = _merge_scheduled_douyin_stranger_params(
            _load_scheduled_douyin_online_config_params(action),
            workflow_params,
        )
    elif use_online_sales_config:
        action = _scheduled_douyin_sales_action_from_context(h5_context) or inferred_sales_action or action
        params = _load_scheduled_douyin_online_config_params(action)
        if action == "search_collect":
            params = _merge_scheduled_douyin_collection_params(params, workflow_params)
        elif action == "precise_touch":
            params = _merge_scheduled_douyin_precise_touch_params(params, workflow_params)
    elif not params or (action == "search_collect" and not _scheduled_douyin_search_keyword(params)):
        online_params = _load_scheduled_douyin_online_config_params(action)
        if action == "search_collect":
            params = _merge_scheduled_douyin_collection_params(online_params, workflow_params)
        elif action == "precise_touch":
            params = _merge_scheduled_douyin_precise_touch_params(online_params, workflow_params)
        else:
            params = online_params
    if h5_one_shot:
        # Shared Online settings may provide the text and account, but they
        # must not change the H5 task into a persistent monitor.
        params = dict(params)
        params["h5_one_shot"] = True
    if not run_id or not action:
        return
    event_status = await _post_task_event(
        cloud,
        base,
        headers,
        run_id,
        "thinking",
        {"text": f"正在执行抖音获客任务：{action}"},
    )
    if _task_event_rejects_local_work(event_status):
        return
    _active_scheduled_douyin_actions[run_id] = action
    try:
        if action == "search_collect":
            result = await _run_scheduled_douyin_search_collect_action(params)
        elif action in {"account_nurture", "self_comment_monitor", "precise_touch", "reply_comments", "mention_comment", "follow_comment", "direct_message", "stranger_message"}:
            result = await _run_scheduled_douyin_sales_action(action, params)
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
            error_text = str(result.get("msg") or result.get("message") or result.get("detail") or "").strip()
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
            # Keep the structured provider result on the run. Without this,
            # the exception handler only stored a plain error string and H5
            # could not show which conversation failed to open.
            await _complete_task_run(
                cloud,
                base,
                headers,
                run_id,
                result_text=error_text or f"douyin_leads {action} failed",
                result_payload=_scheduled_douyin_result_payload(action, result, params),
                error=(error_text or f"douyin_leads {action} failed")[:500],
            )
            return

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
                keyword_total = len(_scheduled_douyin_search_keywords(result)) or 1
                selected_video_total = max(
                    len(selected_task_ids),
                    _safe_int(result_payload.get("selected_videos_total")),
                )
                final_status = str((final_state or {}).get("status") or "").strip().lower()
                if final_status == "done":
                    result_text = (
                        f"搜索完成，共执行 {keyword_total} 个关键词，找到 {search_total} 个视频；"
                        f"已采集 {selected_video_total} 个视频的客户 {comments_collected} 人，"
                        f"精准客户 {precise_total} 人。"
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
    finally:
        _active_scheduled_douyin_actions.pop(run_id, None)


def _scheduled_douyin_action_for_item(item: Dict[str, Any]) -> str:
    """Resolve the actual sales action so a deadline stops the matching worker."""
    payload = _scheduled_payload(item)
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    h5_context = payload.get("h5_context") if isinstance(payload.get("h5_context"), dict) else {}
    action = str(payload.get("action") or "").strip().lower()
    sales_action = str(params.get("sales_action") or "").strip().lower()
    inferred_action = _scheduled_douyin_sales_action_from_payload(payload, item)
    context_action = _scheduled_douyin_sales_action_from_context(h5_context)
    if action == "search_collect" and sales_action and sales_action != "search_collect":
        action = sales_action
    if action == "search_collect" and inferred_action and inferred_action != "search_collect":
        action = inferred_action
    return context_action or inferred_action or action


async def _stop_douyin_action(
    action: str,
    *,
    account_id: int = 0,
) -> Dict[str, Any]:
    """Call the action-specific Douyin stop API in the local process."""
    action = str(action or "").strip().lower()
    try:
        _install_douyin_origin_import_path()
        if action == "account_nurture":
            from douyin_api import douyin_stop_account_nurture  # type: ignore

            request = {"account_ids": [account_id]} if account_id > 0 else None
            result = await douyin_stop_account_nurture(request)
        elif action == "reply_comments":
            from douyin_api import douyin_stop_video_comment  # type: ignore

            result = await douyin_stop_video_comment()
        elif action == "follow_comment":
            from douyin_api import douyin_stop_follow_comment  # type: ignore

            result = await douyin_stop_follow_comment()
        elif action == "direct_message":
            from douyin_api import douyin_stop_interaction  # type: ignore

            result = await douyin_stop_interaction()
        elif action == "mention_comment":
            from douyin_api import douyin_stop_mention_comment  # type: ignore

            result = await douyin_stop_mention_comment()
        elif action == "stranger_message":
            from douyin_api import douyin_stop_stranger_messages  # type: ignore

            result = await douyin_stop_stranger_messages()
        elif action == "search_collect":
            from douyin_api import douyin_stop_tasks  # type: ignore

            result = await douyin_stop_tasks()
        elif action == "self_comment_monitor":
            from douyin_api import douyin_stop_self_comment_monitor  # type: ignore

            result = await douyin_stop_self_comment_monitor(
                {"account_id": account_id} if account_id > 0 else None
            )
        else:
            return {"action": action, "stop_requested": False, "reason": "unsupported_action"}
    except Exception as exc:
        logger.warning("[SCHEDULED-TASK] deadline stop failed action=%s: %s", action, exc)
        return {"action": action, "stop_requested": False, "error": str(exc)[:500]}
    return {
        "action": action,
        "stop_requested": True,
        "result": result if isinstance(result, dict) else {"result": str(result)},
    }


async def _stop_scheduled_douyin_action_for_deadline(item: Dict[str, Any]) -> Dict[str, Any]:
    """Stop a scheduled Douyin action before freeing its next workflow node."""
    run_id = str(item.get("id") or "").strip()
    action = _active_scheduled_douyin_actions.get(run_id) or _scheduled_douyin_action_for_item(item)
    if run_id and run_id not in _active_scheduled_douyin_actions:
        # An already-expired pending run never started this local action. Do
        # not accidentally stop a separately launched manual Douyin task.
        return {"action": action, "stop_requested": False, "reason": "action_not_started"}
    payload = _scheduled_payload(item)
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    return await _stop_douyin_action(action, account_id=_scheduled_douyin_account_id(params))


async def _stop_workflow_node_local_execution(
    item: Dict[str, Any],
    *,
    headers: Dict[str, str],
) -> Dict[str, Any]:
    """Request a cooperative stop for actions that own local background work."""
    kind = str(item.get("task_kind") or "").strip().lower()
    if kind == "douyin_leads":
        return await _stop_scheduled_douyin_action_for_deadline(item)

    if kind == "client_workflow":
        payload = _scheduled_payload(item)
        run_id = str(item.get("id") or "").strip()
        action = _active_client_workflow_actions.get(run_id) or str(payload.get("action") or "").strip().lower()
        if run_id and run_id not in _active_client_workflow_actions:
            return {"action": action, "stop_requested": False, "reason": "action_not_started"}
        if action == "native_wechat_poll":
            params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
            account_id = str(
                params.get("account_id") or native_wechat_engine.LOCAL_DEFAULT_ACCOUNT_ID
            ).strip() or native_wechat_engine.LOCAL_DEFAULT_ACCOUNT_ID
            local_stop = native_wechat_engine.request_auto_reply_stop(account_id)
            return {
                "action": action,
                "stop_requested": bool(local_stop.get("requested")),
                "account_id": account_id,
                "local": local_stop,
            }

    return {"stop_requested": False, "reason": "no_cooperative_stop"}


async def _stop_workflow_node_with_timeout(
    item: Dict[str, Any],
    *,
    headers: Dict[str, str],
) -> Dict[str, Any]:
    """Bound the local stop request so a broken stop endpoint cannot hold the queue."""
    try:
        return await asyncio.wait_for(
            _stop_workflow_node_local_execution(item, headers=headers),
            timeout=_WORKFLOW_NODE_STOP_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "[SCHEDULED-TASK] workflow deadline stop timed out run_id=%s",
            str(item.get("id") or "").strip(),
        )
        return {
            "stop_requested": False,
            "reason": "stop_timeout",
            "timeout_seconds": _WORKFLOW_NODE_STOP_TIMEOUT_SECONDS,
        }


async def _cancel_workflow_execution_task(execution_task: asyncio.Task[Any]) -> None:
    """Cancel the task without allowing cancellation cleanup to block the next node."""
    if execution_task.done():
        return
    execution_task.cancel()
    try:
        await asyncio.wait_for(
            asyncio.shield(execution_task),
            timeout=_WORKFLOW_NODE_CANCEL_GRACE_SECONDS,
        )
    except asyncio.CancelledError:
        pass
    except asyncio.TimeoutError:
        logger.warning("[SCHEDULED-TASK] deadline-cancelled worker did not exit within %.1fs", _WORKFLOW_NODE_CANCEL_GRACE_SECONDS)
    except Exception as exc:
        logger.debug("[SCHEDULED-TASK] deadline-cancelled worker exited with error: %s", exc)


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
        elif source_mode == "seedance_tvc_studio":
            video_model = str(input_refs.get("video_model") or "").strip()
            video_channel = str(input_refs.get("video_channel") or "").strip()
            duration = str(input_refs.get("total_duration_seconds") or "").strip()
            segment_count = str(input_refs.get("segment_count") or "").strip()
            if video_model:
                lines.append(f"分镜头视频模型：{video_model}{(' / ' + video_channel) if video_channel else ''}")
            if duration:
                lines.append(f"分镜头时长：{duration} 秒{(' / ' + segment_count + ' 段') if segment_count else ''}")
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
    duration_value = cap_payload.get("duration")
    if duration_value in (None, ""):
        duration_value = cap_payload.get("duration_seconds")
    pl = GoalVideoPipelinePayload(
        action="run_pipeline",
        goal=generated.get("goal") or _fallback_goal(task_title),
        platform=str(cap_payload.get("platform") or "douyin").strip() or "douyin",
        duration=duration_value,
        aspect_ratio=str(cap_payload.get("aspect_ratio") or "9:16").strip() or "9:16",
        resolution=str(cap_payload.get("resolution") or "720p").strip() or "720p",
        language=str(cap_payload.get("language") or "zh").strip() or "zh",
        memory_scope="none" if generated.get("custom_prompt_used") else "default",
        planning_model=str(cap_payload.get("planning_model") or "").strip() or None,
        image_model=str(cap_payload.get("image_model") or "").strip() or None,
        video_model=str(cap_payload.get("video_model") or "").strip() or None,
        function_mode=str(cap_payload.get("function_mode") or cap_payload.get("functionMode") or "reference").strip() or "reference",
        first_image_url=str(cap_payload.get("first_image_url") or "").strip() or None,
        end_image_url=str(cap_payload.get("end_image_url") or cap_payload.get("last_image_url") or "").strip() or None,
        reference_video_urls=[str(x).strip() for x in (cap_payload.get("reference_video_urls") or []) if str(x).strip()],
        reference_audio_urls=[str(x).strip() for x in (cap_payload.get("reference_audio_urls") or []) if str(x).strip()],
        audio=_workflow_flag(cap_payload.get("audio")) if cap_payload.get("audio") is not None else None,
        generate_audio=_workflow_flag(cap_payload.get("generate_audio")) if cap_payload.get("generate_audio") is not None else None,
        seed=_safe_int(cap_payload.get("seed")) if cap_payload.get("seed") is not None else None,
        negative_prompt=str(cap_payload.get("negative_prompt") or "").strip() or None,
        enable_prompt_expansion=_workflow_flag(cap_payload.get("enable_prompt_expansion")) if cap_payload.get("enable_prompt_expansion") is not None else None,
        multi_shots=_workflow_flag(cap_payload.get("multi_shots")) if cap_payload.get("multi_shots") is not None else None,
        enable_safety_checker=_workflow_flag(cap_payload.get("enable_safety_checker")) if cap_payload.get("enable_safety_checker") is not None else None,
        camera_fixed=_workflow_flag(cap_payload.get("camera_fixed")) if cap_payload.get("camera_fixed") is not None else None,
        style=str(cap_payload.get("style") or "").strip() or None,
        mode=str(cap_payload.get("mode") or "").strip() or None,
        fps=cap_payload.get("fps"),
        cfg_scale=cap_payload.get("cfg_scale"),
        motion_bucket_id=cap_payload.get("motion_bucket_id"),
        consistency_with_text=cap_payload.get("consistency_with_text"),
        video_options=cap_payload.get("video_options") if isinstance(cap_payload.get("video_options"), dict) else {},
        precomputed_plan=cap_payload.get("precomputed_plan") if isinstance(cap_payload.get("precomputed_plan"), dict) else {},
        memory_doc_ids=_goal_video_memory_doc_ids(cap_payload),
    )

    def progress(stage: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        if not run_id:
            return
        asyncio.create_task(
            _post_task_event(cloud, base, headers, run_id, stage[:32], {"text": message, **(extra or {})})
        )

    if source_mode == _SCHEDULED_VIDEO_SOURCE_AI_IMAGE:
        pl.image_model = _normalize_image_model_id(pl.image_model or _get_default_image_generate_model())
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
    duration_value = _safe_int(payload.get("duration") or payload.get("duration_seconds"))
    if duration_value is None or duration_value <= 0:
        duration_value = 10
    pl = CreateVideoPipelinePayload(
        action="run_pipeline",
        prompt=goal,
        topic=str(payload.get("topic") or "").strip(),
        video_type=str(payload.get("video_type") or "brand_promo").strip() or "brand_promo",
        target_audience=str(payload.get("target_audience") or "general_audience").strip() or "general_audience",
        style=str(payload.get("style") or "premium commercial, realistic, cinematic lighting").strip()
        or "premium commercial, realistic, cinematic lighting",
        duration=duration_value,
        scene_count=int(payload.get("scene_count") or 1),
        aspect_ratio=str(payload.get("aspect_ratio") or "9:16").strip() or "9:16",
        resolution=str(payload.get("resolution") or "720p").strip() or "720p",
        language=str(payload.get("language") or "Chinese").strip() or "Chinese",
        planning_model=str(payload.get("planning_model") or "gpt-5.4").strip() or None,
        image_model=str(payload.get("image_model") or "openai/gpt-image-2").strip() or None,
        video_model=str(payload.get("video_model") or "").strip() or None,
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


def _seedance_tvc_text_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        text = str(value or "").strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                raw_items = parsed if isinstance(parsed, list) else [text]
            except Exception:
                raw_items = [text]
        else:
            raw_items = re.split(r"[\s,;，、；]+", text)
    out: List[str] = []
    for item in raw_items:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _seedance_tvc_is_yunwu_veo_model(model: Any) -> bool:
    value = str(model or "").strip().lower().replace("_", "-").replace(" ", "")
    return value in {
        "yunwu-veo3.1-plus",
        "veo3.1-plus",
        "veo3.1",
        "veo31",
        "veo31-fast",
        "veo3.1-fast",
        "yingmeng-plus",
        "影梦plus",
    }


def _seedance_tvc_is_openmind_grok_model(model: Any) -> bool:
    value = str(model or "").strip().lower().replace("_", "-").replace(" ", "")
    return value in {
        _SEEDANCE_TVC_DEFAULT_MODEL,
        "yingmeng1.5plus",
        "影梦1.5plus",
    }


def _seedance_tvc_video_request(payload: Dict[str, Any]) -> tuple[str, str]:
    payload = payload if isinstance(payload, dict) else {}
    explicit_model = str(payload.get("video_model") or payload.get("videoModel") or "").strip()
    explicit_channel = str(payload.get("video_channel") or payload.get("videoChannel") or "").strip()
    ui_model = str(payload.get("model") or payload.get("seedance_model") or explicit_model or _SEEDANCE_TVC_DEFAULT_MODEL).strip()
    if explicit_model and explicit_channel:
        return explicit_model, explicit_channel
    if _seedance_tvc_is_openmind_grok_model(ui_model):
        return _SEEDANCE_TVC_DEFAULT_MODEL, _SEEDANCE_TVC_DEFAULT_CHANNEL
    if _seedance_tvc_is_yunwu_veo_model(ui_model):
        return "veo3.1", "yunwu"
    if explicit_model:
        return explicit_model, explicit_channel
    return ui_model or _SEEDANCE_TVC_DEFAULT_MODEL, explicit_channel


def _seedance_tvc_segment_seconds(video_model: str, video_channel: str) -> int:
    channel = str(video_channel or "").strip().lower()
    if channel == "yunwu" and _seedance_tvc_is_yunwu_veo_model(video_model):
        return 8
    if _seedance_tvc_is_yunwu_veo_model(video_model):
        return 8
    return 10


def _seedance_tvc_prompt(payload: Dict[str, Any]) -> str:
    payload = payload if isinstance(payload, dict) else {}
    for key in ("task_text", "prompt", "creative_prompt", "goal", "description"):
        text = str(payload.get(key) or "").strip()
        if text:
            return text[:2000]
    return ""


def _normalize_seedance_tvc_scheduled_payload(
    cap_payload: Dict[str, Any],
    *,
    generated: Optional[Dict[str, Any]] = None,
    task_title: str = "",
) -> Dict[str, Any]:
    source = dict(cap_payload or {})
    generated = generated if isinstance(generated, dict) else {}
    video_model, video_channel = _seedance_tvc_video_request(source)
    segment_seconds = _seedance_tvc_segment_seconds(video_model, video_channel)
    total_raw = _safe_int(
        source.get("total_duration_seconds")
        or source.get("duration_seconds")
        or source.get("duration")
    )
    count_raw = _safe_int(source.get("segment_count") or source.get("storyboard_count"))
    if total_raw > 0:
        segment_count = int((float(total_raw) / float(segment_seconds)) + 0.5)
    elif count_raw > 0:
        segment_count = count_raw
    else:
        segment_count = 1
    segment_count = max(1, min(6, segment_count))
    total_duration = segment_count * segment_seconds

    asset_id = str(source.get("asset_id") or source.get("image_asset_id") or "").strip()
    image_url = str(source.get("image_url") or source.get("first_image_url") or "").strip()
    reference_asset_ids = _seedance_tvc_text_list(source.get("reference_asset_ids"))
    reference_image_urls = _seedance_tvc_text_list(source.get("reference_image_urls"))
    for aid in _seedance_tvc_text_list(source.get("asset_ids")) + _seedance_tvc_text_list(source.get("attachment_asset_ids")):
        if aid and aid != asset_id and aid not in reference_asset_ids:
            reference_asset_ids.append(aid)
    for url in _seedance_tvc_text_list(source.get("image_urls")):
        if url and url != image_url and url not in reference_image_urls:
            reference_image_urls.append(url)

    reference_count = (1 if (asset_id or image_url) else 0) + len(reference_asset_ids) + len(reference_image_urls)
    purposes = [p for p in _seedance_tvc_text_list(source.get("reference_purposes")) if p]
    if len(purposes) != reference_count:
        purposes = ["storyboard"] * reference_count

    task_text = _seedance_tvc_prompt(source) or str(generated.get("goal") or task_title or "").strip()[:2000]
    workflow_mode = str(source.get("workflow_mode") or "").strip()
    if workflow_mode not in {"storyboard", "direct_video"}:
        workflow_mode = "storyboard"

    video_fallbacks = source.get("video_fallbacks")
    if not isinstance(video_fallbacks, list):
        video_fallbacks = [{"channel": "comfly", "model": "veo3.1-fast"}] if _seedance_tvc_is_yunwu_veo_model(video_model) else []

    return {
        "asset_id": asset_id or None,
        "image_url": image_url or None,
        "reference_asset_ids": reference_asset_ids[:5],
        "reference_image_urls": reference_image_urls[:5],
        "reference_purposes": purposes[:11],
        "total_duration_seconds": total_duration,
        "segment_count": segment_count,
        "segment_duration_seconds": segment_seconds,
        "workflow_mode": workflow_mode,
        "merge_clips": _workflow_flag(source.get("merge_clips"), True),
        "auto_save": _workflow_flag(source.get("auto_save"), True),
        "analysis_model": str(source.get("analysis_model") or "").strip(),
        "image_model": str(source.get("image_model") or "").strip(),
        "image_model_fallback": str(source.get("image_model_fallback") or "nano-banana-2").strip(),
        "video_model": video_model,
        "video_channel": video_channel,
        "video_fallbacks": video_fallbacks,
        "aspect_ratio": str(source.get("aspect_ratio") or "9:16").strip() or "9:16",
        "visual_tone": str(source.get("visual_tone") or "clean_bright").strip() or "clean_bright",
        "rhythm": str(source.get("rhythm") or "smooth").strip() or "smooth",
        "generate_audio": _workflow_flag(source.get("generate_audio"), True),
        "watermark": _workflow_flag(source.get("watermark"), False),
        "task_text": task_text,
        "platform": str(source.get("platform") or "").strip(),
        "country": str(source.get("country") or "").strip(),
        "language": str(source.get("language") or "").strip(),
        "poll_timeout_seconds": source.get("poll_timeout_seconds") or source.get("timeout_seconds"),
        "poll_interval_seconds": source.get("poll_interval_seconds"),
    }


async def _run_seedance_tvc_scheduled_pipeline(
    *,
    cap_payload: Dict[str, Any],
    headers: Dict[str, str],
    cloud: httpx.AsyncClient,
    base: str,
    run_id: str,
) -> Dict[str, Any]:
    payload = _normalize_seedance_tvc_scheduled_payload(cap_payload)
    await _post_task_event(
        cloud,
        base,
        headers,
        run_id,
        "thinking",
        {
            "text": "正在提交创意分镜头工作台",
            "total_duration_seconds": payload.get("total_duration_seconds"),
            "video_model": payload.get("video_model"),
        },
    )
    submitted = await _post_local_api_json(
        "/api/comfly-seedance-tvc/pipeline/start",
        {"payload": payload},
        headers=headers,
        timeout_seconds=180.0,
    )
    job_id = str(submitted.get("job_id") or "").strip()
    poll_path = str(submitted.get("poll_path") or "").strip()
    if not poll_path and job_id:
        poll_path = f"/api/comfly-seedance-tvc/pipeline/jobs/{job_id}"
    if not job_id or not poll_path:
        raise RuntimeError("创意分镜头工作台未返回可查询的任务编号")

    timeout_seconds = _safe_int(cap_payload.get("poll_timeout_seconds") or cap_payload.get("timeout_seconds")) or 7200
    interval = max(2, _safe_int(cap_payload.get("poll_interval_seconds")) or 5)
    deadline = asyncio.get_running_loop().time() + max(30.0, float(timeout_seconds))
    last_job: Dict[str, Any] = {"ok": True, "status": "running", "job_id": job_id}
    while True:
        job = await _get_local_api_json(
            poll_path + ("&" if "?" in poll_path else "?") + "compact=false",
            headers=headers,
            timeout_seconds=180.0,
        )
        last_job = job if isinstance(job, dict) else {"result": job}
        status = str(last_job.get("status") or "").strip().lower()
        if status == "failed":
            raise RuntimeError(str(last_job.get("error") or last_job.get("post_error") or "创意分镜头视频生成失败")[:500])
        if status == "completed":
            final_video = _local_bestseller_final_video(last_job)
            result = {
                "ok": True,
                "capability_id": _SEEDANCE_TVC_CAPABILITY_ID,
                "pipeline": "comfly_seedance_tvc_video",
                "source_mode": "seedance_tvc_studio",
                "status": "completed",
                "job_id": job_id,
                "poll_path": poll_path,
                "payload": payload,
                "result": last_job.get("result") if isinstance(last_job.get("result"), dict) else {},
                "saved_assets": last_job.get("saved_assets") if isinstance(last_job.get("saved_assets"), list) else [],
                "artifacts": last_job.get("artifacts") if isinstance(last_job.get("artifacts"), dict) else {},
            }
            if final_video:
                result["final_video"] = final_video
                result["video_asset_id"] = final_video.get("asset_id") or ""
                result["final_asset_id"] = final_video.get("asset_id") or ""
                result["video_url"] = final_video.get("url") or ""
                result["source_media_urls"] = [final_video.get("url")] if final_video.get("url") else []
            if not _goal_video_pipeline_has_video_result(result):
                raise RuntimeError("创意分镜头视频任务已完成，但未取得最终视频素材")
            return result
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError("创意分镜头视频生成超时，未取得最终成片")
        await asyncio.sleep(interval)


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
        if capability_id in {"goal.video.pipeline", "goal.image.pipeline", "hifly.video.create_by_tts", "create.video.pipeline", "create.ppt.pipeline", _SEEDANCE_TVC_CAPABILITY_ID}:
            asset_context = _scheduled_asset_context_with_urls(attachment_asset_ids, jwt_token, installation_id)
            custom_prompt = _scheduled_custom_prompt(cap_payload)
            provided_seedance_prompt = _seedance_tvc_prompt(cap_payload) if capability_id == _SEEDANCE_TVC_CAPABILITY_ID else ""
            resume_from_image = bool(cap_payload.get("resume_from_image"))
            uses_ip_daily_script = (
                capability_id == "hifly.video.create_by_tts"
                and str(cap_payload.get("script_source") or "").strip() == "ip_daily_industry_hot_oral"
            )
            provided_hifly_script = (
                _hifly_script_text(cap_payload.get("script"))
                or _hifly_script_text(cap_payload.get("text"))
                or _hifly_script_text(cap_payload.get("prompt"))
            ) if capability_id == "hifly.video.create_by_tts" else ""
            if provided_seedance_prompt:
                generated = _generated_from_scheduled_prompt(capability_id, task_title, provided_seedance_prompt)
                generated["custom_prompt_used"] = True
            elif uses_ip_daily_script:
                generated = await _generate_shanjian_workflow_script(
                    source=cap_payload,
                    cloud=cloud,
                    base=base,
                    headers=headers,
                    run_id=run_id,
                )
            elif provided_hifly_script:
                generated = {
                    "title": task_title or str(cap_payload.get("title") or "数字人口播"),
                    "script": provided_hifly_script,
                    "caption_hint": task_title,
                    "language": str(cap_payload.get("language") or "zh-CN").strip() or "zh-CN",
                    "custom_prompt_used": True,
                }
            elif resume_from_image and capability_id in {"goal.video.pipeline", "create.video.pipeline"}:
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
                cap_payload = dict(original_cap_payload or {})
                cap_payload.update(
                    {
                        "video_mode": original_cap_payload.get("video_mode") or "",
                        "source_mode": source_mode,
                        "candidate_group": candidate_group,
                        "goal": goal,
                        "prompt": goal,
                        "memory_doc_ids": _goal_video_memory_doc_ids(original_cap_payload),
                        "reference_asset_ids": original_cap_payload.get("reference_asset_ids") or [],
                        "reference_image_urls": original_cap_payload.get("reference_image_urls") or [],
                        "resume_from_image": bool(original_cap_payload.get("resume_from_image")),
                    }
                )
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
            elif capability_id == _SEEDANCE_TVC_CAPABILITY_ID:
                publish_cfg = {}
                cap_payload = _normalize_seedance_tvc_scheduled_payload(
                    original_cap_payload,
                    generated=generated,
                    task_title=task_title,
                )
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
                if uses_ip_daily_script:
                    skill_prompt = _workflow_script_candidate(generated.get("script"))
                else:
                    skill_prompt = (
                        _hifly_script_text(cap_payload.get("script"))
                        or _hifly_script_text(cap_payload.get("text"))
                        or _hifly_script_text(generated.get("script"))
                        or _fallback_hifly_script(task_title)
                    )
                if not skill_prompt:
                    raise RuntimeError("IP 日更未返回可用于数字人的行业口播文案")
                cap_payload = dict(original_cap_payload or {})
                cap_payload.update(
                    {
                        "title": (generated.get("title") or task_title or "数字人口播")[:80],
                        "avatar": avatar,
                        "virtualman_id": str(cap_payload.get("virtualman_id") or avatar).strip(),
                        "voice": voice,
                        "script": skill_prompt,
                        "text": skill_prompt,
                        "prompt": skill_prompt,
                        "poll_interval_seconds": int(cap_payload.get("poll_interval_seconds") or 10),
                        "poll_timeout_seconds": int(cap_payload.get("poll_timeout_seconds") or 3600),
                    }
                )
            cap_payload = _inject_scheduled_assets_into_capability_payload(cap_payload, attachment_asset_ids)
            if capability_id == _SEEDANCE_TVC_CAPABILITY_ID:
                cap_payload = _normalize_seedance_tvc_scheduled_payload(
                    cap_payload,
                    generated=generated,
                    task_title=task_title,
                )
            await _post_task_event(cloud, base, headers, run_id, "thinking", {"text": f"正在调用 {capability_id}"})
            if capability_id == "hifly.video.create_by_tts":
                result = await _run_shanjian_digital_human_workflow(
                    cap_payload,
                    headers=headers,
                    run_id=run_id,
                    cloud=cloud,
                    base=base,
                    current_item=item,
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
            elif capability_id == _SEEDANCE_TVC_CAPABILITY_ID:
                result = await _run_seedance_tvc_scheduled_pipeline(
                    cap_payload=cap_payload,
                    headers=headers,
                    cloud=cloud,
                    base=base,
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
            elif capability_id == _SEEDANCE_TVC_CAPABILITY_ID:
                refs = _scheduled_goal_video_result_refs(result, jwt_token)
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
            elif capability_id == _SEEDANCE_TVC_CAPABILITY_ID:
                skill_prompt = str(cap_payload.get("task_text") or cap_payload.get("prompt") or skill_prompt).strip()
            elif capability_id == "goal.image.pipeline":
                skill_prompt = str((result.get("plan") or {}).get("image_prompt") or skill_prompt).strip()
            input_refs = {}
            publish_draft: Optional[Dict[str, Any]] = None
            if capability_id == "goal.video.pipeline":
                input_refs = {
                    "video_mode": cap_payload.get("video_mode"),
                    "source_mode": result.get("source_mode") or cap_payload.get("source_mode"),
                    "image_model": result.get("image_model"),
                    "candidate_group": result.get("candidate_group"),
                    "memory_doc_ids": cap_payload.get("memory_doc_ids") or [],
                    "reference_asset_id": result.get("reference_asset_id"),
                    "reference_image_urls": cap_payload.get("reference_image_urls") or [],
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
            elif capability_id == _SEEDANCE_TVC_CAPABILITY_ID:
                input_refs = {
                    "source_mode": "seedance_tvc_studio",
                    "video_model": cap_payload.get("video_model"),
                    "video_channel": cap_payload.get("video_channel"),
                    "segment_count": cap_payload.get("segment_count"),
                    "segment_duration_seconds": cap_payload.get("segment_duration_seconds"),
                    "total_duration_seconds": cap_payload.get("total_duration_seconds"),
                    "aspect_ratio": cap_payload.get("aspect_ratio"),
                    "reference_asset_ids": cap_payload.get("reference_asset_ids") or [],
                    "reference_image_urls": cap_payload.get("reference_image_urls") or [],
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
                    "installation_id": publish_cfg.get("installation_id"),
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
        if capability_id == "comfly.daihuo.pipeline":
            result_text, result_payload = _scheduled_tvc_completion(result)
        else:
            result_text = _compact_result_text(result)
            result_payload = {"capability_id": capability_id, "mcp_result": result}
        await _complete_task_run(
            cloud,
            base,
            headers,
            run_id,
            result_text=result_text,
            result_payload=result_payload,
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
    request_id: str = "",
) -> Dict[str, Any]:
    timeout = httpx.Timeout(timeout_seconds, connect=10.0, read=timeout_seconds, write=30.0, pool=10.0)
    local_headers = _local_chat_headers(headers)
    clean_request_id = str(request_id or "").strip()[:180]
    if clean_request_id:
        local_headers["X-Lobster-Request-Id"] = clean_request_id
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as local:
        try:
            resp = await local.post(_local_api_url(path), json=body or {}, headers=local_headers)
        except httpx.ConnectError as exc:
            raise RuntimeError(_local_api_unavailable_message(path, exc)) from exc
    try:
        data = resp.json() if resp.content else {}
    except Exception:
        data = {"detail": (resp.text or "")[:1000]}
    if resp.status_code >= 400:
        raise RuntimeError(str(data.get("detail") or data.get("message") or data or resp.text)[:500])
    if isinstance(data, dict) and data.get("ok") is False:
        raise RuntimeError(str(data.get("detail") or data.get("error") or data.get("message") or data)[:500])
    return data if isinstance(data, dict) else {"result": data}


async def _wait_for_local_native_wechat_task(
    submitted: Dict[str, Any],
    *,
    headers: Dict[str, str],
    timeout_seconds: float = 1800.0,
) -> Dict[str, Any]:
    task = submitted.get("task") if isinstance(submitted.get("task"), dict) else {}
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        return submitted
    deadline = asyncio.get_running_loop().time() + max(30.0, float(timeout_seconds or 1800.0))
    terminal = {"success", "completed", "failed", "partial_failed", "cancelled", "canceled"}
    while True:
        detail = await _get_local_api_json(
            f"/api/native-wechat/tasks/{quote(task_id, safe='')}",
            headers=headers,
            timeout_seconds=30.0,
        )
        current = detail.get("task") if isinstance(detail.get("task"), dict) else task
        status = str(current.get("status") or "").strip().lower()
        if status in terminal:
            result = {**submitted, "task": current, "queued": False}
            if status == "partial_failed":
                result["task"] = {**current, "original_status": status, "status": "partial_success"}
                return result
            if status not in {"success", "completed"}:
                reason = str(current.get("error_message") or current.get("message") or status).strip()
                raise RuntimeError(reason[:500] or "本机微信任务执行失败")
            return result
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError(f"本机微信任务等待超时：{task_id}")
        await asyncio.sleep(1.0)


async def _request_local_auto_reply_stop(account_id: str, headers: Dict[str, str]) -> None:
    try:
        await _post_local_api_json(
            "/api/native-wechat/auto-reply/stop",
            {"account_id": account_id},
            headers=headers,
            timeout_seconds=8.0,
        )
    except Exception as exc:
        logger.debug("[SCHEDULED-TASK] local WeChat stop request failed account_id=%s: %s", account_id, exc)


async def _post_local_api_form(
    path: str,
    fields: Dict[str, Any],
    *,
    headers: Dict[str, str],
    timeout_seconds: float = 120.0,
) -> Dict[str, Any]:
    timeout = httpx.Timeout(timeout_seconds, connect=10.0, read=timeout_seconds, write=30.0, pool=10.0)
    multipart = [
        (str(key), (None, str(value if value is not None else "")))
        for key, value in (fields or {}).items()
    ]
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as local:
        try:
            resp = await local.post(_local_api_url(path), files=multipart, headers=_local_chat_headers(headers))
        except httpx.ConnectError as exc:
            raise RuntimeError(_local_api_unavailable_message(path, exc)) from exc
    try:
        data = resp.json() if resp.content else {}
    except Exception:
        data = {"detail": (resp.text or "")[:1000]}
    if resp.status_code >= 400:
        raise RuntimeError(str(data.get("detail") or data.get("message") or data or resp.text)[:500])
    if isinstance(data, dict) and data.get("ok") is False:
        raise RuntimeError(str(data.get("detail") or data.get("error") or data.get("message") or data)[:500])
    return data if isinstance(data, dict) else {"result": data}


async def _get_local_api_json(
    path: str,
    *,
    headers: Dict[str, str],
    timeout_seconds: float = 120.0,
) -> Dict[str, Any]:
    timeout = httpx.Timeout(timeout_seconds, connect=10.0, read=timeout_seconds, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as local:
        try:
            resp = await local.get(_local_api_url(path), headers=_local_chat_headers(headers))
        except httpx.ConnectError as exc:
            raise RuntimeError(_local_api_unavailable_message(path, exc)) from exc
    try:
        data = resp.json() if resp.content else {}
    except Exception:
        data = {"detail": (resp.text or "")[:1000]}
    if resp.status_code >= 400:
        raise RuntimeError(str(data.get("detail") or data.get("message") or data or resp.text)[:500])
    if isinstance(data, dict) and data.get("ok") is False:
        raise RuntimeError(str(data.get("detail") or data.get("error") or data.get("message") or data)[:500])
    return data if isinstance(data, dict) else {"result": data}


def _image_studio_reference_urls(value: Any) -> List[str]:
    rows = value if isinstance(value, list) else [value]
    urls: List[str] = []
    for row in rows:
        url = str(row or "").strip()
        if url.startswith(("http://", "https://")) and url not in urls:
            urls.append(url)
        if len(urls) >= 12:
            break
    return urls


def _image_studio_prompt_with_reference_hints(
    prompt: str,
    reference_urls: List[str],
    reference_purposes: Any,
) -> str:
    purposes = reference_purposes if isinstance(reference_purposes, list) else []
    hints: List[str] = []
    for index, _url in enumerate(reference_urls):
        purpose = str(purposes[index] if index < len(purposes) else "auto").strip().lower() or "auto"
        template = _IMAGE_STUDIO_REFERENCE_HINTS.get(purpose, _IMAGE_STUDIO_REFERENCE_HINTS["auto"])
        hints.append(template.replace("{n}", str(index + 1)))
    text = str(prompt or "").strip()
    hint_text = "\n".join(hints)
    return f"{hint_text}\n\n用户提示词：{text}" if hint_text else text


def _image_studio_completed_result(job: Dict[str, Any], *, job_id: str, prompt: str) -> Dict[str, Any]:
    raw_images = job.get("images") if isinstance(job.get("images"), list) else []
    saved_assets = job.get("saved_assets") if isinstance(job.get("saved_assets"), list) else []
    images: List[Dict[str, Any]] = []
    asset_ids: List[str] = []
    urls: List[str] = []

    def add_asset_id(value: Any) -> None:
        asset_id = str(value or "").strip()
        if asset_id and asset_id not in asset_ids:
            asset_ids.append(asset_id)

    def add_url(value: Any) -> None:
        url = str(value or "").strip()
        if url.startswith(("http://", "https://")) and url not in urls:
            urls.append(url)

    for row in raw_images:
        if not isinstance(row, dict):
            continue
        asset_id = str(row.get("asset_id") or "").strip()
        url = str(row.get("source_url") or row.get("url") or "").strip()
        add_asset_id(asset_id)
        add_url(url)
        if asset_id or url:
            images.append({"asset_id": asset_id, "url": url, "source_url": url, "media_type": "image"})
    for row in saved_assets:
        if not isinstance(row, dict):
            continue
        asset = row.get("asset") if isinstance(row.get("asset"), dict) else {}
        asset_id = str(row.get("asset_id") or asset.get("asset_id") or asset.get("id") or "").strip()
        url = str(row.get("source_url") or row.get("url") or asset.get("source_url") or asset.get("url") or "").strip()
        add_asset_id(asset_id)
        add_url(url)
        if (asset_id or url) and not any(item.get("asset_id") == asset_id and item.get("url") == url for item in images):
            images.append({"asset_id": asset_id, "url": url, "source_url": url, "media_type": "image"})

    return {
        "ok": True,
        "job_id": job_id,
        "status": "completed",
        "prompt": prompt,
        "images": images,
        "saved_assets": saved_assets,
        "media_urls": urls,
        "result_refs": {"asset_ids": asset_ids, "urls": urls, "saved_assets": saved_assets},
        "meta": job.get("meta") if isinstance(job.get("meta"), dict) else {},
    }


def _local_bestseller_final_video(job: Dict[str, Any]) -> Dict[str, str]:
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    final_video = result.get("final_video") if isinstance(result.get("final_video"), dict) else {}
    asset_id = str(final_video.get("asset_id") or "").strip()
    url = str(final_video.get("url") or final_video.get("source_url") or "").strip()
    kind = str(final_video.get("kind") or "").strip()
    if asset_id or url:
        return {"asset_id": asset_id, "url": url, "kind": kind or "final_video"}

    priorities = {"local_bestseller_bgm_final": 3, "local_bestseller_captioned": 2, "merged_final": 1}
    candidates: List[tuple[int, Dict[str, str]]] = []
    for item in job.get("saved_assets") if isinstance(job.get("saved_assets"), list) else []:
        if not isinstance(item, dict):
            continue
        asset = item.get("asset") if isinstance(item.get("asset"), dict) else {}
        aid = str(item.get("asset_id") or asset.get("asset_id") or "").strip()
        source_url = str(item.get("source_url") or item.get("url") or asset.get("source_url") or asset.get("url") or "").strip()
        item_kind = str(item.get("kind") or "").strip()
        if aid or source_url:
            candidates.append((priorities.get(item_kind, 0), {"asset_id": aid, "url": source_url, "kind": item_kind}))
    return max(candidates, key=lambda item: item[0])[1] if candidates else {}


async def _wait_local_bestseller_video(
    submitted: Dict[str, Any],
    *,
    headers: Dict[str, str],
    timeout_seconds: float = 7200.0,
    poll_interval_seconds: float = 5.0,
) -> Dict[str, Any]:
    item = submitted.get("item") if isinstance(submitted.get("item"), dict) else {}
    job_id = str(item.get("video_job_id") or item.get("video_task_id") or submitted.get("job_id") or "").strip()
    poll_path = str(item.get("video_poll_path") or submitted.get("poll_path") or "").strip()
    if not poll_path and job_id:
        poll_path = f"/api/comfly-seedance-tvc/pipeline/jobs/{job_id}"
    if not poll_path:
        raise RuntimeError("同城爆款视频未返回可查询的任务ID")

    deadline = asyncio.get_running_loop().time() + max(30.0, float(timeout_seconds))
    while True:
        job = await _get_local_api_json(
            poll_path + ("&" if "?" in poll_path else "?") + "compact=false",
            headers=headers,
            timeout_seconds=180.0,
        )
        status = str(job.get("status") or "").strip().lower()
        if status == "failed":
            raise RuntimeError(str(job.get("error") or job.get("post_error") or "同城爆款视频生成失败")[:500])
        if status == "completed":
            final_video = _local_bestseller_final_video(job)
            if not final_video:
                raise RuntimeError("同城爆款视频任务已结束，但未取得最终视频素材")
            completed_item = {
                **item,
                "video_status": "completed",
                "status": "video_completed",
                "video_asset_id": final_video.get("asset_id") or "",
                "video_url": final_video.get("url") or "",
                "final_video": final_video,
            }
            return {**submitted, "item": completed_item, "job_result": job, "final_video": final_video}
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError("同城爆款视频生成超时，未取得最终成片")
        await asyncio.sleep(max(0.1, float(poll_interval_seconds)))


async def _post_cloud_api_json(
    path: str,
    body: Dict[str, Any],
    *,
    cloud: Optional[httpx.AsyncClient],
    base: str,
    headers: Dict[str, str],
    timeout_seconds: float = 7200.0,
) -> Dict[str, Any]:
    if cloud is None or not base:
        raise RuntimeError("cloud api connection missing")
    timeout = httpx.Timeout(timeout_seconds, connect=10.0, read=timeout_seconds, write=30.0, pool=10.0)
    resp = await cloud.post(f"{base}{path}", json=body or {}, headers=headers, timeout=timeout)
    try:
        data = resp.json() if resp.content else {}
    except Exception:
        data = {"detail": (resp.text or "")[:1000]}
    if resp.status_code >= 400:
        raise RuntimeError(str(data.get("detail") or data.get("message") or data or resp.text)[:500])
    if isinstance(data, dict) and data.get("ok") is False:
        raise RuntimeError(str(data.get("detail") or data.get("error") or data.get("message") or data)[:500])
    return data if isinstance(data, dict) else {"result": data}


async def _ensure_local_workflow_asset(
    *,
    asset_id: str,
    source_url: str,
    media_type: str,
    name: str,
    prompt: str,
    headers: Dict[str, str],
    run_id: str = "",
) -> tuple[str, Dict[str, Any]]:
    """Return an asset ID that is present in this Online client's asset DB.

    Workflow results can contain an asset ID created by the cloud server. That
    ID is not usable by the local /api/publish endpoint until the media has
    been downloaded into the Online asset library. Prefer an existing local
    row, and otherwise materialize it from the public result URL.
    """
    requested_id = str(asset_id or "").strip()
    public_url = str(source_url or "").strip()
    if requested_id:
        try:
            availability = await _get_local_api_json(
                f"/api/assets/{quote(requested_id, safe='')}/availability",
                headers=headers,
                timeout_seconds=20.0,
            )
            local_id = str(availability.get("asset_id") or requested_id).strip()
            if local_id and bool(availability.get("available")):
                logger.info(
                    "[H5-WORKFLOW] local asset ready asset_id=%s requested_asset_id=%s source=existing",
                    local_id,
                    requested_id,
                )
                return local_id, {"status": "existing", "requested_asset_id": requested_id}
        except Exception as exc:
            logger.info(
                "[H5-WORKFLOW] local asset missing asset_id=%s; will materialize from URL=%s err=%s",
                requested_id,
                public_url[:160] if public_url else "-",
                str(exc)[:240],
            )

    if not public_url.startswith(("http://", "https://")):
        raise RuntimeError(
            f"客户端素材不存在: {requested_id or '(empty)'}，且任务没有可转存的公网 URL"
        )

    saved = await _post_local_api_json(
        "/api/assets/save-url",
        {
            "url": public_url,
            "media_type": str(media_type or "video").strip().lower() or "video",
            "asset_origin": "generated",
            "content_visibility": "internal",
            "name": str(name or "H5工作流素材").strip() or "H5工作流素材",
            "prompt": str(prompt or "").strip(),
            "tags": "H5工作流生成",
            "generation_task_id": str(run_id or "").strip() or None,
        },
        headers=headers,
        timeout_seconds=1800.0,
        request_id=f"h5:{run_id}:asset-materialize" if run_id else "",
    )
    local_id = str(saved.get("asset_id") or "").strip()
    if not local_id:
        raise RuntimeError("公网素材已转存，但客户端没有返回本地 asset_id")
    logger.info(
        "[H5-WORKFLOW] local asset materialized requested_asset_id=%s local_asset_id=%s url=%s",
        requested_id or "-",
        local_id,
        public_url[:160],
    )
    return local_id, {
        "status": "materialized",
        "requested_asset_id": requested_id,
        "source_url": public_url,
    }


async def _get_cloud_api_json(
    path: str,
    *,
    cloud: Optional[httpx.AsyncClient],
    base: str,
    headers: Dict[str, str],
    timeout_seconds: float = 30.0,
) -> Dict[str, Any]:
    """Read current cloud-side configuration for long-running local workflows."""
    if cloud is None or not base:
        raise RuntimeError("cloud api connection missing")
    timeout = httpx.Timeout(timeout_seconds, connect=10.0, read=timeout_seconds, write=30.0, pool=10.0)
    resp = await cloud.get(f"{base}{path}", headers=headers, timeout=timeout)
    try:
        data = resp.json() if resp.content else {}
    except Exception:
        data = {"detail": (resp.text or "")[:1000]}
    if resp.status_code >= 400:
        raise RuntimeError(str(data.get("detail") or data.get("message") or data or resp.text)[:500])
    return data if isinstance(data, dict) else {"result": data}


def _parse_run_time(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _workflow_local_run_date(value: Optional[datetime], offset_minutes: int) -> Optional[date]:
    if value is None:
        return None
    utc_value = value
    if value.tzinfo is not None:
        utc_value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return (utc_value + timedelta(minutes=offset_minutes)).date()


def _workflow_target_list(source: Dict[str, Any], *keys: str) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for key in keys:
        value = source.get(key) if isinstance(source, dict) else None
        values = value if isinstance(value, list) else [value]
        for raw in values:
            for part in re.split(r"[\s,，、;；]+", str(raw or "")):
                item = part.strip()
                if not item:
                    continue
                dedupe_key = item.lower()
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                out.append(item)
    return out


def _run_h5_context(run: Dict[str, Any]) -> Dict[str, Any]:
    payload = run.get("payload") if isinstance(run.get("payload"), dict) else {}
    ctx = payload.get("h5_context") if isinstance(payload.get("h5_context"), dict) else {}
    if ctx:
        return ctx
    result_payload = run.get("result_payload") if isinstance(run.get("result_payload"), dict) else {}
    params = result_payload.get("params") if isinstance(result_payload.get("params"), dict) else {}
    return params.get("h5_context") if isinstance(params.get("h5_context"), dict) else {}


def _normalize_parent_material_media_type(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    if text in {"video", "short_video", "video_text", "mp4", "mov"}:
        return "video"
    if text in {"image", "image_text", "images", "picture", "photo", "photos", "jpg", "jpeg", "png", "webp"}:
        return "image"
    return ""


def _local_asset_media_type_map(asset_ids: List[str]) -> Dict[str, str]:
    ordered = [str(aid or "").strip() for aid in asset_ids if str(aid or "").strip()]
    ordered = list(dict.fromkeys(ordered))
    if not ordered:
        return {}
    db = SessionLocal()
    try:
        rows = db.query(Asset).filter(Asset.asset_id.in_(ordered)).all()
        return {
            str(row.asset_id): _normalize_parent_material_media_type(row.media_type)
            for row in rows
            if row and _normalize_parent_material_media_type(row.media_type)
        }
    except Exception as exc:
        logger.warning("[H5-WORKFLOW] lookup parent asset media type failed asset_ids=%s err=%s", ordered[:8], exc)
        return {}
    finally:
        db.close()


def _preferred_parent_material_media_type(params: Dict[str, Any]) -> str:
    raw = str(
        (params or {}).get("media_type")
        or (params or {}).get("content_type")
        or (params or {}).get("publish_media_type")
        or ""
    ).strip().lower().replace("-", "_")
    # H5 workflow currently defaults Moments actions to image_text. For parent
    # outputs, the generated local asset type is more reliable than that default.
    if raw == "image_text":
        return ""
    return _normalize_parent_material_media_type(raw)


def _extract_parent_material(payload: Any, preferred_media_type: str = "") -> Dict[str, Any]:
    video_ids: List[str] = []
    image_ids: List[str] = []
    other_ids: List[str] = []
    video_urls: List[str] = []
    image_urls: List[str] = []
    other_urls: List[str] = []
    seen: set[str] = set()
    skip_keys = {"params", "input_refs", "request", "prompt", "requirements", "h5_context"}
    video_id_keys = {"video_asset_id", "final_video_asset_id", "video_material_id"}
    image_id_keys = {"image_asset_id", "cover_asset_id", "final_image_asset_id", "image_material_id"}
    generic_id_keys = {"asset_id", "final_asset_id", "material_asset_id", "saved_asset_id"}
    video_url_keys = {"video_url", "video_uri", "video_file_url"}
    image_url_keys = {"image_url", "cover_url", "image_file_url"}
    generic_url_keys = {"url", "file_url", "public_url", "media_url"}

    def add(value: Any, kind: str, is_url: bool) -> None:
        text = str(value or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        target = video_urls if is_url and kind == "video" else image_urls if is_url and kind == "image" else other_urls if is_url else video_ids if kind == "video" else image_ids if kind == "image" else other_ids
        target.append(text)

    def visit(value: Any, inherited_kind: str = "") -> None:
        if isinstance(value, list):
            for item in value:
                visit(item, inherited_kind)
            return
        if not isinstance(value, dict):
            return
        item_kind = str(value.get("media_type") or value.get("type") or inherited_kind or "").strip().lower()
        if item_kind not in {"video", "image"}:
            item_kind = ""
        for raw_key, raw_value in value.items():
            key = str(raw_key or "").strip().lower()
            if key in skip_keys:
                continue
            kind = "video" if key in video_id_keys or key in video_url_keys else "image" if key in image_id_keys or key in image_url_keys else item_kind
            if key in video_id_keys or key in image_id_keys or key in generic_id_keys:
                add(raw_value, kind, False)
            elif key in video_url_keys or key in image_url_keys or key in generic_url_keys:
                add(raw_value, kind, True)
            elif key.endswith("_asset_ids") and isinstance(raw_value, list):
                for item in raw_value:
                    add(item, "video" if "video" in key else "image" if "image" in key else item_kind, False)
            elif key.endswith("_urls") and isinstance(raw_value, list):
                for item in raw_value:
                    add(item, "video" if "video" in key else "image" if "image" in key else item_kind, True)
            elif key == "asset_ids" and isinstance(raw_value, list):
                for item in raw_value:
                    add(item, item_kind, False)
            elif key in {"final_video", "captioned_video", "bgm_video"}:
                visit(raw_value, "video")
            elif key in {"assets", "saved_assets", "result_refs", "outputs", "output", "item", "video_result", "image_result", "local_result"}:
                visit(raw_value, item_kind)
            elif isinstance(raw_value, (dict, list)):
                visit(raw_value, item_kind)

    visit(payload)

    if other_ids:
        typed = _local_asset_media_type_map(other_ids)
        remaining_other_ids: List[str] = []
        for aid in other_ids:
            kind = typed.get(aid)
            if kind == "video":
                if aid not in video_ids:
                    video_ids.append(aid)
            elif kind == "image":
                if aid not in image_ids:
                    image_ids.append(aid)
            else:
                remaining_other_ids.append(aid)
        other_ids = remaining_other_ids

    def first_url(*buckets: List[str]) -> str:
        for bucket in buckets:
            if bucket:
                return bucket[0]
        return ""

    def asset_result(asset_id: str, media_type: str, *fallback_buckets: List[str]) -> Dict[str, Any]:
        result = {"asset_id": asset_id, "media_type": media_type}
        fallback = first_url(*fallback_buckets)
        if fallback:
            result["url"] = fallback
        return result

    def url_result(url: str, media_type: str) -> Dict[str, Any]:
        return {"url": url, "media_type": media_type}

    preferred = _normalize_parent_material_media_type(preferred_media_type)
    if preferred == "image":
        if image_ids:
            return asset_result(image_ids[0], "image", image_urls, other_urls)
        if other_ids:
            return asset_result(other_ids[0], "image", image_urls, other_urls)
        if image_urls:
            return url_result(image_urls[0], "image")
        if video_ids:
            return asset_result(video_ids[0], "video", video_urls, other_urls)
        if video_urls:
            return url_result(video_urls[0], "video")
        if other_urls:
            return url_result(other_urls[0], "image")
    elif preferred == "video":
        if video_ids:
            return asset_result(video_ids[0], "video", video_urls, other_urls)
        if video_urls:
            return url_result(video_urls[0], "video")
        return {}

    if video_ids:
        return asset_result(video_ids[0], "video", video_urls, other_urls)
    if video_urls:
        return url_result(video_urls[0], "video")
    if image_ids:
        return asset_result(image_ids[0], "image", image_urls, other_urls)
    if image_urls:
        return url_result(image_urls[0], "image")
    if other_ids:
        return asset_result(other_ids[0], "video", other_urls)
    if other_urls:
        return url_result(other_urls[0], "video")
    return {}


async def _resolve_parent_workflow_results(
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    *,
    params: Dict[str, Any],
    current_item: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    source_node_id = str(params.get("source_workflow_node_id") or "").strip()
    if not source_node_id:
        raise RuntimeError("动作缺少上级节点")
    current_context = params.get("h5_context") if isinstance(params.get("h5_context"), dict) else {}
    template_id = str(current_context.get("workflow_template_id") or "").strip()
    current_time = _parse_run_time(
        (current_item or {}).get("started_at")
        or (current_item or {}).get("claimed_at")
        or (current_item or {}).get("created_at")
    )
    schedule_config = params.get("schedule_config") if isinstance(params.get("schedule_config"), dict) else {}
    try:
        timezone_offset_minutes = int(schedule_config.get("timezone_offset_minutes", 480))
    except (TypeError, ValueError):
        timezone_offset_minutes = 480
    timezone_offset_minutes = max(-840, min(timezone_offset_minutes, 840))
    current_local_date = _workflow_local_run_date(current_time, timezone_offset_minutes)
    try:
        resp = await cloud.get(
            f"{base}/api/scheduled-tasks/runs",
            params={"limit": 200, "offset": 0},
            headers=headers,
        )
        data = resp.json() if resp.content else {}
    except Exception as exc:
        raise RuntimeError("读取上级节点执行记录失败") from exc
    if resp.status_code >= 400 or not isinstance(data, dict):
        raise RuntimeError("读取上级节点执行记录失败")
    runs = data.get("runs") if isinstance(data.get("runs"), list) else []
    candidates: List[Dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, dict) or str(run.get("status") or "").strip().lower() not in {"completed", "success"}:
            continue
        if str(run.get("id") or "").strip() == str((current_item or {}).get("id") or "").strip():
            continue
        ctx = _run_h5_context(run)
        if str(ctx.get("workflow_node_id") or "").strip() != source_node_id:
            continue
        if template_id and str(ctx.get("workflow_template_id") or "").strip() != template_id:
            continue
        run_time = _parse_run_time(run.get("finished_at") or run.get("updated_at") or run.get("created_at"))
        run_schedule_time = _parse_run_time(run.get("created_at")) or run_time
        if current_time and run_schedule_time and run_schedule_time > current_time + timedelta(minutes=10):
            continue
        if current_local_date and _workflow_local_run_date(run_schedule_time, timezone_offset_minutes) != current_local_date:
            continue
        detail_run = run
        run_id = str(run.get("id") or "").strip()
        if run_id:
            try:
                detail_resp = await cloud.get(
                    f"{base}/api/scheduled-tasks/runs/{run_id}",
                    headers=headers,
                )
                detail_data = detail_resp.json() if detail_resp.content else {}
                if detail_resp.status_code < 400 and isinstance(detail_data, dict) and isinstance(detail_data.get("run"), dict):
                    detail_run = detail_data["run"]
            except Exception as exc:
                logger.warning("[H5-WORKFLOW] fetch parent run detail failed run_id=%s err=%s", run_id, exc)
        parent_result_payload = detail_run.get("result_payload") or {}
        candidates.append(
            {
                "source_run_id": run_id,
                "run": detail_run,
                "result_payload": parent_result_payload if isinstance(parent_result_payload, dict) else {},
            }
        )
    return candidates


async def _resolve_parent_workflow_material(
    cloud: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    *,
    params: Dict[str, Any],
    current_item: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not str(params.get("source_workflow_node_id") or "").strip():
        raise RuntimeError("发布动作缺少上级节点")
    parent_runs = await _resolve_parent_workflow_results(
        cloud,
        base,
        headers,
        params=params,
        current_item=current_item,
    )
    candidates: List[Dict[str, Any]] = []
    for parent in parent_runs:
        parent_result_payload = parent.get("result_payload") if isinstance(parent.get("result_payload"), dict) else {}
        material = _extract_parent_material(
            parent_result_payload,
            preferred_media_type=_preferred_parent_material_media_type(params),
        )
        if not material:
            continue
        if material.get("asset_id") and not material.get("url"):
            try:
                cloud_asset = await _get_cloud_api_json(
                    f"/api/assets/{quote(str(material.get('asset_id')), safe='')}",
                    cloud=cloud,
                    base=base,
                    headers=headers,
                    timeout_seconds=20.0,
                )
                cloud_url = str(
                    cloud_asset.get("source_url")
                    or cloud_asset.get("open_url")
                    or cloud_asset.get("preview_url")
                    or ""
                ).strip()
                if cloud_url.startswith(("http://", "https://")):
                    material["url"] = cloud_url
            except Exception as exc:
                logger.info(
                    "[H5-WORKFLOW] cloud asset URL lookup failed asset_id=%s err=%s",
                    str(material.get("asset_id") or "")[:128],
                    str(exc)[:240],
                )
        publish_context = _extract_parent_publish_context(parent_result_payload)
        candidates.append({**material, **publish_context, "source_run_id": str(parent.get("source_run_id") or "")})
    if not candidates:
        raise RuntimeError("上级节点还没有可发布的素材")
    return candidates[0]


_MAINLAND_MOBILE_RE = re.compile(r"(?<!\d)(?:(?:\+?86)[\s-]*)?(1[3-9](?:[\s-]?\d){9})(?!\d)")


def _extract_mainland_mobile_numbers(value: Any, *, limit: int = 100) -> List[str]:
    texts: List[str] = []

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                if key in {"incoming_message", "preview_text"} and isinstance(item, (str, int, float)):
                    text = str(item or "").strip()
                    if text:
                        texts.append(text)
                elif isinstance(item, (dict, list)):
                    collect(item)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    collect(value)
    out: List[str] = []
    seen: set[str] = set()
    for text in texts:
        for match in _MAINLAND_MOBILE_RE.finditer(text):
            phone = re.sub(r"\D", "", match.group(1))
            if len(phone) != 11 or phone in seen:
                continue
            seen.add(phone)
            out.append(phone)
            if len(out) >= max(1, int(limit or 1)):
                return out
    return out


def _workflow_text(value: Any, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit] if text else ""


def _workflow_has_content(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_workflow_has_content(item) for item in value.values())
    if isinstance(value, list):
        return any(_workflow_has_content(item) for item in value)
    return bool(str(value or "").strip())


def _workflow_list_texts(value: Any, limit: int = 20) -> List[str]:
    values = value if isinstance(value, list) else [value]
    out: List[str] = []
    seen: set[str] = set()
    for item in values:
        if isinstance(item, dict):
            text = _workflow_text(
                item.get("display_name")
                or item.get("name")
                or item.get("keyword")
                or item.get("account_name")
                or item.get("account_id")
                or item.get("title"),
                180,
            )
        else:
            text = _workflow_text(item, 180)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _workflow_int_ids(value: Any, limit: int = 20) -> List[int]:
    values = value if isinstance(value, list) else [value]
    out: List[int] = []
    seen: set[int] = set()
    for item in values:
        try:
            ident = int(item or 0)
        except Exception:
            continue
        if ident <= 0 or ident in seen:
            continue
        seen.add(ident)
        out.append(ident)
        if len(out) >= limit:
            break
    return out


def _workflow_auth_token(headers: Dict[str, str]) -> str:
    for key in ("Authorization", "authorization"):
        raw = str((headers or {}).get(key) or "").strip()
        if raw.lower().startswith("bearer "):
            return raw.split(" ", 1)[1].strip()
        if raw:
            return raw
    return ""


def _workflow_installation_id(headers: Dict[str, str]) -> str:
    for key in ("X-Installation-Id", "x-installation-id"):
        value = str((headers or {}).get(key) or "").strip()
        if value:
            return value
    return ""


def _is_local_asset_missing_error(exc: Exception) -> bool:
    """Only retry publish when the local API positively rejected the asset."""
    text = str(exc or "").strip().lower()
    if not text:
        return False
    if "素材不存在" in text or "素材 不存在" in text:
        return True
    return (
        "asset" in text
        and ("not found" in text or "missing" in text or "does not exist" in text)
    )


def _workflow_language_label(language: str) -> str:
    raw = _workflow_text(language, 64)
    lowered = raw.lower()
    if lowered in {"zh", "zh-cn", "中文", "简体中文", "chinese"}:
        return "中文"
    if lowered in {"en", "en-us", "english", "英文", "英语"}:
        return "English"
    if lowered in {"ja", "ja-jp", "japanese", "日文", "日语"}:
        return "日本語"
    if lowered in {"ko", "ko-kr", "korean", "韩文", "韩语"}:
        return "한국어"
    return raw or "中文"


def _workflow_script_candidate(value: Any, limit: int = 700) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text[:limit].strip()


def _provided_shanjian_workflow_script(source: Dict[str, Any]) -> str:
    explicit = (
        _workflow_script_candidate(source.get("script"))
        or _workflow_script_candidate(source.get("text"))
    )
    if explicit:
        return explicit
    if _workflow_text(source.get("script_source"), 64) == "ip_daily_industry_hot_oral":
        return ""
    return _workflow_script_candidate(source.get("prompt"))


def _workflow_memory_doc_summaries(value: Any, limit: int = 8) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: List[Dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            title = _workflow_text(item.get("title") or item.get("name") or item.get("doc_type"), 120)
            content = _workflow_text(
                item.get("content")
                or item.get("summary")
                or item.get("text")
                or item.get("preview")
                or item.get("body"),
                1200,
            )
        else:
            title = ""
            content = _workflow_text(item, 1200)
        if not (title or content):
            continue
        out.append({"title": title, "content": content})
        if len(out) >= limit:
            break
    return out


async def _workflow_event(
    cloud: Optional[httpx.AsyncClient],
    base: str,
    headers: Dict[str, str],
    run_id: str,
    text: str,
) -> None:
    if cloud is None or not base or not run_id:
        return
    await _post_task_event(cloud, base, headers, run_id, "thinking", {"text": text})


async def _generate_shanjian_workflow_script(
    *,
    source: Dict[str, Any],
    cloud: Optional[httpx.AsyncClient],
    base: str,
    headers: Dict[str, str],
    run_id: str,
) -> Dict[str, Any]:
    label = _workflow_text(
        source.get("sales_node_label")
        or source.get("task_title")
        or source.get("title")
        or "数字人口播视频",
        160,
    )
    title = _workflow_text(source.get("title") or label or "数字人口播", 80)
    language = _workflow_text(source.get("language") or source.get("target_language") or "zh-CN", 64)
    if cloud is None or not base:
        raise RuntimeError("数字人生成缺少云端连接，无法调用 IP 日更生成行业口播文案")

    requirements = source.get("requirements") if isinstance(source.get("requirements"), dict) else {}
    keyword_ids = _workflow_int_ids(source.get("keyword_ids"), 20)
    keywords = _workflow_list_texts(source.get("keyword_texts") or source.get("keywords"))
    memory_doc_ids = _workflow_list_texts(source.get("memory_doc_ids"), 12)
    memory_docs = _workflow_memory_doc_summaries(source.get("memory_docs"))
    missing: List[str] = []
    if not _workflow_has_content(requirements):
        missing.append("IP人设定位资料调查")
    if not keywords:
        missing.append("当前启用模板的行业关键词")
    if not (memory_doc_ids or memory_docs):
        missing.append("当前启用模板的记忆文件")
    if missing:
        raise RuntimeError("数字人生成缺少：" + "、".join(missing) + "。请先到 IP人设定位 补足后再启动工作流。")

    persona_json = json.dumps(requirements, ensure_ascii=False, separators=(",", ":"))
    extra_requirements = "\n".join(
        [
            "本次只生成 1 条行业热门口播文案，并直接作为数字人视频的完整口播脚本。",
            f"输出语种必须是：{_workflow_language_label(language)}。",
            "目标口播时长 20 到 25 秒；中文控制在 90 到 120 字且不得超过 120 字，英文控制在 45 到 60 词且不得超过 60 词，其他语种按同等时长控制。",
            "文案必须自然、完整、可直接口播，不写镜头指令、括号说明或虚构数据，不要为了凑长度重复表达。",
            f"IP人设资料：{persona_json[:2200]}",
        ]
    )[:4000]
    await _workflow_event(cloud, base, headers, run_id, "正在调用 IP 日更生成行业热门口播文案")
    generated = await _post_cloud_api_json(
        "/api/ip-content/generate/industry-hot-oral",
        {
            "keyword_ids": keyword_ids,
            "keyword_texts": keywords,
            "memory_docs": memory_docs,
            "extra_requirements": extra_requirements,
            "count": 1,
            "sync_before": bool(source.get("industry_oral_sync_before", False)),
            "group_id": run_id,
        },
        cloud=cloud,
        base=base,
        headers=headers,
        timeout_seconds=600.0,
    )
    records = generated.get("records") if isinstance(generated.get("records"), list) else []
    drafts = generated.get("drafts") if isinstance(generated.get("drafts"), list) else []
    selected: Dict[str, Any] = {}
    script = ""
    for candidate in [*records, *drafts]:
        if not isinstance(candidate, dict):
            continue
        candidate_script = _workflow_script_candidate(
            candidate.get("body") or candidate.get("content") or candidate.get("script") or candidate.get("text")
        )
        if candidate_script:
            selected = candidate
            script = candidate_script
            break
    if not script:
        raise RuntimeError("IP 日更未返回有效的行业口播文案，数字人任务未继续执行")
    await _workflow_event(cloud, base, headers, run_id, "行业热门口播文案已生成，正在用于数字人视频")
    return {
        "title": _workflow_text(selected.get("title") or title or "数字人口播", 80),
        "script": script,
        "caption_hint": _workflow_text(selected.get("title"), 200),
        "language": language or "zh-CN",
        "ip_daily_group_id": _workflow_text(generated.get("group_id"), 128),
        "ip_daily_record_id": _workflow_text(selected.get("record_id") or selected.get("id"), 128),
        "ip_daily_record": selected,
    }


def _normalize_virtualman_candidates(value: Any) -> List[Dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    candidates: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = _workflow_text(row.get("status"), 32).lower()
        if status and status not in {"succeed", "success", "completed", "complete", "done"}:
            continue
        virtualman_id = _workflow_text(row.get("virtualman_id") or row.get("virtualmanId"), 128)
        if not virtualman_id or virtualman_id in seen:
            continue
        seen.add(virtualman_id)
        profile_id = _safe_int(row.get("profile_id") or row.get("id"))
        candidates.append(
            {
                "profile_id": profile_id,
                "virtualman_id": virtualman_id,
                "title": _workflow_text(row.get("title") or row.get("name"), 128),
                "cover_url": _workflow_text(row.get("cover_url") or row.get("coverUrl"), 1000),
            }
        )
    return sorted(
        candidates,
        key=lambda item: (
            0 if _safe_int(item.get("profile_id")) > 0 else 1,
            _safe_int(item.get("profile_id")),
            str(item.get("virtualman_id") or ""),
        ),
    )


def _select_daily_virtualman(
    candidates: List[Dict[str, Any]],
    *,
    local_day: date,
    rotation_key: str,
    sequence_slot: Optional[int] = None,
) -> Dict[str, Any]:
    normalized = _normalize_virtualman_candidates(candidates)
    if not normalized:
        return {}
    if sequence_slot is not None:
        slot = max(0, int(sequence_slot))
        return dict(normalized[(local_day.toordinal() + slot) % len(normalized)])
    digest = hashlib.sha256(str(rotation_key or "digital-human").encode("utf-8")).digest()
    node_offset = int.from_bytes(digest[:8], "big")
    return dict(normalized[(local_day.toordinal() + node_offset) % len(normalized)])


def _normalize_voice_candidates(value: Any) -> List[Dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    candidates: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = _workflow_text(row.get("status"), 32).lower()
        if status and status not in {"succeed", "success", "completed", "complete", "done"}:
            continue
        voice = _workflow_text(row.get("voice") or row.get("voice_id") or row.get("speaker_id") or row.get("speakerId"), 128)
        if not voice or voice in seen:
            continue
        seen.add(voice)
        candidates.append({
            "voice": voice,
            "title": _workflow_text(row.get("title") or row.get("name"), 128),
            "provider": _workflow_text(row.get("provider") or row.get("source"), 32),
            "source_record_id": _safe_int(row.get("source_record_id") or row.get("id")),
        })
    return candidates


def _select_daily_voice(
    candidates: List[Dict[str, Any]],
    *,
    local_day: date,
    rotation_key: str,
    sequence_slot: Optional[int] = None,
) -> Dict[str, Any]:
    normalized = _normalize_voice_candidates(candidates)
    if not normalized:
        return {}
    if sequence_slot is not None:
        slot = max(0, int(sequence_slot))
        return dict(normalized[(local_day.toordinal() + slot) % len(normalized)])
    digest = hashlib.sha256((str(rotation_key or "digital-human") + "|voice").encode("utf-8")).digest()
    offset = int.from_bytes(digest[:8], "big")
    return dict(normalized[(local_day.toordinal() + offset) % len(normalized)])


def _digital_human_rotation_context(
    source: Dict[str, Any],
    current_item: Optional[Dict[str, Any]],
) -> tuple[date, str]:
    schedule = source.get("schedule_config") if isinstance(source.get("schedule_config"), dict) else {}
    try:
        timezone_offset_minutes = int(schedule.get("timezone_offset_minutes", 480))
    except (TypeError, ValueError):
        timezone_offset_minutes = 480
    timezone_offset_minutes = max(-840, min(timezone_offset_minutes, 840))
    run_time = _parse_run_time(
        (current_item or {}).get("scheduled_for")
        or (current_item or {}).get("scheduled_at")
        or (current_item or {}).get("created_at")
        or (current_item or {}).get("started_at")
    )
    local_day = _workflow_local_run_date(run_time or datetime.utcnow(), timezone_offset_minutes) or date.today()
    context = source.get("h5_context") if isinstance(source.get("h5_context"), dict) else {}
    rotation_key = "|".join(
        str(value or "").strip()
        for value in (
            context.get("workflow_template_id"),
            context.get("workflow_node_id"),
            context.get("workflow_node_time"),
            source.get("sales_node_label"),
            source.get("task_title"),
        )
        if str(value or "").strip()
    )
    return local_day, rotation_key or "shanjian-digital-human"


async def _resolve_workflow_virtualman(
    source: Dict[str, Any],
    *,
    cloud: Optional[httpx.AsyncClient],
    base: str,
    headers: Dict[str, str],
    current_item: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    fixed_id = _workflow_text(source.get("virtualman_id") or source.get("virtualmanId"), 128)
    context = source.get("h5_context") if isinstance(source.get("h5_context"), dict) else {}
    selection_mode = _workflow_text(source.get("virtualman_selection_mode"), 32)
    rotation_enabled = selection_mode != "fixed" and (
        selection_mode in {"daily_round_robin", "daily_sequence"}
        or _workflow_text(source.get("script_source"), 64) == "ip_daily_industry_hot_oral"
        or bool(context.get("workflow_node_id"))
    )
    if not rotation_enabled:
        return {"virtualman_id": fixed_id} if fixed_id else {}

    # Refresh the server-side profile list before each generated workflow.
    # A successful response is authoritative (including an empty list), while
    # transient network failures retain the task snapshot as a fallback.
    candidates = _normalize_virtualman_candidates(source.get("virtualman_candidates"))
    profile_refresh_succeeded = False
    if cloud is not None and base:
        try:
            response = await cloud.get(
                f"{base}/api/shanjian-digital-human/profiles",
                headers=headers,
                timeout=30.0,
            )
            data = response.json() if response.content else {}
            if response.status_code < 400 and isinstance(data, dict):
                candidates = _normalize_virtualman_candidates(data.get("items"))
                profile_refresh_succeeded = True
        except Exception as exc:
            logger.warning("[H5-DIGITAL-HUMAN] profile refresh failed, using task snapshot: %s", exc)

    if candidates:
        local_day, rotation_key = _digital_human_rotation_context(source, current_item)
        sequence_slot = (
            max(0, _safe_int(source.get("virtualman_rotation_slot")))
            if "virtualman_rotation_slot" in source
            else None
        )
        selected = _select_daily_virtualman(
            candidates,
            local_day=local_day,
            rotation_key=rotation_key,
            sequence_slot=sequence_slot,
        )
        selected["selection_mode"] = "daily_sequence" if sequence_slot is not None else "daily_round_robin"
        selected["selection_slot"] = sequence_slot
        selected["selection_date"] = local_day.isoformat()
        return selected
    if profile_refresh_succeeded:
        return {}
    return {"virtualman_id": fixed_id} if fixed_id else {}


def _shanjian_video_create_payload(
    source: Dict[str, Any],
    *,
    virtualman_id: str,
    title: str,
    script: str,
    voice: str,
    audio_url: str,
    language: str,
    audio_asset_id: str = "",
    tts_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    long_video = _workflow_flag(source.get("long_video"), False)
    payload: Dict[str, Any] = {
        "virtualman_id": virtualman_id,
        "title": title,
        "language": language,
        "speed_ratio": source.get("speed_ratio") or 1.0,
        "long_video": long_video,
    }
    if script:
        payload["text"] = script
    if voice:
        payload["speaker_id"] = voice
    if audio_url:
        payload["audio_url"] = audio_url
    if audio_asset_id:
        payload["audio_asset_id"] = audio_asset_id

    if long_video:
        duration_value = source.get("video_duration") or source.get("duration_seconds")
        if duration_value in (None, ""):
            duration_value = (tts_data or {}).get("duration_seconds")
        try:
            duration_seconds = int(float(duration_value) + 0.999)
        except (TypeError, ValueError):
            duration_seconds = max(5, int((len("".join(str(script or "").split())) + 3) / 4))
        payload["video_duration"] = max(31, min(duration_seconds, 300))
    else:
        payload["video_duration"] = 30
        payload["hard_max_duration"] = 30

    if "use_template" in source:
        use_template = _workflow_flag(source.get("use_template"), False)
        payload["use_template"] = use_template
        if use_template:
            for key in (
                "template_scene",
                "style_id",
                "materials",
                "material_sound_switch",
                "introduce_name",
                "introduce_description",
                "header_switch",
                "material_switch",
                "subtitle_switch",
                "keyword_switch",
                "watermark_show",
                "material_match_way",
                "resource_preprocess_method",
                "material_composition",
            ):
                if key in source:
                    payload[key] = source[key]
    return payload


async def _run_shanjian_digital_human_workflow(
    source: Dict[str, Any],
    *,
    headers: Dict[str, str],
    run_id: str,
    cloud: Optional[httpx.AsyncClient],
    base: str,
    current_item: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    selected_virtualman = await _resolve_workflow_virtualman(
        source,
        cloud=cloud,
        base=base,
        headers=headers,
        current_item=current_item,
    )
    virtualman_id = _workflow_text(selected_virtualman.get("virtualman_id"), 128)
    drive_mode = _workflow_text(source.get("drive_mode"), 32).lower()
    audio_url = _workflow_text(source.get("audio_url"), 2000)
    audio_asset_id = _workflow_text(source.get("audio_asset_id"), 128)
    audio_mode = drive_mode == "audio" or bool(audio_url or audio_asset_id)
    voice = _workflow_text(source.get("voice") or source.get("speaker_id") or source.get("speakerId"), 128)
    voice_candidates = _normalize_voice_candidates(source.get("voice_candidates"))
    if not audio_mode and voice_candidates:
        local_day, rotation_key = _digital_human_rotation_context(source, current_item)
        sequence_slot = max(0, _safe_int(source.get("virtualman_rotation_slot"))) if "virtualman_rotation_slot" in source else None
        selected_voice = _select_daily_voice(
            voice_candidates,
            local_day=local_day,
            rotation_key=rotation_key,
            sequence_slot=sequence_slot,
        )
        voice = _workflow_text(selected_voice.get("voice"), 128) or voice
    missing = []
    if not virtualman_id:
        missing.append("素材库：请先创建并训练完成可用的数字人形象分身（数字人2.0）")
    if not audio_mode and not voice:
        missing.append("素材库：请先创建可用的声音分身")
    if audio_mode and not audio_url and not audio_asset_id:
        missing.append("请上传或选择驱动音频")
    if missing:
        raise RuntimeError("数字人2.0生成缺少：" + "；".join(missing))
    if cloud is None or not base:
        raise RuntimeError("数字人2.0缺少云端连接，无法提交生成任务")

    if audio_mode:
        generated = {
            "title": _workflow_text(source.get("title") or "数字人口播", 80),
            "script": _provided_shanjian_workflow_script(source),
            "caption_hint": _workflow_text(source.get("title"), 200),
            "language": _workflow_text(source.get("language") or source.get("target_language") or "zh-CN", 64),
            "ip_daily_group_id": "",
            "ip_daily_record_id": "",
            "ip_daily_record": {},
        }
    else:
        provided_script = _provided_shanjian_workflow_script(source)
        if provided_script:
            generated = {
                "title": _workflow_text(source.get("title") or "数字人口播", 80),
                "script": provided_script,
                "caption_hint": _workflow_text(source.get("title"), 200),
                "language": _workflow_text(source.get("language") or source.get("target_language") or "zh-CN", 64),
                "ip_daily_group_id": "",
                "ip_daily_record_id": "",
                "ip_daily_record": {},
            }
        else:
            generated = await _generate_shanjian_workflow_script(
                source=source,
                cloud=cloud,
                base=base,
                headers=headers,
                run_id=run_id,
            )
    script = generated["script"]
    title = generated["title"] or "数字人口播"
    language = generated["language"] or "zh-CN"

    tts_data: Dict[str, Any] = {}
    if not audio_mode:
        tts_payload: Dict[str, Any] = {
            "voice": voice,
            "text": script,
            "rate": str(source.get("rate") or source.get("speed_ratio") or "1"),
            "volume": str(source.get("volume") or "1"),
            "pitch": str(source.get("pitch") if source.get("pitch") is not None else "0"),
            "emotion": str(source.get("emotion") or "happy"),
        }
        instructions = _workflow_text(source.get("instructions"), 500)
        if instructions:
            tts_payload["instructions"] = instructions
        provider = _workflow_text(source.get("voice_provider") or source.get("provider"), 32).lower()
        if provider in {"minimax", "qwen"}:
            tts_payload["voice_provider"] = provider

        await _workflow_event(cloud, base, headers, run_id, "正在合成声音分身音频")
        tts_resp = await cloud.post(
            f"{base}/api/hifly/my/voice/preview-tts",
            json=tts_payload,
            headers=headers,
            timeout=httpx.Timeout(1800.0, connect=10.0, read=1800.0, write=30.0, pool=10.0),
        )
        try:
            tts_data = tts_resp.json() if tts_resp.content else {}
        except Exception:
            tts_data = {"detail": (tts_resp.text or "")[:1000]}
        if not isinstance(tts_data, dict):
            tts_data = {"result": tts_data}
        if tts_resp.status_code >= 400 or tts_data.get("ok") is False:
            raise RuntimeError(str(tts_data.get("detail") or tts_data.get("error") or tts_data.get("message") or tts_data or tts_resp.text)[:500])
        audio_url = str(tts_data.get("audio_url") or "").strip()
        if not audio_url:
            raise RuntimeError("声音分身合成成功，但没有返回可用于数字人2.0的音频地址")
    else:
        await _workflow_event(cloud, base, headers, run_id, "正在使用所选音频驱动数字人2.0")

    await _workflow_event(cloud, base, headers, run_id, "正在提交数字人2.0视频任务")
    create_data = await _post_cloud_api_json(
        "/api/shanjian-digital-human/video/create",
        _shanjian_video_create_payload(
            source,
            virtualman_id=virtualman_id,
            title=title,
            script=script,
            voice=voice,
            audio_url=audio_url,
            audio_asset_id=audio_asset_id,
            language=language,
            tts_data=tts_data,
        ),
        cloud=cloud,
        base=base,
        headers=headers,
        timeout_seconds=1800.0,
    )
    task_id = _workflow_text(create_data.get("task_id"), 128)
    record = create_data.get("record") if isinstance(create_data.get("record"), dict) else {}
    record_id = _safe_int(record.get("id") if record else 0)
    if not task_id and not record_id:
        raise RuntimeError("数字人2.0提交成功但没有返回任务ID")

    poll_timeout = _clamp_int(source.get("poll_timeout_seconds"), 3600, 60, 7200)
    interval = _clamp_int(source.get("poll_interval_seconds"), 10, 5, 60)
    waited = 0
    last: Dict[str, Any] = {"ok": True, "status": "processing", "task_id": task_id, "record": record}
    await _workflow_event(cloud, base, headers, run_id, "正在等待数字人2.0出片")
    while waited <= poll_timeout:
        body: Dict[str, Any] = {}
        if record_id:
            body["record_id"] = record_id
        if task_id:
            body["task_id"] = task_id
        last = await _post_cloud_api_json(
            "/api/shanjian-digital-human/video/task",
            body,
            cloud=cloud,
            base=base,
            headers=headers,
            timeout_seconds=180.0,
        )
        status = _workflow_text(last.get("status"), 64).lower()
        if status in {"succeed", "success", "completed", "complete", "done", "finished"}:
            record = last.get("record") if isinstance(last.get("record"), dict) else record
            video_url = _workflow_text(last.get("video_url") or (record or {}).get("video_url"), 1000)
            cover_url = _workflow_text(last.get("cover_url") or (record or {}).get("cover_url"), 1000)
            if not video_url:
                raise RuntimeError("数字人2.0任务已完成，但没有返回视频链接")
            # The cloud task owns the generated URL, while the follow-up
            # publish endpoint reads the Online client's local asset DB. Make
            # the hand-off explicit so a successful generation is locally
            # publishable as well.
            await _workflow_event(cloud, base, headers, run_id, "正在将成品视频转存到本机素材库")
            local_asset_id = ""
            local_asset: Dict[str, Any] = {"status": "pending"}
            try:
                local_asset_id, local_asset = await _ensure_local_workflow_asset(
                    asset_id="",
                    source_url=video_url,
                    media_type="video",
                    name=title or "数字人口播视频",
                    prompt=script,
                    headers=headers,
                    run_id=run_id,
                )
            except Exception as exc:
                # Keep the cloud generation successful if local materialization
                # is temporarily unavailable. The publish action retries this
                # exact hand-off once it receives a deterministic missing-asset
                # response from the local API.
                local_asset = {"status": "pending", "error": str(exc)[:300]}
                logger.warning(
                    "[H5-WORKFLOW] generated video local materialization pending task_id=%s url=%s err=%s",
                    task_id or record_id,
                    video_url[:160],
                    str(exc)[:300],
                )
            return {
                "ok": True,
                "action": "shanjian_digital_human_video",
                "status": status or "succeed",
                "task_id": task_id or _workflow_text(last.get("task_id"), 128),
                "record_id": record_id,
                "record": record,
                "title": title,
                "script": script,
                "language": language,
                "caption_hint": generated.get("caption_hint") or "",
                "ip_daily_group_id": generated.get("ip_daily_group_id") or "",
                "ip_daily_record_id": generated.get("ip_daily_record_id") or "",
                "ip_daily_record": generated.get("ip_daily_record") or {},
                "virtualman_id": virtualman_id,
                "virtualman_profile_id": selected_virtualman.get("profile_id") or None,
                "virtualman_title": selected_virtualman.get("title") or "",
                "virtualman_selection_mode": selected_virtualman.get("selection_mode") or "fixed",
                "virtualman_selection_slot": selected_virtualman.get("selection_slot"),
                "virtualman_selection_date": selected_virtualman.get("selection_date") or "",
                "voice": voice,
                "media_type": "video",
                "video_url": video_url,
                "video_asset_id": local_asset_id,
                "local_asset": local_asset,
                "cover_url": cover_url,
                "duration": last.get("duration") or (record or {}).get("duration"),
                "source_media_urls": [video_url],
                "media_urls": {"video": [video_url]},
                "result_refs": {"asset_ids": [local_asset_id] if local_asset_id else [], "urls": [video_url]},
                "tts": {
                    "duration_seconds": tts_data.get("duration_seconds") if isinstance(tts_data, dict) else None,
                    "audio_url": (record or {}).get("audio_url") or "",
                },
            }
        if status in {"failed", "fail", "error", "canceled", "cancelled"}:
            raise RuntimeError("数字人2.0生成失败：" + _workflow_text(last.get("message") or (record or {}).get("error_message") or last, 500))
        if waited >= poll_timeout:
            break
        await asyncio.sleep(interval)
        waited += interval
        if waited % 60 < interval:
            await _workflow_event(cloud, base, headers, run_id, f"数字人2.0仍在生成中，已等待 {waited} 秒")

    last["result_ready"] = False
    last["action"] = "shanjian_digital_human_video"
    last["title"] = title
    last["script"] = script
    last["language"] = language
    last["ip_daily_group_id"] = generated.get("ip_daily_group_id") or ""
    last["ip_daily_record_id"] = generated.get("ip_daily_record_id") or ""
    last["ip_daily_record"] = generated.get("ip_daily_record") or {}
    last["virtualman_id"] = virtualman_id
    last["virtualman_profile_id"] = selected_virtualman.get("profile_id") or None
    last["virtualman_title"] = selected_virtualman.get("title") or ""
    last["virtualman_selection_mode"] = selected_virtualman.get("selection_mode") or "fixed"
    last["virtualman_selection_slot"] = selected_virtualman.get("selection_slot")
    last["virtualman_selection_date"] = selected_virtualman.get("selection_date") or ""
    last["voice"] = voice
    last["task_id"] = task_id
    last["record_id"] = record_id
    last["media_type"] = "video"
    return last


def _takeover_monotonic() -> float:
    return asyncio.get_running_loop().time()


async def _run_native_wechat_takeover_session(
    *,
    account_id: str,
    headers: Dict[str, str],
    cloud: Optional[httpx.AsyncClient],
    base: str,
    run_id: str,
    rounds: Optional[int] = None,
    interval_seconds: float = 15.0,
    session_seconds: Optional[float] = None,
    config_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    # The interval starts after a round finishes. Bound the whole session by wall-clock
    # time so slow WeChat scans do not outlive the configured end time.
    interval = max(0.0, float(interval_seconds or 0.0))
    duration_limit = max(1.0, min(float(session_seconds if session_seconds is not None else 1800.0), 86400.0))
    round_limit = max(1, int(rounds)) if rounds is not None else None
    started_at = datetime.utcnow().isoformat()
    started_monotonic = _takeover_monotonic()
    deadline_monotonic = started_monotonic + duration_limit
    output: Dict[str, Any] = {
        "ok": True,
        "mode": "takeover_session",
        "account_id": account_id,
        "session_minutes": round(duration_limit / 60.0, 2),
        "session_seconds": round(duration_limit, 2),
        "interval_seconds": int(interval),
        "completed_rounds": 0,
        "replied": 0,
        "skipped": 0,
        "failed": 0,
        "busy_retry_count": 0,
        "friend_requests_checked": 0,
        "friend_requests_accepted": 0,
        "friend_requests_failed": 0,
        "friend_requests_checked_once": True,
        "items": [],
        "rounds": [],
        "started_at": started_at,
    }
    last_config: Dict[str, Any] = {}
    consecutive_driver_failures = 0
    max_consecutive_driver_failures = 3
    stop_reason = ""
    round_number = 0
    if cloud is not None and base and run_id:
        event_status = await _post_task_event(
            cloud,
            base,
            headers,
            run_id,
            "running",
            {"text": "个微接管启动，正在检查新好友申请（本次仅检查一次）", "stage": "friend_requests"},
        )
        if _task_event_rejects_local_work(event_status):
            await _request_local_auto_reply_stop(account_id, headers)
            stop_reason = "slot_ownership_changed"
    while not stop_reason and _takeover_monotonic() < deadline_monotonic and (
        round_limit is None or round_number < round_limit
    ):
        if round_number and interval:
            remaining = deadline_monotonic - _takeover_monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(interval, remaining))
        if _takeover_monotonic() >= deadline_monotonic:
            break
        round_number += 1
        if cloud is not None and base and run_id:
            event_status = await _post_task_event(
                cloud,
                base,
                headers,
                run_id,
                "running",
                {"text": f"个微私信接管第 {round_number} 轮巡检", "round": round_number, "session_seconds": duration_limit},
            )
            if _task_event_rejects_local_work(event_status):
                await _request_local_auto_reply_stop(account_id, headers)
                stop_reason = "slot_ownership_changed"
                break
        try:
            # A cancelled run can still be inside one synchronous wxauto call
            # for a few seconds after the cloud has rejected its heartbeat.
            # Wait for that local lock to release and retry this same round;
            # otherwise a normal restart is reported as a failed takeover.
            busy_retry_deadline = _takeover_monotonic() + _NATIVE_WECHAT_BUSY_RETRY_SECONDS
            busy_retry_count = 0
            can_recover_busy = bool(cloud is not None and base and run_id)
            while True:
                result = await _post_local_api_json(
                    "/api/native-wechat/auto-reply/run-once",
                    {
                        "account_id": account_id,
                        "force": True,
                        "check_friend_requests": round_number == 1,
                        "config_override": config_override or {},
                    },
                    headers=headers,
                    timeout_seconds=1800.0,
                )
                is_skipped = isinstance(result.get("skipped"), bool) and result.get("skipped")
                reason = str(result.get("reason") or "").strip().lower() if is_skipped else ""
                if reason != "running" or not can_recover_busy:
                    break
                remaining = min(
                    busy_retry_deadline - _takeover_monotonic(),
                    deadline_monotonic - _takeover_monotonic(),
                )
                if remaining <= 0:
                    break
                busy_retry_count += 1
                output["busy_retry_count"] = int(output.get("busy_retry_count") or 0) + 1
                if busy_retry_count == 1:
                    logger.info(
                        "[SCHEDULED-TASK] local WeChat busy; waiting before retry round=%s window=%.1fs config=%s",
                        round_number,
                        _NATIVE_WECHAT_BUSY_RETRY_SECONDS,
                        result.get("config") if isinstance(result.get("config"), dict) else {},
                    )
                await asyncio.sleep(min(_NATIVE_WECHAT_BUSY_RETRY_INTERVAL_SECONDS, remaining))
            if str(result.get("stop_reason") or "").strip().lower() == "cancelled":
                stop_reason = "cancelled"
                break
            # A normal scan reports an integer skipped count. Only the local
            # endpoint's boolean skipped response means that no round ran.
            if isinstance(result.get("skipped"), bool) and result.get("skipped"):
                reason = str(result.get("reason") or "").strip().lower()
                message = "本机微信接管未执行"
                if reason == "running":
                    message = "本机微信仍有上一轮接管占用，本轮没有扫描会话"
                elif reason:
                    message = f"本机微信接管未执行：{reason}"
                output["failed"] += 1
                output["last_error"] = message
                output["rounds"].append(
                    {
                        "round": round_number,
                        "status": "failed",
                        "error": message,
                    }
                )
                stop_reason = "local_wechat_busy" if reason == "running" else "local_wechat_not_executed"
                break
            consecutive_driver_failures = 0
            last_config = result.get("config") if isinstance(result.get("config"), dict) else last_config
            items = [item for item in (result.get("items") or []) if isinstance(item, dict)]
            output["completed_rounds"] += 1
            output["replied"] += _safe_int(result.get("replied"))
            output["skipped"] += _safe_int(result.get("skipped"))
            output["failed"] += _safe_int(result.get("failed"))
            if round_number == 1:
                output["friend_requests_checked"] = _safe_int(result.get("friend_requests_checked"))
                output["friend_requests_accepted"] = _safe_int(result.get("friend_requests_accepted"))
                output["friend_requests_failed"] = _safe_int(result.get("friend_requests_failed"))
            output["items"].extend({**item, "round": round_number} for item in items)
            output["rounds"].append(
                {
                    "round": round_number,
                    "checked_at": result.get("checked_at") or result.get("started_at") or datetime.utcnow().isoformat(),
                    "replied": _safe_int(result.get("replied")),
                    "skipped": _safe_int(result.get("skipped")),
                    "failed": _safe_int(result.get("failed")),
                    "friend_requests_checked": _safe_int(result.get("friend_requests_checked")) if round_number == 1 else 0,
                    "friend_requests_accepted": _safe_int(result.get("friend_requests_accepted")) if round_number == 1 else 0,
                    "friend_requests_failed": _safe_int(result.get("friend_requests_failed")) if round_number == 1 else 0,
                    "friend_requests": (
                        result.get("friend_requests") if round_number == 1 and isinstance(result.get("friend_requests"), dict) else {}
                    ),
                    "items": items,
                    "summary_text": str(result.get("summary_text") or "").strip(),
                }
            )
            if cloud is not None and base and run_id:
                event_status = await _post_task_event(
                    cloud,
                    base,
                    headers,
                    run_id,
                    "running",
                    {
                        "text": f"个微私信接管第 {round_number} 轮已完成",
                        "round": round_number,
                        "session_seconds": duration_limit,
                        "heartbeat": True,
                    },
                )
                if _task_event_rejects_local_work(event_status):
                    await _request_local_auto_reply_stop(account_id, headers)
                    stop_reason = "slot_ownership_changed"
                    break
        except Exception as exc:
            consecutive_driver_failures += 1
            output["failed"] += 1
            output["rounds"].append({"round": round_number, "failed": 1, "error": str(exc)[:500]})
            output["last_error"] = str(exc)[:500]
            if consecutive_driver_failures >= max_consecutive_driver_failures:
                stop_reason = "consecutive_driver_failures"
                break
    if not stop_reason and round_limit is not None and round_number >= round_limit and _takeover_monotonic() < deadline_monotonic:
        stop_reason = "round_limit"
    output["finished_at"] = datetime.utcnow().isoformat()
    output["duration_seconds"] = round(max(0.0, _takeover_monotonic() - started_monotonic), 2)
    output["stop_reason"] = stop_reason or ("session_deadline" if _takeover_monotonic() >= deadline_monotonic else "session_end")
    output["config"] = last_config
    output["group_invite_candidates"] = len(
        {
            str(item.get("peer_id") or "")
            for item in output["items"]
            if item.get("should_invite_group") and str(item.get("peer_id") or "").strip()
        }
    )
    output["ok"] = output["completed_rounds"] > 0 and not stop_reason
    output["summary_text"] = (
        f"个微私信接管已持续巡检 {output['completed_rounds']} 轮，"
        f"检查新好友申请 {output['friend_requests_checked']} 条，已同意 {output['friend_requests_accepted']} 条，"
        f"自动回复 {output['replied']} 条，跳过 {output['skipped']} 条，失败 {output['failed']} 条；"
        f"命中拉群条件 {output['group_invite_candidates']} 个会话。"
    )
    return output


def _native_wechat_group_invite_candidates(parent_payload: Any) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("should_invite_group") and str(node.get("peer_id") or "").strip():
                candidates.append(node)
            for key, value in node.items():
                if key in {"items", "rounds", "local_result", "conversations"} and isinstance(value, (dict, list)):
                    collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    collect(parent_payload)
    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        peer_id = str(item.get("peer_id") or "").strip()
        inbound_id = str(item.get("inbound_message_id") or "").strip()
        key = inbound_id or peer_id
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(dict(item))
    return deduped


async def _run_native_wechat_group_invite_followup(
    source: Dict[str, Any],
    *,
    account_id: str,
    headers: Dict[str, str],
    cloud: Optional[httpx.AsyncClient],
    base: str,
    current_item: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if cloud is None or not base:
        raise RuntimeError("自动拉群无法读取上级私信接管结果")
    wait_seconds = _clamp_int(source.get("parent_wait_seconds"), 1800, 0, 2400)
    poll_seconds = _clamp_int(source.get("parent_poll_seconds"), 60, 15, 300)
    deadline = asyncio.get_running_loop().time() + wait_seconds
    parent_runs: List[Dict[str, Any]] = []
    while True:
        parent_runs = await _resolve_parent_workflow_results(
            cloud,
            base,
            headers,
            params=source,
            current_item=current_item,
        )
        if parent_runs or asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(poll_seconds)
    if not parent_runs:
        return {
            "ok": True,
            "skipped": True,
            "reason": "parent_not_ready",
            "waited_seconds": wait_seconds,
            "message": "等待上级私信接管完成后仍未取得结果，已跳过本轮拉群",
        }
    candidates = _native_wechat_group_invite_candidates(parent_runs[0].get("result_payload"))
    if not candidates:
        return {"ok": True, "skipped": True, "reason": "no_match", "message": "本轮没有会话命中拉群条件"}
    cfg = native_wechat_engine.get_auto_reply_config(account_id)
    primary_contact = str(
        cfg.get("group_invite_primary_contact")
        or source.get("group_invite_primary_contact")
        or ""
    ).strip()
    if not primary_contact:
        legacy_contacts = _workflow_target_list(cfg, "group_invite_contacts")
        legacy_contacts.extend(_workflow_target_list(source, "group_invite_manager_contacts", "group_invite_members"))
        primary_contact = next((item for item in legacy_contacts if str(item or "").strip()), "")
    primary_contact_name = str(
        cfg.get("group_invite_primary_contact_name")
        or source.get("group_invite_primary_contact_name")
        or primary_contact
    ).strip()
    welcome_message = str(
        cfg.get("group_invite_welcome_message")
        or source.get("group_invite_welcome_message")
        or ""
    ).strip()[:4000]
    if not primary_contact:
        return {
            "ok": True,
            "skipped": True,
            "reason": "missing_group_contacts",
            "matched": len(candidates),
            "candidates": candidates,
            "message": f"已识别 {len(candidates)} 个符合拉群条件的会话，但未从通讯录配置主联系人",
        }
    tasks: List[Dict[str, Any]] = []
    for candidate in candidates:
        peer_id = str(candidate.get("peer_id") or "").strip()
        contacts = list(dict.fromkeys([peer_id, primary_contact]))
        if len(contacts) < 2:
            continue
        result = await _post_local_api_json(
            "/api/native-wechat/groups/create",
            {
                "account_id": account_id,
                "contacts": contacts,
                "welcome_message": welcome_message,
                "dedup_key": str(candidate.get("group_invite_dedup_key") or "")[:160],
                "source_peer_id": peer_id,
                "source_inbound_message_id": str(candidate.get("inbound_message_id") or "")[:160],
                "group_invite_reason": str(candidate.get("group_invite_reason") or "")[:300],
                "matched_group_keywords": list(candidate.get("matched_group_keywords") or [])[:20],
            },
            headers=headers,
            timeout_seconds=300.0,
        )
        tasks.append(
            {
                "peer_id": peer_id,
                "display_name": candidate.get("display_name") or peer_id,
                "reason": candidate.get("group_invite_reason") or "",
                "matched_keywords": candidate.get("matched_group_keywords") or [],
                "primary_contact": primary_contact,
                "primary_contact_name": primary_contact_name,
                "welcome_message": welcome_message,
                "task": result.get("task") if isinstance(result.get("task"), dict) else {},
            }
        )
    return {
        "ok": True,
        "matched": len(candidates),
        "queued": len(tasks),
        "tasks": tasks,
        "message": f"命中拉群条件 {len(candidates)} 个会话，已提交 {len(tasks)} 个建群任务",
    }


async def _run_client_workflow_action(
    action: str,
    params: Dict[str, Any],
    *,
    headers: Dict[str, str],
    run_id: str,
    cloud: Optional[httpx.AsyncClient] = None,
    base: str = "",
    current_item: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    source = params if isinstance(params, dict) else {}
    native_account_id = str(source.get("account_id") or native_wechat_engine.LOCAL_DEFAULT_ACCOUNT_ID).strip() or native_wechat_engine.LOCAL_DEFAULT_ACCOUNT_ID
    if action == "image_studio_generate":
        prompt = str(source.get("prompt") or "").strip()
        if not prompt:
            raise RuntimeError("请填写图片需求")
        reference_urls = _image_studio_reference_urls(source.get("reference_image_urls"))
        final_prompt = _image_studio_prompt_with_reference_hints(
            prompt,
            reference_urls,
            source.get("reference_purposes"),
        )
        submission = await _post_local_api_form(
            "/api/comfly-image-studio/generate/start",
            {
                "prompt": final_prompt,
                "model": str(source.get("model") or "gpt-image-2").strip() or "gpt-image-2",
                "aspect_ratio": str(source.get("aspect_ratio") or "9:16").strip() or "9:16",
                "quality": str(source.get("quality") or "high").strip() or "high",
                "background": str(source.get("background") or "auto").strip() or "auto",
                "reference_image_urls": ",".join(reference_urls),
            },
            headers=headers,
            timeout_seconds=120.0,
        )
        job_id = str(submission.get("job_id") or "").strip()
        if not job_id:
            raise RuntimeError("图片任务提交成功，但没有返回任务ID")
        timeout_seconds = float(_clamp_int(source.get("poll_timeout_seconds"), 1200, 30, 7200))
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            job = await _get_local_api_json(
                f"/api/comfly-image-studio/jobs/{job_id}",
                headers=headers,
                timeout_seconds=120.0,
            )
            status = str(job.get("status") or "").strip().lower()
            if status == "completed":
                return _image_studio_completed_result(job, job_id=job_id, prompt=prompt)
            if status == "failed":
                raise RuntimeError(str(job.get("error") or "图片生成失败")[:500])
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError(f"图片生成等待超时，任务ID：{job_id}")
            await asyncio.sleep(2.5)
    if action == "shanjian_digital_human_video":
        return await _run_shanjian_digital_human_workflow(
            source,
            headers=headers,
            run_id=run_id,
            cloud=cloud,
            base=base,
            current_item=current_item,
        )
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
    if action == "local_bestseller_daily_video":
        profile = source.get("profile") if isinstance(source.get("profile"), dict) else {}
        profile = {str(key): value for key, value in profile.items() if str(value or "").strip()}
        missing = _local_bestseller_missing_fields(source, profile)
        if missing:
            raise RuntimeError("IP人设定位缺少：" + "、".join(missing) + "。请先补全后再启动销售员工。")
        days = _clamp_int(source.get("days"), 30, 1, 30)
        day = _local_bestseller_workflow_day(source, days)
        scene = await _post_local_api_json(
            "/api/local-bestseller/scene/generate",
            {
                "profile": profile,
                "days": days,
                "day": day,
                "model": str(source.get("model") or "gpt-image-2").strip() or "gpt-image-2",
                "quality": str(source.get("quality") or "high").strip() or "high",
            },
            headers=headers,
            timeout_seconds=900.0,
        )
        scene_item = scene.get("item") if isinstance(scene.get("item"), dict) else {}
        video_submission = await _post_local_api_json(
            "/api/local-bestseller/video/generate",
            {
                "profile": profile,
                "days": days,
                "day": day,
                "item": scene_item,
                "video_model": str(source.get("video_model") or "grok-imagine-video-1.5-preview").strip() or "grok-imagine-video-1.5-preview",
            },
            headers=headers,
            timeout_seconds=7200.0,
        )
        video = await _wait_local_bestseller_video(
            video_submission,
            headers=headers,
            timeout_seconds=7200.0,
        )
        item = video.get("item") if isinstance(video.get("item"), dict) else scene_item
        return {
            "ok": True,
            "mode": "daily_video",
            "day": day,
            "days": days,
            "item": item,
            "items": [item] if item else [],
            "scene_result": scene,
            "video_result": video,
        }
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
    if action == "native_wechat_group_invite" or (
        action == "native_wechat_poll"
        and str(source.get("followup_action") or "").strip().lower() == "group_invite"
    ):
        message = "自动拉群已并入个人微信接管：接管每轮发现新消息后即时判断并拉群，不再单独创建拉群任务。"
        return {
            "ok": True,
            "skipped": True,
            "action": action,
            "reason": "group_invite_folded_into_takeover",
            "message": message,
            "summary_text": message,
        }
    if action == "native_wechat_poll":
        interval_seconds = max(1, min(_safe_int(source.get("message_poll_interval_seconds")) or 15, 300))
        h5_context = source.get("h5_context") if isinstance(source.get("h5_context"), dict) else {}
        window_start = h5_context.get("workflow_node_time") or source.get("sales_schedule_start")
        window_end = h5_context.get("workflow_node_end_time") or source.get("sales_schedule_end")
        configured_minutes = _safe_int(source.get("takeover_session_minutes"))
        derived_minutes = _workflow_minutes_between(window_start, window_end)
        has_workflow_window = bool(str(window_start or "").strip())
        session_minutes = configured_minutes or derived_minutes
        if session_minutes <= 0:
            session_minutes = 1 if has_workflow_window else 30
        session_minutes = max(1, min(session_minutes, 1440))
        session_seconds = float(session_minutes * 60)
        # The configured node window determines when this run is queued.  Its
        # duration starts when the device actually claims and starts the run;
        # a delayed FIFO run must still receive the full configured session.
        config_override = {
            key: source[key]
            for key in (
                "language",
                "target_language",
                "group_invite_enabled",
                "group_invite_memory_doc_id",
                "group_invite_keywords",
                "group_invite_contacts",
                "group_invite_primary_contact",
                "group_invite_primary_contact_name",
                "group_invite_welcome_message",
                "private_sessions_per_round",
                "max_private_sessions_per_round",
            )
            if key in source
        }
        # Existing scheduled tasks may predate language persistence. Resolve the
        # current personal template on every takeover session so a template
        # change from Chinese to English takes effect without recreating a task.
        if not config_override.get("language") and not config_override.get("target_language"):
            try:
                mounted = await _get_cloud_api_json(
                    "/api/h5-chat/mounted-accounts",
                    cloud=cloud,
                    base=base,
                    headers=headers,
                )
                accounts = mounted.get("accounts") if isinstance(mounted.get("accounts"), list) else []
                language = next(
                    (
                        str(row.get("auto_reply_language") or "").strip()
                        for row in accounts
                        if isinstance(row, dict)
                        and str(row.get("scope") or "").strip().lower() == "wechat"
                        and str(row.get("auto_reply_language") or "").strip()
                    ),
                    "",
                )
                if language:
                    config_override["language"] = language
            except Exception:
                # Cloud config lookup is supplemental; the local persisted config
                # remains the fallback when the cloud is temporarily unavailable.
                pass
        if run_id:
            _active_client_workflow_actions[run_id] = action
        try:
            return await _run_native_wechat_takeover_session(
                account_id=native_account_id,
                headers=headers,
                cloud=cloud,
                base=base,
                run_id=run_id,
                interval_seconds=interval_seconds,
                session_seconds=session_seconds,
                config_override=config_override,
            )
        finally:
            if run_id:
                _active_client_workflow_actions.pop(run_id, None)
    if action == "native_wechat_add_friend":
        targets = _workflow_target_list(source, "targets", "phones", "phone_numbers", "keywords", "keyword")
        extracted_phones: List[str] = []
        source_mode = str(source.get("source_mode") or "").strip().lower()
        if source_mode in {
            "douyin_private_message_phone",
            "douyin_private_message_mobile",
            "douyin_private_message_wechat_id",
        }:
            if cloud is None or not base:
                raise RuntimeError("自动加好友无法读取抖音私信结果")
            parent_runs = await _resolve_parent_workflow_results(
                cloud,
                base,
                headers,
                params=source,
                current_item=current_item,
            )
            if parent_runs:
                extracted_phones = _extract_mainland_mobile_numbers(parent_runs[0].get("result_payload"))
        targets = list(dict.fromkeys([*targets, *extracted_phones]))
        if not targets:
            return {
                "ok": True,
                "skipped": True,
                "reason": "missing_targets",
                "extracted_phones": [],
                "message": "本轮抖音私信未识别到客户发送的手机号，已跳过加好友",
            }
        result = await _post_local_api_json(
            "/api/native-wechat/friends/add",
            {
                "account_id": native_account_id,
                "targets": targets,
                "apply_message": str(source.get("apply_message") or "").strip(),
                "remark": str(source.get("remark") or "").strip(),
                "tags": source.get("tags") if isinstance(source.get("tags"), list) else [],
                "permission": str(source.get("permission") or "朋友圈").strip() or "朋友圈",
                "prepare_only": bool(source.get("prepare_only", False)),
            },
            headers=headers,
            timeout_seconds=300.0,
        )
        result["targets"] = targets
        result["extracted_phones"] = extracted_phones
        return result
    if action == "native_wechat_moments_engage":
        targets = _workflow_target_list(source, "contact_wx_nos", "targets", "contacts", "names")
        if not targets:
            return {"ok": True, "skipped": True, "reason": "missing_targets", "message": "未配置朋友圈互动目标，已跳过"}
        moment_action = str(source.get("moment_action") or source.get("mode") or "like_comment").strip().lower() or "like_comment"
        max_scrolls = max(1, min(_safe_int(source.get("max_scrolls") or 6), 30))
        submitted = await _post_local_api_json(
            "/api/native-wechat/moments/engage",
            {
                "account_id": native_account_id,
                "targets": targets,
                "moment_action": moment_action,
                "dry_run": False,
                "max_scrolls": max_scrolls,
            },
            headers=headers,
            timeout_seconds=30.0,
            request_id=f"h5:{run_id}:native-wechat-moments:engage" if run_id else "",
        )
        result = await _wait_for_local_native_wechat_task(
            submitted,
            headers=headers,
            timeout_seconds=1800.0,
        )
        result["targets"] = targets
        result["moment_action"] = moment_action
        return result
    if action == "ip_moments_generate_images":
        return await _post_local_api_json("/api/ip-content/moments/images/generate", source, headers=headers, timeout_seconds=7200.0)
    if action == "publish_content":
        platform = str(source.get("platform") or "").strip().lower()
        material = str(source.get("asset_id") or "").strip()
        source_url = str(source.get("url") or "").strip()
        material_source: Dict[str, Any] = {}
        if not material and not source_url and str(source.get("source_mode") or "").strip() == "parent_latest_run":
            if cloud is None or not base:
                raise RuntimeError("发布动作无法读取上级节点结果")
            material_source = await _resolve_parent_workflow_material(
                cloud,
                base,
                headers,
                params=source,
                current_item=current_item,
            )
            material = str(material_source.get("asset_id") or "").strip()
            source_url = str(material_source.get("url") or "").strip()
        # A workflow may carry only the cloud asset ID. Resolve its public
        # source URL before checking the local asset DB so the same
        # materialization path works for direct and parent-linked publish
        # nodes.
        if material and not source_url and cloud is not None and base:
            try:
                cloud_asset = await _get_cloud_api_json(
                    f"/api/assets/{quote(material, safe='')}",
                    cloud=cloud,
                    base=base,
                    headers=headers,
                    timeout_seconds=20.0,
                )
                candidate_url = str(
                    cloud_asset.get("source_url")
                    or cloud_asset.get("open_url")
                    or cloud_asset.get("preview_url")
                    or ""
                ).strip()
                if candidate_url.startswith(("http://", "https://")):
                    source_url = candidate_url
                    material_source.setdefault("media_type", cloud_asset.get("media_type") or "")
                    logger.info(
                        "[H5-WORKFLOW] resolved cloud publish asset URL asset_id=%s url=%s",
                        material,
                        source_url[:160],
                    )
            except Exception as exc:
                logger.info(
                    "[H5-WORKFLOW] direct cloud publish asset URL lookup failed asset_id=%s err=%s",
                    material,
                    str(exc)[:240],
                )
        source_script = str(material_source.get("source_script") or "").strip()
        publish_title = str(source.get("title") or "").strip()
        publish_description = str(source.get("description") or source.get("prompt") or "").strip()
        publish_tags = str(source.get("tags") or "").strip()
        generated_publish_copy: Dict[str, str] = {}
        if bool(source.get("ai_publish_copy", True)):
            generated_publish_copy = await _generate_scheduled_publish_copy(
                base=base,
                headers=headers,
                capability_id=str(material_source.get("source_capability_id") or "publish_content").strip(),
                generated={
                    "title": str(material_source.get("source_title") or source.get("source_workflow_node_label") or "").strip(),
                    "script": source_script,
                    "caption_hint": str(material_source.get("source_caption") or "").strip(),
                    "tags": str(material_source.get("source_tags") or "").strip(),
                    "language": str(material_source.get("source_language") or "").strip(),
                },
                result=material_source,
                refs={
                    "asset_ids": [material] if material else [],
                    "urls": [source_url] if source_url else [],
                },
                platform=platform,
                task_title=str(material_source.get("source_title") or source.get("source_workflow_node_label") or "发布内容").strip(),
                caption=str(material_source.get("source_caption") or "").strip(),
                source_script=source_script,
            )
            publish_title = publish_title or generated_publish_copy.get("title", "")
            publish_description = publish_description or generated_publish_copy.get("description", "")
            publish_tags = publish_tags or generated_publish_copy.get("tags", "")
        if _is_wechat_moments_platform(platform):
            publish_title = ""
        elif _is_wechat_channels_platform(platform):
            publish_title = _wechat_channels_short_title(publish_title, publish_description)
        save_result: Dict[str, Any] = {}
        if source_url and not _is_wechat_moments_platform(platform):
            material, save_result = await _ensure_local_workflow_asset(
                asset_id=material,
                source_url=source_url,
                media_type=str(material_source.get("media_type") or source.get("media_type") or "video").strip() or "video",
                name=str(
                    source.get("name")
                    or source.get("title")
                    or material_source.get("source_title")
                    or "H5工作流素材"
                ).strip(),
                prompt=str(
                    source.get("description")
                    or source.get("title")
                    or material_source.get("source_script")
                    or ""
                ).strip(),
                headers=headers,
                run_id=run_id,
            )
        if _is_wechat_moments_platform(platform):
            draft = {
                "asset_id": material,
                "source_url": source_url,
                "url": source_url,
                "platform": "wechat_moments",
                "platform_name": str(source.get("platform_name") or "微信朋友圈").strip(),
                "account_id": str(source.get("account_id") or native_wechat_engine.LOCAL_DEFAULT_ACCOUNT_ID).strip(),
                "account_nickname": str(source.get("account_nickname") or "本机微信").strip(),
                "title": publish_title,
                "description": publish_description or str(material_source.get("source_caption") or material_source.get("description") or "").strip(),
                "tags": publish_tags,
                "media_type": str(material_source.get("media_type") or source.get("media_type") or "image_text").strip() or "image_text",
                "options": source.get("options") if isinstance(source.get("options"), dict) else {},
            }
            publish_result = await _submit_local_publish_draft(draft=draft, headers=headers)
            return {
                "ok": True,
                "asset_id": material,
                "source_url": source_url,
                "source_run_id": str(material_source.get("source_run_id") or ""),
                "save_result": save_result,
                "publish_copy": {"title": publish_title, "description": publish_description, "tags": publish_tags},
                "publish_result": publish_result,
            }
        if not material:
            raise RuntimeError("发布中心入库缺少素材 ID 或公网链接")
        account_nickname = str(source.get("account_nickname") or "").strip()
        if not account_nickname:
            raise RuntimeError("发布中心入库缺少发布账号昵称")
        publish_options = dict(source.get("options")) if isinstance(source.get("options"), dict) else {}
        if source_script:
            publish_options["_source_prompt"] = source_script
        publish_body: Dict[str, Any] = {
            "asset_id": material,
            "account_nickname": account_nickname,
            "title": publish_title or None,
            "description": publish_description or None,
            "tags": publish_tags or None,
            # Publishing automation is local, but all copy generation belongs to the cloud API.
            "ai_publish_copy": False,
            "options": publish_options,
        }
        account_id = str(source.get("account_id") or "").strip()
        if re.fullmatch(r"-?\d+", account_id):
            publish_body["account_id"] = int(account_id)
        try:
            publish_result = await _post_local_api_json(
                "/api/publish",
                publish_body,
                headers=headers,
                timeout_seconds=7200.0,
            )
        except RuntimeError as exc:
            # A file can disappear between the readiness check and the
            # publish request. Re-materialize once from the immutable result
            # URL, then retry only this deterministic local-asset failure.
            if not source_url or not _is_local_asset_missing_error(exc):
                raise
            logger.warning(
                "[H5-WORKFLOW] publish reported missing local asset; rematerializing once asset_id=%s url=%s",
                material,
                source_url[:160],
            )
            material, save_result = await _ensure_local_workflow_asset(
                asset_id="",
                source_url=source_url,
                media_type=str(material_source.get("media_type") or source.get("media_type") or "video").strip() or "video",
                name=str(source.get("name") or source.get("title") or "H5工作流素材").strip(),
                prompt=str(source.get("description") or source.get("title") or "").strip(),
                headers=headers,
                run_id=run_id,
            )
            publish_body["asset_id"] = material
            publish_result = await _post_local_api_json(
                "/api/publish",
                publish_body,
                headers=headers,
                timeout_seconds=7200.0,
            )
        return {
            "ok": True,
            "asset_id": material,
            "source_run_id": str(material_source.get("source_run_id") or ""),
            "save_result": save_result,
            "publish_copy": {"title": publish_title, "description": publish_description, "tags": publish_tags},
            "publish_result": publish_result,
        }
    raise RuntimeError(f"暂不支持的客户端工作流：{action}")


def _client_workflow_result_text(action: str, result: Dict[str, Any]) -> str:
    if action == "image_studio_generate":
        images = result.get("images") if isinstance(result.get("images"), list) else []
        return f"AI设计图已生成，共 {len(images) or 1} 张。"
    if action == "shanjian_digital_human_video":
        task_id = str(result.get("task_id") or "").strip()
        if result.get("result_ready") is False:
            return "数字人2.0视频任务已提交，仍在生成中。" + (f" 任务ID：{task_id}" if task_id else "")
        video_url = str(result.get("video_url") or "").strip()
        return "数字人2.0视频生成完成。" + (f" 任务ID：{task_id}" if task_id else "") + (f" 视频：{video_url}" if video_url else "")
    if action == "local_bestseller_daily_video":
        item = result.get("item") if isinstance(result.get("item"), dict) else {}
        task_id = str(item.get("video_task_id") or item.get("video_job_id") or "").strip()
        day = _safe_int(result.get("day") or item.get("day"))
        return f"同城爆款 Day {day or 1} 视频任务已提交。" + (f" 视频任务ID：{task_id}" if task_id else "")
    if action.startswith("local_bestseller"):
        items = result.get("items") if isinstance(result.get("items"), list) else []
        return f"同城爆款任务完成，已生成 {len(items)} 条内容。" if items else "同城爆款任务已完成。"
    if action == "viral_video_remix_start":
        task_id = str(result.get("task_id") or result.get("job_id") or "").strip()
        return "爆款复刻任务已提交到客户端。" + (f" 任务ID：{task_id}" if task_id else "")
    if action == "wecom_poll_reply":
        return "企业微信客服已执行一次拉取与自动回复检查。"
    if action == "native_wechat_poll":
        if result.get("skipped"):
            return str(result.get("message") or "个微任务已跳过。")
        if "queued" in result and "matched" in result:
            return str(result.get("message") or "个微自动拉群任务已处理。")
        summary_text = str(result.get("summary_text") or "").strip()
        if summary_text:
            return summary_text
        replied = int(result.get("replied") or result.get("success") or 0)
        skipped = int(result.get("skipped_count") or result.get("skipped") or 0)
        return f"个微私信接管已完成，回复 {replied} 条，跳过 {skipped} 条。"
    if action == "native_wechat_add_friend":
        if result.get("skipped"):
            return str(result.get("message") or "个微自动加好友已跳过。")
        task = result.get("task") if isinstance(result.get("task"), dict) else {}
        task_id = str(task.get("id") or "").strip()
        return "个微自动加好友已加入队列。" + (f" 任务ID：{task_id}" if task_id else "")
    if action == "native_wechat_moments_engage":
        if result.get("skipped"):
            return str(result.get("message") or "朋友圈互动已跳过。")
        task = result.get("task") if isinstance(result.get("task"), dict) else {}
        payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
        items = payload.get("engage_results") if isinstance(payload.get("engage_results"), list) else []
        completed = sum(1 for item in items if isinstance(item, dict) and str(item.get("status") or "").lower() in {"success", "skipped"})
        failed = sum(1 for item in items if isinstance(item, dict) and str(item.get("status") or "").lower() in {"failed", "partial_failed"})
        liked = sum(int(item.get("liked") or 0) for item in items if isinstance(item, dict))
        commented = sum(int(item.get("commented") or 0) for item in items if isinstance(item, dict))
        already_commented = sum(int(item.get("already_commented") or 0) for item in items if isinstance(item, dict))
        if str(task.get("original_status") or "").lower() == "partial_failed" or failed:
            return f"朋友圈互动部分完成：处理 {completed} 个，失败 {failed} 个；点赞 {liked} 条，评论 {commented + already_commented} 条。"
        return f"朋友圈互动完成：处理 {completed} 个；点赞 {liked} 条，评论 {commented + already_commented} 条。"
    if action == "ip_moments_generate_images":
        return f"朋友圈图片生成完成：{int(result.get('record_count') or 0)} 条文案，{int(result.get('image_count') or 0)} 张图片。"
    if action == "publish_content":
        asset_id = str(result.get("asset_id") or "").strip()
        publish_result = result.get("publish_result") if isinstance(result.get("publish_result"), dict) else {}
        status = str(publish_result.get("status") or publish_result.get("state") or "").strip()
        return "发布中心任务已提交。" + (f" 素材：{asset_id}" if asset_id else "") + (f" 状态：{status}" if status else "")
    return _compact_result_text(result)


def _client_workflow_failure_reason(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    status = str(result.get("status") or result.get("state") or "").strip().lower()
    explicitly_failed = result.get("ok") is False or result.get("success") is False
    if not explicitly_failed and status not in {"failed", "failure", "error", "timeout", "cancelled", "canceled"}:
        return ""
    reason = (
        result.get("error_message")
        or result.get("error")
        or result.get("detail")
        or result.get("last_error")
        or result.get("message")
        or result.get("summary_text")
        or "客户端工作流执行失败"
    )
    return _workflow_text(reason, 500) or "客户端工作流执行失败"


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
    params = dict(params)
    if isinstance(payload.get("h5_context"), dict) and "h5_context" not in params:
        params["h5_context"] = payload.get("h5_context")
    if isinstance(payload.get("schedule_config"), dict) and "schedule_config" not in params:
        params["schedule_config"] = payload.get("schedule_config")
    if not run_id or not action:
        return
    event_status = await _post_task_event(
        cloud,
        base,
        headers,
        run_id,
        "thinking",
        {"text": f"正在执行客户端工作流：{action}"},
    )
    if _task_event_rejects_local_work(event_status):
        return
    try:
        result = await _run_client_workflow_action(
            action,
            params,
            headers=headers,
            run_id=run_id,
            cloud=cloud,
            base=base,
            current_item=item,
        )
        result_payload = {
            "task_kind": "client_workflow",
            "action": action,
            "params": params,
            "local_result": result,
        }
        failure_reason = _client_workflow_failure_reason(result)
        await _complete_task_run(
            cloud,
            base,
            headers,
            run_id,
            result_text=_client_workflow_result_text(action, result),
            result_payload=result_payload,
            error=failure_reason,
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
    await _sync_openclaw_memory_for_context(jwt_token, installation_id, "scheduled-task")
    # ``openclaw_message`` is a legacy name kept in old cloud rows. All such
    # runs now use the normal direct chat executor and never start Gateway.
    kind = str(item.get("task_kind") or "chat_message").strip().lower()
    if kind == "openclaw_message":
        kind = "chat_message"
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
        # Unknown/legacy chat task kinds are handled as ordinary chat rather
        # than falling back to the retired OpenClaw executor.
        await _run_scheduled_chat_message(
            client,
            base,
            headers,
            item,
            openclaw=False,
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
    item: Optional[Dict[str, Any]] = None,
) -> None:
    interval = float(os.environ.get("LOBSTER_SCHEDULED_TASK_HEARTBEAT_SEC", "60") or "60")
    interval = min(60.0, max(30.0, interval))

    async def send_heartbeat() -> bool:
        status = await _post_task_event(
            client,
            base,
            headers,
            run_id,
            "heartbeat",
            {"text": "local task running", "heartbeat": True},
        )
        if _task_event_rejects_local_work(status):
            logger.info(
                "[SCHEDULED-TASK] heartbeat rejected; requesting local worker stop run_id=%s status=%s",
                run_id,
                status,
            )
            if isinstance(item, dict):
                try:
                    await _stop_workflow_node_with_timeout(item, headers=headers)
                except Exception as exc:
                    logger.debug(
                        "[SCHEDULED-TASK] rejected heartbeat local stop failed run_id=%s: %s",
                        run_id,
                        exc,
                    )
            return False
        return True
    try:
        status = await _post_task_event(
            client,
            base,
            headers,
            run_id,
            "heartbeat",
            {"text": "本地执行中", "heartbeat": True},
        )
        if _task_event_rejects_local_work(status):
            logger.info(
                "[SCHEDULED-TASK] initial heartbeat rejected; requesting local worker stop run_id=%s status=%s",
                run_id,
                status,
            )
            if isinstance(item, dict):
                await _stop_workflow_node_with_timeout(item, headers=headers)
            return
    except Exception as exc:
        logger.debug("[SCHEDULED-TASK] heartbeat failed run_id=%s: %s", run_id, exc)
    while True:
        await asyncio.sleep(interval)
        try:
            status = await _post_task_event(
                client,
                base,
                headers,
                run_id,
                "heartbeat",
                {"text": "本地执行中", "heartbeat": True},
            )
            if _task_event_rejects_local_work(status):
                logger.info(
                    "[SCHEDULED-TASK] heartbeat rejected; requesting local worker stop run_id=%s status=%s",
                    run_id,
                    status,
                )
                if isinstance(item, dict):
                    await _stop_workflow_node_with_timeout(item, headers=headers)
                return
        except Exception as exc:
            logger.debug("[SCHEDULED-TASK] heartbeat failed run_id=%s: %s", run_id, exc)


def _workflow_node_deadline_message(deadline: datetime) -> str:
    _ = deadline
    return "节点时间已结束，本次任务已自动停止，后续节点继续执行。"


async def _report_workflow_node_deadline_expired(
    client: httpx.AsyncClient,
    base: str,
    headers: Dict[str, str],
    item: Dict[str, Any],
    *,
    deadline: datetime,
    phase: str,
    stop_result: Optional[Dict[str, Any]] = None,
) -> None:
    """Make node expiry visible to both new and older cloud server builds."""
    run_id = str(item.get("id") or "").strip()
    if not run_id:
        return
    message = _workflow_node_deadline_message(deadline)
    payload = {
        "reason": "workflow_node_deadline_expired",
        "deadline_at": deadline.astimezone(timezone.utc).isoformat(),
        "phase": phase,
        "text": message,
    }
    if stop_result:
        payload["local_stop"] = stop_result
    event_status = await _post_task_event(client, base, headers, run_id, "cancelled", payload)
    if event_status and event_status not in {200, 201, 202, 204, 409}:
        logger.debug(
            "[SCHEDULED-TASK] deadline event not accepted run_id=%s status=%s",
            run_id,
            event_status,
        )
    if _task_event_rejects_local_work(event_status):
        # The current cloud implementation has already persisted a canonical
        # cancelled result at the deadline. Do not send a fallback error
        # completion that could overwrite it on a different code path.
        return
    try:
        await _complete_task_run(
            client,
            base,
            headers,
            run_id,
            result_text=message,
            result_payload=payload,
            # Older cloud versions have no cancelled completion state. An
            # error still releases the serial queue; newer versions convert
            # this callback to their canonical cancelled node-expiry result.
            error=message,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("[SCHEDULED-TASK] deadline completion callback failed run_id=%s: %s", run_id, exc)


async def _run_scheduled_task_with_workflow_deadline(
    client: httpx.AsyncClient,
    base: str,
    jwt_token: str,
    installation_id: str,
    headers: Dict[str, str],
    item: Dict[str, Any],
) -> None:
    """Run a claimed workflow node only until its absolute node deadline.

    A queued node may be claimed late, but it must not receive a fresh full
    session from the claim time.  The local action is stopped cooperatively
    first, then the coroutine is cancelled if it does not exit promptly.
    """
    deadline = _workflow_node_deadline_utc(item)
    if deadline is None:
        await _process_scheduled_task(client, base, jwt_token, installation_id, item)
        return

    remaining_seconds = _workflow_node_remaining_seconds(item)
    if remaining_seconds is None or remaining_seconds <= 0:
        stop_result = await _stop_workflow_node_with_timeout(item, headers=headers)
        await _report_workflow_node_deadline_expired(
            client,
            base,
            headers,
            item,
            deadline=deadline,
            phase="before_start",
            stop_result=stop_result,
        )
        return

    execution_task = asyncio.create_task(
        _process_scheduled_task(client, base, jwt_token, installation_id, item)
    )
    try:
        done, _pending = await asyncio.wait({execution_task}, timeout=remaining_seconds)
        if execution_task in done:
            await execution_task
            return

        logger.info(
            "[SCHEDULED-TASK] workflow node deadline reached run_id=%s deadline=%s",
            str(item.get("id") or "").strip(),
            deadline.isoformat(),
        )
        stop_result = await _stop_workflow_node_with_timeout(item, headers=headers)
        await _report_workflow_node_deadline_expired(
            client,
            base,
            headers,
            item,
            deadline=deadline,
            phase="while_running",
            stop_result=stop_result,
        )

        # Cooperative workers get a brief chance to flush cleanup.  This is
        # important for local loops that observe an explicit stop flag.
        try:
            await asyncio.wait_for(
                asyncio.shield(execution_task),
                timeout=_WORKFLOW_NODE_CANCEL_GRACE_SECONDS,
            )
        except asyncio.TimeoutError:
            await _cancel_workflow_execution_task(execution_task)
        except Exception as exc:
            logger.debug("[SCHEDULED-TASK] stopped worker exited with error: %s", exc)
    except asyncio.CancelledError:
        if not execution_task.done():
            try:
                await _stop_workflow_node_with_timeout(item, headers=headers)
            finally:
                await _cancel_workflow_execution_task(execution_task)
        raise
    finally:
        if not execution_task.done():
            await _cancel_workflow_execution_task(execution_task)


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
    douyin_lock = None
    douyin_lock_acquired = False
    douyin_marker_id = ""
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        keepalive: Optional[asyncio.Task] = None
        if run_id:
            keepalive = asyncio.create_task(_scheduled_task_keepalive(client, base, headers, run_id, item))
        try:
            if _scheduled_item_uses_douyin_runtime(item):
                _install_douyin_origin_import_path()
                from douyin_api import (  # type: ignore
                    douyin_schedule_execution_lock,
                    set_douyin_schedule_external_work,
                )

                douyin_lock = douyin_schedule_execution_lock
                await douyin_lock.acquire()
                douyin_lock_acquired = True
                douyin_marker_id = f"server:{run_id}"
                payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
                set_douyin_schedule_external_work(
                    douyin_marker_id,
                    active=True,
                    label=str(item.get("title") or payload.get("action") or "scheduled Douyin task"),
                )
                await _wait_for_local_douyin_runtime_idle(run_id)
            await _run_scheduled_task_with_workflow_deadline(
                client,
                base,
                jwt_token,
                installation_id,
                headers,
                item,
            )
        finally:
            if keepalive:
                keepalive.cancel()
                try:
                    await keepalive
                except asyncio.CancelledError:
                    pass
            if douyin_marker_id:
                try:
                    set_douyin_schedule_external_work(douyin_marker_id, active=False)
                except Exception:
                    pass
            if douyin_lock_acquired and douyin_lock and douyin_lock.locked():
                douyin_lock.release()
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
    heartbeat_interval = _channel_interval("LOBSTER_H5_CHAT_HEARTBEAT_INTERVAL_SEC", 30.0, 30.0)
    sleep_missing_auth = 10.0
    logged_missing = False
    last_heartbeat_at = 0.0
    last_h5_poll_at = 0.0
    last_task_poll_at = 0.0
    last_publish_poll_at = 0.0
    max_h5_concurrency = _channel_concurrency("LOBSTER_H5_CHAT_CONCURRENCY", 2, 5)
    # A physical Online installation is a single worker. The server also
    # serializes claims, but keeping the client at one prevents overlapping
    # local Playwright/WeChat work before the next poll observes the lock.
    max_task_concurrency = _channel_concurrency("LOBSTER_SCHEDULED_TASK_CONCURRENCY", 1, 1)
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
                        json={
                            "display_name": "local-online",
                            "publish_accounts": _build_publish_account_snapshot(jwt_token),
                            "wechat_contacts": _build_native_wechat_contact_snapshot(),
                            "capabilities": _h5_client_capabilities(),
                        },
                        headers=headers,
                    )
                    if heartbeat_resp.status_code == 401:
                        logger.warning("[H5-CHAT] heartbeat auth rejected; clearing stale channel token")
                        clear_channel_fallback("h5_heartbeat_401")
                        last_heartbeat_at = 0.0
                        await asyncio.sleep(sleep_missing_auth)
                        continue
                    if heartbeat_resp.status_code == 409:
                        logger.warning("[H5-CHAT] heartbeat slot rejected; clearing stale channel token")
                        clear_channel_fallback("h5_heartbeat_409")
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
                    if resp.status_code == 409:
                        logger.warning("[H5-CHAT] cloud slot rejected; clearing stale channel token")
                        clear_channel_fallback("h5_pending_409")
                        last_heartbeat_at = 0.0
                        await asyncio.sleep(sleep_missing_auth)
                        continue
                    else:
                        resp.raise_for_status()
                        items = (resp.json() or {}).get("items") or []
                task_items: list[Dict[str, Any]] = []
                # A publish follow-up uses the same local account/browser as
                # a scheduled task. Do not poll the other queue while either
                # kind is already running in this process.
                local_douyin_busy_reason = _local_douyin_busy_reason()
                task_slots = (
                    max(0, max_task_concurrency - len(active_task_runs))
                    if not active_publish_runs and not local_douyin_busy_reason
                    else 0
                )
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
                    if task_resp.status_code == 409:
                        logger.warning("[SCHEDULED-TASK] cloud slot rejected; clearing stale channel token")
                        clear_channel_fallback("scheduled_task_pending_409")
                        last_heartbeat_at = 0.0
                        await asyncio.sleep(sleep_missing_auth)
                        continue
                    if task_resp.status_code < 400:
                        task_items = (task_resp.json() or {}).get("items") or []
                    elif task_resp.status_code != 404:
                        logger.debug("[SCHEDULED-TASK] pending request HTTP %s: %s", task_resp.status_code, task_resp.text[:300])
                publish_items: list[Dict[str, Any]] = []
                publish_slots = (
                    max(0, max_publish_concurrency - len(active_publish_runs))
                    if not active_task_runs and not local_douyin_busy_reason
                    else 0
                )
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
                    if publish_resp.status_code == 409:
                        logger.warning("[SCHEDULED-PUBLISH] cloud slot rejected; clearing stale channel token")
                        clear_channel_fallback("scheduled_publish_pending_409")
                        last_heartbeat_at = 0.0
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
