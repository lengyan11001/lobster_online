from __future__ import annotations

import asyncio
import logging
import re
import shlex
import subprocess
import unicodedata
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..core.config import get_settings
from ..services.creative_job_cloud_sync import sync_creative_job_to_cloud
from ..services.comfly_seedance_tvc_job_store import (
    create_job_record,
    delete_job,
    get_job,
    list_jobs_for_user,
    read_manifest_artifacts,
    read_manifest_progress,
    update_job,
)
from ..services.comfly_seedance_tvc_pipeline_runner import (
    _api_base_for_pipeline,
    build_pipeline_input,
    collect_video_urls_from_pipeline_result,
    resolve_reference_images_for_pipeline_async,
    run_storyboard_pipeline_sync,
)
from ..services.comfly_veo_exec import _resolve_comfly_credentials
from .assets import (
    ASSETS_DIR,
    SaveAssetReq,
    _content_type_for_asset_filename,
    _compute_save_url_dedupe_key,
    _final_save_url_dedupe_key,
    _gen_asset_id,
    _normalize_auth_server_base,
    _resolve_v3_tasks_url_for_download,
    _save_asset_from_url_locked,
    _upload_to_tos,
    _save_url_lock_for,
)
from .comfly_ecommerce_detail import _register_local_file_url
from .auth import _ServerUser, get_current_user_media_edit
from ..models import Asset

router = APIRouter()
logger = logging.getLogger(__name__)
_LOCAL_BESTSELLER_CAPTION_LOCK = asyncio.Lock()


class ComflySeedancePipelinePayload(BaseModel):
    asset_id: Optional[str] = Field(None, description="主参考图素材 ID，与 image_url 二选一")
    image_url: Optional[str] = Field(None, description="主参考图公网 URL，与 asset_id 二选一")
    reference_asset_ids: List[str] = Field(default_factory=list, description="额外参考图素材 ID 列表")
    reference_image_urls: List[str] = Field(default_factory=list, description="额外参考图公网 URL 列表")
    reference_purposes: List[str] = Field(
        default_factory=list,
        description="与主参考图、额外 URL、额外素材 ID 顺序一致的逐图用途",
    )
    merge_clips: bool = Field(True, description="是否将多个分镜片段合成为一个成片")
    storyboard_count: Optional[int] = Field(None, ge=1, le=6, description="兼容旧字段；若传入，将按当前模型单段时长推导总时长")
    segment_count: Optional[int] = Field(None, ge=1, le=6, description="兼容旧字段；若传入，必须与 total_duration_seconds / 单段时长一致")
    segment_duration_seconds: Optional[int] = Field(None, description="每段时长：Seedance 为 10 秒，云雾 Veo 为 8 秒")
    total_duration_seconds: Optional[int] = Field(None, description="总时长按模型支持的单段时长计算，最多 6 段")
    workflow_mode: str = Field("storyboard", description="storyboard=完整分镜流程；direct_video=上传图+提示词直接图生视频")
    auto_save: bool = Field(True, description="完成后自动入库")
    task_text: str = Field("", description="补充任务说明")
    platform: str = ""
    country: str = ""
    language: str = ""
    output_dir: Optional[str] = None
    isolate_job_dir: bool = True
    analysis_model: Optional[str] = None
    analysis_model_fallback: Optional[str] = None
    image_model: Optional[str] = None
    image_model_fallback: Optional[str] = None
    video_model: Optional[str] = None
    video_channel: Optional[str] = None
    video_base_url: Optional[str] = None
    video_fallbacks: List[Dict[str, Any]] = Field(default_factory=list, description="Ordered video fallback providers, each item supports channel/base_url/model.")
    aspect_ratio: str = "9:16"
    resolution: str = "720P"
    visual_tone: str = "clean_bright"
    rhythm: str = "smooth"
    generate_audio: bool = True
    watermark: bool = False


class ComflySeedanceRunBody(BaseModel):
    payload: ComflySeedancePipelinePayload


def _default_runs_root() -> str:
    return str(Path(__file__).resolve().parents[3] / "skills" / "comfly_seedance_tvc_video" / "runs")


async def _fetch_video_provider_policy(
    *,
    request: Request,
    model: str,
    channel: str,
) -> Dict[str, Any]:
    server_base = (get_settings().auth_server_base or "").strip().rstrip("/")
    auth = _request_auth_header(request)
    if not server_base or not auth:
        return {}
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(
                f"{server_base}/api/comfly-proxy/video/provider-policy",
                params={"model": model or "", "channel": channel or "", "feature": "seedance_tvc"},
                headers={"Authorization": auth},
            )
        if resp.status_code >= 400:
            logger.warning("[seedance-tvc] video provider policy fetch failed status=%s body=%s", resp.status_code, resp.text[:300])
            return {}
        data = resp.json() if resp.content else {}
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("[seedance-tvc] video provider policy fetch error: %s", exc)
        return {}


