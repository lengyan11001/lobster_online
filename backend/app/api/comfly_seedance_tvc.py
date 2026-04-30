from __future__ import annotations

import asyncio
import re
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..services.comfly_seedance_tvc_job_store import (
    create_job_record,
    get_job,
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
    SaveAssetReq,
    _compute_save_url_dedupe_key,
    _final_save_url_dedupe_key,
    _resolve_v3_tasks_url_for_download,
    _save_asset_from_url_locked,
    _save_url_lock_for,
)
from .auth import _ServerUser, get_current_user_media_edit

router = APIRouter()


class ComflySeedancePipelinePayload(BaseModel):
    asset_id: Optional[str] = Field(None, description="主参考图素材 ID，与 image_url 二选一")
    image_url: Optional[str] = Field(None, description="主参考图公网 URL，与 asset_id 二选一")
    reference_asset_ids: List[str] = Field(default_factory=list, description="额外参考图素材 ID 列表")
    reference_image_urls: List[str] = Field(default_factory=list, description="额外参考图公网 URL 列表")
    merge_clips: bool = Field(True, description="最终始终会合并所有视频段；该字段保留仅为兼容旧调用")
    storyboard_count: Optional[int] = Field(None, ge=1, le=6, description="兼容旧字段；若传入，将按 storyboard_count * 10 秒推导总时长")
    segment_count: Optional[int] = Field(None, ge=1, le=6, description="兼容旧字段；若传入，必须与 total_duration_seconds / 10 一致")
    segment_duration_seconds: Optional[int] = Field(None, description="每段时长固定为 10 秒；该字段若传入必须是 10")
    total_duration_seconds: Optional[int] = Field(None, description="总时长仅支持 10/20/30/40/50/60 秒，默认 20 秒")
    auto_save: bool = Field(True, description="完成后自动入库")
    task_text: str = Field("", description="补充任务说明")
    platform: str = ""
    country: str = ""
    language: str = ""
    output_dir: Optional[str] = None
    isolate_job_dir: bool = True
    analysis_model: Optional[str] = None
    image_model: Optional[str] = None
    video_model: Optional[str] = None
    aspect_ratio: str = "9:16"
    generate_audio: bool = True
    watermark: bool = False


class ComflySeedanceRunBody(BaseModel):
    payload: ComflySeedancePipelinePayload


def _default_runs_root() -> str:
    return str(Path(__file__).resolve().parents[3] / "skills" / "comfly_seedance_tvc_video" / "runs")


def _validate_payload(pl: ComflySeedancePipelinePayload) -> None:
    if bool(pl.asset_id and pl.image_url):
        raise HTTPException(status_code=400, detail="asset_id 与 image_url 请勿同时传")
    if not pl.asset_id and not pl.image_url:
        raise HTTPException(status_code=400, detail="请提供 asset_id 或 image_url")
    if pl.segment_duration_seconds is not None and int(pl.segment_duration_seconds) != 10:
        raise HTTPException(status_code=400, detail="segment_duration_seconds 目前固定为 10 秒")

    requested_count = pl.segment_count if pl.segment_count is not None else pl.storyboard_count
    requested_total = pl.total_duration_seconds
    if requested_total is None and requested_count is not None:
        requested_total = int(requested_count) * 10

    if requested_total is not None and int(requested_total) not in {10, 20, 30, 40, 50, 60}:
        raise HTTPException(status_code=400, detail="total_duration_seconds 仅支持 10/20/30/40/50/60 秒")
    if requested_count is not None and int(requested_count) * 10 != int(requested_total or 20):
        raise HTTPException(status_code=400, detail="segment_count/storyboard_count 必须与 total_duration_seconds / 10 一致")


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
    _ = _api_base_for_pipeline(api_base)
    return build_pipeline_input(
        reference_image=reference_images[0],
        reference_images=reference_images,
        api_key=api_key,
        api_base=api_base,
        merge_clips=pl.merge_clips,
        storyboard_count=pl.storyboard_count,
        segment_count=pl.segment_count,
        segment_duration_seconds=pl.segment_duration_seconds,
        total_duration_seconds=pl.total_duration_seconds,
        output_dir=effective_output_dir,
        platform=pl.platform,
        country=pl.country,
        language=pl.language,
        task_text=pl.task_text,
        analysis_model=pl.analysis_model,
        image_model=pl.image_model,
        video_model=pl.video_model,
        aspect_ratio=pl.aspect_ratio,
        generate_audio=pl.generate_audio,
        watermark=pl.watermark,
    )


