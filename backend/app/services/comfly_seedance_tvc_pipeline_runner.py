"""Run the Seedance TVC pipeline script from skills/comfly_seedance_tvc_video."""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from ..models import Asset
from ..api.assets import (
    _is_internal_asset_http_url,
    _source_url_is_fetchable_for_upstream,
    _transfer_url_via_sutui,
    get_asset_public_url,
)
from .comfly_veo_exec import _comfly_upload_failure_detail

logger = logging.getLogger(__name__)

_pipeline_module = None
_MODULE_NAME = "lobster_comfly_seedance_tvc_pipeline"


def _lobster_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _pipeline_script_path() -> Path:
    return _lobster_root() / "skills" / "comfly_seedance_tvc_video" / "scripts" / "comfly_seedance_storyboard_pipeline.py"


def _bundled_ffmpeg_exe() -> Optional[str]:
    root = _lobster_root()
    candidates = [
        root / "skills" / "comfly_veo3_daihuo_video" / "tools" / "ffmpeg" / "windows" / "ffmpeg.exe",
        root / "deps" / "ffmpeg" / "ffmpeg.exe",
    ]
    if sys.platform == "win32":
        for path in candidates:
            if path.is_file():
                return str(path)
    return None


def _api_base_for_pipeline(api_base: str) -> str:
    b = (api_base or "").strip().rstrip("/")
    if b.lower().endswith("/v1"):
        return b[:-3].rstrip("/")
    return b


def _load_pipeline_module():
    global _pipeline_module
    if _pipeline_module is not None:
        return _pipeline_module
    script = _pipeline_script_path()
    if not script.is_file():
        raise HTTPException(status_code=503, detail=f"未找到 Seedance TVC 流水线脚本: {script}")
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, script)
    if spec is None or spec.loader is None:
        raise HTTPException(status_code=503, detail="无法加载 Seedance TVC pipeline 模块")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    _pipeline_module = mod
    return mod


def resolve_reference_image_for_pipeline(
    *,
    user_id: int,
    db: Session,
    request: Request,
    asset_id: Optional[str],
    image_url: Optional[str],
) -> str:
    u = (image_url or "").strip()
    if u:
        if not u.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="image_url 必须为 http(s) 公网直链")
        if _is_internal_asset_http_url(u):
            raise HTTPException(status_code=400, detail="image_url 不是上游可访问的公网直链，请重新上传素材或先转存")
        return u
    aid = (asset_id or "").strip()
    if not aid:
        return ""
    url = get_asset_public_url(aid, user_id, request, db)
    if not url:
        raise HTTPException(status_code=400, detail=_comfly_upload_failure_detail(aid, user_id, db))
    return url


async def _resolve_direct_reference_image_url_for_pipeline(
    *,
    url: str,
    label: str,
    request: Request,
    user: Any = None,
) -> str:
    clean = (url or "").strip()
    if not clean:
        return ""
    if not clean.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail=f"{label} 必须为 http(s) 公网直链")

    needs_transfer = _is_internal_asset_http_url(clean)
    if not needs_transfer:
        try:
            needs_transfer = not _source_url_is_fetchable_for_upstream(clean)
        except Exception as exc:
            logger.warning("[seedance-tvc] probe reference image failed label=%s url=%s err=%s", label, clean[:120], exc)
            needs_transfer = True

    if not needs_transfer:
        return clean

    transferred = await _transfer_url_via_sutui(clean, "image", user=user, request=request)
    if transferred and transferred.startswith(("http://", "https://")) and not _is_internal_asset_http_url(transferred):
        try:
            if _source_url_is_fetchable_for_upstream(transferred):
                logger.info("[seedance-tvc] reference image transferred label=%s from=%s to=%s", label, clean[:100], transferred[:100])
                return transferred
        except Exception as exc:
            logger.warning("[seedance-tvc] transferred reference image probe failed label=%s url=%s err=%s", label, transferred[:120], exc)

    raise HTTPException(
        status_code=400,
        detail=(
            f"{label} 不是上游可访问的公网图片链接，已尝试转存但失败。"
            "请重新上传到素材库，或配置 TOS 后再提交。"
        ),
    )