def _policy_video_fallbacks(policy: Dict[str, Any]) -> List[Dict[str, Any]]:
    providers = policy.get("providers") if isinstance(policy, dict) else None
    if not isinstance(providers, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in providers[1:]:
        if not isinstance(item, dict):
            continue
        channel = str(item.get("channel") or "").strip()
        model = str(item.get("model") or "").strip()
        if not channel or not model:
            continue
        provider: Dict[str, Any] = {"channel": channel, "model": model}
        if str(item.get("base_url") or "").strip():
            provider["base_url"] = str(item.get("base_url") or "").strip()
        out.append(provider)
    return out


def _absolute_policy_base(base_url: str, server_base: str, default_base: str) -> str:
    raw = str(base_url or "").strip()
    if raw.startswith(("http://", "https://")):
        return raw
    if raw.startswith("/"):
        return f"{server_base.rstrip('/')}{raw}"
    return raw or default_base


def _is_veo31_request(channel: str, model: str) -> bool:
    channel_hint = (channel or "").strip().lower()
    model_hint = (model or "").strip().lower().replace("_", "-").replace(" ", "")
    return channel_hint in {"yunwu", "云雾", "雲霧"} or model_hint in {
        "yunwu-veo3.1-plus",
        "veo3.1-plus",
        "veo3.1",
        "veo31",
        "veo31-fast",
        "veo3.1-fast",
    }


def _is_wan30_request(channel: str, model: str) -> bool:
    channel_hint = (channel or "").strip().lower().replace("_", "-")
    model_hint = (model or "").strip().lower().replace("_", "-").replace(" ", "")
    return channel_hint in {"dashscope", "dashscope-wan30", "dashscope_wan30", "wan30", "wan3", "wan3.0", "qianwen", "千问", "万相"} or model_hint in {
        "wan3.0",
        "wan30",
        "wan3.0-video",
        "wan-3.0",
        "万相3.0",
        "万相-3.0",
    }


def _is_grok_video_request(channel: str, model: str) -> bool:
    model_hint = (model or "").strip().lower().replace("_", "-").replace(" ", "")
    return model_hint in {
        "grok-1.5-video-6s",
        "grok-1.5-video-10s",
        "grok-1.5-video-15s",
        "grok-imagine-video-1.5-preview",
        "grok-imagine-1.0-video",
        "grok-video-3",
        "yingmeng1.5plus",
        "影梦1.5plus",
    } or model_hint.startswith("xai/grok-imagine-video/")


def _validate_payload(pl: ComflySeedancePipelinePayload) -> None:
    if bool(pl.asset_id and pl.image_url):
        raise HTTPException(status_code=400, detail="asset_id 与 image_url 请勿同时传")
    has_reference = bool(
        (pl.asset_id or "").strip()
        or (pl.image_url or "").strip()
        or any(str(x).strip() for x in (pl.reference_asset_ids or []))
        or any(str(x).strip() for x in (pl.reference_image_urls or []))
    )
    if not has_reference and not (pl.task_text or "").strip():
        raise HTTPException(status_code=400, detail="请提供参考图或创意提示词")
    reference_count = sum(
        [
            1 if (pl.asset_id or pl.image_url) else 0,
            len([x for x in pl.reference_image_urls if str(x).strip()]),
            len([x for x in pl.reference_asset_ids if str(x).strip()]),
        ]
    )
    allowed_purposes = {"storyboard", "person", "product", "style", "scene", "auto"}
    purposes = [str(x or "").strip().lower() for x in pl.reference_purposes]
    if purposes and len(purposes) != reference_count:
        raise HTTPException(status_code=400, detail="reference_purposes 必须与参考图片数量和顺序一一对应")
    invalid_purposes = sorted({x for x in purposes if x not in allowed_purposes})
    if invalid_purposes:
        raise HTTPException(status_code=400, detail=f"不支持的参考图用途: {', '.join(invalid_purposes)}")
    if pl.visual_tone not in {"clean_bright", "lifestyle_warm", "luxury_refined", "cinematic_contrast"}:
        raise HTTPException(status_code=400, detail="visual_tone 参数无效")
    if pl.rhythm not in {"smooth", "dynamic", "product_focus", "storytelling"}:
        raise HTTPException(status_code=400, detail="rhythm 参数无效")
    uses_wan30 = _is_wan30_request(pl.video_channel or "", pl.video_model or "")
    uses_yunwu_veo = _is_veo31_request(pl.video_channel or "", pl.video_model or "")
    if uses_wan30:
        if pl.segment_count is not None and int(pl.segment_count) != 1:
            raise HTTPException(status_code=400, detail="Wan3.0 单次请求只能提交 1 条视频")
        for field_name in ("segment_duration_seconds", "total_duration_seconds"):
            value = getattr(pl, field_name)
            if value is not None and not 5 <= int(value) <= 30:
                raise HTTPException(status_code=400, detail=f"Wan3.0 的 {field_name} 必须在 5～30 秒之间")
        if (
            pl.segment_duration_seconds is not None
            and pl.total_duration_seconds is not None
            and int(pl.segment_duration_seconds) != int(pl.total_duration_seconds)
        ):
            raise HTTPException(status_code=400, detail="Wan3.0 的 segment_duration_seconds 必须等于 total_duration_seconds")
        return

    segment_seconds = 8 if uses_yunwu_veo else 10
    if pl.segment_duration_seconds is not None and int(pl.segment_duration_seconds) != segment_seconds:
        raise HTTPException(status_code=400, detail=f"segment_duration_seconds 当前模型固定为 {segment_seconds} 秒")

    requested_count = pl.segment_count if pl.segment_count is not None else pl.storyboard_count
    requested_total = pl.total_duration_seconds
    if requested_total is None and requested_count is not None:
        requested_total = int(requested_count) * segment_seconds

    allowed_totals = sorted({segment_seconds * i for i in range(1, 7)})
    if requested_total is not None and int(requested_total) not in allowed_totals:
        wanted_total = int(requested_total)
        if wanted_total > allowed_totals[-1]:
            allowed_text = "/".join(str(x) for x in allowed_totals)
            raise HTTPException(
                status_code=400,
                detail=f"total_duration_seconds 最多支持 {allowed_totals[-1]} 秒（可选 {allowed_text} 秒）",
            )
        # 不是单段时长的整数倍：向上取整到最近的合法总时长（例：16 秒 → 20 秒 / 2 段）
        requested_total = next(x for x in allowed_totals if x >= wanted_total)
        pl.total_duration_seconds = requested_total
    if requested_count is not None and int(requested_count) * segment_seconds < int(requested_total or segment_seconds * 2):
        raise HTTPException(
            status_code=400,
            detail=f"segment_count/storyboard_count 不足以覆盖 total_duration_seconds（每段 {segment_seconds} 秒）",
        )


async def _prepare_pipeline_input(
    *,
    pl: ComflySeedancePipelinePayload,
    current_user: _ServerUser,
    db: Session,
    request: Request,
    effective_output_dir: str,
) -> Dict[str, Any]:
    reference_images = await resolve_reference_images_for_pipeline_async(
        user_id=current_user.id,
        db=db,
        request=request,
        asset_id=pl.asset_id,
        image_url=pl.image_url,
        reference_asset_ids=pl.reference_asset_ids,
        reference_image_urls=pl.reference_image_urls,
        user=current_user,
    )
    if pl.reference_purposes and len(pl.reference_purposes) != len(reference_images):
        raise HTTPException(status_code=400, detail="参考图片存在重复或无效项，无法与逐图用途一一对应")
    api_base, api_key = _resolve_comfly_credentials(current_user.id, db, request)
    pipe_base = _api_base_for_pipeline(api_base)
    video_channel = (pl.video_channel or "").strip().lower()
    video_base_url = (pl.video_base_url or "").strip()
    video_model = (pl.video_model or "").strip()
    explicit_wan30_request = _is_wan30_request(video_channel, video_model)
    if explicit_wan30_request:
        # Wan3.0 is a direct single-request workflow. Do not let the
        # managed Seedance provider policy replace it with a fixed-segment
        # provider, otherwise valid 5-30 second durations are rejected.
        video_channel = "dashscope"
    if video_model.lower().replace(" ", "") in {"yunwu-veo3.1-plus", "veo3.1-plus", "veo3.1"}:
        video_channel = "yunwu"
        video_model = "veo3.1"
    if _is_grok_video_request(video_channel, video_model):
        video_channel = video_channel or "comfly"
    if video_channel in {"yunwu", "云雾", "雲霧"}:
        video_channel = "yunwu"
        video_base_url = video_base_url or pipe_base
        video_model = video_model or "veo3.1"
    policy = (
        {}
        if explicit_wan30_request
        else await _fetch_video_provider_policy(
            request=request,
            model=video_model or pl.video_model or "",
            channel=video_channel or pl.video_channel or "",
        )
    )
    policy_providers = policy.get("providers") if isinstance(policy.get("providers"), list) else []
    server_base_for_policy = (get_settings().auth_server_base or "").strip().rstrip("/")
    if policy_providers:
        primary = policy_providers[0] if isinstance(policy_providers[0], dict) else {}
        video_channel = str(primary.get("channel") or video_channel or "").strip()
        video_model = str(primary.get("model") or video_model or pl.video_model or "").strip()
        video_base_url = _absolute_policy_base(str(primary.get("base_url") or ""), server_base_for_policy, video_base_url or pipe_base)
        video_fallbacks = _policy_video_fallbacks(policy)
        for item in video_fallbacks:
            item["base_url"] = _absolute_policy_base(str(item.get("base_url") or ""), server_base_for_policy, pipe_base)
    else:
        video_fallbacks = pl.video_fallbacks
    requested_count = pl.segment_count if pl.segment_count is not None else pl.storyboard_count
    if requested_count is None and pl.total_duration_seconds is not None:
        uses_yunwu_veo = _is_veo31_request(video_channel or pl.video_channel or "", video_model or pl.video_model or "")
        requested_count = int(pl.total_duration_seconds) // (8 if uses_yunwu_veo else 10)
    workflow_mode = (pl.workflow_mode or "storyboard").strip().lower().replace("-", "_") or "storyboard"
    logger.info(
        "[seedance-tvc] prepared pipeline user_id=%s workflow_mode=%s references=%s segment_count=%s segment_seconds=%s video_channel=%s video_model=%s aspect_ratio=%s resolution=%s",
        current_user.id,
        workflow_mode,
        len(reference_images),
        requested_count or pl.segment_count or pl.storyboard_count or 1,
        pl.segment_duration_seconds,
        video_channel or "",
        video_model or pl.video_model or "",
        pl.aspect_ratio or "",
        pl.resolution or "",
    )
    return build_pipeline_input(
        reference_image=reference_images[0] if reference_images else "",
        reference_images=reference_images,
        reference_purposes=pl.reference_purposes,
        api_key=api_key,
        api_base=api_base,
        merge_clips=pl.merge_clips,
        storyboard_count=pl.storyboard_count,
        segment_count=pl.segment_count,
        segment_duration_seconds=pl.segment_duration_seconds,
        total_duration_seconds=pl.total_duration_seconds,
        workflow_mode=workflow_mode,
        output_dir=effective_output_dir,
        platform=pl.platform,
        country=pl.country,
        language=pl.language,
        task_text=pl.task_text,
        analysis_model=pl.analysis_model,
        analysis_model_fallback=pl.analysis_model_fallback,
        image_model=pl.image_model,
        image_model_fallback=pl.image_model_fallback,
        video_model=video_model or pl.video_model,
        video_channel=video_channel,
        video_base_url=video_base_url,
        video_fallbacks=video_fallbacks,
        aspect_ratio=pl.aspect_ratio,
        resolution=pl.resolution,
        visual_tone=pl.visual_tone,
        rhythm=pl.rhythm,
        generate_audio=pl.generate_audio,
        watermark=pl.watermark,
    )


def _request_auth_header(request: Optional[Request]) -> str:
    if request is None:
        return ""
    return (request.headers.get("Authorization") or "").strip()


def _request_installation_id(request: Optional[Request]) -> str:
    if request is None:
        return ""
    return (
        request.headers.get("X-Installation-Id")
        or request.headers.get("x-installation-id")
        or ""
    ).strip()


def _normalized_auth_header(auth_header: str) -> str:
    raw = (auth_header or "").strip()
    if not raw:
        return ""
    return raw if raw.lower().startswith("bearer ") else f"Bearer {raw}"


async def _save_seedance_video_to_server(
    body: SaveAssetReq,
    *,
    auth_header: str = "",
    installation_id: str = "",
) -> Optional[Dict[str, Any]]:
    server_base = (get_settings().auth_server_base or "").strip().rstrip("/")
    auth = _normalized_auth_header(auth_header)
    if not server_base or not auth:
        logger.warning(
            "[seedance-tvc] skip cloud asset save: auth_server_base=%s auth_header=%s",
            bool(server_base),
            bool(auth),
        )
        return None

    payload: Dict[str, Any] = {
        "url": body.url,
        "media_type": "video",
        "tags": body.tags,
        "prompt": body.prompt,
        "model": body.model,
    }
    if body.dedupe_hint_url:
        payload["dedupe_hint_url"] = body.dedupe_hint_url
    if body.generation_task_id:
        payload["generation_task_id"] = body.generation_task_id

    headers = {
        "Authorization": auth,
        "Content-Type": "application/json",
    }
    if installation_id:
        headers["X-Installation-Id"] = installation_id

    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True, trust_env=False) as client:
            resp = await client.post(f"{server_base}/api/assets/save-url", json=payload, headers=headers)
        if resp.status_code >= 400:
            logger.warning(
                "[seedance-tvc] cloud asset save failed status=%s body=%s url=%s",
                resp.status_code,
                (resp.text or "")[:500],
                body.url[:160],
            )
            return None
        data = resp.json()
        logger.info(
            "[seedance-tvc] cloud asset saved asset_id=%s source_url=%s",
            data.get("asset_id"),
            str(data.get("source_url") or "")[:160],
        )
        return data if isinstance(data, dict) else None
    except Exception as e:
        logger.warning("[seedance-tvc] cloud asset save error: %s", e, exc_info=True)
        return None


