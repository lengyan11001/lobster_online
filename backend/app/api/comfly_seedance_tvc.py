from __future__ import annotations

import asyncio
import logging
import re
import shlex
import subprocess
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
    get_job,
    list_jobs_for_user,
    read_manifest_progress,
    update_job,
)
from ..services.comfly_seedance_tvc_pipeline_runner import (
    _api_base_for_pipeline,
    build_pipeline_input,
    collect_video_urls_from_pipeline_result,
    resolve_reference_images_for_pipeline,
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
    merge_clips: bool = Field(True, description="最终始终会合并所有视频段；该字段保留仅为兼容旧调用")
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
    image_model: Optional[str] = None
    image_model_fallback: Optional[str] = None
    video_model: Optional[str] = None
    video_channel: Optional[str] = None
    video_base_url: Optional[str] = None
    video_fallbacks: List[Dict[str, Any]] = Field(default_factory=list, description="Ordered video fallback providers, each item supports channel/base_url/model.")
    aspect_ratio: str = "9:16"
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


def _is_grok_video_request(channel: str, model: str) -> bool:
    model_hint = (model or "").strip().lower().replace("_", "-").replace(" ", "")
    return model_hint in {
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
    uses_yunwu_veo = _is_veo31_request(pl.video_channel or "", pl.video_model or "")
    segment_seconds = 8 if uses_yunwu_veo else 10
    if pl.segment_duration_seconds is not None and int(pl.segment_duration_seconds) != segment_seconds:
        raise HTTPException(status_code=400, detail=f"segment_duration_seconds 当前模型固定为 {segment_seconds} 秒")

    requested_count = pl.segment_count if pl.segment_count is not None else pl.storyboard_count
    requested_total = pl.total_duration_seconds
    if requested_total is None and requested_count is not None:
        requested_total = int(requested_count) * segment_seconds

    allowed_totals = {segment_seconds * i for i in range(1, 7)}
    if requested_total is not None and int(requested_total) not in allowed_totals:
        allowed_text = "/".join(str(x) for x in sorted(allowed_totals))
        raise HTTPException(status_code=400, detail=f"total_duration_seconds 仅支持 {allowed_text} 秒")
    if requested_count is not None and int(requested_count) * segment_seconds != int(requested_total or segment_seconds * 2):
        raise HTTPException(status_code=400, detail=f"segment_count/storyboard_count 必须与 total_duration_seconds / {segment_seconds} 一致")


async def _prepare_pipeline_input(
    *,
    pl: ComflySeedancePipelinePayload,
    current_user: _ServerUser,
    db: Session,
    request: Request,
    effective_output_dir: str,
) -> Dict[str, Any]:
    reference_images = resolve_reference_images_for_pipeline(
        user_id=current_user.id,
        db=db,
        request=request,
        asset_id=pl.asset_id,
        image_url=pl.image_url,
        reference_asset_ids=pl.reference_asset_ids,
        reference_image_urls=pl.reference_image_urls,
    )
    api_base, api_key = _resolve_comfly_credentials(current_user.id, db, request)
    pipe_base = _api_base_for_pipeline(api_base)
    video_channel = (pl.video_channel or "").strip().lower()
    video_base_url = (pl.video_base_url or "").strip()
    video_model = (pl.video_model or "").strip()
    if video_model.lower().replace(" ", "") in {"yunwu-veo3.1-plus", "veo3.1-plus", "veo3.1"}:
        video_channel = "yunwu"
        video_model = "veo3.1"
    if _is_grok_video_request(video_channel, video_model):
        video_channel = video_channel or "openmind"
    if video_channel in {"yunwu", "云雾", "雲霧"}:
        video_channel = "yunwu"
        video_base_url = video_base_url or pipe_base
        video_model = video_model or "veo3.1"
    policy = await _fetch_video_provider_policy(request=request, model=video_model or pl.video_model or "", channel=video_channel or pl.video_channel or "")
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
    if (
        workflow_mode == "storyboard"
        and len(reference_images) >= 1
        and (pl.task_text or "").strip()
        and int(requested_count or 1) == 1
    ):
        workflow_mode = "direct_video"
    logger.info(
        "[seedance-tvc] prepared pipeline user_id=%s workflow_mode=%s references=%s segment_count=%s segment_seconds=%s video_channel=%s video_model=%s",
        current_user.id,
        workflow_mode,
        len(reference_images),
        requested_count or pl.segment_count or pl.storyboard_count or 1,
        pl.segment_duration_seconds,
        video_channel or "",
        video_model or pl.video_model or "",
    )
    return build_pipeline_input(
        reference_image=reference_images[0] if reference_images else "",
        reference_images=reference_images,
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
        image_model=pl.image_model,
        image_model_fallback=pl.image_model_fallback,
        video_model=video_model or pl.video_model,
        video_channel=video_channel,
        video_base_url=video_base_url,
        video_fallbacks=video_fallbacks,
        aspect_ratio=pl.aspect_ratio,
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
    }
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
            tags="auto,comfly.seedance.tvc.pipeline,merged",
            meta=meta,
        )
        db.add(asset)
        db.commit()
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
    for raw in _pipeline_result_video_candidates(result):
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


def _ass_event(style: str, text: str, *, start: str = "0:00:00.00", end: str = "0:00:10.00", margin_v: int = 0) -> str:
    return f"Dialogue: 0,{start},{end},{style},,0,0,{int(margin_v)},,{text}"


def _local_bestseller_rank_table_ass_content(subtitle_text: str, *, day: Any = None) -> str:
    lines = _local_bestseller_caption_lines(subtitle_text)
    title = lines[0] if lines else "我国南北城市分布"
    subtitle = lines[1] if len(lines) > 1 else "湖北竟然是南方"
    south = ["上海", "江苏", "浙江", "安徽", "江西", "湖北", "湖南", "四川", "重庆", "贵州", "云南", "福建", "广东", "广西", "海南"]
    north = ["北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江", "山东", "河南", "陕西", "甘肃", "青海", "宁夏", "新疆"]
    events = [
        _ass_event("RankTitleRed", _escape_ass_text(title), margin_v=64),
        _ass_event("RankTitleYellow", _escape_ass_text(subtitle), margin_v=140),
        _ass_event("RankHeader", r"{\pos(328,300)}南方"),
        _ass_event("RankHeader", r"{\pos(752,300)}北方"),
        _ass_event("RankList", r"{\pos(328,390)}" + _escape_ass_text("\n".join(south))),
        _ass_event("RankList", r"{\pos(752,390)}" + _escape_ass_text("\n".join(north))),
    ]
    return "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: RankTitleRed,Microsoft YaHei,78,{_ass_color('#ff2d2d')},{_ass_color('#ffffff')},{_ass_color('#101010')},&H00000000,-1,0,0,0,100,100,0,0,1,7,2,8,54,54,64,1",
        f"Style: RankTitleYellow,Microsoft YaHei,72,{_ass_color('#fff200')},{_ass_color('#ffffff')},{_ass_color('#101010')},&H00000000,-1,0,0,0,100,100,0,0,1,7,2,8,54,54,140,1",
        f"Style: RankHeader,Microsoft YaHei,76,{_ass_color('#ffffff')},{_ass_color('#ffffff')},{_ass_color('#d91f1f')},{_ass_color('#d91f1f')},-1,0,0,0,100,100,0,0,3,14,0,5,54,54,0,1",
        f"Style: RankList,Microsoft YaHei,44,{_ass_color('#fff200')},{_ass_color('#ffffff')},{_ass_color('#101010')},&H00000000,-1,0,0,0,100,100,0,0,1,5,1,8,36,36,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events,
        "",
    ])