async def _save_pipeline_videos(
    *,
    urls: List[tuple],
    request: Optional[Request],
    current_user: _ServerUser,
    video_model: str,
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
        saved.append({"source_url": url, "task_id": task_id, "asset": row})
    return saved


def _video_model_from_result(result: Dict[str, Any]) -> str:
    cfg = result.get("config") if isinstance(result.get("config"), dict) else {}
    return str(cfg.get("video_model") or "") if isinstance(cfg, dict) else ""


async def _seedance_job_runner(job_id: str) -> None:
    job = get_job(job_id)
    if not job:
        return
    inp = deepcopy(job.get("inp") or {})
    auto_save = bool(job.get("auto_save"))
    user_id = int(job.get("user_id") or 0)
    try:
        result = await asyncio.to_thread(run_storyboard_pipeline_sync, inp)
    except Exception as e:
        update_job(job_id, status="failed", error=str(e)[:2000])
        return

    saved_assets: List[Dict[str, Any]] = []
    if auto_save:
        pairs = collect_video_urls_from_pipeline_result(result)
        if pairs:
            db = SessionLocal()
            try:
                saved_assets = await _save_pipeline_videos(
                    urls=pairs,
                    request=None,
                    current_user=_ServerUser(id=user_id),
                    video_model=_video_model_from_result(result),
                )
            except HTTPException as he:
                detail = he.detail if isinstance(he.detail, str) else str(he.detail)
                update_job(job_id, status="failed", error=f"流水线成功但入库失败: {detail}", result=result)
                db.close()
                return
            finally:
                db.close()
    update_job(job_id, status="completed", error=None, result=result, saved_assets=saved_assets)


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
    out: Dict[str, Any] = {
        "ok": True,
        "job_id": job.get("job_id"),
        "status": st,
        "auto_save": job.get("auto_save"),
        "created_at_ts": job.get("created_at_ts"),
        "updated_at_ts": job.get("updated_at_ts"),
    }
    job_out = job.get("job_output_dir") or ""
    if st == "running":
        prog = read_manifest_progress(str(job_out))
        if prog:
            out["progress"] = _redact_progress_for_client(prog)
    if st == "failed":
        out["error"] = job.get("error")
        if include_full and job.get("result") is not None:
            out["result"] = job.get("result")
    if st == "completed":
        if include_full:
            out["result"] = job.get("result")
            out["saved_assets"] = job.get("saved_assets") or []
        prog = read_manifest_progress(str(job_out))
        if prog:
            out["progress"] = _redact_progress_for_client(prog)
    return out


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
    create_job_record(
        user_id=current_user.id,
        inp=inp,
        auto_save=pl.auto_save,
        job_output_dir=effective_dir,
        job_id=job_id,
    )

    def _log_task_done(task: asyncio.Task) -> None:
        try:
            _ = task.exception()
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(_seedance_job_runner(job_id))
    task.add_done_callback(_log_task_done)
    return {"ok": True, "async": True, "job_id": job_id, "poll_path": f"/api/comfly-seedance-tvc/pipeline/jobs/{job_id}"}


@router.get("/api/comfly-seedance-tvc/pipeline/jobs/{job_id}")
async def comfly_seedance_pipeline_job_status(
    job_id: str,
    compact: bool = False,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if int(job.get("user_id") or -1) != int(current_user.id):
        raise HTTPException(status_code=403, detail="无权查看该任务")
    return _job_status_response(job, include_full=not compact)