async def _upload_local_bytes_to_auth_server(
    *,
    data: bytes,
    filename: str,
    content_type: str,
    auth_header: str = "",
    installation_id: str = "",
    timeout: float = 180.0,
) -> tuple[Optional[str], Dict[str, Any]]:
    server_base = (get_settings().auth_server_base or "").strip().rstrip("/")
    auth = _normalized_auth_header(auth_header)
    if not server_base:
        return None, {"error": "AUTH_SERVER_BASE missing"}
    if not auth:
        return None, {"error": "Authorization Bearer missing"}
    upload_base = _normalize_auth_server_base(server_base)
    upload_url = f"{upload_base}/api/assets/upload-temp"
    headers = {"Authorization": auth}
    if installation_id:
        headers["X-Installation-Id"] = installation_id
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=True) as client:
            resp = await client.post(
                upload_url,
                files={"file": (filename or "upload.bin", data, content_type or "application/octet-stream")},
                headers=headers,
            )
        diag: Dict[str, Any] = {"status_code": resp.status_code}
        if resp.status_code >= 400:
            diag["error"] = f"server returned {resp.status_code}"
            diag["body_snip"] = (resp.text or "")[:400]
            return None, diag
        payload = resp.json() if resp.content else {}
        if not isinstance(payload, dict):
            return None, {"error": "response is not object", "status_code": resp.status_code}
        public_url = str(payload.get("public_url") or "").strip()
        if not public_url:
            return None, {"error": "missing public_url", "status_code": resp.status_code}
        diag["storage"] = str(payload.get("storage") or "")
        diag["temp_id"] = str(payload.get("temp_id") or "")
        return public_url, diag
    except Exception as exc:
        return None, {"error": f"{type(exc).__name__}: {exc}"}