def _local_bestseller_ass_content(subtitle_text: str, subtitle_style: Optional[Dict[str, Any]] = None, *, day: Any = None) -> str:
    style = subtitle_style if isinstance(subtitle_style, dict) else {}
    if str(style.get("variant") or "").strip() == "rank_table" and int(day or 0) == 1:
        return _local_bestseller_rank_table_ass_content(subtitle_text, day=day)
    lines = _local_bestseller_caption_lines(subtitle_text)
    title = lines[0] if lines else ""
    body = lines[1:] if len(lines) > 1 else []
    events: List[str] = []
    if title:
        events.append(f"Dialogue: 0,0:00:00.00,0:00:10.00,Title,,0,72,0,,{_escape_ass_text(title)}")
    if body:
        body_text = "\n".join(body)
        events.append(f"Dialogue: 0,0:00:00.00,0:00:10.00,Body,,0,112,0,,{_escape_ass_text(body_text)}")
    if not events:
        events.append("Dialogue: 0,0:00:00.00,0:00:10.00,Body,,0,112,0,,")
    return "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Title,Microsoft YaHei,64,{_ass_color('#ff2d2d')},{_ass_color('#ffffff')},{_ass_color('#101010')},&H99000000,-1,0,0,0,100,100,0,0,1,5,1,8,72,72,72,1",
        f"Style: Body,Microsoft YaHei,58,{_ass_color('#fff200')},{_ass_color('#ffffff')},{_ass_color('#101010')},&H99000000,-1,0,0,0,100,100,0,0,1,5,1,8,80,80,112,1",
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
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        quoted = " ".join(shlex.quote(x) for x in cmd)
        raise RuntimeError((proc.stderr or proc.stdout or f"ffmpeg failed: {quoted}")[:2000])