async def resolve_reference_images_for_pipeline_async(
    *,
    user_id: int,
    db: Session,
    request: Request,
    asset_id: Optional[str],
    image_url: Optional[str],
    reference_asset_ids: Optional[List[str]] = None,
    reference_image_urls: Optional[List[str]] = None,
    user: Any = None,
) -> List[str]:
    resolved: List[str] = []

    primary_direct = await _resolve_direct_reference_image_url_for_pipeline(
        url=image_url or "",
        label="image_url",
        request=request,
        user=user,
    )
    if primary_direct:
        resolved.append(primary_direct)
    elif (asset_id or "").strip():
        primary_asset = get_asset_public_url((asset_id or "").strip(), user_id, request, db)
        if not primary_asset:
            raise HTTPException(status_code=400, detail=_comfly_upload_failure_detail(asset_id or "", user_id, db))
        primary_asset = await _resolve_direct_reference_image_url_for_pipeline(
            url=primary_asset,
            label=f"asset_id「{asset_id}」",
            request=request,
            user=user,
        )
        if primary_asset:
            resolved.append(primary_asset)

    for index, url in enumerate(reference_image_urls or [], start=1):
        u = await _resolve_direct_reference_image_url_for_pipeline(
            url=url or "",
            label=f"reference_image_urls[{index}]",
            request=request,
            user=user,
        )
        if u and u not in resolved:
            resolved.append(u)

    for aid in reference_asset_ids or []:
        a = (aid or "").strip()
        if not a:
            continue
        u = get_asset_public_url(a, user_id, request, db)
        if not u:
            raise HTTPException(status_code=400, detail=_comfly_upload_failure_detail(a, user_id, db))
        u = await _resolve_direct_reference_image_url_for_pipeline(
            url=u,
            label=f"reference_asset_ids「{a}」",
            request=request,
            user=user,
        )
        if u and u not in resolved:
            resolved.append(u)

    return resolved


def resolve_reference_images_for_pipeline(
    *,
    user_id: int,
    db: Session,
    request: Request,
    asset_id: Optional[str],
    image_url: Optional[str],
    reference_asset_ids: Optional[List[str]] = None,
    reference_image_urls: Optional[List[str]] = None,
) -> List[str]:
    resolved: List[str] = []

    primary = resolve_reference_image_for_pipeline(
        user_id=user_id,
        db=db,
        request=request,
        asset_id=asset_id,
        image_url=image_url,
    )
    if primary:
        resolved.append(primary)

    for url in reference_image_urls or []:
        u = (url or "").strip()
        if not u:
            continue
        if not u.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="reference_image_urls 必须为 http(s) 公网直链")
        if u not in resolved:
            resolved.append(u)

    for aid in reference_asset_ids or []:
        a = (aid or "").strip()
        if not a:
            continue
        u = get_asset_public_url(a, user_id, request, db)
        if not u:
            raise HTTPException(status_code=400, detail=_comfly_upload_failure_detail(a, user_id, db))
        if u not in resolved:
            resolved.append(u)

    return resolved