async def _save_local_final_video_asset(
    *,
    local_path: str,
    current_user: _ServerUser,
    prompt: str,
    video_model: str,
    auth_header: str = "",
    installation_id: str = "",
    generation_task_id: str = "",
    tags: str = "auto,comfly.seedance.tvc.pipeline,merged",
    meta_extra: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    path_text = str(local_path or "").strip()
    if not path_text:
        return None
    path = Path(path_text)
    try:
        path = path.resolve()
    except Exception:
        pass
    if not path.is_file():
        logger.warning("[seedance-tvc] final merged video missing path=%s", path)
        return None

    data = path.read_bytes()
    suffix = (path.suffix or ".mp4").lower()
    content_type = _content_type_for_asset_filename(path.name) or "video/mp4"
    asset_id = _gen_asset_id()
    filename = f"{asset_id}{suffix}"
    local_asset_path = ASSETS_DIR / filename
    local_asset_path.write_bytes(data)

    source_url = _upload_to_tos(data, f"assets/{filename}", content_type)
    server_diag: Dict[str, Any] = {}
    if not source_url:
        source_url, server_diag = await _upload_local_bytes_to_auth_server(
            data=data,
            filename=filename,
            content_type=content_type,
            auth_header=auth_header,
            installation_id=installation_id,
        )
    if not source_url:
        logger.warning(
            "[seedance-tvc] final merged video public upload failed path=%s diag=%s",
            str(path),
            server_diag,
        )
        return None

    meta: Dict[str, Any] = {
        "seedance_final_video": True,
        "origin_local_path": str(path),
        # 最终交付件：素材库「生成素材」与内容库可见。
        "asset_origin": "generated",
        "content_visibility": "visible",
    }
    if meta_extra:
        meta.update(meta_extra)
    if generation_task_id:
        meta["generation_task_id"] = generation_task_id[:128]

    db = SessionLocal()
    try:
        asset = Asset(
            asset_id=asset_id,
            user_id=current_user.id,
            filename=filename,
            media_type="video",
            file_size=len(data),
            source_url=source_url,
            prompt=(prompt or "").strip()[:500] or None,
            model=(video_model or "").strip()[:128] or None,
            tags=tags,
            meta=meta,
        )
        db.add(asset)
        db.commit()
        # 最终成片已经入库：同一任务的字幕件/合成件降级为中间产物，不给用户展示。
        await asyncio.to_thread(
            _demote_process_assets_for_job,
            generation_task_id,
            user_id=int(getattr(current_user, "id", 0) or 0),
        )
        return {
            "asset_id": asset_id,
            "filename": filename,
            "media_type": "video",
            "file_size": len(data),
            "source_url": source_url,
            "path": str(local_asset_path),
        }
    finally:
        db.close()


# 生成链路里的“过程件”型号：加字幕中间件（合成/加 BGM 的那份是交付件，不能埋掉）。
# 按口径：素材库「生成素材」与内容库都只展示最终交付件，过程件一律不给用户看
# （仍可按 asset_id 取用，不影响发布链路）。2026-09-22 用户确认。
_PROCESS_ASSET_MODELS: tuple = (
    "local-bestseller-caption-ffmpeg",
)


def _demote_process_assets_for_job(job_id: str, *, user_id: int = 0) -> int:
    """最终成片入库后，把同一任务的过程件降级为中间产物（素材库/内容库都不展示）。"""
    clean = str(job_id or "").strip()
    if not clean:
        return 0
    db = SessionLocal()
    changed = 0
    try:
        query = db.query(Asset).filter(Asset.model.in_(_PROCESS_ASSET_MODELS))
        if int(user_id or 0) > 0:
            query = query.filter(Asset.user_id == int(user_id))
        rows = query.order_by(Asset.created_at.desc()).limit(400).all()
        for row in rows:
            meta = dict(row.meta or {}) if isinstance(row.meta, dict) else {}
            job_keys = {
                str(meta.get("seedance_job_id") or "").strip(),
                str(meta.get("generation_task_id") or "").strip(),
            }
            if clean not in job_keys:
                continue
            if meta.get("asset_origin") == "intermediate" and meta.get("content_visibility") == "hidden":
                continue
            meta["asset_origin"] = "intermediate"
            meta["content_visibility"] = "hidden"
            meta["demoted_by"] = "final_asset_saved"
            row.meta = meta
            changed += 1
        if changed:
            db.commit()
            logger.info(
                "[seedance-tvc] demoted %s process asset(s) to intermediate job_id=%s",
                changed,
                clean,
            )
        return changed
    except Exception as exc:
        logger.warning("[seedance-tvc] demote process assets failed job_id=%s err=%s", clean, str(exc)[:200])
        return 0
    finally:
        db.close()


async def _save_pipeline_videos(
    *,
    urls: List[tuple],
    request: Optional[Request],
    current_user: _ServerUser,
    video_model: str,
    auth_header: str = "",
    installation_id: str = "",
) -> List[Dict[str, Any]]:
    saved: List[Dict[str, Any]] = []
    for url, task_id, title_hint in urls:
        # 比例被裁/补过的分镜是本地文件（不是 URL）：直接按本地文件入库，保证素材库也是目标比例
        if not str(url or "").startswith(("http://", "https://")):
            local_row = await _save_local_final_video_asset(
                local_path=url,
                current_user=current_user,
                prompt=title_hint or "",
                video_model=video_model,
                auth_header=auth_header or _request_auth_header(request),
                installation_id=installation_id or _request_installation_id(request),
                generation_task_id=task_id or "",
                tags="auto,comfly.seedance.tvc.pipeline,shot",
                meta_extra={"seedance_shot_clip": True},
            )
            if local_row:
                saved.append({"source_url": url, "task_id": task_id, "asset": local_row})
            else:
                logger.warning("[seedance-tvc] local shot clip save skipped path=%s", url)
            continue
        body = SaveAssetReq(
            url=url,
            media_type="video",
            tags="auto,comfly.seedance.tvc.pipeline",
            prompt=title_hint[:500] if title_hint else None,
            model=(video_model or "")[:128] or None,
            generation_task_id=task_id[:128] if task_id else None,
        )
        effective = await _resolve_v3_tasks_url_for_download(body.url, "video", current_user, request=request)
        base_dk = _compute_save_url_dedupe_key(body.url, effective, body.dedupe_hint_url)
        dk = _final_save_url_dedupe_key(
            base_dk,
            body.generation_task_id,
            dedupe_hint_url=body.dedupe_hint_url,
            body_url=body.url,
        )
        async with _save_url_lock_for(current_user.id, dk):
            row = await _save_asset_from_url_locked(dk, body, request, current_user, effective_url_resolved=effective)
        cloud_row = await _save_seedance_video_to_server(
            body,
            auth_header=auth_header or _request_auth_header(request),
            installation_id=installation_id or _request_installation_id(request),
        )
        item = {"source_url": url, "task_id": task_id, "asset": row}
        if cloud_row:
            item["cloud_asset"] = cloud_row
        saved.append(item)
    return saved


def _video_model_from_result(result: Dict[str, Any]) -> str:
    cfg = result.get("config") if isinstance(result.get("config"), dict) else {}
    return str(cfg.get("video_model") or "") if isinstance(cfg, dict) else ""


def _pipeline_result_video_url(result: Dict[str, Any]) -> str:
    final_video = result.get("final_video") if isinstance(result.get("final_video"), dict) else {}
    return str(final_video.get("url") or final_video.get("path") or "").strip()


def _pipeline_result_video_candidates(result: Dict[str, Any]) -> List[str]:
    final_video = result.get("final_video") if isinstance(result.get("final_video"), dict) else {}
    candidates: List[str] = []
    for key in ("url", "path"):
        value = str(final_video.get(key) or "").strip()
        if value:
            candidates.append(value)
    for pair in collect_video_urls_from_pipeline_result(result):
        try:
            value = str(pair[0] or "").strip()
        except Exception:
            value = ""
        if value:
            candidates.append(value)
    out: List[str] = []
    seen = set()
    for value in candidates:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _is_local_bestseller_postprocessed_video(result: Dict[str, Any]) -> bool:
    final_video = result.get("final_video") if isinstance(result.get("final_video"), dict) else {}
    kind = str(final_video.get("kind") or "").strip().lower()
    hint = str(final_video.get("hint") or "").strip()
    if kind in {"local_bestseller_captioned", "local_bestseller_bgm_final"}:
        return True
    if "同城爆款字幕成片" in hint or "同城爆款字幕+BGM成片" in hint:
        return True
    captioned_video = result.get("captioned_video") if isinstance(result.get("captioned_video"), dict) else {}
    bgm_video = result.get("bgm_video") if isinstance(result.get("bgm_video"), dict) else {}
    final_ref = str(final_video.get("url") or final_video.get("path") or "").strip()
    for item in (captioned_video, bgm_video):
        ref = str(item.get("source_url") or item.get("path") or "").strip()
        if final_ref and ref and final_ref == ref:
            return True
    return False


def _normalize_video_download_ref(raw: str, *, job: Dict[str, Any]) -> str:
    value = str(raw or "").strip().strip('"').strip("'")
    if not value:
        return ""
    if value.startswith("//"):
        return "https:" + value
    if value.startswith(("http://", "https://")):
        return value
    path = Path(value)
    if not path.is_absolute():
        base = Path(job.get("job_output_dir") or _default_runs_root())
        path = base / value
    try:
        if path.is_file():
            return str(path)
    except Exception:
        pass
    return value


def _is_usable_video_download_ref(ref: str) -> bool:
    value = str(ref or "").strip()
    if not value:
        return False
    if value.startswith(("http://", "https://")):
        return True
    try:
        return Path(value).is_file()
    except Exception:
        return False


def _select_pipeline_video_download_ref(result: Dict[str, Any], *, job: Dict[str, Any]) -> str:
    candidates = _pipeline_result_video_candidates(result)
    # Avoid re-burning subtitles onto a previously captioned/BGM-final local bestseller output.
    if _is_local_bestseller_postprocessed_video(result):
        final_video = result.get("final_video") if isinstance(result.get("final_video"), dict) else {}
        final_refs = {
            str(final_video.get("url") or "").strip(),
            str(final_video.get("path") or "").strip(),
        }
        candidates = [raw for raw in candidates if str(raw or "").strip() not in final_refs]
        if not candidates:
            candidates = _pipeline_result_video_candidates(result)
    for raw in candidates:
        ref = _normalize_video_download_ref(raw, job=job)
        if _is_usable_video_download_ref(ref):
            return ref
        if ref:
            logger.warning("[seedance-tvc] skip unusable video ref for captioning: %s", ref[:300])
    return ""


def _pipeline_result_failure_error(result: Dict[str, Any]) -> str:
    failed = result.get("failed_segments")
    if not isinstance(failed, list) or not failed:
        failed = result.get("failed_shots")
    if isinstance(failed, list):
        for item in reversed(failed):
            if not isinstance(item, dict):
                continue
            err = str(item.get("error") or "").strip()
            if err:
                return err[:2000]
    final_video = result.get("final_video") if isinstance(result.get("final_video"), dict) else {}
    hint = str(final_video.get("hint") or "").strip()
    if hint:
        return hint[:2000]
    return "视频生成失败，未产出可播放的视频。"


def _pipeline_result_should_fail(result: Dict[str, Any]) -> bool:
    if _pipeline_result_video_url(result):
        return False
    failed = result.get("failed_segments")
    if not isinstance(failed, list):
        failed = result.get("failed_shots")
    return bool(failed)


def _seedance_task_text(inp: Dict[str, Any]) -> str:
    task = inp.get("task") if isinstance(inp.get("task"), dict) else {}
    return str(task.get("text") or inp.get("task_text") or "").strip()


def _local_bestseller_caption_lines(text: str) -> List[str]:
    lines: List[str] = []
    for raw in str(text or "").replace("。", "\n").replace("！", "！\n").replace("？", "？\n").splitlines():
        line = raw.strip()
        line = re.sub(r"^(标题文案|数字人口播内容|口播内容|文案内容|坐标|音乐)\s*[:：]\s*", "", line).strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines[:8]


def _escape_ass_text(text: str) -> str:
    return str(text or "").replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def _ass_color(hex_color: str) -> str:
    raw = str(hex_color or "").strip().lstrip("#")
    if len(raw) != 6:
        raw = "FFFFFF"
    rr, gg, bb = raw[0:2], raw[2:4], raw[4:6]
    return f"&H00{bb}{gg}{rr}"


_ASS_PLAY_RES_X = 1080
_ASS_PLAY_RES_Y = 1920
_ASS_SAFE_MARGIN_X = 52
_ASS_SAFE_TOP = 96
_ASS_SAFE_BOTTOM = 1810
_ASS_NO_LINE_START = "，。！？、；：）】》》」』…·%"


def _ass_char_units(ch: str) -> float:
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 1.0
    if ch.isspace():
        return 0.35
    return 0.55


def _ass_display_units(text: str) -> float:
    return sum(_ass_char_units(ch) for ch in str(text or ""))


def _ass_max_units(font_size: int, *, margin_x: int = _ASS_SAFE_MARGIN_X) -> float:
    usable = max(160, _ASS_PLAY_RES_X - 2 * int(margin_x))
    return max(4.0, usable / max(1.0, float(font_size or 1)))


def _ass_fit_font_size(text: str, *, base_size: int, margin_x: int = _ASS_SAFE_MARGIN_X, min_size: int) -> int:
    """把字号压到该行刚好放得进画面（不小于 min_size）。"""
    units = _ass_display_units(text)
    if units <= 0:
        return int(base_size)
    usable = max(160, _ASS_PLAY_RES_X - 2 * int(margin_x))
    fitted = int(usable / units)
    return max(int(min_size), min(int(base_size), fitted))


def _wrap_ass_line(text: str, *, font_size: int, margin_x: int = _ASS_SAFE_MARGIN_X, max_lines: int = 6) -> List[str]:
    """\pos 事件不会自动换行，这里按显示宽度手动折行。"""
    raw = str(text or "").strip()
    if not raw:
        return [""]
    limit = _ass_max_units(font_size, margin_x=margin_x)
    lines: List[str] = []
    current = ""
    used = 0.0
    for ch in raw:
        width = _ass_char_units(ch)
        if current and used + width > limit:
            if ch in _ASS_NO_LINE_START:
                current += ch
                lines.append(current)
                current = ""
                used = 0.0
                continue
            lines.append(current)
            current = ch
            used = width
            continue
        current += ch
        used += width
    if current:
        lines.append(current)
    if max_lines > 0 and len(lines) > max_lines:
        head = lines[: max_lines - 1]
        head.append("".join(lines[max_lines - 1 :]))
        lines = head
    return lines


def _ass_event(style: str, text: str, *, start: str = "0:00:00.00", end: str = "0:00:10.00", margin_v: int = 0) -> str:
    return f"Dialogue: 0,{start},{end},{style},,0,0,{int(margin_v)},,{text}"


def _rank_list_events(style: str, items: List[str], *, x: int, start_y: int, step_y: int) -> List[str]:
    events: List[str] = []
    for idx, item in enumerate(items):
        y = start_y + idx * step_y
        events.append(_ass_event(style, rf"{{\pos({x},{y})}}" + _escape_ass_text(item)))
    return events


def _stacked_center_events(style: str, items: List[str], *, x: int, start_y: int, step_y: int) -> List[str]:
    events: List[str] = []
    for idx, item in enumerate(items):
        y = start_y + idx * step_y
        events.append(_ass_event(style, rf"{{\pos({x},{y})}}" + _escape_ass_text(item)))
    return events


def _local_bestseller_rank_table_ass_content(subtitle_text: str, *, day: Any = None) -> str:
    lines = _local_bestseller_caption_lines(subtitle_text)
    title = lines[0] if lines else "我国南北城市分布"
    subtitle = lines[1] if len(lines) > 1 else "湖北竟然是南方"
    south = ["上海", "江苏", "浙江", "安徽", "江西", "湖北", "湖南", "四川", "重庆", "贵州", "云南", "福建", "广东", "广西", "海南"]
    north = ["北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江", "山东", "河南", "陕西", "甘肃", "青海", "宁夏", "新疆"]
    # 顶部大红标题/黄副标题：先按画面宽度压字号，必要时折行，避免顶到画面外
    title_font = _ass_fit_font_size(title, base_size=96, margin_x=54, min_size=58)
    title_wrapped = _wrap_ass_line(title, font_size=title_font, margin_x=54, max_lines=2)
    title_step = max(56, int(round(title_font * 1.2)))
    subtitle_font = _ass_fit_font_size(subtitle, base_size=88, margin_x=54, min_size=54)
    subtitle_wrapped = _wrap_ass_line(subtitle, font_size=subtitle_font, margin_x=54, max_lines=2)
    subtitle_step = max(52, int(round(subtitle_font * 1.2)))
    subtitle_y = 64 + len(title_wrapped) * title_step + 8
    events: List[str] = []
    for idx, piece in enumerate(title_wrapped):
        events.append(_ass_event("RankTitleRed", rf"{{\pos(540,{64 + idx * title_step})\fs{title_font}}}" + _escape_ass_text(piece)))
    for idx, piece in enumerate(subtitle_wrapped):
        events.append(_ass_event("RankTitleYellow", rf"{{\pos(540,{subtitle_y + idx * subtitle_step})\fs{subtitle_font}}}" + _escape_ass_text(piece)))
    list_start_y = 462
    list_rows = max(len(south), len(north), 1)
    # 行距自适应：保证最后一行（含字号高度）仍留在画面安全区内
    list_bottom_limit = _ASS_SAFE_BOTTOM - 96
    if list_rows <= 1:
        list_step_y = 100
    else:
        list_step_y = min(100, max(72, int((list_bottom_limit - list_start_y) / (list_rows - 1))))
    events.extend([
        _ass_event("RankHeader", r"{\pos(328,336)}南方"),
        _ass_event("RankHeader", r"{\pos(752,336)}北方"),
        *_rank_list_events("RankList", south, x=328, start_y=list_start_y, step_y=list_step_y),
        *_rank_list_events("RankList", north, x=752, start_y=list_start_y, step_y=list_step_y),
    ])
    return "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {_ASS_PLAY_RES_X}",
        f"PlayResY: {_ASS_PLAY_RES_Y}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: RankTitleRed,Microsoft YaHei,96,{_ass_color('#ff2d2d')},{_ass_color('#ffffff')},{_ass_color('#101010')},&H00000000,-1,0,0,0,100,100,0,0,1,8,2,8,54,54,64,1",
        f"Style: RankTitleYellow,Microsoft YaHei,88,{_ass_color('#fff200')},{_ass_color('#ffffff')},{_ass_color('#101010')},&H00000000,-1,0,0,0,100,100,0,0,1,8,2,8,54,54,150,1",
        f"Style: RankHeader,Microsoft YaHei,90,{_ass_color('#ffffff')},{_ass_color('#ffffff')},{_ass_color('#d91f1f')},{_ass_color('#d91f1f')},-1,0,0,0,100,100,0,0,3,15,0,5,54,54,0,1",
        f"Style: RankList,Microsoft YaHei,90,{_ass_color('#fff200')},{_ass_color('#ffffff')},{_ass_color('#101010')},&H00000000,-1,0,0,0,100,112,0,0,1,6,1,8,36,36,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events,
        "",
    ])