def _save_local_bestseller_caption_asset(
    *,
    user_id: int,
    output_path: Path,
    source_video_url: str,
    subtitle_text: str,
    job_id: str,
    day: Any,
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
            if final_asset:
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
                final_video = result.get("final_video") if isinstance(result.get("final_video"), dict) else {}

        pairs = collect_video_urls_from_pipeline_result(result)
        if pairs:
            try:
                segment_assets = await _save_pipeline_videos(
                    urls=pairs,
                    request=None,
                    current_user=_ServerUser(id=user_id),
                    video_model=_video_model_from_result(result),
                    auth_header=auth_header,
                    installation_id=installation_id,
                )
                saved_assets.extend(segment_assets)
            except HTTPException as he:
                detail = he.detail if isinstance(he.detail, str) else str(he.detail)
                logger.warning(
                    "[seedance-tvc] segment asset save failed after completion job_id=%s detail=%s",
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
                    post_error=f"segment asset save failed: {detail}",
                )
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


def _local_bestseller_caption_required(job: Dict[str, Any]) -> bool:
    meta = job.get("meta") if isinstance(job.get("meta"), dict) else {}
    return meta.get("feature") == "local_bestseller" and bool(str(meta.get("subtitle_text") or "").strip())


def _result_has_captioned_video(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    captioned = result.get("captioned_video")
    if not isinstance(captioned, dict):
        return False
    return bool(str(captioned.get("source_url") or captioned.get("open_url") or captioned.get("path") or "").strip())


def _job_status_response(job: Dict[str, Any], *, include_full: bool) -> Dict[str, Any]:
    stored_status = (job.get("status") or "").strip()
    st = stored_status
    post_status = (job.get("post_status") or "").strip()
    post_stage = (job.get("post_stage") or "").strip()
    result = job.get("result")
    requires_caption = _local_bestseller_caption_required(job)
    caption_ready = _result_has_captioned_video(result)
    post_error = str(job.get("post_error") or "").strip()
    if requires_caption and stored_status == "completed" and not caption_ready:
        if post_status == "failed" or post_stage == "caption_failed":
            st = "failed"
        else:
            st = "running"
    out: Dict[str, Any] = {
        "ok": True,
        "job_id": job.get("job_id"),
        "status": st,
        "post_status": job.get("post_status"),
        "post_stage": job.get("post_stage"),
        "post_error": post_error or None,
        "requires_caption": requires_caption,
        "caption_ready": caption_ready,
        "auto_save": job.get("auto_save"),
        "created_at_ts": job.get("created_at_ts"),
        "updated_at_ts": job.get("updated_at_ts"),
    }
    job_out = job.get("job_output_dir") or ""
    prog = read_manifest_progress(str(job_out))
    if prog:
        out["progress"] = _redact_progress_for_client(prog)
        out["progress_percent"] = prog.get("progress_percent")
        out["progress_label"] = prog.get("progress_label")
        out["progress_detail"] = prog.get("progress_detail")
    if requires_caption and not caption_ready and st == "running":
        out["progress_label"] = "字幕合成中"
        out["progress_detail"] = "视频已生成，正在合成上屏字幕"
    if st == "failed":
        out["error"] = post_error or job.get("error")
        if include_full and result is not None:
            out["result"] = result
    if st == "completed":
        if include_full:
            out["result"] = result
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
    limit: int = 12,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
):
    rows = list_jobs_for_user(int(current_user.id), limit=limit)
    return {
        "ok": True,
        "items": [_recent_job_summary(job, request=request) for job in rows],
    }

