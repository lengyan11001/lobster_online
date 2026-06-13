from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .assets import ASSETS_DIR, _asset_file_token, _save_bytes_or_tos, get_asset_public_url
from .auth import _ServerUser, get_current_user_for_local
from ..core.config import settings
from ..db import SessionLocal, get_db
from ..models import Asset, CapabilityCallLog
from ..services.media_edit_exec import find_ffmpeg

logger = logging.getLogger(__name__)
router = APIRouter()

_ROOT_DIR = Path(__file__).resolve().parents[3]
_JOBS_DIR = _ROOT_DIR / "data" / "cutcli_local_templates"
_JOBS_DIR.mkdir(parents=True, exist_ok=True)
_FEATURE = "cutcli_template_local"
_STT_MODEL = "volcengine/speech-to-text/bigmodel-v2"

_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "auto_caption_pop_huazi_v1": {
        "id": "auto_caption_pop_huazi_v1",
        "kind": "auto_caption",
        "name": "爆点黄字弹跳",
        "description": "黄字蓝边大字幕，强强调，适合口播卖点和钩子句。",
        "aspect_ratio": "source",
        "tags": ["爆点", "黄字蓝边", "弹跳"],
        "quality_label": "中下大字 + 爆点弹跳",
        "sample_video_url": "/client/client-code/cutcli_templates/auto_caption_pop_huazi_v1.mp4",
        "caption_style": {
            "id": "yellow_burst",
            "ass_layout": "center_burst",
            "ass_font_size": 84,
            "ass_primary": "&H0000F7FF",
            "ass_outline": "&H00FF3600",
            "ass_shadow": 5,
            "ass_border": 7,
            "ass_alignment": 2,
            "ass_margin_v": 170,
            "max_chars": 12,
            "cutcli_font_size": 14,
            "cutcli_transform_x": "0",
            "cutcli_transform_y": "-0.55",
            "cutcli_text_color": "#FFFFFF",
            "cutcli_border_color": "#041B51",
            "cutcli_border_width": "0.09",
            "cutcli_in_animation": "响亮强调",
            "cutcli_loop_animation": "逐字放大",
            "cutcli_text_effect": "4B黄字蓝边投影",
        },
    },
    "auto_caption_clean_fade_v1": {
        "id": "auto_caption_clean_fade_v1",
        "kind": "auto_caption",
        "name": "访谈清透字幕",
        "description": "细描边白字，位置克制，适合课程、采访和解释型内容。",
        "aspect_ratio": "source",
        "tags": ["访谈", "课程", "清透"],
        "quality_label": "下三分之一 + 轻渐显",
        "sample_video_url": "/client/client-code/cutcli_templates/auto_caption_clean_fade_v1.mp4",
        "caption_style": {
            "id": "clean_fade",
            "ass_layout": "lower_clean",
            "ass_font_size": 62,
            "ass_primary": "&H00FFFFFF",
            "ass_outline": "&H00231B12",
            "ass_shadow": 2,
            "ass_border": 4,
            "ass_alignment": 2,
            "ass_margin_v": 210,
            "max_chars": 16,
            "cutcli_font_size": 11,
            "cutcli_transform_x": "0",
            "cutcli_transform_y": "-0.72",
            "cutcli_text_color": "#FFFFFF",
            "cutcli_border_color": "#111827",
            "cutcli_border_width": "0.065",
            "cutcli_in_animation": "渐显",
            "cutcli_loop_animation": "",
            "cutcli_text_effect": "",
        },
    },
    "auto_caption_neon_focus_v1": {
        "id": "auto_caption_neon_focus_v1",
        "kind": "auto_caption",
        "name": "科技侧标字幕",
        "description": "左侧 UI 标注式字幕，快速打字效果，适合 AI、产品演示和科技感内容。",
        "aspect_ratio": "source",
        "tags": ["科技", "侧标", "打字"],
        "quality_label": "左侧终端快打 + 青蓝霓虹",
        "sample_video_url": "/client/client-code/cutcli_templates/auto_caption_neon_focus_v1.mp4",
        "caption_style": {
            "id": "side_neon",
            "ass_layout": "side_neon",
            "ass_font_size": 58,
            "ass_primary": "&H00FFFB7C",
            "ass_outline": "&H004D2A06",
            "ass_shadow": 5,
            "ass_border": 5,
            "ass_alignment": 7,
            "ass_margin_v": 185,
            "max_chars": 12,
            "typewriter": True,
            "typing_interval_ms": 85,
            "cutcli_font_size": 11,
            "cutcli_transform_x": "-0.56",
            "cutcli_transform_y": "0.38",
            "cutcli_text_color": "#7CFBFF",
            "cutcli_border_color": "#062A4D",
            "cutcli_border_width": "0.085",
            "cutcli_in_animation": "故障打字",
            "cutcli_loop_animation": "",
            "cutcli_text_effect": "",
        },
    },
    "auto_caption_punch_big_v1": {
        "id": "auto_caption_punch_big_v1",
        "kind": "auto_caption",
        "name": "短剧重击大字",
        "description": "居中大字幕，短句冲击感更强，适合情绪反转和短剧钩子。",
        "aspect_ratio": "source",
        "tags": ["短剧", "重击", "钩子"],
        "quality_label": "居中重击 + 情绪反转",
        "sample_video_url": "/client/client-code/cutcli_templates/auto_caption_punch_big_v1.mp4",
        "caption_style": {
            "id": "punch_big",
            "ass_layout": "dramatic_hook",
            "ass_font_size": 96,
            "ass_primary": "&H0000F7FF",
            "ass_outline": "&H003F1207",
            "ass_shadow": 7,
            "ass_border": 8,
            "ass_alignment": 5,
            "ass_margin_v": 170,
            "max_chars": 9,
            "cutcli_font_size": 16,
            "cutcli_transform_x": "0",
            "cutcli_transform_y": "-0.34",
            "cutcli_text_color": "#FFFFFF",
            "cutcli_border_color": "#07123F",
            "cutcli_border_width": "0.10",
            "cutcli_in_animation": "响亮强调",
            "cutcli_loop_animation": "逐字放大",
            "cutcli_text_effect": "4B黄字蓝边投影",
        },
    },
}


class LocalTemplateTaskBody(BaseModel):
    template_id: str = "auto_caption_pop_huazi_v1"
    render_mode: str = "ffmpeg"
    asset_id: str = ""
    video_url: str = ""
    overlay_texts: Dict[str, Any] = Field(default_factory=dict)
    position_overrides: Dict[str, Any] = Field(default_factory=dict)
    callback_url: str = ""
    external_task_id: str = ""


class LocalTemplateCapabilityBody(BaseModel):
    action: str = "start"
    template_id: str = "auto_caption_pop_huazi_v1"
    render_mode: str = "ffmpeg"
    asset_id: str = ""
    video_url: str = ""
    overlay_texts: Dict[str, Any] = Field(default_factory=dict)
    position_overrides: Dict[str, Any] = Field(default_factory=dict)
    job_id: str = ""
    limit: int = 20
    external_task_id: str = ""


def _now_ts() -> int:
    return int(time.time())


def _dt_to_ts(value: Any) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp())
    return 0


def _job_to_public(row: CapabilityCallLog) -> Dict[str, Any]:
    req = row.request_payload if isinstance(row.request_payload, dict) else {}
    resp = row.response_payload if isinstance(row.response_payload, dict) else {}
    meta = resp.get("meta") if isinstance(resp.get("meta"), dict) else {}
    template = resp.get("template") if isinstance(resp.get("template"), dict) else {}
    job_id = row.chat_session_id or ""
    preview_asset_id = resp.get("preview_asset_id") or resp.get("final_asset_id") or ""
    preview_url = resp.get("preview_url") or resp.get("open_url") or ""
    open_url = resp.get("open_url") or resp.get("preview_url") or ""
    if preview_asset_id and (not preview_url or str(preview_url).startswith("/api/assets/file/")):
        preview_url = _signed_asset_url(str(preview_asset_id))
    if preview_asset_id and (not open_url or str(open_url).startswith("/api/assets/file/")):
        open_url = _signed_asset_url(str(preview_asset_id))
    return {
        "ok": True,
        "job_id": job_id,
        "status": row.status or ("completed" if row.success else "failed"),
        "stage": resp.get("stage") or "",
        "template_id": req.get("template_id") or template.get("id") or "",
        "template_name": template.get("name") or req.get("template_name") or "",
        "source_asset_id": req.get("asset_id") or resp.get("source_asset_id") or "",
        "source_name": resp.get("source_name") or req.get("source_name") or "",
        "preview_asset_id": preview_asset_id,
        "preview_url": preview_url,
        "open_url": open_url,
        "caption_count": resp.get("caption_count") or 0,
        "render_strategy": resp.get("render_strategy") or req.get("render_mode") or "",
        "error": row.error_message or resp.get("error") or "",
        "error_code": resp.get("error_code") or "",
        "created_at": _dt_to_ts(row.created_at),
        "updated_at": int(meta.get("updated_at") or _dt_to_ts(row.created_at) or _now_ts()),
        "poll_path": f"/api/cutcli/local/templates/jobs/{job_id}" if job_id else "",
        "meta": meta,
    }