def _local_bestseller_ass_content(subtitle_text: str, subtitle_style: Optional[Dict[str, Any]] = None, *, day: Any = None) -> str:
    style = subtitle_style if isinstance(subtitle_style, dict) else {}
    variant = str(style.get("variant") or "").strip()
    if variant == "rank_table":
        return _local_bestseller_rank_table_ass_content(subtitle_text, day=day)
    scene_only_days = {1, 3, 4, 5, 8}
    lines = _local_bestseller_caption_lines(subtitle_text)
    title = lines[0] if lines else ""
    body = lines[1:] if len(lines) > 1 else []
    entries: List[tuple[str, str]] = []
    if title:
        entries.append(("Title", title))
    if body:
        entries.extend(("Body", line) for line in body[:4])

    base_fonts = {"Title": 94, "Body": 88}
    fonts = dict(base_fonts)
    rendered: List[tuple[str, str]] = []
    # 先按标准字号折行；行数太多就整体缩一号再折，保证整块字幕压在画面里
    for scale in (1.0, 0.92, 0.84, 0.76):
        fonts = {key: max(52, int(round(value * scale))) for key, value in base_fonts.items()}
        rendered = []
        for style_name, text in entries:
            for piece in _wrap_ass_line(text, font_size=fonts[style_name]):
                rendered.append((style_name, piece))
        if len(rendered) <= 8:
            break
    rendered = rendered[:8]

    use_top_layout = int(day or 0) in scene_only_days
    if rendered:
        max_font = max(fonts[style_name] for style_name, _ in rendered)
        step = max(56, int(round(max_font * 1.22)))
        height = (len(rendered) - 1) * step
        if use_top_layout:
            start_y = _ASS_SAFE_TOP + 36
        else:
            anchor_y = 1040 if variant == "large_center_stack" else 1010
            start_y = int(round(anchor_y - height / 2))
        start_y = max(_ASS_SAFE_TOP, start_y)
        if start_y + height > _ASS_SAFE_BOTTOM:
            start_y = max(_ASS_SAFE_TOP, _ASS_SAFE_BOTTOM - height)
    else:
        step = 102
        start_y = 312 if use_top_layout else 1010

    events: List[str] = []
    cursor_y = start_y
    for idx, (style_name, text) in enumerate(rendered):
        y = start_y + idx * step
        events.append(_ass_event(style_name, rf"{{\pos(540,{y})\fs{fonts[style_name]}}}" + _escape_ass_text(text)))
        cursor_y = y
    if not events:
        fallback_y = 312 if use_top_layout else 1010
        events.append(_ass_event("Body", rf"{{\pos(540,{fallback_y})}}"))
    return "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {_ASS_PLAY_RES_X}",
        f"PlayResY: {_ASS_PLAY_RES_Y}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Title,Microsoft YaHei,94,{_ass_color('#ff2d2d')},{_ass_color('#ffffff')},{_ass_color('#101010')},&H66000000,-1,0,0,0,100,104,0,0,1,8,2,8,48,48,0,1",
        f"Style: Body,Microsoft YaHei,88,{_ass_color('#fff200')},{_ass_color('#ffffff')},{_ass_color('#101010')},&H66000000,-1,0,0,0,100,108,0,0,1,7,2,8,52,52,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events,
        "",
    ])


def _ffmpeg_path_from_job(job: Dict[str, Any]) -> str:
    inp = job.get("inp") if isinstance(job.get("inp"), dict) else {}
    ff = str(inp.get("ffmpeg_path") or "").strip()
    return ff or "ffmpeg"


async def _download_video_to_path(url: str, path: Path) -> None:
    ref = str(url or "").strip()
    if not ref:
        raise RuntimeError("视频下载失败：URL 为空")
    if not ref.startswith(("http://", "https://")):
        local = Path(ref)
        if local.is_file():
            path.write_bytes(local.read_bytes())
            return
        raise RuntimeError(f"视频下载失败：不是有效 URL 或本地文件不存在: {ref[:300]}")
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True, trust_env=False) as client:
        resp = await client.get(ref)
    resp.raise_for_status()
    path.write_bytes(resp.content)


async def _download_media_to_path(ref: str, path: Path) -> None:
    value = str(ref or "").strip()
    if not value:
        raise RuntimeError("素材下载失败：引用为空")
    local = Path(value)
    if not value.startswith(("http://", "https://")) and local.is_file():
        path.write_bytes(local.read_bytes())
        return
    await _download_video_to_path(value, path)