def build_pipeline_input(
    *,
    reference_image: str,
    reference_images: Optional[List[str]],
    reference_purposes: Optional[List[str]],
    api_key: str,
    api_base: str,
    merge_clips: bool,
    storyboard_count: Optional[int],
    segment_count: Optional[int] = None,
    segment_duration_seconds: Optional[int] = None,
    total_duration_seconds: Optional[int] = None,
    output_dir: Optional[str],
    platform: str,
    country: str,
    language: str,
    task_text: str = "",
    analysis_model: Optional[str] = None,
    image_model: Optional[str] = None,
    image_model_fallback: Optional[str] = None,
    video_model: Optional[str] = None,
    video_channel: Optional[str] = None,
    video_base_url: Optional[str] = None,
    video_fallbacks: Optional[List[Dict[str, Any]]] = None,
    workflow_mode: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
    visual_tone: Optional[str] = None,
    rhythm: Optional[str] = None,
    generate_audio: Optional[bool] = None,
    watermark: Optional[bool] = None,
) -> Dict[str, Any]:
    base = _api_base_for_pipeline(api_base)
    effective_total_duration = total_duration_seconds
    requested_count = segment_count if segment_count is not None else storyboard_count
    if effective_total_duration is None and requested_count is not None:
        model_hint = str(video_model or "").strip().lower().replace(" ", "")
        uses_veo = str(video_channel or "").strip().lower() in {"yunwu", "云雾", "雲霧"} or model_hint in {
            "yunwu-veo3.1-plus",
            "veo3.1-plus",
            "veo3.1",
        }
        effective_total_duration = int(requested_count) * int(segment_duration_seconds or (8 if uses_veo else 10))
    inp: Dict[str, Any] = {
        "apikey": api_key,
        "base_url": base,
        "merge_clips": bool(merge_clips),
    }
    primary_ref = str(reference_image or "").strip()
    if primary_ref:
        inp["reference_image"] = primary_ref
    refs = [str(x).strip() for x in (reference_images or []) if str(x).strip()]
    if refs:
        inp["reference_images"] = refs
    purposes = [str(x or "").strip().lower() for x in (reference_purposes or []) if str(x or "").strip()]
    if purposes:
        inp["reference_purposes"] = purposes
    if storyboard_count is not None:
        inp["storyboard_count"] = int(storyboard_count)
    if segment_count is not None:
        inp["segment_count"] = int(segment_count)
    if segment_duration_seconds is not None:
        inp["segment_duration_seconds"] = int(segment_duration_seconds)
    if effective_total_duration is not None:
        inp["total_duration_seconds"] = int(effective_total_duration)
    if (workflow_mode or "").strip():
        inp["workflow_mode"] = workflow_mode.strip()
    if output_dir:
        inp["output_dir"] = output_dir
    if (platform or "").strip():
        inp["platform"] = platform.strip()
    if (country or "").strip():
        inp["country"] = country.strip()
    if (language or "").strip():
        inp["language"] = language.strip()
    if (task_text or "").strip():
        inp["task_text"] = task_text.strip()
    if (analysis_model or "").strip():
        inp["analysis_model"] = analysis_model.strip()
    if (image_model or "").strip():
        inp["image_model"] = image_model.strip()
    if (image_model_fallback or "").strip():
        inp["image_model_fallback"] = image_model_fallback.strip()
    if (video_model or "").strip():
        inp["video_model"] = video_model.strip()
    if (video_channel or "").strip():
        inp["video_channel"] = video_channel.strip()
    if (video_base_url or "").strip():
        inp["video_base_url"] = _api_base_for_pipeline(video_base_url)
    if video_fallbacks:
        inp["video_fallbacks"] = list(video_fallbacks)
    if (aspect_ratio or "").strip():
        inp["aspect_ratio"] = aspect_ratio.strip()
    if (visual_tone or "").strip():
        inp["visual_tone"] = visual_tone.strip()
    if (rhythm or "").strip():
        inp["rhythm"] = rhythm.strip()
    if generate_audio is not None:
        inp["generate_audio"] = bool(generate_audio)
    if watermark is not None:
        inp["watermark"] = bool(watermark)
    ff = _bundled_ffmpeg_exe()
    if ff:
        inp["ffmpeg_path"] = ff
    return inp


def run_storyboard_pipeline_sync(inp: Dict[str, Any]) -> Dict[str, Any]:
    mod = _load_pipeline_module()
    return mod.run_pipeline(inp)


def collect_video_urls_from_pipeline_result(data: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    fv = data.get("final_video")
    if isinstance(fv, dict):
        u = (fv.get("url") or "").strip()
        if u.startswith("http"):
            return [(u, "final", "Seedance 成片")]
    out: List[Tuple[str, str, str]] = []
    for shot in data.get("completed_shots") or []:
        if not isinstance(shot, dict):
            continue
        url = (shot.get("mp4url") or "").strip()
        if not url.startswith("http"):
            continue
        tid = (shot.get("video_task_id") or "").strip()
        title = (shot.get("title_cn") or shot.get("scene_cn") or "").strip() or f"shot_{shot.get('index', '')}"
        out.append((url, tid, title[:200]))
    return out
