"""电商详情图流水线 API：商品图 -> 多张详情页 -> 长图 -> 素材入库。"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..models import Asset
from ..services.comfly_ecommerce_detail_job_store import (
    create_job_record,
    get_job,
    read_manifest_progress,
    update_job,
)
from ..services.comfly_ecommerce_detail_pipeline_runner import (
    build_pipeline_input,
    resolve_public_image_for_pipeline,
    resolve_reference_images_for_pipeline,
    run_pipeline_sync,
)
from ..services.comfly_veo_exec import _resolve_comfly_credentials
from .assets import _save_bytes_or_tos
from .auth import _ServerUser, get_current_user_media_edit

logger = logging.getLogger(__name__)
router = APIRouter()


class EcommerceDetailPipelinePayload(BaseModel):
    asset_id: Optional[str] = Field(None, description="商品主图素材 ID，与 image_url 二选一")
    image_url: Optional[str] = Field(None, description="商品主图公网 URL，与 asset_id 二选一")
    reference_asset_ids: List[str] = Field(default_factory=list, description="补充参考图素材 ID")
    reference_image_urls: List[str] = Field(default_factory=list, description="补充参考图公网 URL")
    page_count: Optional[int] = Field(12, ge=10, le=16)
    auto_save: bool = True
    platform: str = ""
    country: str = ""
    language: str = ""
    analysis_model: Optional[str] = None
    image_model: Optional[str] = None
    output_dir: Optional[str] = None
    isolate_job_dir: bool = True


class EcommerceDetailRunBody(BaseModel):
    payload: EcommerceDetailPipelinePayload


def _default_runs_root() -> str:
    return str(Path(__file__).resolve().parents[3] / "skills" / "comfly_ecommerce_detail" / "runs")


def _validate_payload(pl: EcommerceDetailPipelinePayload) -> None:
    if bool(pl.asset_id and pl.image_url):
        raise HTTPException(status_code=400, detail="asset_id 和 image_url 不能同时传")
    if not pl.asset_id and not pl.image_url:
        raise HTTPException(status_code=400, detail="请提供 asset_id 或 image_url")


async def _prepare_pipeline_input(
    *,
    pl: EcommerceDetailPipelinePayload,
    current_user: _ServerUser,
    db: Session,
    request: Request,
    effective_output_dir: str,
) -> Dict[str, object]:
    product_image = resolve_public_image_for_pipeline(
        user_id=current_user.id,
        db=db,
        request=request,
        asset_id=pl.asset_id,
        image_url=pl.image_url,
    )
    reference_images = resolve_reference_images_for_pipeline(
        user_id=current_user.id,
        db=db,
        request=request,
        asset_ids=pl.reference_asset_ids,
        image_urls=pl.reference_image_urls,
    )
    api_base, api_key = _resolve_comfly_credentials(current_user.id, db)
    return build_pipeline_input(
        product_image=product_image,
        reference_images=reference_images,
        api_key=api_key,
        api_base=api_base,
        analysis_model=pl.analysis_model,
        image_model=pl.image_model,
        page_count=pl.page_count,
        output_dir=effective_output_dir,
        platform=pl.platform,
        country=pl.country,
        language=pl.language,
    )


def _save_local_image_asset(
    *,
    local_path: str,
    user_id: int,
    db: Session,
    prompt: str,
    model: str,
    tags: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    path = Path((local_path or "").strip())
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    aid, fname, fsize, tos_url = _save_bytes_or_tos(raw, path.suffix.lower() or ".png", "image/png")
    source_url = (tos_url or "").strip() or ""
    asset = Asset(
        asset_id=aid,
        user_id=user_id,
        filename=fname,
        media_type="image",
        file_size=fsize,
        source_url=source_url or None,
        prompt=prompt[:2000],
        model=(model or "")[:128] or None,
        tags=tags[:500],
        meta=meta or {},
    )
    db.add(asset)
    db.commit()
    return {
        "asset_id": aid,
        "filename": fname,
        "media_type": "image",
        "file_size": fsize,
        "source_url": source_url,
    }


def _save_pipeline_images(*, result: Dict[str, Any], user_id: int, db: Session) -> Dict[str, Any]:
    image_model = str((result.get("config") or {}).get("image_model") or "")
    saved_pages: List[Dict[str, Any]] = []
    for page in result.get("page_results") or []:
        if not isinstance(page, dict):
            continue
        asset_row = _save_local_image_asset(
            local_path=str(page.get("local_path") or ""),
            user_id=user_id,
            db=db,
            prompt=str(page.get("title") or page.get("slot") or "详情页"),
            model=image_model,
            tags="auto,comfly.ecommerce.detail_pipeline,page",
            meta={
                "origin": "comfly_ecommerce_detail_page",
                "slot": page.get("slot"),
                "page_index": page.get("index"),
            },
        )
        if asset_row:
            saved_pages.append(
                {
                    "index": int(page.get("index") or 0),
                    "slot": str(page.get("slot") or ""),
                    "asset": asset_row,
                }
            )
    final_info = result.get("final_long_image") if isinstance(result.get("final_long_image"), dict) else {}
    final_asset = _save_local_image_asset(
        local_path=str(final_info.get("path") or ""),
        user_id=user_id,
        db=db,
        prompt="电商详情长图",
        model=image_model,
        tags="auto,comfly.ecommerce.detail_pipeline,long_image",
        meta={
            "origin": "comfly_ecommerce_detail_long_image",
            "page_count": final_info.get("page_count"),
        },
    )
    return {"pages": saved_pages, "final": {"asset": final_asset} if final_asset else None}


async def _job_runner(job_id: str) -> None:
    job = get_job(job_id)
    if not job:
        return
    inp = deepcopy(job.get("inp") or {})
    auto_save = bool(job.get("auto_save"))
    user_id = int(job.get("user_id") or 0)
    try:
        result = await asyncio.to_thread(run_pipeline_sync, inp)
    except Exception as e:
        logger.exception("[comfly_ecommerce_detail] job %s failed", job_id[:12])
        update_job(job_id, status="failed", error=str(e)[:2000])
        return
    saved_assets: Dict[str, Any] = {"pages": [], "final": None}
    if auto_save:
        db = SessionLocal()
        try:
            saved_assets = _save_pipeline_images(result=result, user_id=user_id, db=db)
        except Exception:
            logger.exception("[comfly_ecommerce_detail] job %s auto_save failed", job_id[:12])
            update_job(job_id, status="failed", error="流水线执行成功，但素材入库失败", result=result)
            db.close()
            return
        finally:
            db.close()
    update_job(job_id, status="completed", error=None, result=result, saved_assets=saved_assets)


def _redact_progress_for_client(progress: Any) -> Any:
    if not isinstance(progress, dict):
        return progress
    red = {k: v for k, v in progress.items() if k not in ("manifest_file", "run_dir")}
    last_steps = red.get("last_steps")
    if isinstance(last_steps, list):
        cleaned = []
        for item in last_steps:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            err = row.get("error")
            if isinstance(err, str) and err.strip():
                row["error"] = re.sub(
                    r"(?:[A-Za-z]:[/\\][^\s\"'<>|]{2,320}|(?:\\\\|/)[^\s\"'<>|]{0,320}(?:[/\\](?:skills|job_runs|runs)[/\\]|\.py\b)[^\s\"'<>|]{0,320})",
                    "...",
                    err.strip(),
                    flags=re.IGNORECASE,
                )[:400]
            cleaned.append(row)
        red["last_steps"] = cleaned
    return red


def _job_status_response(job: Dict[str, Any], *, include_full: bool) -> Dict[str, Any]:
    status = (job.get("status") or "").strip()
    out: Dict[str, Any] = {
        "ok": True,
        "job_id": job.get("job_id"),
        "status": status,
        "auto_save": job.get("auto_save"),
        "created_at_ts": job.get("created_at_ts"),
        "updated_at_ts": job.get("updated_at_ts"),
    }
    if status == "running":
        progress = read_manifest_progress(str(job.get("job_output_dir") or ""))
        if progress:
            out["progress"] = _redact_progress_for_client(progress)
    if status == "failed":
        out["error"] = job.get("error")
        if include_full and job.get("result") is not None:
            out["result"] = job.get("result")
    if status == "completed":
        if include_full:
            out["result"] = job.get("result")
            out["saved_assets"] = job.get("saved_assets") or {}
        progress = read_manifest_progress(str(job.get("job_output_dir") or ""))
        if progress:
            out["progress"] = _redact_progress_for_client(progress)
    return out


@router.post("/api/comfly-ecommerce-detail/pipeline/run")
async def ecommerce_detail_pipeline_run(
    body: EcommerceDetailRunBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
    db: Session = Depends(get_db),
):
    pl = body.payload
    _validate_payload(pl)
    inp = await _prepare_pipeline_input(
        pl=pl,
        current_user=current_user,
        db=db,
        request=request,
        effective_output_dir=(pl.output_dir or "").strip() or _default_runs_root(),
    )
    try:
        result = await asyncio.to_thread(run_pipeline_sync, inp)
    except Exception as e:
        logger.exception("[comfly_ecommerce_detail] pipeline failed user_id=%s", current_user.id)
        raise HTTPException(status_code=500, detail=str(e)[:2000]) from e
    saved_assets: Dict[str, Any] = {"pages": [], "final": None}
    if pl.auto_save:
        try:
            saved_assets = _save_pipeline_images(result=result, user_id=current_user.id, db=db)
        except Exception as e:
            logger.exception("[comfly_ecommerce_detail] auto_save failed")
            raise HTTPException(status_code=500, detail=f"流水线执行成功，但素材入库失败: {e}") from e
    return {"ok": True, "pipeline": "comfly_ecommerce_detail", "result": result, "saved_assets": saved_assets}


@router.post("/api/comfly-ecommerce-detail/pipeline/start")
async def ecommerce_detail_pipeline_start(
    body: EcommerceDetailRunBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
    db: Session = Depends(get_db),
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

    def _log_task(task: asyncio.Task) -> None:
        try:
            if task.exception() is not None:
                logger.exception("[comfly_ecommerce_detail] background job error job_id=%s", job_id[:12])
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(_job_runner(job_id))
    task.add_done_callback(_log_task)
    return {
        "ok": True,
        "async": True,
        "job_id": job_id,
        "poll_path": f"/api/comfly-ecommerce-detail/pipeline/jobs/{job_id}",
    }


@router.get("/api/comfly-ecommerce-detail/pipeline/jobs/{job_id}")
async def ecommerce_detail_pipeline_job_status(
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