def _run_caption_ffmpeg(ffmpeg_path: str, input_path: Path, ass_path: Path, output_path: Path) -> None:
    ass_filter_path = str(ass_path).replace("\\", "/").replace(":", "\\:")
    vf = f"subtitles='{ass_filter_path}'"
    cmd = [
        ffmpeg_path,
        "-y",
        "-i",
        str(input_path),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except OSError as exc:
        details = {
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "filename": getattr(exc, "filename", None),
            "errno": getattr(exc, "errno", None),
            "winerror": getattr(exc, "winerror", None),
            "command": cmd,
            "ffmpeg_path": ffmpeg_path,
            "input_path": str(input_path),
            "ass_path": str(ass_path),
        }
        logger.exception("[seedance-tool-error] caption ffmpeg execution failed details=%s", details)
        raise RuntimeError(f"ffmpeg execution failed: {details}") from exc
    if proc.returncode != 0:
        quoted = " ".join(shlex.quote(x) for x in cmd)
        error = (proc.stderr or proc.stdout or f"ffmpeg failed: {quoted}")[:2000]
        logger.error("[seedance-tool-error] caption ffmpeg returned nonzero returncode=%s command=%s stderr=%s", proc.returncode, cmd, error)
        raise RuntimeError(error)


def _run_bgm_ffmpeg(ffmpeg_path: str, input_path: Path, bgm_path: Path, output_path: Path, volume: float) -> None:
    gain = f"{min(1.0, max(0.0, float(volume))):.3f}"
    cmd = [
        ffmpeg_path,
        "-y",
        "-i",
        str(input_path),
        "-stream_loop",
        "-1",
        "-i",
        str(bgm_path),
        "-filter_complex",
        f"[1:a]volume={gain}[bgm]",
        "-map",
        "0:v:0",
        "-map",
        "[bgm]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except OSError as exc:
        details = {
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "filename": getattr(exc, "filename", None),
            "errno": getattr(exc, "errno", None),
            "winerror": getattr(exc, "winerror", None),
            "command": cmd,
            "ffmpeg_path": ffmpeg_path,
            "input_path": str(input_path),
            "bgm_path": str(bgm_path),
        }
        logger.exception("[seedance-tool-error] bgm ffmpeg execution failed details=%s", details)
        raise RuntimeError(f"ffmpeg execution failed: {details}") from exc
    if proc.returncode != 0:
        quoted = " ".join(shlex.quote(x) for x in cmd)
        error = (proc.stderr or proc.stdout or f"ffmpeg failed: {quoted}")[:2000]
        logger.error("[seedance-tool-error] bgm ffmpeg returned nonzero returncode=%s command=%s stderr=%s", proc.returncode, cmd, error)
        raise RuntimeError(error)


def _save_local_bestseller_caption_asset(
    *,
    user_id: int,
    output_path: Path,
    source_video_url: str,
    subtitle_text: str,
    job_id: str,
    day: Any,
    content_visibility: str = "internal",
) -> Dict[str, Any]:
    data = output_path.read_bytes()
    aid = _gen_asset_id()
    fname = f"{aid}.mp4"
    asset_path = ASSETS_DIR / fname
    asset_path.write_bytes(data)
    ct = _content_type_for_asset_filename(fname)
    tos_url = _upload_to_tos(data, f"assets/{fname}", ct)
    db = SessionLocal()
    try:
        row = Asset(
            asset_id=aid,
            user_id=user_id,
            filename=fname,
            media_type="video",
            file_size=len(data),
            source_url=tos_url,
            prompt=subtitle_text[:500],
            model="local-bestseller-caption-ffmpeg",
            tags="auto,local_bestseller.captioned_video",
            meta={
                "source_video_url": source_video_url,
                "seedance_job_id": job_id,
                "local_bestseller_day": day,
                "captioned": True,
                # 加字幕件默认不对外（后面还有 BGM 合成件时它就是中间产物）；
                # 只有当本任务没有 BGM（这一份就是交付件）时才标 visible。
                "content_visibility": str(content_visibility or "internal").strip().lower() or "internal",
            },
        )
        db.add(row)
        db.commit()
        return {
            "asset_id": aid,
            "filename": fname,
            "media_type": "video",
            "file_size": len(data),
            "source_url": tos_url or "",
            "path": str(asset_path),
        }
    finally:
        db.close()


def _save_local_bestseller_post_asset(
    *,
    user_id: int,
    output_path: Path,
    source_video_url: str,
    subtitle_text: str,
    job_id: str,
    day: Any,
    bgm_name: str = "",
    bgm_url: str = "",
    kind: str = "local_bestseller_postprocessed",
) -> Dict[str, Any]:
    data = output_path.read_bytes()
    aid = _gen_asset_id()
    fname = f"{aid}.mp4"
    asset_path = ASSETS_DIR / fname
    asset_path.write_bytes(data)
    ct = _content_type_for_asset_filename(fname)
    tos_url = _upload_to_tos(data, f"assets/{fname}", ct)
    db = SessionLocal()
    try:
        row = Asset(
            asset_id=aid,
            user_id=user_id,
            filename=fname,
            media_type="video",
            file_size=len(data),
            source_url=tos_url,
            prompt=(subtitle_text or bgm_name)[:500],
            model="local-bestseller-post-ffmpeg",
            tags="auto,local_bestseller.postprocessed_video",
            meta={
                "source_video_url": source_video_url,
                "seedance_job_id": job_id,
                "local_bestseller_day": day,
                "captioned": bool(subtitle_text),
                "bgm_added": bool(bgm_url),
                "bgm_name": bgm_name,
                "bgm_url": bgm_url,
                "kind": kind,
                # 合成/加 BGM 的这一份就是交付给用户的成片：可见；若之后另有最终件入库，
                # _demote_process_assets_for_job 会把它降级为中间产物（这时它才是过程件）。
                "asset_origin": "generated",
                "content_visibility": "visible",
            },
        )
        db.add(row)
        db.commit()
        return {
            "asset_id": aid,
            "filename": fname,
            "media_type": "video",
            "file_size": len(data),
            "source_url": tos_url or "",
            "path": str(asset_path),
        }
    finally:
        db.close()


async def _caption_local_bestseller_video_if_needed(
    *,
    job_id: str,
    job: Dict[str, Any],
    result: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    meta = job.get("meta") if isinstance(job.get("meta"), dict) else {}
    if meta.get("feature") != "local_bestseller":
        return None
    subtitle_text = str(meta.get("subtitle_text") or "").strip()
    if not subtitle_text:
        return None
    video_url = _select_pipeline_video_download_ref(result, job=job)
    if not video_url:
        raise RuntimeError("同城爆款字幕合成失败：未找到原视频 URL")

    async with _LOCAL_BESTSELLER_CAPTION_LOCK:
        update_job(job_id, post_status="captioning", post_stage="burn_subtitle")
        work_dir = Path(job.get("job_output_dir") or _default_runs_root()) / "caption_post"
        work_dir.mkdir(parents=True, exist_ok=True)
        raw_path = work_dir / "raw.mp4"
        ass_path = work_dir / "caption.ass"
        out_path = work_dir / "captioned.mp4"
        subtitle_style = meta.get("subtitle_style") if isinstance(meta.get("subtitle_style"), dict) else {}
        ass_path.write_text(
            _local_bestseller_ass_content(subtitle_text, subtitle_style, day=meta.get("day")),
            encoding="utf-8",
        )
        await _download_video_to_path(video_url, raw_path)
        await asyncio.to_thread(_run_caption_ffmpeg, _ffmpeg_path_from_job(job), raw_path, ass_path, out_path)
        return _save_local_bestseller_caption_asset(
            user_id=int(job.get("user_id") or 0),
            output_path=out_path,
            source_video_url=video_url,
            subtitle_text=subtitle_text,
            job_id=job_id,
            day=meta.get("day"),
            # 有 BGM 时后面还会合成一份（那一份才是交付件），这里先不对外；
            # 没有 BGM 时这份加字幕视频就是交付件。
            content_visibility="internal" if str((meta.get("bgm") or {}).get("bgm_url") or "").strip() else "visible",
        )


async def _mix_local_bestseller_bgm_if_needed(
    *,
    job_id: str,
    job: Dict[str, Any],
    result: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    meta = job.get("meta") if isinstance(job.get("meta"), dict) else {}
    if meta.get("feature") != "local_bestseller":
        return None
    bgm = meta.get("bgm") if isinstance(meta.get("bgm"), dict) else {}
    bgm_url = str(bgm.get("bgm_url") or bgm.get("url") or "").strip()
    if not bgm_url:
        return None
    try:
        volume = float(bgm.get("volume") if bgm.get("volume") is not None else 0.24)
    except Exception:
        volume = 0.24
    volume = min(1.0, max(0.0, volume))
    final_video = result.get("final_video") if isinstance(result.get("final_video"), dict) else {}
    preferred_ref = _normalize_video_download_ref(str(final_video.get("path") or final_video.get("url") or ""), job=job)
    video_ref = preferred_ref if _is_usable_video_download_ref(preferred_ref) else _select_pipeline_video_download_ref(result, job=job)
    if not video_ref:
        raise RuntimeError("同城爆款背景音乐合成失败：未找到可用视频")

    async with _LOCAL_BESTSELLER_CAPTION_LOCK:
        update_job(job_id, post_status="mixing_bgm", post_stage="mix_bgm")
        work_dir = Path(job.get("job_output_dir") or _default_runs_root()) / "caption_post"
        work_dir.mkdir(parents=True, exist_ok=True)
        input_path = work_dir / "bgm_input.mp4"
        bgm_path = work_dir / "bgm_track.m4a"
        out_path = work_dir / "bgm_final.mp4"
        await _download_media_to_path(video_ref, input_path)
        await _download_media_to_path(bgm_url, bgm_path)
        await asyncio.to_thread(_run_bgm_ffmpeg, _ffmpeg_path_from_job(job), input_path, bgm_path, out_path, volume)
        return _save_local_bestseller_post_asset(
            user_id=int(job.get("user_id") or 0),
            output_path=out_path,
            source_video_url=video_ref,
            subtitle_text=str(meta.get("subtitle_text") or "").strip(),
            job_id=job_id,
            day=meta.get("day"),
            bgm_name=str(bgm.get("music_name") or "").strip(),
            bgm_url=bgm_url,
            kind="local_bestseller_bgm_final",
        )


async def _seedance_job_runner(job_id: str) -> None:
    job = get_job(job_id)
    if not job:
        return
    inp = deepcopy(job.get("inp") or {})
    auto_save = bool(job.get("auto_save"))
    user_id = int(job.get("user_id") or 0)
    auth_header = str(job.get("auth_header") or "")
    installation_id = str(job.get("installation_id") or "")
    task_text = _seedance_task_text(inp)
    request_payload = {"inp": inp, "auto_save": auto_save}
    await sync_creative_job_to_cloud(
        auth_header=auth_header,
        installation_id=installation_id,
        job_id=job_id,
        feature_type="seedance_tvc",
        provider="comfly_seedance",
        status="running",
        stage="generating",
        title="创意视频任务",
        prompt=task_text,
        request_payload=request_payload,
    )
    try:
        result = await asyncio.to_thread(run_storyboard_pipeline_sync, inp)
    except Exception as e:
        error = str(e)[:2000]
        update_job(job_id, status="failed", error=error)
        await sync_creative_job_to_cloud(
            auth_header=auth_header,
            installation_id=installation_id,
            job_id=job_id,
            feature_type="seedance_tvc",
            provider="comfly_seedance",
            status="failed",
            stage="failed",
            title="创意视频任务",
            prompt=task_text,
            request_payload=request_payload,
            error=error,
        )
        return

    if _pipeline_result_should_fail(result):
        error = _pipeline_result_failure_error(result)
        update_job(job_id, status="failed", error=error, result=result, saved_assets=[])
        await sync_creative_job_to_cloud(
            auth_header=auth_header,
            installation_id=installation_id,
            job_id=job_id,
            feature_type="seedance_tvc",
            provider="comfly_seedance",
            status="failed",
            stage="failed",
            title="创意视频任务",
            prompt=task_text,
            request_payload=request_payload,
            result_payload=result,
            error=error,
            meta={"auto_save": auto_save},
        )
        return

    saved_assets: List[Dict[str, Any]] = []
    final_video = result.get("final_video") if isinstance(result.get("final_video"), dict) else {}
    final_video_path = str(final_video.get("path") or "").strip()

    update_job(
        job_id,
        status="completed",
        error=None,
        result=result,
        saved_assets=[],
        post_status="running" if auto_save else None,
        post_stage="saving_assets" if auto_save else None,
    )

    if auto_save:
        try:
            # A task only counts as auto-saved after its final playable video
            # has reached the user's material library. Prefer the local merged
            # file, then fall back to the final public URL for direct-video jobs.
            if final_video_path:
                final_asset = await _save_local_final_video_asset(
                    local_path=final_video_path,
                    current_user=_ServerUser(id=user_id),
                    prompt=task_text,
                    video_model=_video_model_from_result(result),
                    auth_header=auth_header,
                    installation_id=installation_id,
                    generation_task_id=job_id,
                )
                if not final_asset:
                    raise RuntimeError("最终成片转存到素材库失败")
                saved_assets.append(
                    {
                        "source_url": final_asset.get("source_url") or "",
                        "task_id": job_id,
                        "asset": final_asset,
                        "kind": "merged_final",
                    }
                )
                result = {
                    **result,
                    "final_video": {
                        **final_video,
                        "url": final_asset.get("source_url") or final_video.get("url") or None,
                        "asset_id": final_asset.get("asset_id") or "",
                        "kind": "merged_final",
                    },
                }
            else:
                pairs = collect_video_urls_from_pipeline_result(result)
                if not pairs:
                    raise RuntimeError("视频已生成，但未找到可转存到素材库的成片地址")
                remote_assets = await _save_pipeline_videos(
                    urls=pairs,
                    request=None,
                    current_user=_ServerUser(id=user_id),
                    video_model=_video_model_from_result(result),
                    auth_header=auth_header,
                    installation_id=installation_id,
                )
                for item in remote_assets:
                    if str(item.get("task_id") or "").strip() == "final":
                        item["kind"] = "merged_final"
                saved_assets.extend(remote_assets)

            if not saved_assets:
                raise RuntimeError("视频已生成，但转存素材库后未返回素材记录")
        except Exception as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            detail = str(detail or "未知错误")[:2000]
            logger.warning(
                "[seedance-tvc] final asset save failed after completion job_id=%s detail=%s",
                job_id,
                detail,
            )
            update_job(
                job_id,
                status="completed",
                error=None,
                result=result,
                saved_assets=saved_assets,
                post_status="failed",
                post_stage="saving_assets",
                post_error=f"final asset save failed: {detail}",
            )
            await sync_creative_job_to_cloud(
                auth_header=auth_header,
                installation_id=installation_id,
                job_id=job_id,
                feature_type="seedance_tvc",
                provider="comfly_seedance",
                status="completed",
                stage="saving_assets",
                title="创意视频任务",
                prompt=task_text,
                request_payload=request_payload,
                result_payload=result,
                saved_assets=saved_assets,
                error=f"final asset save failed: {detail}",
                meta={"auto_save": auto_save},
            )
            return
    caption_asset: Optional[Dict[str, Any]] = None
    try:
        caption_asset = await _caption_local_bestseller_video_if_needed(job_id=job_id, job=job, result=result)
    except Exception as exc:
        error = str(exc)[:2000]
        update_job(
            job_id,
            status="completed",
            error=None,
            result=result,
            saved_assets=saved_assets,
            post_status="failed",
            post_stage="caption_failed",
            post_error=f"caption failed: {error}",
        )
        await sync_creative_job_to_cloud(
            auth_header=auth_header,
            installation_id=installation_id,
            job_id=job_id,
            feature_type="seedance_tvc",
            provider="comfly_seedance",
            status="completed",
            stage="caption_failed",
            title="创意视频任务",
            prompt=task_text,
            request_payload=request_payload,
            result_payload=result,
            saved_assets=saved_assets,
            error=f"caption failed: {error}",
            meta={"auto_save": auto_save},
        )
        return
    if caption_asset:
        saved_assets = [*saved_assets, {"asset": caption_asset, "kind": "local_bestseller_captioned"}]
        result = {
            **result,
            "captioned_video": caption_asset,
            "final_video": {
                **(result.get("final_video") if isinstance(result.get("final_video"), dict) else {}),
                "url": caption_asset.get("source_url") or None,
                "path": caption_asset.get("path") or "",
                "asset_id": caption_asset.get("asset_id") or "",
                "kind": "local_bestseller_captioned",
                "hint": "同城爆款字幕成片已完成。",
            },
        }

    bgm_asset: Optional[Dict[str, Any]] = None
    try:
        bgm_asset = await _mix_local_bestseller_bgm_if_needed(job_id=job_id, job=job, result=result)
    except Exception as exc:
        error = str(exc)[:2000]
        update_job(
            job_id,
            status="completed",
            error=None,
            result=result,
            saved_assets=saved_assets,
            post_status="failed",
            post_stage="bgm_failed",
            post_error=f"bgm failed: {error}",
        )
        await sync_creative_job_to_cloud(
            auth_header=auth_header,
            installation_id=installation_id,
            job_id=job_id,
            feature_type="seedance_tvc",
            provider="comfly_seedance",
            status="completed",
            stage="bgm_failed",
            title="创意视频任务",
            prompt=task_text,
            request_payload=request_payload,
            result_payload=result,
            saved_assets=saved_assets,
            error=f"bgm failed: {error}",
            meta={"auto_save": auto_save},
        )
        return
    if bgm_asset:
        saved_assets = [*saved_assets, {"asset": bgm_asset, "kind": "local_bestseller_bgm_final"}]
        result = {
            **result,
            "bgm_video": bgm_asset,
            "final_video": {
                **(result.get("final_video") if isinstance(result.get("final_video"), dict) else {}),
                "url": bgm_asset.get("source_url") or None,
                "path": bgm_asset.get("path") or "",
                "asset_id": bgm_asset.get("asset_id") or "",
                "kind": "local_bestseller_bgm_final",
                "hint": "同城爆款字幕+BGM成片已完成。",
            },
        }

    update_job(
        job_id,
        status="completed",
        error=None,
        result=result,
        saved_assets=saved_assets,
        post_status="completed" if auto_save else None,
        post_stage="completed" if auto_save else None,
    )
    await sync_creative_job_to_cloud(
        auth_header=auth_header,
        installation_id=installation_id,
        job_id=job_id,
        feature_type="seedance_tvc",
        provider="comfly_seedance",
        status="completed",
        stage="completed",
        title="创意视频任务",
        prompt=task_text,
        request_payload=request_payload,
        result_payload=result,
        saved_assets=saved_assets,
        meta={"auto_save": auto_save},
    )


async def start_seedance_tvc_pipeline_job(
    *,
    pl: ComflySeedancePipelinePayload,
    request: Request,
    current_user: _ServerUser,
    db: Session,
    title: str = "创意视频任务",
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _validate_payload(pl)
    runs_root = (pl.output_dir or "").strip() or _default_runs_root()
    job_id = uuid.uuid4().hex
    effective_dir = str(Path(runs_root) / "job_runs" / job_id) if pl.isolate_job_dir else runs_root
    inp = await _prepare_pipeline_input(
        pl=pl,
        current_user=current_user,
        db=db,
        request=request,
        effective_output_dir=effective_dir,
    )
    auth_header = _request_auth_header(request)
    installation_id = _request_installation_id(request)
    create_job_record(
        user_id=current_user.id,
        inp=inp,
        auto_save=pl.auto_save,
        job_output_dir=effective_dir,
        job_id=job_id,
        auth_header=auth_header,
        installation_id=installation_id,
        meta=meta,
    )
    asyncio.create_task(sync_creative_job_to_cloud(
        auth_header=auth_header,
        installation_id=installation_id,
        job_id=job_id,
        feature_type="seedance_tvc",
        provider="comfly_seedance",
        status="running",
        stage="queued",
        title=title,
        prompt=pl.task_text,
        request_payload={"payload": pl.model_dump(), "inp": inp},
        meta={"auto_save": pl.auto_save, "duration": pl.total_duration_seconds, **(meta or {})},
    ))

    def _log_task_done(task: asyncio.Task) -> None:
        try:
            _ = task.exception()
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(_seedance_job_runner(job_id))
    task.add_done_callback(_log_task_done)
    return {"ok": True, "async": True, "job_id": job_id, "poll_path": f"/api/comfly-seedance-tvc/pipeline/jobs/{job_id}"}


def _redact_progress_for_client(prog: Any) -> Any:
    if not isinstance(prog, dict):
        return prog
    red = {k: v for k, v in prog.items() if k not in ("manifest_file", "run_dir")}
    last_steps = red.get("last_steps")
    if isinstance(last_steps, list):
        cleaned: List[Dict[str, Any]] = []
        for item in last_steps:
            if not isinstance(item, dict):
                continue
            one = dict(item)
            err = one.get("error")
            if isinstance(err, str) and err.strip():
                one["error"] = re.sub(
                    r"(?:[A-Za-z]:[/\\][^\s\"'<>|]{2,320}|(?:\\\\|/)[^\s\"'<>|]{0,320}(?:[/\\](?:skills|job_runs|runs)[/\\]|\.py\b)[^\s\"'<>|]{0,320})",
                    "...",
                    err.strip(),
                    flags=re.IGNORECASE,
                )[:400]
            cleaned.append(one)
        red["last_steps"] = cleaned
    return red


def _job_status_response(job: Dict[str, Any], *, include_full: bool) -> Dict[str, Any]:
    st = (job.get("status") or "").strip()
    meta = job.get("meta") if isinstance(job.get("meta"), dict) else {}
    subtitle_text = str(meta.get("subtitle_text") or "").strip()
    requires_caption = meta.get("feature") == "local_bestseller" and bool(subtitle_text)
    post_status = job.get("post_status")
    post_stage = job.get("post_stage")
    caption_ready = bool(post_stage == "completed" and post_status == "completed") or bool(
        isinstance(job.get("result"), dict) and isinstance(job.get("result", {}).get("captioned_video"), dict)
    )
    out: Dict[str, Any] = {
        "ok": True,
        "job_id": job.get("job_id"),
        "status": st,
        "post_status": post_status,
        "post_stage": post_stage,
        "post_error": job.get("post_error"),
        "auto_save": job.get("auto_save"),
        "created_at_ts": job.get("created_at_ts"),
        "updated_at_ts": job.get("updated_at_ts"),
        "requires_caption": requires_caption,
        "caption_ready": caption_ready,
    }
    job_out = job.get("job_output_dir") or ""
    prog = read_manifest_progress(str(job_out))
    if prog:
        out["progress"] = _redact_progress_for_client(prog)
        out["progress_percent"] = prog.get("progress_percent")
        out["progress_label"] = prog.get("progress_label")
        out["progress_detail"] = prog.get("progress_detail")
    artifacts = read_manifest_artifacts(str(job_out))
    if artifacts:
        out["artifacts"] = {
            k: v for k, v in artifacts.items()
            if k not in ("manifest_file", "run_dir")
        }
    if st == "failed":
        out["error"] = job.get("error")
        if include_full and job.get("result") is not None:
            out["result"] = job.get("result")
    if st == "completed":
        if include_full:
            out["result"] = job.get("result")
            out["saved_assets"] = job.get("saved_assets") or []
    return out


def _recent_job_summary(job: Dict[str, Any], request: Optional[Request] = None) -> Dict[str, Any]:
    payload = _job_status_response(job, include_full=True)
    result = payload.get("result")
    if request is not None and result is not None:
        payload["result"] = _with_local_final_video_url(result, request, job)
    inp = job.get("inp") if isinstance(job.get("inp"), dict) else {}
    task = inp.get("task") if isinstance(inp.get("task"), dict) else {}
    prompt = str(task.get("text") or inp.get("task_text") or "").strip()
    meta = job.get("meta") if isinstance(job.get("meta"), dict) else {}
    return {
        "job_id": payload.get("job_id"),
        "status": payload.get("status"),
        "created_at_ts": payload.get("created_at_ts"),
        "updated_at_ts": payload.get("updated_at_ts"),
        "progress": payload.get("progress"),
        "progress_percent": payload.get("progress_percent"),
        "progress_label": payload.get("progress_label"),
        "progress_detail": payload.get("progress_detail"),
        "artifacts": payload.get("artifacts"),
        "error": payload.get("error"),
        "result": payload.get("result"),
        "saved_assets": payload.get("saved_assets") or [],
        "title": str(meta.get("title") or "创意视频任务"),
        "prompt": prompt,
    }


def _with_local_final_video_url(result: Any, request: Request, job: Dict[str, Any]) -> Any:
    if not isinstance(result, dict):
        return result
    final_video = result.get("final_video") if isinstance(result.get("final_video"), dict) else {}
    if not isinstance(final_video, dict):
        return result
    final_url = str(final_video.get("url") or "").strip()
    if final_url:
        return result
    final_path = str(final_video.get("path") or "").strip()
    if not final_path:
        return result
    path = Path(final_path)
    if not path.is_absolute():
        base = Path(job.get("job_output_dir") or _default_runs_root())
        path = base / path
    try:
        if not path.is_file():
            return result
    except Exception:
        return result
    local_url = _register_local_file_url(request, str(path))
    return {
        **result,
        "final_video": {
            **final_video,
            "url": local_url,
            "local_preview_url": local_url,
        },
    }


@router.post("/api/comfly-seedance-tvc/pipeline/run")
async def comfly_seedance_pipeline_run(
    body: ComflySeedanceRunBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
    db=Depends(get_db),
):
    pl = body.payload
    _validate_payload(pl)
    runs_root = (pl.output_dir or "").strip() or _default_runs_root()
    inp = await _prepare_pipeline_input(
        pl=pl,
        current_user=current_user,
        db=db,
        request=request,
        effective_output_dir=runs_root,
    )
    try:
        result = await asyncio.to_thread(run_storyboard_pipeline_sync, inp)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:2000]) from e

    saved_assets: List[Dict[str, Any]] = []
    if pl.auto_save:
        pairs = collect_video_urls_from_pipeline_result(result)
        if pairs:
            saved_assets = await _save_pipeline_videos(
                urls=pairs,
                request=request,
                current_user=current_user,
                video_model=_video_model_from_result(result),
                auth_header=_request_auth_header(request),
                installation_id=_request_installation_id(request),
            )
    return {"ok": True, "pipeline": "comfly_seedance_tvc_video", "result": result, "saved_assets": saved_assets}


@router.post("/api/comfly-seedance-tvc/pipeline/start")
async def comfly_seedance_pipeline_start(
    body: ComflySeedanceRunBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
    db=Depends(get_db),
):
    pl = body.payload
    return await start_seedance_tvc_pipeline_job(
        pl=pl,
        request=request,
        current_user=current_user,
        db=db,
    )


@router.get("/api/comfly-seedance-tvc/pipeline/jobs/{job_id}")
async def comfly_seedance_pipeline_job_status(
    job_id: str,
    request: Request,
    compact: bool = False,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if int(job.get("user_id") or -1) != int(current_user.id):
        raise HTTPException(status_code=403, detail="无权查看该任务")
    payload = _job_status_response(job, include_full=not compact)
    if not compact and request is not None and payload.get("result") is not None:
        payload["result"] = _with_local_final_video_url(payload.get("result"), request, job)
    return payload


@router.get("/api/comfly-seedance-tvc/pipeline/jobs")
async def comfly_seedance_pipeline_jobs(
    request: Request,
    limit: int = 60,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
):
    rows = list_jobs_for_user(int(current_user.id), limit=limit)
    return {
        "ok": True,
        "items": [_recent_job_summary(job, request=request) for job in rows],
    }


@router.delete("/api/comfly-seedance-tvc/pipeline/jobs/{job_id}")
async def comfly_seedance_pipeline_job_delete(
    job_id: str,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
):
    if not delete_job(job_id, user_id=int(current_user.id)):
        raise HTTPException(status_code=404, detail="job not found")
    return {"ok": True, "job_id": job_id}