def _create_job(
    db: Session,
    *,
    user_id: int,
    job_id: str,
    template: Dict[str, Any],
    render_mode: str,
    asset_id: str,
    video_url: str,
    source_name: str,
    overlay_texts: Optional[Dict[str, Any]] = None,
    position_overrides: Optional[Dict[str, Any]] = None,
    external_task_id: str = "",
) -> CapabilityCallLog:
    row = CapabilityCallLog(
        user_id=user_id,
        capability_id=_FEATURE,
        upstream="local",
        upstream_tool="cutcli-template",
        success=False,
        credits_charged=0,
        latency_ms=None,
        request_payload={
            "template_id": template.get("id"),
            "template_name": template.get("name"),
            "render_mode": render_mode,
            "asset_id": asset_id,
            "video_url": video_url,
            "source_name": source_name,
            "overlay_texts": overlay_texts or {},
            "position_overrides": position_overrides or {},
            "external_task_id": external_task_id,
        },
        response_payload={
            "stage": "queued",
            "status": "queued",
            "template": _template_public(template),
            "meta": {"created_at": _now_ts(), "updated_at": _now_ts(), "position_overrides": position_overrides or {}},
        },
        error_message=None,
        source="cutcli-template-local",
        chat_session_id=job_id,
        status="queued",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _get_job(db: Session, user_id: int, job_id: str) -> CapabilityCallLog:
    row = (
        db.query(CapabilityCallLog)
        .filter(
            CapabilityCallLog.user_id == user_id,
            CapabilityCallLog.capability_id == _FEATURE,
            CapabilityCallLog.chat_session_id == job_id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="job not found")
    return row


def _update_job(
    db: Session,
    job_id: str,
    *,
    status: Optional[str] = None,
    stage: Optional[str] = None,
    success: Optional[bool] = None,
    error: Optional[str] = None,
    response_updates: Optional[Dict[str, Any]] = None,
    meta_updates: Optional[Dict[str, Any]] = None,
) -> None:
    row = (
        db.query(CapabilityCallLog)
        .filter(CapabilityCallLog.capability_id == _FEATURE, CapabilityCallLog.chat_session_id == job_id)
        .first()
    )
    if not row:
        return
    payload = dict(row.response_payload) if isinstance(row.response_payload, dict) else {}
    if response_updates:
        payload.update(response_updates)
    if status is not None:
        row.status = status
        payload["status"] = status
    if stage is not None:
        payload["stage"] = stage
    meta = dict(payload.get("meta")) if isinstance(payload.get("meta"), dict) else {}
    if meta_updates:
        meta.update(meta_updates)
    meta["updated_at"] = _now_ts()
    payload["meta"] = meta
    row.response_payload = payload
    if success is not None:
        row.success = success
    if error is not None:
        row.error_message = error
        payload["error"] = error
    db.add(row)
    db.commit()


def _template_public(template: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(template)
    row.setdefault("input_modes", ["upload", "asset_id", "video_url"])
    row.setdefault("preserve_source_video", True)
    row["preview_url"] = row.get("preview_url") or row.get("sample_video_url") or ""
    row["render_path"] = f"/api/cutcli/local/templates/{row.get('id')}/render"
    row["render_modes"] = row.get("render_modes") or ["ffmpeg", "cutcli_cloud"]
    return row


_DEFAULT_OVERLAY_LIMITS = {
    "top_text": 32,
    "title": 24,
    "subtitle": 40,
    "badge": 16,
    # Backward compatibility for template configs issued before the copy fields were normalized.
    "headline": 32,
    "subheadline": 36,
}


def _overlay_limit_map(fields: Optional[List[Dict[str, Any]]]) -> Dict[str, int]:
    limits = dict(_DEFAULT_OVERLAY_LIMITS)
    if not isinstance(fields, list):
        return limits
    for field in fields:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or "").strip()
        if not key:
            continue
        try:
            limit = int(field.get("max_length") or 0)
        except Exception:
            limit = 0
        if limit > 0:
            limits[key] = min(limit, 120)
    return limits


def _overlay_allowed_keys(fields: Optional[List[Dict[str, Any]]]) -> set[str]:
    if not isinstance(fields, list):
        return set(_DEFAULT_OVERLAY_LIMITS.keys())
    if not fields:
        return set()
    allowed = {str(field.get("key") or "").strip() for field in fields if isinstance(field, dict)}
    allowed.discard("")
    if "top_text" in allowed or "title" in allowed:
        allowed.add("headline")
    if "subtitle" in allowed:
        allowed.add("subheadline")
    return allowed


def _overlay_fields_from_template(template: Dict[str, Any]) -> List[Dict[str, Any]]:
    strategy = template.get("generation_strategy") if isinstance(template.get("generation_strategy"), dict) else {}
    fields = strategy.get("overlay_fields") if isinstance(strategy.get("overlay_fields"), list) else None
    if fields is None:
        fields = template.get("overlay_fields") if isinstance(template.get("overlay_fields"), list) else []
    return fields


def _clean_overlay_texts(value: Optional[Dict[str, Any]], fields: Optional[List[Dict[str, Any]]] = None) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not isinstance(value, dict):
        return out
    limits = _overlay_limit_map(fields)
    keys = _overlay_allowed_keys(fields)
    for key in keys:
        if key not in value:
            continue
        text = _clean_overlay_text(value.get(key))
        limit = max(1, int(limits.get(key) or 120))
        out[key] = "".join(list(text)[:limit])
    return out


def _json_clone(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return value


def _deep_merge_dict(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = _json_clone(base) if isinstance(base, dict) else {}
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_dict(out[key], value)
        else:
            out[key] = _json_clone(value)
    return out


def _video_orientation(width: Optional[int], height: Optional[int]) -> str:
    try:
        w = int(width or 0)
        h = int(height or 0)
    except Exception:
        w, h = 0, 0
    return "portrait" if h > w else "landscape"


def _apply_orientation_style(style: Dict[str, Any], *, width: Optional[int] = None, height: Optional[int] = None, orientation: str = "") -> Dict[str, Any]:
    out = _json_clone(style or {})
    current = (orientation or _video_orientation(width, height)).strip().lower()
    styles = out.get("orientation_styles") if isinstance(out.get("orientation_styles"), dict) else {}
    patch = styles.get(current) if isinstance(styles.get(current), dict) else {}
    if patch:
        out = _deep_merge_dict(out, patch)
    out["current_orientation"] = current
    return out


def _orientation_defaults_for_style(style: Dict[str, Any]) -> Dict[str, Any]:
    overlay = style.get("overlay_style") if isinstance(style.get("overlay_style"), dict) else {}
    layout = str(overlay.get("layout") or style.get("ass_layout") or "").strip()
    if layout == "right_vertical_card":
        return {
            "font_size": 10,
            "ass_font_size": 50,
            "ass_margin_v": 96,
            "caption_position": {"x": 0.0, "y": -0.74},
            "overlay_style": {
                "card_width_ratio": 0.12,
                "card_height_ratio": 0.62,
                "title_x_ratio": 0.84,
                "title_y_ratio": 0.34,
                "title_font_size": 28,
                "subtitle_font_size": 24,
            },
        }
    if layout == "education_focus_bar":
        return {
            "font_size": 9,
            "ass_font_size": 44,
            "ass_margin_v": 96,
            "caption_position": {"x": 0.0, "y": -0.78},
            "overlay_style": {
                "top_y_ratio": 0.20,
                "top_screen_y_ratio": 0.20,
                "headline_y_ratio": 0.20,
                "top_font_size": 54,
                "top_sub_font_size": 46,
                "top_min_font_size": 24,
                "top_sub_min_font_size": 22,
                "title_font_size": 24,
                "subtitle_font_size": 18,
                "subtitle_gap": 29,
                "badge_font_size": 32,
                "badge_height_ratio": 0.10,
                "badge_y_ratio": 0.60,
            },
        }
    if layout == "tea_center_title":
        return {
            "font_size": 9,
            "ass_font_size": 48,
            "ass_margin_v": 92,
            "caption_position": {"x": 0.0, "y": -0.78},
            "overlay_style": {
                "headline_y_ratio": 0.34,
                "subheadline_y_ratio": 0.49,
                "headline_font_size": 58,
                "subheadline_font_size": 38,
            },
        }
    if layout == "red_yellow_hook":
        return {
            "font_size": 10,
            "ass_font_size": 52,
            "ass_margin_v": 92,
            "caption_position": {"x": 0.0, "y": -0.74},
            "overlay_style": {
                "headline_y_ratio": 0.18,
                "subheadline_y_ratio": 0.32,
                "headline_font_size": 56,
                "subheadline_font_size": 48,
            },
        }
    if layout == "top_banner":
        return {
            "font_size": 10,
            "ass_font_size": 54,
            "ass_margin_v": 98,
            "caption_position": {"x": 0.0, "y": -0.74},
            "overlay_style": {
                "banner_height_ratio": 0.30,
                "headline_y_ratio": 0.54,
                "top_y_ratio": 0.16,
                "top_screen_y_ratio": 0.16,
                "headline_font_size": 54,
                "profile_x_ratio": 0.08,
                "profile_y_ratio": 0.66,
                "profile_title_font_size": 24,
                "profile_subtitle_font_size": 18,
            },
        }
    if layout == "center_quote":
        return {
            "font_size": 9,
            "ass_font_size": 46,
            "ass_margin_v": 98,
            "caption_position": {"x": 0.0, "y": -0.78},
            "overlay_style": {
                "headline_y_ratio": 0.41,
                "subheadline_y_ratio": 0.55,
                "headline_font_size": 54,
                "subheadline_font_size": 28,
            },
        }
    if layout == "market_label":
        return {
            "font_size": 10,
            "ass_font_size": 50,
            "ass_margin_v": 96,
            "caption_position": {"x": 0.0, "y": -0.78},
            "overlay_style": {
                "headline_y_ratio": 0.55,
                "badge_y_ratio": 0.40,
                "headline_font_size": 56,
                "badge_font_size": 28,
            },
        }
    if layout == "black_gold_quote":
        return {
            "font_size": 9,
            "ass_font_size": 48,
            "ass_margin_v": 96,
            "caption_position": {"x": 0.0, "y": -0.78},
            "overlay_style": {
                "headline_y_ratio": 0.42,
                "subheadline_y_ratio": 0.58,
                "headline_font_size": 54,
                "subheadline_font_size": 58,
            },
        }
    if layout == "tcm_waist_banner":
        return {
            "font_size": 9,
            "ass_font_size": 48,
            "ass_margin_v": 96,
            "caption_position": {"x": 0.0, "y": -0.78},
            "overlay_style": {
                "headline_y_ratio": 0.43,
                "badge_y_ratio": 0.57,
                "headline_font_size": 52,
                "badge_font_size": 28,
            },
        }
    if layout == "news_brief":
        return {
            "font_size": 9,
            "ass_font_size": 46,
            "ass_margin_v": 96,
            "caption_position": {"x": 0.0, "y": -0.78},
            "overlay_style": {
                "headline_y_ratio": 0.16,
                "title_x_ratio": 0.43,
                "subheadline_x_ratio": 0.58,
                "headline_font_size": 56,
            },
        }
    if layout == "side_neon":
        return {
            "font_size": 9,
            "ass_font_size": 44,
            "ass_margin_v": 96,
            "caption_position": {"x": -0.42, "y": 0.24},
        }
    if layout == "dramatic_hook":
        return {
            "font_size": 11,
            "ass_font_size": 62,
            "ass_margin_v": 96,
            "caption_position": {"x": 0.0, "y": -0.28},
        }
    if layout == "center_burst":
        return {
            "font_size": 11,
            "ass_font_size": 58,
            "ass_margin_v": 96,
            "caption_position": {"x": 0.0, "y": -0.58},
        }
    if layout == "lower_clean":
        return {
            "font_size": 9,
            "ass_font_size": 44,
            "ass_margin_v": 96,
            "caption_position": {"x": 0.0, "y": -0.76},
        }
    return {}


def _ensure_orientation_style_defaults(style: Dict[str, Any]) -> Dict[str, Any]:
    out = _json_clone(style or {})
    landscape_defaults = _orientation_defaults_for_style(out)
    if not landscape_defaults:
        return out
    styles = out.get("orientation_styles") if isinstance(out.get("orientation_styles"), dict) else {}
    current_landscape = styles.get("landscape") if isinstance(styles.get("landscape"), dict) else {}
    styles["landscape"] = _deep_merge_dict(landscape_defaults, current_landscape)
    out["orientation_styles"] = styles
    return out


def _clamp_ratio(value: Any, default: float = 0.5) -> float:
    try:
        n = float(value)
    except Exception:
        n = float(default)
    return max(0.03, min(0.97, n))


def _clamp_norm(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except Exception:
        n = float(default)
    return max(-0.95, min(0.95, n))


def _parse_position_overrides(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        data = raw
    else:
        text = str(raw or "").strip()
        if not text:
            return {}
        try:
            data = json.loads(text)
        except Exception:
            return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, Any] = {}
    caption = data.get("caption") if isinstance(data.get("caption"), dict) else {}
    if caption:
        out["caption"] = {
            "x": _clamp_norm(caption.get("x"), 0.0),
            "y": _clamp_norm(caption.get("y"), -0.66),
        }
    overlay = data.get("overlay") if isinstance(data.get("overlay"), dict) else {}
    clean_overlay: Dict[str, Any] = {}
    for key in ("top_text", "headline", "title", "subtitle", "subheadline", "badge"):
        value = overlay.get(key)
        if not isinstance(value, dict):
            continue
        clean_overlay[key] = {
            "x_ratio": _clamp_ratio(value.get("x_ratio"), 0.5),
            "y_ratio": _clamp_ratio(value.get("y_ratio"), 0.5),
        }
    if clean_overlay:
        out["overlay"] = clean_overlay
    return out


def _apply_position_overrides(style: Dict[str, Any], position_overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    next_style = json.loads(json.dumps(style or {}, ensure_ascii=False))
    overrides = position_overrides if isinstance(position_overrides, dict) else {}
    caption = overrides.get("caption") if isinstance(overrides.get("caption"), dict) else {}
    if caption:
        next_style["transform_x"] = f"{_clamp_norm(caption.get('x'), 0.0):.4f}".rstrip("0").rstrip(".")
        next_style["transform_y"] = f"{_clamp_norm(caption.get('y'), -0.66):.4f}".rstrip("0").rstrip(".")
        next_style["cutcli_transform_x"] = next_style["transform_x"]
        next_style["cutcli_transform_y"] = next_style["transform_y"]
    overlay = overrides.get("overlay") if isinstance(overrides.get("overlay"), dict) else {}
    if overlay:
        overlay_style = dict(next_style.get("overlay_style") or {})
        layout = str(overlay_style.get("layout") or "").strip()

        def apply_pos(keys: Tuple[str, ...], x_key: str, y_key: str) -> None:
            for key in keys:
                pos = overlay.get(key)
                if isinstance(pos, dict):
                    overlay_style[x_key] = _clamp_ratio(pos.get("x_ratio"), overlay_style.get(x_key, 0.5))
                    overlay_style[y_key] = _clamp_ratio(pos.get("y_ratio"), overlay_style.get(y_key, 0.5))
                    return

        apply_pos(("top_text", "headline"), "headline_x_ratio", "headline_y_ratio")
        apply_pos(("top_text", "headline"), "top_x_ratio", "top_y_ratio")
        apply_pos(("top_text", "headline"), "top_screen_x_ratio", "top_screen_y_ratio")
        apply_pos(("title",), "title_x_ratio", "title_y_ratio")
        apply_pos(("title",), "profile_x_ratio", "profile_y_ratio")
        if layout not in {"top_banner", "education_focus_bar"}:
            apply_pos(("title",), "headline_x_ratio", "headline_y_ratio")
        apply_pos(("subtitle", "subheadline"), "subheadline_x_ratio", "subheadline_y_ratio")
        apply_pos(("badge",), "badge_x_ratio", "badge_y_ratio")
        next_style["overlay_style"] = overlay_style
    if overrides:
        next_style["position_overrides"] = overrides
    return next_style


def _signed_asset_url(asset_id: str, *, absolute: bool = False, expiry_sec: int = 86400) -> str:
    expiry = int(time.time()) + int(expiry_sec)
    token = _asset_file_token(asset_id, expiry)
    path = f"/api/assets/file/{asset_id}?token={token}&expiry={expiry}"
    base = (getattr(settings, "public_base_url", None) or "").strip().rstrip("/")
    if absolute and base:
        return f"{base}{path}"
    return path


def _auth_header(request: Optional[Request]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if request is not None:
        token = (request.headers.get("Authorization") or "").strip()
        if token:
            headers["Authorization"] = token
        installation_id = (
            request.headers.get("X-Installation-Id")
            or request.headers.get("x-installation-id")
            or ""
        ).strip()
        if installation_id:
            headers["X-Installation-Id"] = installation_id
    return headers


def _safe_ext(name: str, default: str = ".mp4") -> str:
    ext = Path(name or "").suffix.lower()
    return ext if ext in {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".flv", ".wmv"} else default


def _run_cmd(
    args: List[str],
    *,
    timeout: int = 180,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
) -> str:
    logger.info("[cutcli-local] run: %s", " ".join(str(x) for x in args[:5]))
    proc = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        if len(msg) > 1800:
            msg = msg[-1800:]
        raise RuntimeError(msg or f"command failed: {proc.returncode}")
    return proc.stdout or ""


def _json_from_cmd(args: List[str], *, timeout: int = 180, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    out = _run_cmd(args, timeout=timeout, env=env)
    try:
        data = json.loads(out)
    except Exception as exc:
        raise RuntimeError(f"command did not return JSON: {out[:500]}") from exc
    return data if isinstance(data, dict) else {}


def _ffprobe_path(ffmpeg: str) -> str:
    p = Path(ffmpeg)
    cand = p.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    if cand.exists():
        return str(cand)
    candidates = []
    if os.name == "nt":
        candidates.extend(
            [
                _ROOT_DIR / "deps" / "ffmpeg" / "ffprobe.exe",
                _ROOT_DIR
                / "skills"
                / "comfly_veo3_daihuo_video"
                / "tools"
                / "ffmpeg"
                / "windows"
                / "ffprobe.exe",
            ]
        )
    else:
        candidates.append(_ROOT_DIR / "deps" / "ffmpeg" / "ffprobe")
    for item in candidates:
        if item.exists():
            return str(item.resolve())
    found = shutil.which("ffprobe.exe" if os.name == "nt" else "ffprobe")
    return found or ""


def _probe_video(ffmpeg: str, source: str) -> Dict[str, Any]:
    ffprobe = _ffprobe_path(ffmpeg)
    if ffprobe:
        raw = _run_cmd(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,duration:format=duration",
                "-of",
                "json",
                source,
            ],
            timeout=90,
        )
        try:
            data = json.loads(raw)
        except Exception as exc:
            raise RuntimeError("cannot read video info") from exc
        stream = (data.get("streams") or [{}])[0] or {}
        fmt = data.get("format") or {}
        duration = stream.get("duration") or fmt.get("duration") or 0
        width = int(stream.get("width") or 1080)
        height = int(stream.get("height") or 1920)
    else:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", source],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
        probe_text = (proc.stderr or "") + "\n" + (proc.stdout or "")
        size_match = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", probe_text)
        dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe_text)
        width = int(size_match.group(1)) if size_match else 1080
        height = int(size_match.group(2)) if size_match else 1920
        duration = 0
        if dur_match:
            duration = int(dur_match.group(1)) * 3600 + int(dur_match.group(2)) * 60 + float(dur_match.group(3))
    try:
        duration_f = float(duration)
    except Exception:
        duration_f = 0.0
    return {
        "width": width,
        "height": height,
        "duration": max(duration_f, 0.1),
    }


def _asset_local_path(asset: Asset) -> Optional[Path]:
    filename = asset.filename or ""
    if not filename or "/" in filename or "\\" in filename:
        return None
    p = ASSETS_DIR / filename
    return p if p.exists() else None


def _resolve_source(
    *,
    db: Session,
    user_id: int,
    request: Optional[Request],
    job_dir: Path,
    file: Optional[UploadFile] = None,
    asset_id: str = "",
    video_url: str = "",
) -> Tuple[str, str, str, str]:
    aid = (asset_id or "").strip()
    if aid:
        asset = db.query(Asset).filter(Asset.asset_id == aid, Asset.user_id == user_id).first()
        if not asset:
            raise HTTPException(status_code=404, detail="asset not found")
        if (asset.media_type or "").lower() != "video":
            raise HTTPException(status_code=400, detail="asset is not a video")
        local = _asset_local_path(asset)
        public_url = get_asset_public_url(aid, user_id, request, db) if request is not None else (asset.source_url or "")
        if local:
            return str(local), aid, asset.filename or aid, public_url or ""
        if public_url:
            return public_url, aid, asset.filename or aid, public_url
        raise HTTPException(status_code=400, detail="asset has no local file or public URL")

    url = (video_url or "").strip()
    if url.startswith(("http://", "https://")):
        return url, "", Path(url.split("?")[0]).name or "remote-video.mp4", url

    if file is not None:
        raise HTTPException(status_code=400, detail="file source should be saved to asset before render")
    raise HTTPException(status_code=400, detail="asset_id or video_url is required")


def _extract_audio(ffmpeg: str, source: str, out_path: Path) -> None:
    _run_cmd(
        [
            ffmpeg,
            "-y",
            "-i",
            source,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-acodec",
            "pcm_s16le",
            str(out_path),
        ],
        timeout=900,
    )
    if not out_path.exists() or out_path.stat().st_size <= 128:
        raise RuntimeError("extracted audio is empty")


def _save_asset_record(
    db: Session,
    *,
    user_id: int,
    asset_id: str,
    filename: str,
    media_type: str,
    file_size: int,
    source_url: str,
    prompt: str,
    model: str,
    tags: str,
    meta: Dict[str, Any],
) -> None:
    asset = Asset(
        asset_id=asset_id,
        user_id=user_id,
        filename=filename,
        media_type=media_type,
        file_size=file_size,
        source_url=source_url,
        prompt=prompt,
        model=model,
        tags=tags,
        meta=meta,
    )
    db.add(asset)
    db.commit()


def _set_asset_source_url(db: Session, asset_id: str, user_id: int, source_url: str) -> None:
    url = (source_url or "").strip()
    if not url:
        return
    row = db.query(Asset).filter(Asset.asset_id == asset_id, Asset.user_id == user_id).first()
    if not row:
        return
    row.source_url = url
    db.add(row)
    db.commit()


def _save_binary_asset(
    db: Session,
    *,
    user_id: int,
    data: bytes,
    ext: str,
    content_type: str,
    media_type: str,
    prompt: str,
    model: str,
    tags: str,
    meta: Dict[str, Any],
) -> Tuple[str, str, int, str, str]:
    aid, fname, fsize, tos_url = _save_bytes_or_tos(data, ext, content_type)
    source_url = tos_url or ""
    _save_asset_record(
        db,
        user_id=user_id,
        asset_id=aid,
        filename=fname,
        media_type=media_type,
        file_size=fsize,
        source_url=source_url,
        prompt=prompt,
        model=model,
        tags=tags,
        meta=meta,
    )
    return aid, fname, fsize, source_url, str(ASSETS_DIR / fname)


def _server_base() -> str:
    base = (settings.auth_server_base or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("AUTH_SERVER_BASE is not configured")
    if base.lower().startswith("http://") and not re.search(
        r"^http://(127\.0\.0\.1|localhost|\[?::1\]?)(:\d+)?$",
        base,
        re.IGNORECASE,
    ):
        base = "https://" + base[7:]
    return base


def _upload_temp_to_server(
    *,
    data: bytes,
    filename: str,
    content_type: str,
    auth_headers: Dict[str, str],
    timeout: float = 180.0,
) -> str:
    if not data:
        raise RuntimeError("temporary upload data is empty")
    headers: Dict[str, str] = {}
    for key in ("Authorization", "X-Installation-Id"):
        value = (auth_headers or {}).get(key) or (auth_headers or {}).get(key.lower())
        if value:
            headers[key] = str(value)
    if not headers.get("Authorization"):
        raise RuntimeError("server temporary upload requires Authorization")
    files = {"file": (filename or "upload.bin", data, content_type or "application/octet-stream")}
    with httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False) as client:
        resp = client.post(f"{_server_base()}/api/assets/upload-temp", files=files, headers=headers)
    try:
        payload = resp.json()
    except Exception:
        payload = {"detail": resp.text}
    if resp.status_code >= 400:
        detail = payload.get("detail") if isinstance(payload, dict) else payload
        raise RuntimeError(_safe_error(detail))
    public_url = (payload.get("public_url") if isinstance(payload, dict) else "") or ""
    if not str(public_url).startswith(("http://", "https://")):
        raise RuntimeError("server temporary upload did not return public_url")
    return str(public_url)


def _call_server(path: str, payload: Dict[str, Any], auth_headers: Dict[str, str], *, timeout: float = 900.0) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    headers.update(auth_headers or {})
    with httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False) as client:
        resp = client.post(f"{_server_base()}{path}", json=payload, headers=headers)
    try:
        data = resp.json()
    except Exception:
        data = {"detail": resp.text}
    if resp.status_code >= 400 or (isinstance(data, dict) and data.get("ok") is False):
        detail = data.get("detail") if isinstance(data, dict) else data
        raise RuntimeError(_safe_error(detail))
    return data if isinstance(data, dict) else {}


def _get_server(path: str, auth_headers: Dict[str, str], *, timeout: float = 120.0) -> Dict[str, Any]:
    headers = {"Accept": "application/json"}
    headers.update(auth_headers or {})
    with httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False) as client:
        resp = client.get(f"{_server_base()}{path}", headers=headers)
    try:
        data = resp.json()
    except Exception:
        data = {"detail": resp.text}
    if resp.status_code >= 400 or (isinstance(data, dict) and data.get("ok") is False):
        detail = data.get("detail") if isinstance(data, dict) else data
        raise RuntimeError(_safe_error(detail))
    return data if isinstance(data, dict) else {}


def _template_caption_style(template: Dict[str, Any]) -> Dict[str, Any]:
    strategy = template.get("generation_strategy") if isinstance(template.get("generation_strategy"), dict) else {}
    style = strategy.get("caption_style") if isinstance(strategy.get("caption_style"), dict) else None
    if style is None:
        style = template.get("caption_style") if isinstance(template.get("caption_style"), dict) else {}
    merged = dict(style or {})
    if not merged.get("caption_max_chars") and merged.get("max_chars"):
        merged["caption_max_chars"] = merged.get("max_chars")
    overlay_fields = _overlay_fields_from_template(template)
    if overlay_fields and not merged.get("overlay_fields"):
        merged["overlay_fields"] = overlay_fields
    return _ensure_orientation_style_defaults(merged)


def _resolve_server_template(template_id: str, auth_headers: Dict[str, str]) -> Dict[str, Any]:
    tid = (template_id or "").strip()
    data = _get_server("/api/cutcli/templates", auth_headers, timeout=120.0)
    templates = data.get("templates") if isinstance(data, dict) else None
    if not isinstance(templates, list):
        raise RuntimeError("server did not return cutcli templates")
    for item in templates:
        if isinstance(item, dict) and str(item.get("id") or "") == tid:
            style = _template_caption_style(item)
            if not style:
                raise RuntimeError(f"template {tid} has no generation strategy")
            return item
    raise RuntimeError(f"template not found on server: {tid}")


def _safe_error(value: Any, limit: int = 1200) -> str:
    if isinstance(value, dict):
        for key in ("message", "detail", "error", "code"):
            if value.get(key):
                return _safe_error(value.get(key), limit)
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value or "").strip()
    if len(text) > limit:
        return text[-limit:]
    return text


_EDGE_PUNCT = " \t\r\n,.;:!?\uff0c\u3002\uff01\uff1f\uff1b\uff1a\u3001\"'\u201c\u201d\u2018\u2019\uff08\uff09()[]\u3010\u3011<>\u300a\u300b"


def _clean_caption_text(text: Any) -> str:
    s = str(text or "").replace("\u3000", " ").replace("\r\n", "\n").replace("\r", "\n").strip()
    s = re.sub(r"[ \t\f\v]+", " ", s)
    s = re.sub(r" *\n+ *", "\n", s)
    lines = [part.strip(_EDGE_PUNCT).strip() for part in s.split("\n")]
    return "\n".join(part for part in lines if part).strip()


def _clean_overlay_text(text: Any) -> str:
    s = str(text or "").replace("\u3000", " ").replace("\r\n", "\n").replace("\r", "\n").strip()
    s = re.sub(r"[ \t\f\v]+", " ", s)
    s = re.sub(r" *\n+ *", "\n", s)
    return "\n".join(part.strip() for part in s.split("\n") if part.strip()).strip()


def _caption_text_lines(text: str) -> List[str]:
    lines = [part.strip() for part in str(text or "").split("\n") if part.strip()]
    return lines or [""]


def _caption_display_len(text: str) -> int:
    total = 0
    for ch in text:
        if ch.isspace():
            continue
        total += 1
    return total


def _caption_line_display_len(text: str) -> int:
    return max((_caption_display_len(line) for line in _caption_text_lines(text)), default=0)


def _is_ascii_word_char(ch: str) -> bool:
    return bool(ch and re.match(r"[A-Za-z0-9]", ch))


def _is_cjk_char(ch: str) -> bool:
    return bool(ch and "\u4e00" <= ch <= "\u9fff")


def _caption_needs_space_between(left: str, right: str) -> bool:
    left = str(left or "").rstrip()
    right = str(right or "").lstrip()
    if not left or not right:
        return False
    a = left[-1]
    b = right[0]
    right_lower = right.lower()
    if right_lower in {"'s", "'re", "'ve", "'ll", "'d", "'m", "n't"}:
        return False
    if b in ".,!?;:%)]}\uff0c\u3002\uff01\uff1f\uff1b\uff1a\u3001":
        return False
    if a in "([{":
        return False
    if a in "-/" or b in "-/":
        return False
    if _is_ascii_word_char(a) and _is_ascii_word_char(b):
        return True
    if a in ".,!?;:" and (_is_ascii_word_char(b) or _is_cjk_char(b)):
        return True
    if (_is_ascii_word_char(a) and _is_cjk_char(b)) or (_is_cjk_char(a) and _is_ascii_word_char(b)):
        return True
    return False


def _join_caption_fragments(parts: List[str]) -> str:
    out = ""
    for raw in parts:
        piece = _clean_caption_text(raw)
        if not piece:
            continue
        if out and _caption_needs_space_between(out, piece):
            out += " "
        out += piece
    return _clean_caption_text(out)


def _caption_visual_units(text: str) -> float:
    line_units: List[float] = []
    for line in _caption_text_lines(text):
        total = 0.0
        for ch in line:
            if ch.isspace():
                total += 0.28
            elif re.match(r"[A-Za-z0-9]", ch):
                total += 0.56
            elif ch in _EDGE_PUNCT or ch in _CAPTION_HARD_BREAK_CHARS or ch in _CAPTION_SOFT_BREAK_CHARS:
                total += 0.38
            elif "\u4e00" <= ch <= "\u9fff":
                total += 1.0
            else:
                total += 0.86
        line_units.append(total)
    return max(line_units or [0.0])


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _caption_layout_scale_headroom(caption_style: Dict[str, Any]) -> float:
    layout = str(caption_style.get("ass_layout") or "")
    if layout == "dramatic_hook":
        return 1.34
    if layout == "center_burst":
        return 1.24
    if layout == "side_neon":
        return 1.08
    return 1.06


def _caption_usable_width(caption_style: Dict[str, Any], *, video_width: Optional[int] = None) -> float:
    width = max(360, int(video_width or 720))
    border = max(0, int(caption_style.get("ass_border") or 0))
    shadow = max(0, int(caption_style.get("ass_shadow") or 0))
    layout = str(caption_style.get("ass_layout") or "")
    usable_ratio = 0.84
    if layout == "center_burst":
        usable_ratio = 0.88
    elif layout == "lower_clean":
        usable_ratio = 0.88
    elif layout == "side_neon":
        usable_ratio = 0.62
    elif layout == "dramatic_hook":
        usable_ratio = 0.78
    return max(180.0, width * usable_ratio - border * 4 - shadow * 2)


def _caption_ass_font_size_value(cap: Dict[str, Any], caption_style: Dict[str, Any]) -> int:
    ass_font_size = int(caption_style.get("ass_font_size") or 86)
    base_cli_size = int(caption_style.get("font_size") or 13)
    cap_cli_size = int(cap.get("fontSize") or base_cli_size)
    min_ass_font_size = int(caption_style.get("min_ass_font_size") or (30 if str(caption_style.get("current_orientation") or "") == "landscape" else 44))
    return max(min_ass_font_size, ass_font_size + (cap_cli_size - base_cli_size) * 7)


def _caption_font_unit_width(font_size: int, caption_style: Dict[str, Any]) -> float:
    return max(36.0, font_size * 0.9 * _caption_layout_scale_headroom(caption_style))


def _safe_caption_visual_units(caption_style: Dict[str, Any], *, video_width: Optional[int] = None) -> float:
    configured = max(4, int(caption_style.get("caption_max_chars") or 11))
    pattern = str(caption_style.get("font_size_pattern") or "steady")
    base_cli_size = int(caption_style.get("font_size") or 13)
    worst_cli_size = base_cli_size
    if pattern == "punch":
        worst_cli_size += 2
    elif pattern == "burst":
        worst_cli_size += 1
    ass_font_size = _caption_ass_font_size_value({"fontSize": worst_cli_size}, caption_style)
    usable_width = _caption_usable_width(caption_style, video_width=video_width)
    font_unit_width = _caption_font_unit_width(ass_font_size, caption_style)
    return max(3.0, min(float(configured), usable_width / font_unit_width))


def _caption_visual_overflows(
    cap: Dict[str, Any],
    caption_style: Dict[str, Any],
    *,
    video_width: Optional[int] = None,
) -> bool:
    text = _clean_caption_text(cap.get("text"))
    if not text:
        return False
    usable_width = _caption_usable_width(caption_style, video_width=video_width)
    fs = _caption_ass_font_size_value(cap, caption_style)
    estimated_width = _caption_visual_units(text) * _caption_font_unit_width(fs, caption_style)
    if estimated_width <= usable_width:
        return False
    if "\n" in text and _caption_fits_wrapped_visual_width(
        text,
        max_chars=max(4, int(caption_style.get("caption_max_chars") or 11)),
        max_visual_units=_safe_caption_visual_units(caption_style, video_width=video_width),
    ):
        scaled = int(fs * (usable_width / max(1.0, estimated_width)) * 0.98)
        min_ass_font_size = int(caption_style.get("min_ass_font_size") or (30 if str(caption_style.get("current_orientation") or "") == "landscape" else 44))
        return scaled < min_ass_font_size
    return True


def _caption_fits_visual_width(
    text: str,
    *,
    max_chars: int,
    max_visual_units: Optional[float],
) -> bool:
    if _caption_line_display_len(text) > max_chars:
        return False
    if max_visual_units is not None and _caption_visual_units(text) > max_visual_units:
        return False
    return True


def _caption_fits_wrapped_visual_width(
    text: str,
    *,
    max_chars: int,
    max_visual_units: Optional[float],
    max_lines: int = 3,
) -> bool:
    lines = _caption_text_lines(text)
    if len(lines) > max_lines:
        return False
    if max((_caption_display_len(line) for line in lines), default=0) > max_chars:
        return False
    if max_visual_units is not None:
        for line in lines:
            if _caption_visual_units(line) > max_visual_units:
                return False
    return True


_CAPTION_PROTECTED_PHRASES = (
    "\u9700\u8981\u5177\u5907",
    "\u8d22\u7a0e\u987e\u95ee",
    "\u4e00\u4e2a\u4e13\u4e1a",
    "\u4e13\u4e1a\u9760\u8c31",
    "\u9760\u8c31\u7684",
    "\u5177\u5907",
    "\u9700\u8981",
    "\u8d22\u7a0e",
    "\u987e\u95ee",
    "\u4e13\u4e1a",
    "\u9760\u8c31",
    "\u5b57\u5e55",
    "\u914d\u97f3",
    "\u81ea\u52a8",
    "\u6570\u5b57\u4eba",
)
_CAPTION_BAD_LINE_END_CHARS = set("\u9700\u5177\u8d22\u987e\u4e13\u9760\u6570\u81ea\u914d\u5b57")
_CAPTION_BAD_LINE_START_CHARS = set("\u5907\u95ee\u7a0e\u4e1a\u8c31\u97f3\u5e55\u52a8")
_CAPTION_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['\u2019][A-Za-z0-9]+)?|[\u4e00-\u9fff]|[^\s]")


def _caption_tokens(text: str) -> List[str]:
    return _CAPTION_TOKEN_RE.findall(text or "")


def _caption_boundary_splits_protected(text: str, index: int) -> bool:
    if index <= 0 or index >= len(text):
        return False
    for phrase in _CAPTION_PROTECTED_PHRASES:
        start = text.find(phrase)
        while start >= 0:
            end = start + len(phrase)
            if start < index < end:
                return True
            start = text.find(phrase, start + 1)
    return False


def _caption_boundary_score(text: str, index: int, target_units: float) -> float:
    left = text[:index]
    right = text[index:]
    left_units = _caption_visual_units(left)
    right_units = _caption_visual_units(right)
    score = abs(left_units - right_units) * 1.4 + abs(left_units - target_units) * 0.7
    if _caption_boundary_splits_protected(text, index):
        score += 100.0
    if _caption_display_len(left) < 3 or _caption_display_len(right) < 3:
        score += 35.0
    if left and left[-1] in _CAPTION_BAD_LINE_END_CHARS:
        score += 18.0
    if right and right[0] in _CAPTION_BAD_LINE_START_CHARS:
        score += 18.0
    if left and left[-1] in _CAPTION_HARD_BREAK_CHARS:
        score -= 20.0
    elif left and left[-1] in _CAPTION_SOFT_BREAK_CHARS:
        score -= 10.0
    for phrase in ("\u9700\u8981", "\u5177\u5907", "\u8d22\u7a0e", "\u987e\u95ee"):
        if right.startswith(phrase):
            score -= 6.0
    return score


def _caption_split_boundary_score(left: str, right: str, *, max_visual_units: Optional[float]) -> float:
    left = _clean_caption_text(left)
    right = _clean_caption_text(right)
    if not left or not right:
        return 999.0
    left_units = _caption_visual_units(left)
    limit = max_visual_units if max_visual_units is not None else max(1.0, left_units)
    score = abs(left_units - limit * 0.82)
    if _caption_display_len(left) <= 2:
        score += 18.0
    if _caption_display_len(right) <= 1:
        score += 12.0
    a = left[-1]
    b = right[0]
    if a in _CAPTION_HARD_BREAK_CHARS:
        score -= 18.0
    elif a in _CAPTION_SOFT_BREAK_CHARS:
        score -= 9.0
    if _is_ascii_word_char(a) and _is_ascii_word_char(b):
        score += 80.0
    if _is_cjk_char(a) and _is_cjk_char(b):
        if a in "的了着过和与及或是要需把被将会能可在到对从给让就都也很更最先后中里上下一二三四五六七八九十":
            score -= 3.0
        if b in "的了着过和与及或是要需把被将会能可在到对从给让就都也很更最先后中里上下一二三四五六七八九十":
            score += 3.0
        if a in _CAPTION_BAD_LINE_END_CHARS:
            score += 12.0
        if b in _CAPTION_BAD_LINE_START_CHARS:
            score += 12.0
    return score


def _caption_best_two_line_wrap(
    text: str,
    *,
    max_chars: int,
    max_visual_units: Optional[float],
) -> str:
    clean = _clean_caption_text(text)
    if not clean or "\n" in clean:
        return clean
    tokens = _caption_tokens(clean)
    if len(tokens) < 2:
        return clean
    target_units = _caption_visual_units(clean) / 2.0
    best: Tuple[float, str] = (float("inf"), clean)
    for index in range(1, len(tokens)):
        left = _join_caption_fragments(tokens[:index])
        right = _join_caption_fragments(tokens[index:])
        if _caption_display_len(left) < 2 or _caption_display_len(right) < 2:
            continue
        candidate = left + "\n" + right
        if not _caption_fits_wrapped_visual_width(
            candidate,
            max_chars=max_chars,
            max_visual_units=max_visual_units,
        ):
            continue
        plain_left_len = len(_join_caption_fragments(tokens[:index]))
        score = _caption_boundary_score(clean, plain_left_len, target_units)
        if score < best[0]:
            best = (score, candidate)
    return best[1]


def _caption_wrap_score(lines: List[str], *, max_chars: int, max_visual_units: Optional[float]) -> Optional[float]:
    if not lines or any(not line for line in lines):
        return None
    if len(lines) > 3:
        return None
    units = [_caption_visual_units(line) for line in lines]
    display_lens = [_caption_display_len(line) for line in lines]
    visual_limit = max_visual_units if max_visual_units is not None else float(max_chars)
    if any(length > max_chars for length in display_lens):
        return None
    if any(unit > visual_limit for unit in units):
        return None
    avg = sum(units) / len(units)
    score = sum(abs(unit - avg) for unit in units)
    score += (len(lines) - 1) * 0.35
    for line in lines[:-1]:
        if line and line[-1] in _CAPTION_BAD_LINE_END_CHARS:
            score += 6.0
    for line in lines[1:]:
        if line and line[0] in _CAPTION_BAD_LINE_START_CHARS:
            score += 6.0
    if display_lens and display_lens[-1] <= 2 and len(lines) > 1:
        score += 8.0
    return score


def _caption_best_multiline_wrap(
    text: str,
    *,
    max_chars: int,
    max_visual_units: Optional[float],
    max_lines: int = 3,
) -> str:
    clean = _clean_caption_text(text)
    if not clean or "\n" in clean:
        return clean
    tokens = _caption_tokens(clean)
    if len(tokens) < 2:
        return clean
    best: Tuple[float, str] = (float("inf"), clean)

    def joined(start: int, end: int) -> str:
        return _join_caption_fragments(tokens[start:end])

    line_counts = range(1, min(max_lines, len(tokens)) + 1)
    for line_count in line_counts:
        if line_count == 1:
            lines = [clean]
            score = _caption_wrap_score(lines, max_chars=max_chars, max_visual_units=max_visual_units)
            if score is not None and score < best[0]:
                best = (score, clean)
            continue
        if line_count == 2:
            for a in range(1, len(tokens)):
                lines = [joined(0, a), joined(a, len(tokens))]
                score = _caption_wrap_score(lines, max_chars=max_chars, max_visual_units=max_visual_units)
                if score is None:
                    continue
                score += _caption_boundary_score(clean, len(lines[0]), _caption_visual_units(clean) / line_count) * 0.08
                candidate = "\n".join(lines)
                if score < best[0]:
                    best = (score, candidate)
            continue
        for a in range(1, len(tokens) - 1):
            for b in range(a + 1, len(tokens)):
                lines = [joined(0, a), joined(a, b), joined(b, len(tokens))]
                score = _caption_wrap_score(lines, max_chars=max_chars, max_visual_units=max_visual_units)
                if score is None:
                    continue
                score += _caption_boundary_score(clean, len(lines[0]), _caption_visual_units(clean) / line_count) * 0.05
                candidate = "\n".join(lines)
                if score < best[0]:
                    best = (score, candidate)
    return best[1]


def _split_caption_text(
    text: str,
    max_chars: int = 11,
    *,
    max_visual_units: Optional[float] = None,
) -> List[str]:
    tokens = _caption_tokens(text or "")
    expanded: List[str] = []
    for tok in tokens:
        if (
            max_visual_units is not None
            and len(tok) > 1
            and re.match(r"^[A-Za-z0-9]+$", tok)
            and _caption_visual_units(tok) > max_visual_units
        ):
            chunk = ""
            for ch in tok:
                candidate = chunk + ch
                if chunk and not _caption_fits_visual_width(
                    candidate,
                    max_chars=max_chars,
                    max_visual_units=max_visual_units,
                ):
                    expanded.append(chunk)
                    chunk = ch
                else:
                    chunk = candidate
            if chunk:
                expanded.append(chunk)
            continue
        expanded.append(tok)
    tokens = expanded
    chunks: List[str] = []
    cur = ""
    cur_tokens: List[str] = []
    for tok in tokens:
        candidate = _join_caption_fragments([cur, tok])
        if cur and not _caption_fits_visual_width(
            candidate,
            max_chars=max_chars,
            max_visual_units=max_visual_units,
        ):
            split_at = 0
            best_score = float("inf")
            for idx in range(1, len(cur_tokens)):
                left = _join_caption_fragments(cur_tokens[:idx])
                right = _join_caption_fragments(cur_tokens[idx:])
                if not _caption_fits_visual_width(left, max_chars=max_chars, max_visual_units=max_visual_units):
                    continue
                score = _caption_split_boundary_score(left, right, max_visual_units=max_visual_units)
                if score < best_score:
                    best_score = score
                    split_at = idx
            if split_at > 0:
                chunks.append(_clean_caption_text(_join_caption_fragments(cur_tokens[:split_at])))
                cur_tokens = cur_tokens[split_at:] + [tok]
                cur = _join_caption_fragments(cur_tokens)
            else:
                chunks.append(_clean_caption_text(cur))
                cur_tokens = [tok]
                cur = tok
        else:
            cur = candidate
            cur_tokens.append(tok)
    if cur:
        chunks.append(_clean_caption_text(cur))
    chunks = [x for x in chunks if x]
    return chunks


def _wrap_caption_text(
    text: str,
    *,
    max_chars: int,
    max_visual_units: Optional[float],
    max_lines: int = 3,
) -> str:
    clean = _clean_caption_text(text)
    if not clean:
        return ""
    if _caption_fits_visual_width(clean, max_chars=max_chars, max_visual_units=max_visual_units):
        return clean
    if re.search(r"[A-Za-z]", clean) and _caption_fits_wrapped_visual_width(
        clean,
        max_chars=max_chars,
        max_visual_units=max_visual_units,
        max_lines=1,
    ):
        return clean
    best_wrap = _caption_best_multiline_wrap(
        clean,
        max_chars=max_chars,
        max_visual_units=max_visual_units,
        max_lines=max_lines,
    )
    if best_wrap != clean:
        return best_wrap
    visual_limit = max_visual_units if max_visual_units is not None else float(max_chars)
    total_units = _caption_visual_units(clean)
    balanced_limit = (total_units / max(1, max_lines)) + 0.5
    wrap_limit = max(4.0, min(float(max_chars), max(visual_limit, balanced_limit)))
    tokens = _caption_tokens(clean)
    lines: List[str] = []
    cur = ""
    for tok in tokens:
        candidate = _join_caption_fragments([cur, tok])
        if cur and (
            _caption_display_len(candidate) > max_chars
            or _caption_visual_units(candidate) > wrap_limit
        ):
            lines.append(_clean_caption_text(cur))
            cur = tok
        else:
            cur = candidate
    if cur:
        lines.append(_clean_caption_text(cur))
    lines = [line for line in lines if line]
    if not lines or len(lines) > max_lines:
        return clean
    wrapped = "\n".join(lines)
    return (
        wrapped
        if _caption_fits_wrapped_visual_width(
            wrapped,
            max_chars=max_chars,
            max_visual_units=max_visual_units,
            max_lines=max_lines,
        )
        else clean
    )


_CAPTION_HARD_BREAK_CHARS = set(".!?;\u3002\uff01\uff1f\uff1b")
_CAPTION_SOFT_BREAK_CHARS = set(",:\u3001\uff0c\uff1a")


def _caption_word_fragments(text: Any, start_ms: int, end_ms: int) -> List[Dict[str, Any]]:
    raw = str(text or "").replace("\u3000", " ").strip()
    raw = re.sub(r"\s+", " ", raw)
    if not raw:
        return []

    pieces: List[Tuple[str, bool, bool]] = []
    cur = ""
    for ch in raw:
        cur += ch
        if ch in _CAPTION_HARD_BREAK_CHARS:
            pieces.append((cur, True, False))
            cur = ""
        elif ch in _CAPTION_SOFT_BREAK_CHARS:
            pieces.append((cur, False, True))
            cur = ""
    if cur:
        pieces.append((cur, False, False))
    if not pieces:
        return []

    span = max(len(pieces), end_ms - start_ms)
    weights = [max(1, _caption_display_len(piece[0])) for piece in pieces]
    total_weight = max(1, sum(weights))
    cursor = start_ms
    fragments: List[Dict[str, Any]] = []
    for idx, (piece, sentence_end, soft_end) in enumerate(pieces):
        if idx == len(pieces) - 1:
            frag_end = end_ms
        else:
            frag_end = min(end_ms, cursor + max(1, int(span * weights[idx] / total_weight)))
        clean = _clean_caption_text(piece)
        if clean:
            fragments.append(
                {
                    "text": clean,
                    "start_ms": cursor,
                    "end_ms": max(cursor + 1, frag_end),
                    "sentence_end": sentence_end,
                    "soft_end": soft_end,
                }
            )
        elif fragments:
            fragments[-1]["sentence_end"] = bool(fragments[-1].get("sentence_end") or sentence_end)
            fragments[-1]["soft_end"] = bool(fragments[-1].get("soft_end") or soft_end)
        else:
            fragments.append(
                {
                    "text": "",
                    "start_ms": cursor,
                    "end_ms": max(cursor + 1, frag_end),
                    "sentence_end": sentence_end,
                    "soft_end": soft_end,
                }
            )
        cursor = max(cursor + 1, frag_end)
    return fragments


def _caption_utterance_segments(utterances: Any) -> List[Dict[str, Any]]:
    if not isinstance(utterances, list):
        return []

    segments: List[Dict[str, Any]] = []
    for utt in utterances:
        if not isinstance(utt, dict):
            continue
        utt_words: List[Dict[str, Any]] = []
        for item in utt.get("words") or []:
            if not isinstance(item, dict):
                continue
            try:
                start_ms = int(float(item.get("start_time")))
                end_ms = int(float(item.get("end_time")))
            except Exception:
                continue
            if start_ms < 0 or end_ms <= start_ms:
                continue
            utt_words.extend(_caption_word_fragments(item.get("text"), start_ms, end_ms))
        if utt_words:
            utt_words.sort(key=lambda x: (x["start_ms"], x["end_ms"]))
            segments.append(
                {
                    "words": utt_words,
                    "start_ms": int(utt_words[0]["start_ms"]),
                    "end_ms": int(utt_words[-1]["end_ms"]),
                }
            )
            continue

        text = _clean_caption_text(utt.get("text"))
        if not text:
            continue
        try:
            start_ms = int(float(utt.get("start_time") or 0))
            end_ms = int(float(utt.get("end_time") or start_ms + 1200))
        except Exception:
            start_ms, end_ms = 0, 1200
        if end_ms <= start_ms:
            end_ms = start_ms + 1200
        segments.append({"text": text, "start_ms": start_ms, "end_ms": end_ms})

    segments.sort(key=lambda x: (int(x.get("start_ms") or 0), int(x.get("end_ms") or 0)))
    return segments


def _extract_stt_output(stt_data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(stt_data, dict):
        return {}
    for key in ("output", "result"):
        value = stt_data.get(key)
        if isinstance(value, dict):
            return value
    data = stt_data.get("data")
    if isinstance(data, dict):
        return _extract_stt_output(data)
    return stt_data


def _captions_from_stt(
    stt_data: Dict[str, Any],
    *,
    video_duration_sec: float,
    style: Optional[Dict[str, Any]] = None,
    caption_style: Optional[Dict[str, Any]] = None,
    video_width: Optional[int] = None,
) -> List[Dict[str, Any]]:
    caption_style = caption_style or style or {}
    output = _extract_stt_output(stt_data)
    utterances = output.get("utterances") if isinstance(output, dict) else None
    captions: List[Dict[str, Any]] = []
    video_end_us = max(100_000, int(max(video_duration_sec, 0.1) * 1_000_000))
    utterance_segments = _caption_utterance_segments(utterances)
    max_chars = int(caption_style.get("caption_max_chars") or 11)
    max_visual_units = _safe_caption_visual_units(caption_style, video_width=video_width)
    caption_max_lines = max(1, min(3, int(caption_style.get("caption_max_lines") or 2)))
    font_size_pattern = str(caption_style.get("font_size_pattern") or "steady")
    base_font_size = int(caption_style.get("font_size") or 13)

    def caption_font_size(index: int, text: str) -> int:
        display_len = _caption_display_len(text)
        wrap_adjust = 1 if "\n" in text else 0
        if re.search(r"[A-Za-z]", text) and " " in text:
            wrap_adjust += 1
        if font_size_pattern == "punch":
            return max(9, base_font_size + (2 if display_len <= 4 else 0) - wrap_adjust)
        if font_size_pattern == "burst":
            return max(9, base_font_size + (1 if index % 2 == 0 else 0) - wrap_adjust)
        if font_size_pattern == "side_neon":
            return max(9, base_font_size - (1 if display_len >= 8 else 0) - wrap_adjust)
        return max(9, base_font_size - wrap_adjust)

    def caption_position(index: int) -> Tuple[Optional[float], Optional[float]]:
        configured = caption_style.get("caption_position") if isinstance(caption_style.get("caption_position"), dict) else {}
        if configured:
            return _float_value(configured.get("x"), 0.0), _float_value(configured.get("y"), _float_value(caption_style.get("transform_y"), -0.66))
        layout = str(caption_style.get("ass_layout") or "")
        if layout == "side_neon":
            return (-0.58, 0.46 if index % 2 == 0 else 0.32)
        if layout == "dramatic_hook":
            return (0.0, -0.26 if index % 2 == 0 else -0.40)
        if layout in {"right_vertical_card", "tea_center_title", "red_yellow_hook"}:
            return (0.0, -0.70)
        if layout == "black_gold_quote":
            return (0.0, -0.82)
        if layout == "tcm_waist_banner":
            return (0.0, -0.76)
        if layout == "news_brief":
            return (0.0, -0.78)
        if layout == "education_focus_bar":
            return (0.0, -0.82)
        if layout == "center_burst":
            return (0.0, -0.52 if index % 2 == 0 else -0.64)
        if layout == "lower_clean":
            return (0.0, -0.72)
        return None, None

    def add_caption(text: str, start_ms: int, end_ms: int) -> None:
        clean = _clean_caption_text(text)
        if not clean:
            return
        clean = _join_caption_fragments(_caption_text_lines(clean))
        if not _caption_fits_visual_width(
            clean,
            max_chars=max_chars,
            max_visual_units=max_visual_units,
        ):
            wrapped = _wrap_caption_text(
                clean,
                max_chars=max_chars,
                max_visual_units=max_visual_units,
                max_lines=caption_max_lines,
            )
            if wrapped and _caption_fits_wrapped_visual_width(
                wrapped,
                max_chars=max_chars,
                max_visual_units=max_visual_units,
                max_lines=caption_max_lines,
            ):
                clean = wrapped
            else:
                sub_chunks = _split_caption_text(
                    clean,
                    max_chars=max_chars,
                    max_visual_units=max_visual_units,
                )
                if len(sub_chunks) > 1:
                    span_ms = max(len(sub_chunks) * 320, end_ms - start_ms)
                    step_ms = max(320, int(span_ms / len(sub_chunks)))
                    for idx, chunk in enumerate(sub_chunks):
                        add_caption(chunk, start_ms + idx * step_ms, start_ms + (idx + 1) * step_ms)
                    return
        start_us = max(0, int(start_ms * 1000))
        end_us = max(start_us + 450_000, int(end_ms * 1000))
        if start_us >= video_end_us:
            return
        end_us = min(video_end_us, end_us)
        if captions and start_us < int(captions[-1]["end"]):
            start_us = int(captions[-1]["end"])
            end_us = max(end_us, start_us + 350_000)
        if end_us <= start_us:
            return
        item = {
            "text": clean,
            "start": start_us,
            "end": min(video_end_us, end_us),
        }
        item["fontSize"] = caption_font_size(len(captions), clean)
        pos_x, pos_y = caption_position(len(captions))
        if pos_x is not None:
            item["transformX"] = pos_x
        if pos_y is not None:
            item["transformY"] = pos_y
        in_animation = str(caption_style.get("in_animation") or "").strip()
        if in_animation:
            item["inAnimation"] = in_animation
            item["inAnimationDuration"] = int(caption_style.get("in_animation_duration") or 0)
        loop_animation = str(caption_style.get("loop_animation") or "").strip()
        if loop_animation:
            item["loopAnimation"] = loop_animation
            item["loopAnimationDuration"] = int(caption_style.get("loop_animation_duration") or 0)
        captions.append(item)

    def add_caption_chunks(text: str, start_ms: int, end_ms: int, *, min_step_ms: int = 500) -> None:
        chunks = _split_caption_text(
            text,
            max_chars=max_chars,
            max_visual_units=max_visual_units,
        )
        if not chunks:
            return
        span = max(min_step_ms, end_ms - start_ms)
        step = max(min_step_ms, int(span / len(chunks)))
        for idx, chunk in enumerate(chunks):
            add_caption(chunk, start_ms + idx * step, start_ms + (idx + 1) * step)

    if utterance_segments:
        for entry in utterance_segments:
            segment = entry.get("words")
            if not segment:
                add_caption_chunks(
                    str(entry.get("text") or ""),
                    int(entry.get("start_ms") or 0),
                    int(entry.get("end_ms") or 0),
                    min_step_ms=500,
                )
                continue
            cur_words: List[str] = []
            cur_start: Optional[int] = None
            cur_end = 0
            for idx, word in enumerate(segment):
                text = str(word.get("text") or "")
                if not text:
                    if cur_words and word.get("sentence_end") and cur_start is not None:
                        add_caption(_join_caption_fragments(cur_words), cur_start, cur_end)
                        cur_words = []
                        cur_start = None
                    continue

                gap_ms = int(word["start_ms"] - cur_end) if cur_words else 0
                candidate_start = cur_start if cur_start is not None else int(word["start_ms"])
                candidate = _join_caption_fragments(cur_words + [text])
                dur_ms = int(word["end_ms"] - candidate_start)
                if cur_words and (
                    gap_ms >= 360
                    or not _caption_fits_visual_width(candidate, max_chars=max_chars, max_visual_units=max_visual_units)
                    or dur_ms > 2300
                ):
                    add_caption(_join_caption_fragments(cur_words), int(cur_start or 0), cur_end)
                    cur_words = []
                    cur_start = None

                if cur_start is None:
                    cur_start = int(word["start_ms"])
                cur_words.append(text)
                cur_end = int(word["end_ms"])

                current = _join_caption_fragments(cur_words)
                next_word = segment[idx + 1] if idx + 1 < len(segment) else None
                next_gap = int(next_word["start_ms"] - cur_end) if next_word else None
                current_ms = int(cur_end - cur_start)
                hard_after = bool(word.get("sentence_end")) or (next_gap is not None and next_gap >= 480)
                soft_after = bool(word.get("soft_end")) and (
                    _caption_display_len(current) >= 6 or current_ms >= 900
                )
                full_enough = (
                    not _caption_fits_visual_width(
                        current,
                        max_chars=max_chars,
                        max_visual_units=max_visual_units,
                    )
                    or current_ms >= 2300
                )
                if hard_after or soft_after or full_enough:
                    add_caption(_join_caption_fragments(cur_words), cur_start, cur_end)
                    cur_words = []
                    cur_start = None
                    cur_end = 0

            if cur_words and cur_start is not None:
                add_caption(_join_caption_fragments(cur_words), cur_start, cur_end)
    else:
        text = _clean_caption_text(output.get("text") if isinstance(output, dict) else "")
        add_caption_chunks(text, 0, int(video_end_us / 1000), min_step_ms=900)

    deduped: List[Dict[str, Any]] = []
    prev = ""
    for cap in captions:
        text = _clean_caption_text(cap.get("text"))
        if not text or text == prev:
            continue
        item = dict(cap)
        item["text"] = text
        deduped.append(item)
        prev = text
    return deduped


def _ass_color(value: str) -> str:
    value = str(value or "").strip()
    return value if value.startswith("&H") else "&H00FFFFFF"


def _clamp_int(value: float, min_value: int, max_value: int) -> int:
    return max(min_value, min(max_value, int(round(value))))


def _ass_x_from_norm(value: Any, width: int) -> int:
    margin = max(36, int(width * 0.06))
    half_span = max(1, (width / 2) - margin)
    return _clamp_int((width / 2) + _float_value(value, 0.0) * half_span, margin, width - margin)


def _ass_y_from_norm(value: Any, height: int) -> int:
    margin = max(60, int(height * 0.075))
    half_span = max(1, (height / 2) - margin)
    return _clamp_int((height / 2) - _float_value(value, -0.62) * half_span, margin, height - margin)


def _ass_escape(text: str) -> str:
    return _clean_caption_text(text).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def _ass_move(width: int, height: int, style: Dict[str, Any], cap: Dict[str, Any]) -> Tuple[int, int, int]:
    layout = style.get("ass_layout") or "center_burst"
    if layout == "side_neon":
        norm_x = _float_value(cap.get("transformX", style.get("transform_x", -0.56)), -0.56)
        anchor = 7 if norm_x <= 0 else 9
        x = _ass_x_from_norm(norm_x, width)
        y = _ass_y_from_norm(cap.get("transformY", style.get("transform_y", 0.38)), height)
        return anchor, x, y
    if layout == "dramatic_hook":
        return 5, _ass_x_from_norm(cap.get("transformX", style.get("transform_x", 0)), width), _ass_y_from_norm(cap.get("transformY", style.get("transform_y", -0.34)), height)
    if layout in {"right_vertical_card", "tea_center_title", "red_yellow_hook"}:
        return 2, _ass_x_from_norm(cap.get("transformX", style.get("transform_x", 0)), width), _ass_y_from_norm(cap.get("transformY", style.get("transform_y", -0.70)), height)
    if layout == "black_gold_quote":
        return 2, _ass_x_from_norm(cap.get("transformX", style.get("transform_x", 0)), width), _ass_y_from_norm(cap.get("transformY", style.get("transform_y", -0.82)), height)
    if layout == "tcm_waist_banner":
        return 2, _ass_x_from_norm(cap.get("transformX", style.get("transform_x", 0)), width), _ass_y_from_norm(cap.get("transformY", style.get("transform_y", -0.76)), height)
    if layout == "news_brief":
        return 2, _ass_x_from_norm(cap.get("transformX", style.get("transform_x", 0)), width), _ass_y_from_norm(cap.get("transformY", style.get("transform_y", -0.78)), height)
    if layout == "education_focus_bar":
        return 2, _ass_x_from_norm(cap.get("transformX", style.get("transform_x", 0)), width), _ass_y_from_norm(cap.get("transformY", style.get("transform_y", -0.82)), height)
    if layout == "lower_clean":
        return 2, _ass_x_from_norm(cap.get("transformX", style.get("transform_x", 0)), width), _ass_y_from_norm(cap.get("transformY", style.get("transform_y", -0.72)), height)
    return 2, _ass_x_from_norm(cap.get("transformX", style.get("transform_x", 0)), width), _ass_y_from_norm(cap.get("transformY", style.get("transform_y", -0.55)), height)


def _caption_ass_text(style: Dict[str, Any], cap: Dict[str, Any], width: int, height: int) -> str:
    base_text = _ass_escape(cap.get("text") or "")
    is_typewriter = bool(style.get("typewriter")) or str(style.get("caption_motion") or "") == "typewriter"
    if not is_typewriter:
        return base_text
    clean = _clean_caption_text(cap.get("text") or "")
    chars = [ch for ch in clean.replace("\n", " ") if ch]
    if not chars:
        return base_text
    step = max(35, int(style.get("typing_interval_ms") or 85))
    out = ""
    for idx, ch in enumerate(chars):
        out += "{\\alpha&HFF&\\t(%d,%d,\\alpha&H00&)}%s" % (idx * step, idx * step + 40, _ass_escape(ch))
    return out


def _overlay_text_value(overlay_texts: Dict[str, Any], style: Dict[str, Any], key: str) -> str:
    if isinstance(overlay_texts, dict) and key in overlay_texts:
        return _clean_overlay_text(overlay_texts.get(key))
    for field in style.get("overlay_fields") or []:
        if isinstance(field, dict) and str(field.get("key") or "") == key:
            return _clean_overlay_text(field.get("default"))
    return ""


def _overlay_text_value_any(overlay_texts: Dict[str, Any], style: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        if isinstance(overlay_texts, dict) and key in overlay_texts:
            return _clean_overlay_text(overlay_texts.get(key))
    for key in keys:
        value = _overlay_text_value(overlay_texts, style, key)
        if value:
            return value
    return ""


def _ass_text_with_newline(value: str) -> str:
    return _clean_overlay_text(value).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def _overlay_plain_lines(value: str) -> List[str]:
    return [line for line in _clean_overlay_text(value).split("\n") if line.strip()]


def _vertical_text(value: str) -> str:
    text = _clean_overlay_text(value).replace("\n", "")
    return "\n".join(ch for ch in text if ch.strip())


def _vertical_units(value: str) -> List[str]:
    text = _clean_overlay_text(value).replace("\n", "")
    units: List[str] = []
    buf = ""
    for ch in text:
        if not ch.strip():
            continue
        if ch.isascii() and ch.isalnum():
            buf += ch
            continue
        if buf:
            units.append(buf)
            buf = ""
        units.append(ch)
    if buf:
        units.append(buf)
    return units


def _fit_overlay_font_size(raw_size: int, lines: List[str], *, width: int, height_limit: int, min_size: int = 34) -> int:
    if not lines:
        return max(min_size, int(raw_size))
    max_units = max(_caption_visual_units(line) for line in lines) or 1.0
    by_width = int(max(80, width * 0.88) / max_units)
    by_height = int(max(24, height_limit) / (max(1, len(lines)) * 1.12))
    safe_min = max(14, min(int(min_size), by_width, by_height))
    return max(safe_min, min(int(raw_size), by_width, by_height))


def _ass_box_dialogue(end: str, color: str, points: List[Tuple[int, int]], *, layer: int = 2) -> str:
    if not points:
        points = [(0, 0)]
    min_x = min(x for x, _ in points)
    min_y = min(y for _, y in points)
    rel_points = [(x - min_x, y - min_y) for x, y in points]
    if rel_points[-1] != rel_points[0]:
        rel_points.append(rel_points[0])
    path = "m %d %d" % rel_points[0]
    for x, y in rel_points[1:]:
        path += " l %d %d" % (x, y)
    return "Dialogue: %d,0:00:00.00,%s,OverlayBox,,0,0,0,,{\\an7\\pos(%d,%d)\\p1\\c%s\\alpha&H00&}%s{\\p0}" % (layer, end, min_x, min_y, color, path)


def _vertical_unit_dialogues(
    *,
    end: str,
    units: List[str],
    style_name: str,
    x: int,
    center_y: int,
    font_size: int,
    line_height: int,
    color: str = "&H00FFFFFF",
    outline: str = "&H00000000",
    border: int = 0,
    shadow: int = 0,
    layer: int = 5,
) -> List[str]:
    if not units:
        return []
    total_h = max(1, (len(units) - 1) * line_height)
    start_y = int(center_y - total_h / 2)
    lines: List[str] = []
    for idx, unit in enumerate(units):
        y = start_y + idx * line_height
        lines.append(
            "Dialogue: %d,0:00:00.00,%s,%s,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c%s\\3c%s\\bord%d\\shad%d}%s"
            % (layer, end, style_name, x, y, font_size, color, outline, border, shadow, _ass_text_with_newline(unit))
        )
    return lines


def _overlay_dialogues(style: Dict[str, Any], overlay_texts: Dict[str, Any], width: int, height: int, duration_sec: float) -> List[str]:
    overlay_style = style.get("overlay_style") if isinstance(style.get("overlay_style"), dict) else {}
    layout = str(overlay_style.get("layout") or "").strip()
    top_text = _overlay_text_value_any(overlay_texts, style, "top_text", "headline")
    title = _overlay_text_value_any(overlay_texts, style, "title", "headline")
    subtitle = _overlay_text_value_any(overlay_texts, style, "subtitle", "subheadline")
    badge = _overlay_text_value(overlay_texts, style, "badge")
    if not any([top_text, title, subtitle, badge]):
        return []
    end = _ass_time(max(0.5, duration_sec))
    lines: List[str] = []
    title_x_ratio = _float_value(overlay_style.get("headline_x_ratio"), _float_value(overlay_style.get("title_x_ratio"), 0.5))
    title_x = int(width * title_x_ratio)
    subtitle_x = int(width * _float_value(overlay_style.get("subheadline_x_ratio"), title_x_ratio))
    top_x = int(width * _float_value(overlay_style.get("top_screen_x_ratio"), _float_value(overlay_style.get("top_x_ratio"), title_x_ratio)))
    top_y_screen_ratio = _float_value(overlay_style.get("top_screen_y_ratio"), _float_value(overlay_style.get("top_y_ratio"), _float_value(overlay_style.get("headline_y_ratio"), 0.12)))
    badge_x = int(width * _float_value(overlay_style.get("badge_x_ratio"), 0.5))
    if layout == "right_vertical_card":
        card_w_ratio = _float_value(overlay_style.get("card_width_ratio"), 0.19)
        card_h_ratio = _float_value(overlay_style.get("card_height_ratio"), 0.34)
        card_w = int(width * card_w_ratio)
        card_h = int(height * card_h_ratio)
        default_cx = 0.90 - card_w_ratio / 2
        center_x = int(width * _float_value(overlay_style.get("title_x_ratio"), default_cx))
        center_y = int(height * _float_value(overlay_style.get("title_y_ratio"), 0.27))
        x1 = _clamp_int(center_x - card_w // 2, 0, max(0, width - card_w))
        x2 = x1 + card_w
        y1 = _clamp_int(center_y - card_h // 2, 0, max(0, height - card_h))
        y2 = y1 + card_h
        lines.append(_ass_box_dialogue(end, str(overlay_style.get("card_color") or "&H00E8724B"), [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]))
        title_units = _vertical_units(title)
        subtitle_units = _vertical_units(subtitle)
        if title_units or subtitle_units:
            title_min = max(14, min(24, int(card_h / 14)))
            sub_min = max(12, min(20, int(card_h / 16)))
            fs = max(title_min, min(int(overlay_style.get("title_font_size") or 40), int(card_h / max(8.2, len(title_units) * 1.03))))
            sub_fs = max(sub_min, min(int(overlay_style.get("subtitle_font_size") or 28), int(card_h / max(5.8, len(subtitle_units) * 1.0))))
            title_x = x1 + int(card_w * 0.60)
            subtitle_x = x1 + int(card_w * 0.36)
            title_y = y1 + int(card_h * 0.52)
            subtitle_y = y1 + int(card_h * 0.55)
            lines.extend(_vertical_unit_dialogues(end=end, units=title_units, style_name="OverlayTitle", x=title_x, center_y=title_y, font_size=fs, line_height=int(fs * 1.08), layer=5))
            lines.extend(_vertical_unit_dialogues(end=end, units=subtitle_units, style_name="OverlaySub", x=subtitle_x, center_y=subtitle_y, font_size=sub_fs, line_height=int(sub_fs * 1.05), layer=5))
        return lines
    if layout == "education_focus_bar":
        top_lines = _overlay_plain_lines(top_text)
        if top_text:
            y = int(height * _float_value(overlay_style.get("top_y_ratio"), 0.08))
            first = top_lines[0] if top_lines else top_text
            rest = "\n".join(top_lines[1:])
            fs1 = _fit_overlay_font_size(int(overlay_style.get("top_font_size") or 74), [first], width=int(width * 0.82), height_limit=int(height * 0.12), min_size=int(overlay_style.get("top_min_font_size") or 46))
            lines.append("Dialogue: 7,0:00:00.00,%s,OverlayTitle,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c&H0000F7FF&\\3c&H00000000&\\bord4\\shad1}%s" % (end, top_x, y, fs1, _ass_text_with_newline(first)))
            if rest:
                fs2 = _fit_overlay_font_size(int(overlay_style.get("top_sub_font_size") or 66), _overlay_plain_lines(rest), width=int(width * 0.92), height_limit=int(height * 0.14), min_size=int(overlay_style.get("top_sub_min_font_size") or 42))
                lines.append("Dialogue: 7,0:00:00.00,%s,OverlayTitle,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c&H00FFFFFF&\\3c&H00000000&\\bord5\\shad1}%s" % (end, top_x, y + int(fs1 * 0.90), fs2, _ass_text_with_newline(rest)))
        if title or subtitle:
            x = int(width * _float_value(overlay_style.get("title_x_ratio"), 0.09))
            y = int(height * _float_value(overlay_style.get("title_y_ratio"), 0.43))
            title_fs = int(overlay_style.get("title_font_size") or 32)
            subtitle_fs = int(overlay_style.get("subtitle_font_size") or 26)
            subtitle_gap = int(overlay_style.get("subtitle_gap") or 42)
            if title:
                lines.append("Dialogue: 7,0:00:00.00,%s,OverlayTitle,,0,0,0,,{\\an1\\pos(%d,%d)\\fs%d\\c&H0000F7FF&\\3c&H00111111&\\bord2\\shad1}%s" % (end, x, y, title_fs, _ass_text_with_newline(title)))
            if subtitle:
                lines.append("Dialogue: 7,0:00:00.00,%s,OverlaySub,,0,0,0,,{\\an1\\pos(%d,%d)\\fs%d\\c&H00FFFFFF&\\3c&H00111111&\\bord2\\shad1}%s" % (end, x, y + subtitle_gap, subtitle_fs, _ass_text_with_newline(subtitle)))
        if badge:
            bar_w = int(width * 0.84)
            bar_h = int(height * _float_value(overlay_style.get("badge_height_ratio"), 0.07))
            x1 = _clamp_int(badge_x - bar_w // 2, 0, max(0, width - bar_w))
            badge_center_y = int(height * _float_value(overlay_style.get("badge_y_ratio"), 0.58))
            y1 = _clamp_int(badge_center_y - bar_h // 2, 0, max(0, height - bar_h))
            lines.append(_ass_box_dialogue(end, "&H00FFFFFF", [(x1, y1), (x1 + bar_w, y1), (x1 + bar_w, y1 + bar_h), (x1, y1 + bar_h)], layer=4))
            fs = _fit_overlay_font_size(int(overlay_style.get("badge_font_size") or 42), [badge], width=bar_w - 40, height_limit=bar_h - 8, min_size=20)
            text_x = _clamp_int(badge_x, x1 + 20, x1 + bar_w - 20)
            lines.append("Dialogue: 8,0:00:00.00,%s,OverlayTitle,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c&H00000000&\\bord0\\shad0}%s" % (end, text_x, y1 + bar_h // 2, fs, _ass_text_with_newline(badge)))
        return lines
    if layout == "tea_center_title":
        y = int(height * _float_value(overlay_style.get("headline_y_ratio"), 0.47))
        title_fs = 0
        if title:
            fs = _fit_overlay_font_size(int(overlay_style.get("headline_font_size") or 92), _overlay_plain_lines(title), width=int(width * 0.84), height_limit=int(height * 0.11), min_size=54)
            title_fs = fs
            lines.append("Dialogue: 7,0:00:00.00,%s,OverlayTitle,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c&H00FFFFFF&\\3c&H00111111&\\bord5\\shad2}%s" % (end, title_x, y, fs, _ass_text_with_newline(title)))
        if subtitle:
            fs2 = _fit_overlay_font_size(int(overlay_style.get("subheadline_font_size") or 56), _overlay_plain_lines(subtitle), width=int(width * 0.86), height_limit=int(height * 0.09), min_size=38)
            default_sub_y = (y + int(max(title_fs * 0.92, fs2 * 1.70, height * 0.072))) / max(1, height)
            sub_y = int(height * _float_value(overlay_style.get("subheadline_y_ratio"), default_sub_y))
            lines.append("Dialogue: 7,0:00:00.00,%s,OverlaySub,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c&H0000F7FF&\\3c&H00111111&\\bord4\\shad2}%s" % (end, subtitle_x, sub_y, fs2, _ass_text_with_newline(subtitle)))
        return lines
    if layout == "red_yellow_hook":
        y = int(height * _float_value(overlay_style.get("headline_y_ratio"), 0.10))
        if title:
            fs = _fit_overlay_font_size(int(overlay_style.get("headline_font_size") or 78), _overlay_plain_lines(title), width=int(width * 0.9), height_limit=int(height * 0.10), min_size=46)
            lines.append("Dialogue: 7,0:00:00.00,%s,OverlayTitle,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c&H002620D8&\\3c&H00FFFFFF&\\bord4\\shad2\\frz-2}%s" % (end, title_x, y, fs, _ass_text_with_newline(title)))
        if subtitle:
            fs2 = _fit_overlay_font_size(int(overlay_style.get("subheadline_font_size") or 70), _overlay_plain_lines(subtitle), width=int(width * 0.86), height_limit=int(height * 0.09), min_size=44)
            lines.append("Dialogue: 7,0:00:00.00,%s,OverlaySub,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c&H0000F7FF&\\3c&H00000000&\\bord4\\shad2\\frz-2}%s" % (end, subtitle_x, int(height * _float_value(overlay_style.get("subheadline_y_ratio"), (y + int(fs2 * 0.98)) / max(1, height))), fs2, _ass_text_with_newline(subtitle)))
        return lines
    if layout == "black_gold_quote":
        y = int(height * _float_value(overlay_style.get("headline_y_ratio"), 0.44))
        if title:
            fs = _fit_overlay_font_size(int(overlay_style.get("headline_font_size") or 78), _overlay_plain_lines(title), width=int(width * 0.86), height_limit=int(height * 0.11), min_size=48)
            lines.append("Dialogue: 7,0:00:00.00,%s,OverlayTitle,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c&H00FFFFFF&\\3c&H00000000&\\bord5\\shad2}%s" % (end, title_x, y, fs, _ass_text_with_newline(title)))
        if subtitle:
            fs2 = _fit_overlay_font_size(int(overlay_style.get("subheadline_font_size") or 86), _overlay_plain_lines(subtitle), width=int(width * 0.90), height_limit=int(height * 0.14), min_size=50)
            lines.append("Dialogue: 7,0:00:00.00,%s,OverlaySub,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c&H0000F7FF&\\3c&H00000000&\\bord5\\shad3}%s" % (end, subtitle_x, int(height * _float_value(overlay_style.get("subheadline_y_ratio"), (y + int(fs2 * 0.95)) / max(1, height))), fs2, _ass_text_with_newline(subtitle)))
        return lines
    if layout == "tcm_waist_banner":
        y = int(height * _float_value(overlay_style.get("headline_y_ratio"), 0.43))
        if title:
            fs = _fit_overlay_font_size(int(overlay_style.get("headline_font_size") or 76), _overlay_plain_lines(title), width=int(width * 0.88), height_limit=int(height * 0.11), min_size=46)
            lines.append("Dialogue: 7,0:00:00.00,%s,OverlayTitle,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c&H0000B8FF&\\3c&H00000000&\\bord5\\shad3}%s" % (end, title_x, y, fs, _ass_text_with_newline(title)))
        if badge:
            badge_fs = _fit_overlay_font_size(int(overlay_style.get("badge_font_size") or 38), [badge], width=int(width * 0.48), height_limit=int(height * 0.052), min_size=24)
            box_w = max(int(width * 0.46), int(_caption_visual_units(badge) * badge_fs * 0.78) + int(width * 0.08))
            box_h = max(int(height * 0.044), int(badge_fs * 1.28))
            x1 = _clamp_int(badge_x - box_w // 2, 0, max(0, width - box_w))
            x2 = x1 + box_w
            default_badge_center = y + int(max(48, (int(overlay_style.get("headline_font_size") or 76)) * 0.62)) + box_h // 2
            badge_center_y = int(height * _float_value(overlay_style.get("badge_y_ratio"), default_badge_center / max(1, height)))
            y1 = _clamp_int(badge_center_y - box_h // 2, 0, max(0, height - box_h))
            y2 = y1 + box_h
            cut = max(18, int(box_h * 0.48))
            mid = (y1 + y2) // 2
            points = [(x1 + cut, y1), (x2 - cut, y1), (x2, mid), (x2 - cut, y2), (x1 + cut, y2), (x1, mid)]
            lines.append(_ass_box_dialogue(end, str(overlay_style.get("waist_color") or "&H006B4A38"), points, layer=3))
            text_x = _clamp_int(badge_x, x1 + cut, x2 - cut)
            lines.append("Dialogue: 8,0:00:00.00,%s,OverlaySub,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c&H00FFFFFF&\\3c&H00000000&\\bord2\\shad1}%s" % (end, text_x, mid, badge_fs, _ass_text_with_newline(badge)))
        return lines
    if layout == "news_brief":
        y = int(height * _float_value(overlay_style.get("headline_y_ratio"), 0.11))
        fs = _fit_overlay_font_size(int(overlay_style.get("headline_font_size") or 82), _overlay_plain_lines((title + subtitle).strip()) or [title or subtitle], width=int(width * 0.86), height_limit=int(height * 0.10), min_size=48)
        if title:
            lines.append("Dialogue: 7,0:00:00.00,%s,OverlayTitle,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c&H0000F7FF&\\3c&H00000000&\\bord5\\shad2}%s" % (end, int(width * _float_value(overlay_style.get("title_x_ratio"), 0.41)), y, fs, _ass_text_with_newline(title)))
        if subtitle:
            lines.append("Dialogue: 7,0:00:00.00,%s,OverlaySub,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c&H00FFFFFF&\\3c&H00000000&\\bord5\\shad2}%s" % (end, int(width * _float_value(overlay_style.get("subheadline_x_ratio"), 0.58)), y, fs, _ass_text_with_newline(subtitle)))
        return lines
    if layout == "top_banner":
        top_lines = _overlay_plain_lines(top_text)
        profile_title = _overlay_text_value_any(overlay_texts, style, "title")
        profile_subtitle = _overlay_text_value_any(overlay_texts, style, "subtitle", "subheadline")
        title_lines = _overlay_plain_lines(profile_title)
        subtitle_lines = _overlay_plain_lines(profile_subtitle)
        profile_lines = title_lines + subtitle_lines
        default_ratio = 0.30 if len(top_lines) > 1 else 0.22
        ratio = _float_value(overlay_style.get("banner_height_ratio"), default_ratio)
        box_h = min(max(120, int(height * ratio)), max(120, height - 24))
        bg = str(overlay_style.get("banner_color") or "&HA8F3E7CF")
        text_color = str(overlay_style.get("headline_color") or "&H001F4A86")
        outline = str(overlay_style.get("headline_outline") or "&H00FFFFFF")
        raw_fs = int(overlay_style.get("headline_font_size") or max(58, width * 0.085))
        fs = _fit_overlay_font_size(raw_fs, top_lines, width=width, height_limit=int(box_h * 0.88), min_size=40)
        text_y = int(box_h * _float_value(overlay_style.get("headline_y_ratio"), 0.56))
        if top_text:
            lines.append("Dialogue: 2,0:00:00.00,%s,OverlayBox,,0,0,0,,{\\pos(%d,%d)\\p1\\c%s}m 0 0 l %d 0 l %d %d l 0 %d{\\p0}" % (end, width // 2, box_h // 2, bg, width, width, box_h, box_h))
            lines.append("Dialogue: 5,0:00:00.00,%s,OverlayTitle,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c%s\\3c%s\\bord4\\shad1}%s" % (end, top_x, int(height * top_y_screen_ratio), fs, text_color, outline, _ass_text_with_newline(top_text)))
        if profile_lines:
            base_x = int(width * _float_value(overlay_style.get("profile_x_ratio"), 0.10))
            base_y = int(height * _float_value(overlay_style.get("profile_y_ratio"), 0.57))
            profile_limit = int(width * 0.48)
            title_fs = _fit_overlay_font_size(int(overlay_style.get("profile_title_font_size") or max(34, width * 0.045)), title_lines, width=profile_limit, height_limit=int(height * 0.12), min_size=24)
            subtitle_fs = _fit_overlay_font_size(int(overlay_style.get("profile_subtitle_font_size") or max(22, width * 0.027)), subtitle_lines, width=profile_limit, height_limit=int(height * 0.14), min_size=18)
            if profile_title:
                lines.append("Dialogue: 6,0:00:00.00,%s,OverlayTitle,,0,0,0,,{\\an1\\pos(%d,%d)\\fs%d\\c&H00FFFFFF&\\3c&H002A1606&\\bord4\\shad2}%s" % (end, base_x, base_y, title_fs, _ass_text_with_newline(profile_title)))
            if profile_subtitle:
                sub_y = base_y + max(30, int(title_fs * 0.78))
                lines.append("Dialogue: 6,0:00:00.00,%s,OverlaySub,,0,0,0,,{\\an1\\pos(%d,%d)\\fs%d\\c&H00FFFFFF&\\3c&H002A1606&\\bord2\\shad2}%s" % (end, base_x, sub_y, subtitle_fs, _ass_text_with_newline(profile_subtitle)))
        return lines
    if layout == "center_quote":
        headline = title
        subheadline = subtitle
        headline_lines = _overlay_plain_lines(headline)
        text_color = str(overlay_style.get("headline_color") or "&H00FFFFFF")
        outline = str(overlay_style.get("headline_outline") or "&H00222931")
        raw_fs = int(overlay_style.get("headline_font_size") or max(58, width * 0.09))
        fs = _fit_overlay_font_size(raw_fs, headline_lines, width=width, height_limit=int(height * 0.24), min_size=42)
        sub_fs = int(overlay_style.get("subheadline_font_size") or max(26, width * 0.04))
        y = int(height * _float_value(overlay_style.get("headline_y_ratio"), 0.48))
        if headline:
            lines.append("Dialogue: 5,0:00:00.00,%s,OverlayTitle,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c%s\\3c%s\\bord5\\shad2}%s" % (end, title_x, y, fs, text_color, outline, _ass_text_with_newline(headline)))
        if subheadline:
            lines.append("Dialogue: 5,0:00:00.00,%s,OverlaySub,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c&H00FFFFFF&\\3c&H001E293B&\\bord2\\shad2}%s" % (end, subtitle_x, int(height * _float_value(overlay_style.get("subheadline_y_ratio"), (y + int(fs * 0.78)) / max(1, height))), sub_fs, _ass_text_with_newline(subheadline)))
        return lines
    if layout == "market_label":
        headline = title
        headline_lines = _overlay_plain_lines(headline)
        text_color = str(overlay_style.get("headline_color") or "&H00FFFFFF")
        outline = str(overlay_style.get("headline_outline") or "&H00111111")
        badge_color = str(overlay_style.get("badge_color") or "&H001B7BE6")
        raw_fs = int(overlay_style.get("headline_font_size") or max(60, width * 0.088))
        fs = _fit_overlay_font_size(raw_fs, headline_lines, width=width, height_limit=int(height * 0.24), min_size=42)
        y = int(height * _float_value(overlay_style.get("headline_y_ratio"), 0.58))
        if badge:
            box_w = int(width * 0.37)
            x1 = _clamp_int(badge_x - box_w // 2, 0, max(0, width - box_w))
            x2 = x1 + box_w
            badge_center_y = int(height * _float_value(overlay_style.get("badge_y_ratio"), max(0.05, (y - int(fs * 0.68)) / max(1, height))))
            box_h = max(int(height * 0.052), int(fs * 0.52))
            y1 = _clamp_int(badge_center_y - box_h // 2, 0, max(0, height - box_h))
            y2 = y1 + box_h
            lines.append("Dialogue: 3,0:00:00.00,%s,OverlayBox,,0,0,0,,{\\pos(0,0)\\p1\\c%s}m %d %d l %d %d l %d %d l %d %d{\\p0}" % (end, badge_color, x1, y1, x2, y1, x2, y2, x1, y2))
            lines.append("Dialogue: 6,0:00:00.00,%s,OverlaySub,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c&H00FFFFFF&\\bord0\\shad0}%s" % (end, _clamp_int(badge_x, x1 + 12, x2 - 12), (y1 + y2) // 2, max(20, int(fs * 0.34)), _ass_text_with_newline(badge)))
        if headline:
            lines.append("Dialogue: 5,0:00:00.00,%s,OverlayTitle,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c%s\\3c%s\\bord5\\shad3}%s" % (end, title_x, y, fs, text_color, outline, _ass_text_with_newline(headline)))
        return lines
    # Default compact headline, useful for the original four templates.
    y = int(height * 0.18)
    fallback_title = title or top_text
    if fallback_title:
        lines.append("Dialogue: 5,0:00:00.00,%s,OverlayTitle,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c&H00FFFFFF&\\3c&H00111827&\\bord4\\shad2}%s" % (end, title_x, y, max(46, int(width * 0.062)), _ass_text_with_newline(fallback_title)))
    if badge:
        lines.append("Dialogue: 5,0:00:00.00,%s,OverlaySub,,0,0,0,,{\\an5\\pos(%d,%d)\\fs%d\\c&H0000F7FF&\\3c&H00111827&\\bord3\\shad1}%s" % (end, badge_x, y + max(54, int(width * 0.07)), max(28, int(width * 0.04)), _ass_text_with_newline(badge)))
    return lines


def _write_ass(path: Path, captions: List[Dict[str, Any]], style: Dict[str, Any], width: int, height: int, overlay_texts: Optional[Dict[str, Any]] = None, duration_sec: Optional[float] = None) -> None:
    font_size = int(style.get("ass_font_size") or 72)
    if width <= 720:
        font_size = max(34, int(font_size * 0.82))
    border = int(style.get("ass_border") or 5)
    shadow = int(style.get("ass_shadow") or 2)
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: %d" % width,
        "PlayResY: %d" % height,
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        "Style: Default,Microsoft YaHei,%d,%s,&H00FFFFFF,%s,&H64000000,-1,0,0,0,100,100,0,0,1,%d,%d,%d,40,40,%d,1"
        % (
            font_size,
            _ass_color(style.get("ass_primary") or ""),
            _ass_color(style.get("ass_outline") or ""),
            border,
            shadow,
            int(style.get("ass_alignment") or 2),
            int(style.get("ass_margin_v") or 180),
        ),
        "Style: OverlayTitle,Microsoft YaHei,%d,&H00FFFFFF,&H00FFFFFF,&H00111827,&H64000000,-1,0,0,0,100,100,0,0,1,4,2,5,30,30,30,1"
        % max(40, int(width * 0.07)),
        "Style: OverlaySub,Microsoft YaHei,%d,&H00FFFFFF,&H00FFFFFF,&H00111827,&H64000000,-1,0,0,0,100,100,0,0,1,2,1,5,30,30,30,1"
        % max(26, int(width * 0.038)),
        "Style: OverlayBox,Microsoft YaHei,32,&H80FFFFFF,&H80FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1",
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    if duration_sec is None:
        duration_sec = 0.0
        for cap in captions:
            duration_sec = max(duration_sec, float(cap.get("end") or 0) / 1_000_000.0)
    lines.extend(_overlay_dialogues(style, overlay_texts or {}, width, height, max(duration_sec or 0.5, 0.5)))
    for cap in captions:
        start = max(0.0, float(cap.get("start") or 0) / 1_000_000.0)
        end = max(start + 0.2, float(cap.get("end") or 0) / 1_000_000.0)
        align, x, y = _ass_move(width, height, style, cap)
        text = _caption_ass_text(style, cap, width, height)
        override = "{\\an%d\\pos(%d,%d)}" % (align, x, y)
        lines.append("Dialogue: 0,%s,%s,Default,,0,0,0,,%s%s" % (_ass_time(start), _ass_time(end), override, text))
    path.write_text("\n".join(lines), encoding="utf-8")


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - math.floor(seconds)) * 100))
    if cs >= 100:
        s += 1
        cs = 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ffmpeg_filter_path(path: Path) -> str:
    text = str(path.resolve()).replace("\\", "/")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\\'")
    return text


def _render_ffmpeg(
    *,
    ffmpeg: str,
    source: str,
    out_path: Path,
    ass_path: Path,
) -> None:
    _run_cmd(
        [
            ffmpeg,
            "-y",
            "-i",
            source,
            "-vf",
            "ass='%s'" % _ffmpeg_filter_path(ass_path),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(out_path),
        ],
        timeout=1800,
    )
    if not out_path.exists() or out_path.stat().st_size <= 1024:
        raise RuntimeError("ffmpeg render output is empty")


def _find_cutcli() -> str:
    env_path = (os.environ.get("CUTCLI_BIN") or os.environ.get("CUTCLI_PATH") or "").strip().strip('"')
    candidates = [env_path] if env_path else []
    candidates.extend(
        [
            str(Path.home() / "bin" / ("cutcli.exe" if os.name == "nt" else "cutcli")),
            "cutcli.exe" if os.name == "nt" else "cutcli",
        ]
    )
    for cand in candidates:
        if not cand:
            continue
        path = Path(cand)
        if path.exists():
            return str(path.resolve())
        if shutil.which(cand):
            return cand
    raise RuntimeError("cutcli is not installed or CUTCLI_BIN is not configured")


def _write_json(path: Path, value: Any) -> str:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _cutcli_env(job_dir: Path) -> Dict[str, str]:
    env = os.environ.copy()
    drafts_dir = job_dir / "cutcli_drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    env["CUT_DRAFTS_DIR"] = str(drafts_dir)
    return env


def _build_cutcli_draft(
    *,
    cutcli: str,
    job_dir: Path,
    source: str,
    public_source: str,
    source_info: Dict[str, Any],
    captions: List[Dict[str, Any]],
    style: Dict[str, Any],
    overlay_texts: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Path]:
    env = _cutcli_env(job_dir)
    draft_name = "lobster_caption_" + job_dir.name
    width = int(source_info.get("width") or 1080)
    height = int(source_info.get("height") or 1920)
    created = _json_from_cmd(
        [cutcli, "draft", "create", "--name", draft_name, "--width", str(width), "--height", str(height), "--pretty"],
        timeout=90,
        env=env,
    )
    draft_id = str(created.get("draftId") or draft_name)
    duration_us = max(100_000, int(float(source_info.get("duration") or 0.1) * 1_000_000))
    video_json = _write_json(
        job_dir / "cutcli_video.json",
        [
            {
                "videoUrl": public_source or source,
                "width": width,
                "height": height,
                "duration": duration_us,
                "start": 0,
                "end": duration_us,
                "volume": 1,
            }
        ],
    )
    _run_cmd([cutcli, "videos", "add", draft_id, "--video-infos", video_json], timeout=180, env=env)
    caption_payload = []
    headline = _overlay_text_value_any(overlay_texts or {}, style, "top_text", "title", "headline")
    if headline:
        overlay_style = style.get("overlay_style") if isinstance(style.get("overlay_style"), dict) else {}
        headline_x_ratio = _float_value(
            overlay_style.get("top_screen_x_ratio"),
            _float_value(overlay_style.get("top_x_ratio"), _float_value(overlay_style.get("headline_x_ratio"), 0.5)),
        )
        headline_y_ratio = _float_value(
            overlay_style.get("top_screen_y_ratio"),
            _float_value(overlay_style.get("top_y_ratio"), _float_value(overlay_style.get("headline_y_ratio"), 0.14)),
        )
        caption_payload.append(
            {
                "text": headline,
                "start": 0,
                "end": duration_us,
                "fontSize": max(int(style.get("font_size") or 12), int(overlay_style.get("cutcli_headline_font_size") or 16)),
                "transformX": _clamp_norm((headline_x_ratio - 0.5) / 0.5, 0.0),
                "transformY": _clamp_norm((0.5 - headline_y_ratio) / 0.5, 0.72),
                "inAnimation": style.get("in_animation") or style.get("cutcli_in_animation") or "",
                "inAnimationDuration": int(style.get("in_animation_duration") or 260_000),
            }
        )
    for cap in captions:
        item = {
            "text": cap["text"],
            "start": cap["start"],
            "end": cap["end"],
            "fontSize": cap.get("fontSize"),
            "inAnimation": cap.get("inAnimation"),
            "inAnimationDuration": cap.get("inAnimationDuration"),
        }
        if cap.get("loopAnimation"):
            item["loopAnimation"] = cap.get("loopAnimation")
            item["loopAnimationDuration"] = cap.get("loopAnimationDuration")
        caption_payload.append(item)
    captions_json = _write_json(job_dir / "cutcli_captions.json", caption_payload)
    cmd = [
        cutcli,
        "captions",
        "add",
        draft_id,
        "--captions",
        captions_json,
        "--font-size",
        str(style.get("font_size") or style.get("cutcli_font_size") or 12),
        "--bold",
        "--alignment",
        "0",
        "--text-color",
        str(style.get("text_color") or style.get("cutcli_text_color") or "#FFFFFF"),
        "--border-color",
        str(style.get("border_color") or style.get("cutcli_border_color") or "#111827"),
        "--border-width",
        str(style.get("border_width") or style.get("cutcli_border_width") or "0.07"),
        "--transform-x",
        str(style.get("transform_x") or style.get("cutcli_transform_x") or "0"),
        "--transform-y",
        str(style.get("transform_y") or style.get("cutcli_transform_y") or "-0.65"),
        "--has-shadow",
        "--shadow-color",
        "#000000",
    ]
    text_effect = style.get("text_effect") or style.get("cutcli_text_effect")
    if text_effect:
        cmd.extend(["--text-effect", str(text_effect)])
    _run_cmd(cmd, timeout=180, env=env)
    zip_path = job_dir / f"{draft_id}.zip"
    _run_cmd([cutcli, "draft", "zip", draft_id, "--output", str(zip_path)], timeout=240, env=env)
    if not zip_path.exists() or zip_path.stat().st_size <= 0:
        raise RuntimeError("draft zip is empty")
    return draft_id, zip_path


def _extract_video_url(value: Any) -> str:
    best: List[Tuple[int, str]] = []

    def visit(item: Any, key: str = "") -> None:
        if isinstance(item, dict):
            for k, v in item.items():
                visit(v, str(k))
        elif isinstance(item, list):
            for v in item:
                visit(v, key)
        elif isinstance(item, str) and item.startswith(("http://", "https://")):
            score = 0
            lower = item.lower()
            if any(x in lower for x in (".mp4", ".mov", ".webm")):
                score += 10
            if any(x in key.lower() for x in ("video", "render", "result", "url", "output")):
                score += 4
            if ".zip" in lower:
                score -= 50
            best.append((score, item))

    visit(value)
    if not best:
        return ""
    best.sort(key=lambda x: x[0], reverse=True)
    return best[0][1]


def _run_job(
    *,
    job_id: str,
    user_id: int,
    template: Dict[str, Any],
    render_mode: str,
    asset_id: str,
    video_url: str,
    overlay_texts: Optional[Dict[str, Any]],
    position_overrides: Optional[Dict[str, Any]],
    auth_headers: Dict[str, str],
) -> None:
    db = SessionLocal()
    job_dir = _JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    current_stage = "queued"
    try:
        current_stage = "resolve_source"
        _update_job(db, job_id, status="running", stage="resolve_source")
        source, source_asset_id, source_name, public_source = _resolve_source(
            db=db,
            user_id=user_id,
            request=None,
            job_dir=job_dir,
            asset_id=asset_id,
            video_url=video_url,
        )
        ffmpeg = find_ffmpeg()
        source_info = _probe_video(ffmpeg, source)
        style = _apply_position_overrides(
            _apply_orientation_style(
                _template_caption_style(template),
                width=int(source_info.get("width") or 0),
                height=int(source_info.get("height") or 0),
            ),
            position_overrides or {},
        )
        if not style:
            raise RuntimeError(f"template {template.get('id') or ''} has no caption strategy")

        current_stage = "extract_audio"
        _update_job(db, job_id, stage="extract_audio", response_updates={"source_name": source_name, "source_asset_id": source_asset_id})
        audio_path = job_dir / "audio.wav"
        _extract_audio(ffmpeg, source, audio_path)
        audio_asset_id, _audio_fname, _audio_size, audio_url, _audio_local = _save_binary_asset(
            db,
            user_id=user_id,
            data=audio_path.read_bytes(),
            ext=".wav",
            content_type="audio/wav",
            media_type="audio",
            prompt=f"cutcli template audio | {source_name}",
            model="local:ffmpeg-extract-audio",
            tags="cutcli_template,local_audio_extract",
            meta={"cutcli_job_id": job_id, "source_asset_id": source_asset_id, "source_name": source_name, "created_at": _now_ts()},
        )
        if not audio_url:
            audio_url = _upload_temp_to_server(
                data=audio_path.read_bytes(),
                filename=_audio_fname,
                content_type="audio/wav",
                auth_headers=auth_headers,
            )
            _set_asset_source_url(db, audio_asset_id, user_id, audio_url)
        if not audio_url.startswith(("http://", "https://")):
            raise RuntimeError("audio extracted but no public URL is available for STT")

        current_stage = "stt"
        _update_job(db, job_id, stage="stt", response_updates={"audio_asset_id": audio_asset_id, "audio_url": audio_url})
        stt_result = _call_server(
            "/api/cutcli/stt/transcribe",
            {
                "audio_url": audio_url,
                "caption_style": style,
                "video_duration_sec": float(source_info.get("duration") or 0.1),
                "video_width": int(source_info.get("width") or 1080),
                "return_captions": True,
            },
            auth_headers,
            timeout=1200.0,
        )
        stt_data = stt_result.get("stt_data") if isinstance(stt_result.get("stt_data"), dict) else stt_result
        captions = stt_result.get("captions") if isinstance(stt_result.get("captions"), list) else []
        if not captions:
            captions = _captions_from_stt(
                stt_data,
                video_duration_sec=float(source_info.get("duration") or 0.1),
                style=style,
                video_width=int(source_info.get("width") or 1080),
            )
        if not captions:
            raise RuntimeError("STT returned no usable captions")
        (job_dir / "captions.json").write_text(json.dumps(captions, ensure_ascii=False, indent=2), encoding="utf-8")
        current_stage = "render"
        _update_job(db, job_id, stage="render", response_updates={"caption_count": len(captions), "stt_model": _STT_MODEL})

        if render_mode == "cutcli_cloud":
            cutcli = _find_cutcli()
            draft_id, zip_path = _build_cutcli_draft(
                cutcli=cutcli,
                job_dir=job_dir,
                source=source,
                public_source=public_source,
                source_info=source_info,
                captions=captions,
                style=style,
                overlay_texts=overlay_texts or {},
            )
            zip_asset_id, _zip_fname, _zip_size, zip_url, _zip_local = _save_binary_asset(
                db,
                user_id=user_id,
                data=zip_path.read_bytes(),
                ext=".zip",
                content_type="application/zip",
                media_type="document",
                prompt=f"cutcli template draft | {source_name}",
                model=f"cutcli-draft:{template.get('id')}",
                tags="cutcli_template,draft_zip",
                meta={"cutcli_job_id": job_id, "draft_id": draft_id, "created_at": _now_ts()},
            )
            if not zip_url:
                zip_url = _upload_temp_to_server(
                    data=zip_path.read_bytes(),
                    filename=_zip_fname,
                    content_type="application/zip",
                    auth_headers=auth_headers,
                    timeout=300.0,
                )
                _set_asset_source_url(db, zip_asset_id, user_id, zip_url)
            if not zip_url.startswith(("http://", "https://")):
                raise RuntimeError("draft zip created but no public URL is available for cloud render")
            _update_job(
                db,
                job_id,
                stage="cloud_render",
                response_updates={"draft_id": draft_id, "draft_zip_asset_id": zip_asset_id, "draft_zip_url": zip_url},
            )
            cloud = _call_server(
                "/api/cutcli/cloud/render-draft",
                {"draft_id": draft_id, "draft_zip_url": zip_url, "mirror_to_tos": True},
                auth_headers,
                timeout=2400.0,
            )
            final_url = cloud.get("open_url") or cloud.get("preview_url") or _extract_video_url(cloud)
            if not final_url:
                raise RuntimeError("CutCLI cloud render did not return a video URL")
            final_asset_id = uuid.uuid4().hex[:12]
            _save_asset_record(
                db,
                user_id=user_id,
                asset_id=final_asset_id,
                filename=f"cutcli_cloud_{job_id}.mp4",
                media_type="video",
                file_size=int(cloud.get("file_size") or 0) or None,
                source_url=final_url,
                prompt=f"{template.get('name')} | {source_name}",
                model=f"cutcli-cloud:{template.get('id')}",
                tags="cutcli_template,auto_caption,cloud_render",
                meta={"cutcli_job_id": job_id, "draft_id": draft_id, "source_asset_id": source_asset_id, "created_at": _now_ts()},
            )
            open_url = final_url
            preview_url = final_url
            render_strategy = "cutcli_cloud"
        else:
            ass_path = job_dir / "captions.ass"
            out_path = job_dir / "final.mp4"
            _write_ass(
                ass_path,
                captions,
                style,
                int(source_info.get("width") or 1080),
                int(source_info.get("height") or 1920),
                overlay_texts=overlay_texts or {},
                duration_sec=float(source_info.get("duration") or 0.1),
            )
            _render_ffmpeg(ffmpeg=ffmpeg, source=source, out_path=out_path, ass_path=ass_path)
            final_asset_id, _fname, fsize, source_url, _local = _save_binary_asset(
                db,
                user_id=user_id,
                data=out_path.read_bytes(),
                ext=".mp4",
                content_type="video/mp4",
                media_type="video",
                prompt=f"{template.get('name')} | {source_name}",
                model=f"local-ffmpeg:{template.get('id')}",
                tags="cutcli_template,auto_caption,local_ffmpeg",
                meta={"cutcli_job_id": job_id, "source_asset_id": source_asset_id, "caption_count": len(captions), "created_at": _now_ts()},
            )
            preview_url = source_url
            open_url = source_url
            if not open_url:
                open_url = _signed_asset_url(final_asset_id)
                preview_url = open_url
            render_strategy = "local_ffmpeg"
            _update_job(db, job_id, response_updates={"file_size": fsize})

        _update_job(
            db,
            job_id,
            status="completed",
            stage="completed",
            success=True,
            response_updates={
                "preview_url": preview_url,
                "open_url": open_url,
                "preview_asset_id": final_asset_id,
                "final_asset_id": final_asset_id,
                "caption_count": len(captions),
                "render_strategy": render_strategy,
                "source_asset_id": source_asset_id,
                "source_name": source_name,
                "overlay_texts": overlay_texts or {},
                "position_overrides": position_overrides or {},
                "quality": {"caption_count": len(captions), "template_id": template.get("id"), "stt_model": _STT_MODEL},
            },
        )
    except Exception as exc:
        err_text = _safe_error(exc, limit=2000)
        logger.exception("[cutcli-local] job failed job_id=%s stage=%s error=%s", job_id, current_stage, err_text)
        _update_job(
            db,
            job_id,
            status="failed",
            stage="failed",
            success=False,
            error=err_text,
            response_updates={"error_code": "cutcli_local_failed", "failed_stage": current_stage},
        )
    finally:
        db.close()
        shutil.rmtree(job_dir, ignore_errors=True)


def _start_background_job(
    *,
    job_id: str,
    user_id: int,
    template: Dict[str, Any],
    render_mode: str,
    asset_id: str,
    video_url: str,
    overlay_texts: Optional[Dict[str, Any]],
    position_overrides: Optional[Dict[str, Any]],
    auth_headers: Dict[str, str],
) -> None:
    thread = threading.Thread(
        target=_run_job,
        kwargs={
            "job_id": job_id,
            "user_id": user_id,
            "template": template,
            "render_mode": render_mode,
            "asset_id": asset_id,
            "video_url": video_url,
            "overlay_texts": overlay_texts or {},
            "position_overrides": position_overrides or {},
            "auth_headers": auth_headers,
        },
        daemon=True,
    )
    thread.start()


def _start_template_job_from_values(
    *,
    request: Request,
    db: Session,
    user_id: int,
    template_id: str,
    render_mode: str,
    asset_id: str = "",
    video_url: str = "",
    overlay_texts: Optional[Dict[str, Any]] = None,
    position_overrides: Optional[Any] = None,
    source_name: str = "",
    external_task_id: str = "",
) -> Dict[str, Any]:
    try:
        template = _resolve_server_template(template_id, _auth_header(request))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    mode = (render_mode or "ffmpeg").strip().lower()
    render_modes = template.get("render_modes") if isinstance(template.get("render_modes"), list) else ["ffmpeg", "cutcli_cloud"]
    if mode not in {"ffmpeg", "cutcli_cloud"} or mode not in render_modes:
        raise HTTPException(status_code=400, detail="render_mode must be ffmpeg or cutcli_cloud")
    aid = (asset_id or "").strip()
    url = (video_url or "").strip()
    if not aid and not url:
        raise HTTPException(status_code=400, detail="asset_id or video_url is required")
    clean_overlay_texts = _clean_overlay_texts(overlay_texts or {}, _overlay_fields_from_template(template))
    clean_position_overrides = _parse_position_overrides(position_overrides)
    job_id = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]
    row = _create_job(
        db,
        user_id=user_id,
        job_id=job_id,
        template=template,
        render_mode=mode,
        asset_id=aid,
        video_url=url,
        source_name=source_name or aid or url,
        overlay_texts=clean_overlay_texts,
        position_overrides=clean_position_overrides,
        external_task_id=(external_task_id or "").strip(),
    )
    _start_background_job(
        job_id=job_id,
        user_id=user_id,
        template=template,
        render_mode=mode,
        asset_id=aid,
        video_url=url,
        overlay_texts=clean_overlay_texts,
        position_overrides=clean_position_overrides,
        auth_headers=_auth_header(request),
    )
    return _job_to_public(row)


async def _save_upload_as_asset(
    *,
    file: UploadFile,
    db: Session,
    user_id: int,
) -> Tuple[str, str]:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="file is empty")
    ext = _safe_ext(file.filename or "source.mp4")
    aid, fname, fsize, source_url, _local = _save_binary_asset(
        db,
        user_id=user_id,
        data=data,
        ext=ext,
        content_type="video/mp4",
        media_type="video",
        prompt=f"cutcli template upload | {file.filename or 'source.mp4'}",
        model="local:upload",
        tags="cutcli_template,source_video",
        meta={"source_filename": file.filename or "source.mp4", "created_at": _now_ts()},
    )
    return aid, source_url


def _extract_preview_frame_bytes(ffmpeg: str, source_path: Path) -> bytes:
    out_path = _JOBS_DIR / f"preview_{uuid.uuid4().hex}.jpg"
    try:
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-y",
            "-ss",
            "0.08",
            "-i",
            str(source_path),
            "-frames:v",
            "1",
            "-vf",
            "scale='min(720,iw)':-2",
            "-q:v",
            "3",
            "-update",
            "1",
            str(out_path),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
        if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size <= 0:
            raise RuntimeError((proc.stderr or proc.stdout or "ffmpeg preview failed")[-1200:])
        return out_path.read_bytes()
    finally:
        try:
            out_path.unlink(missing_ok=True)
        except Exception:
            pass


@router.post("/api/cutcli/local/templates/preview-frame", summary="Extract local upload preview frame")
async def extract_local_template_preview_frame(
    file: UploadFile = File(...),
    _: _ServerUser = Depends(get_current_user_for_local),
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="file is empty")
    ext = _safe_ext(file.filename or "source.mp4")
    temp_path = _JOBS_DIR / f"preview_source_{uuid.uuid4().hex}{ext}"
    try:
        temp_path.write_bytes(data)
        frame = _extract_preview_frame_bytes(find_ffmpeg(), temp_path)
        return Response(content=frame, media_type="image/jpeg")
    except Exception as exc:
        logger.warning("[cutcli-template] preview frame extract failed filename=%s error=%s", file.filename, exc)
        raise HTTPException(status_code=400, detail=f"preview frame failed: {_safe_error(exc)}") from exc
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


@router.get("/api/cutcli/local/templates", summary="Local CutCLI template list")
def list_local_cutcli_templates(
    request: Request,
    _: _ServerUser = Depends(get_current_user_for_local),
):
    return _get_server("/api/cutcli/templates", _auth_header(request), timeout=120.0)


@router.post("/api/cutcli/local/templates/{template_id}/render", summary="Start local template render")
async def start_local_template_render(
    template_id: str,
    request: Request,
    file: Optional[UploadFile] = File(None),
    asset_id: str = Form(""),
    video_url: str = Form(""),
    render_mode: str = Form("ffmpeg"),
    position_overrides: str = Form(""),
    source_orientation: str = Form(""),
    top_text: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
    subtitle: Optional[str] = Form(None),
    headline: Optional[str] = Form(None),
    subheadline: Optional[str] = Form(None),
    badge: Optional[str] = Form(None),
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    try:
        template = _resolve_server_template(template_id, _auth_header(request))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    mode = (render_mode or "ffmpeg").strip().lower()
    render_modes = template.get("render_modes") if isinstance(template.get("render_modes"), list) else ["ffmpeg", "cutcli_cloud"]
    if mode not in {"ffmpeg", "cutcli_cloud"} or mode not in render_modes:
        raise HTTPException(status_code=400, detail="render_mode must be ffmpeg or cutcli_cloud")
    aid = (asset_id or "").strip()
    source_url = (video_url or "").strip()
    source_name = source_url or aid
    if file is not None:
        aid, _uploaded_url = await _save_upload_as_asset(file=file, db=db, user_id=current_user.id)
        source_url = ""
        source_name = file.filename or aid
    if not aid and not source_url:
        raise HTTPException(status_code=400, detail="file, asset_id, or video_url is required")
    raw_overlays = {}
    if top_text is not None:
        raw_overlays["top_text"] = top_text
    if title is not None:
        raw_overlays["title"] = title
    if subtitle is not None:
        raw_overlays["subtitle"] = subtitle
    if headline is not None:
        raw_overlays["headline"] = headline
    if subheadline is not None:
        raw_overlays["subheadline"] = subheadline
    if badge is not None:
        raw_overlays["badge"] = badge
    overlay_texts = _clean_overlay_texts(raw_overlays, _overlay_fields_from_template(template))
    clean_position_overrides = _parse_position_overrides(position_overrides)
    job_id = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]
    row = _create_job(
        db,
        user_id=current_user.id,
        job_id=job_id,
        template=template,
        render_mode=mode,
        asset_id=aid,
        video_url=source_url,
        source_name=source_name,
        overlay_texts=overlay_texts,
        position_overrides=clean_position_overrides,
    )
    _start_background_job(
        job_id=job_id,
        user_id=current_user.id,
        template=template,
        render_mode=mode,
        asset_id=aid,
        video_url=source_url,
        overlay_texts=overlay_texts,
        position_overrides=clean_position_overrides,
        auth_headers=_auth_header(request),
    )
    return _job_to_public(row)


@router.post("/api/cutcli/local/tasks/start", summary="Start local template render from cloud/H5 task")
async def start_local_template_task(
    body: LocalTemplateTaskBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    return _start_template_job_from_values(
        request=request,
        db=db,
        user_id=current_user.id,
        template_id=body.template_id,
        render_mode=body.render_mode,
        asset_id=body.asset_id,
        video_url=body.video_url,
        overlay_texts=body.overlay_texts,
        position_overrides=body.position_overrides,
        external_task_id=body.external_task_id,
    )


@router.post("/api/cutcli/local/capability", summary="Invoke local template customization capability")
async def invoke_local_template_capability(
    body: LocalTemplateCapabilityBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    action = (body.action or "start").strip().lower()
    if action in {"start", "run", "render"}:
        return _start_template_job_from_values(
            request=request,
            db=db,
            user_id=current_user.id,
            template_id=body.template_id,
            render_mode=body.render_mode,
            asset_id=body.asset_id,
            video_url=body.video_url,
            overlay_texts=body.overlay_texts,
            position_overrides=body.position_overrides,
            external_task_id=body.external_task_id,
        )
    if action in {"poll", "status", "get"}:
        jid = (body.job_id or "").strip()
        if not jid:
            raise HTTPException(status_code=400, detail="job_id is required")
        return _job_to_public(_get_job(db, current_user.id, jid))
    if action in {"list", "history"}:
        max_items = max(1, min(int(body.limit or 20), 100))
        rows = (
            db.query(CapabilityCallLog)
            .filter(CapabilityCallLog.user_id == current_user.id, CapabilityCallLog.capability_id == _FEATURE)
            .order_by(CapabilityCallLog.created_at.desc())
            .limit(max_items)
            .all()
        )
        return {"ok": True, "jobs": [_job_to_public(row) for row in rows]}
    if action in {"templates", "list_templates"}:
        return _get_server("/api/cutcli/templates", _auth_header(request), timeout=120.0)
    raise HTTPException(status_code=400, detail="action must be start, poll, list, or templates")


@router.get("/api/cutcli/local/templates/jobs", summary="Local CutCLI template jobs")
def list_local_template_jobs(
    limit: int = 50,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    max_items = max(1, min(int(limit or 50), 100))
    rows = (
        db.query(CapabilityCallLog)
        .filter(CapabilityCallLog.user_id == current_user.id, CapabilityCallLog.capability_id == _FEATURE)
        .order_by(CapabilityCallLog.created_at.desc())
        .limit(max_items)
        .all()
    )
    return {"ok": True, "jobs": [_job_to_public(row) for row in rows]}


@router.get("/api/cutcli/local/templates/jobs/{job_id}", summary="Local CutCLI template job detail")
def get_local_template_job(
    job_id: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    return _job_to_public(_get_job(db, current_user.id, job_id))
