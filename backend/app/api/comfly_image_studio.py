import json
import logging
import asyncio
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pathlib import Path
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..services.creative_job_cloud_sync import sync_creative_job_to_cloud
from ..services.comfly_image_studio_job_store import create_job_record, get_job, update_job
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

logger = logging.getLogger(__name__)
router = APIRouter()
_EXAMPLE_SOURCE_PATH = Path(__file__).resolve().parents[3] / "static" / "data" / "comfly-image-studio-examples.json"

_ASPECT_TO_SIZE = {
    "1:1": "1024x1024",
    "3:2": "1536x1024",
    "16:9": "1920x1080",
    "2:3": "1024x1536",
    "9:16": "1080x1920",
}


def _normalize_image_aspect_ratio(value: str) -> str:
    ratio = str(value or "").strip()
    if ratio in _ASPECT_TO_SIZE:
        return ratio
    return "1:1"

_FALLBACK_EXAMPLES = [
    {
        "id": 1050,
        "title": "3D 彩墙人物",
        "prompt": "A stylized 3D animated young woman leaning against a textured abstract wall made of layered cracked paint panels in warm yellow, coral, pink and muted purple gradients. Soft cinematic lighting, warm pastel palette, painterly textures, dreamy atmosphere, Pixar-style 3D illustration, ultra detailed.",
        "prompt_zh": "一位风格化 3D 动画年轻女性倚靠在抽象彩色墙面前，墙面由层叠龟裂的颜料板组成，带有暖黄色、珊瑚粉、粉色和柔和紫色渐变。柔和电影光，暖 pastel 色调，绘画质感，梦幻氛围，皮克斯风 3D 插画，细节丰富。",
        "cover_image": "https://raw.githubusercontent.com/songguoxs/gpt4o-image-prompts/master/images/1050.jpeg",
        "model": "gpt-image-2",
        "tags": ["3D", "插画", "暖色"],
    },
    {
        "id": 1049,
        "title": "角色设定草图",
        "prompt": "Character sheet sketch of a subject, featuring multiple angles and expressive facial variations, drawn in pencil and ballpoint pen on a clean white background. Soft pastel palette, sharp linework, hand-drawn manga style, clear design-sheet composition.",
        "prompt_zh": "角色设定草图，同一个主体的多角度视图和丰富表情变化，铅笔与圆珠笔手绘质感，干净白底，柔和 pastel 色彩，清晰利落线条，手绘漫画风，明确的设定图构图。",
        "cover_image": "https://raw.githubusercontent.com/songguoxs/gpt4o-image-prompts/master/images/1049.jpeg",
        "model": "gpt-image-2",
        "tags": ["角色", "线稿", "设定"],
    },
]


def _load_example_rows() -> List[Dict[str, Any]]:
    try:
        if _EXAMPLE_SOURCE_PATH.exists():
            raw = _EXAMPLE_SOURCE_PATH.read_text(encoding="utf-8")
            rows = json.loads(raw)
            if isinstance(rows, list) and rows:
                return [item for item in rows if isinstance(item, dict)]
    except Exception:
        logger.warning("[comfly_image_studio] failed to load examples from %s", _EXAMPLE_SOURCE_PATH, exc_info=True)
    return list(_FALLBACK_EXAMPLES)


def _normalize_example_row(row: Dict[str, Any]) -> Dict[str, Any]:
    prompt = str(row.get("prompt") or "").strip()
    prompt_en = str(row.get("prompt_en") or "").strip()
    prompt_zh = str(row.get("prompt_zh") or "").strip()
    tags = row.get("tags")
    normalized_tags = []
    if isinstance(tags, list):
        normalized_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
    return {
        "id": row.get("id"),
        "title": str(row.get("title") or row.get("name") or "未命名示例").strip(),
        "prompt": prompt_zh or prompt or prompt_en,
        "prompt_en": prompt_en,
        "prompt_zh": prompt_zh,
        "cover_image": str(row.get("cover_image") or "").strip(),
        "model": str(row.get("model") or "gpt-image-2").strip() or "gpt-image-2",
        "tags": normalized_tags[:6],
    }


@router.get("/api/comfly-image-studio/examples")
async def comfly_image_studio_examples(
    offset: int = Query(0, ge=0),
    limit: int = Query(24, ge=1, le=120),
    _: _ServerUser = Depends(get_current_user_media_edit),
):
    rows = [_normalize_example_row(row) for row in _load_example_rows()]
    total = len(rows)
    sliced = rows[offset : offset + limit]
    return {
        "ok": True,
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": sliced,
        "has_more": offset + len(sliced) < total,
    }


def _response_preview(item: Dict[str, Any]) -> Dict[str, str]:
    url = str(item.get("url") or "").strip()
    b64_json = str(item.get("b64_json") or "").strip()
    data_url = ""
    if b64_json:
        payload = b64_json.split(",", 1)[-1] if b64_json.startswith("data:image") else b64_json
        data_url = f"data:image/png;base64,{payload}"
    return {
        "url": url,
        "data_url": data_url,
    }


def _pick_error_detail(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("error") or payload.get("message")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        if isinstance(detail, dict):
            inner = detail.get("message") or detail.get("detail")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    text = (resp.text or "").strip()
    return text[:800] if text else f"上游接口请求失败：HTTP {resp.status_code}"


def _comfly_endpoint(api_base: str, path: str) -> str:
    base = (api_base or "").strip().rstrip("/")
    if base.lower().endswith("/v1") and path.startswith("/v1/"):
        base = base[:-3].rstrip("/")
    return f"{base}{path}"


def _status_response(job: Dict[str, Any]) -> Dict[str, Any]:
    st = str(job.get("status") or "").strip()
    out: Dict[str, Any] = {
        "ok": st != "failed",
        "job_id": job.get("job_id"),
        "status": st,
        "stage": job.get("stage"),
        "created_at_ts": job.get("created_at_ts"),
        "updated_at_ts": job.get("updated_at_ts"),
    }
    if st == "completed":
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        out.update(result)
        out["saved_assets"] = job.get("saved_assets") or result.get("saved_assets") or []
    elif st == "failed":
        out["error"] = job.get("error") or "图片生成失败"
    return out


async def _generate_image_studio_core(
    *,
    request: Request,
    current_user: _ServerUser,
    db: Session,
    prompt: str,
    model: str,
    aspect_ratio: str,
    quality: str,
    background: str,
    upload_payloads: List[Dict[str, Any]],
    reference_image_urls: List[str] | None = None,
    auto_save: bool = True,
) -> Dict[str, Any]:
    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="请输入图片提示词")

    normalized_ratio = _normalize_image_aspect_ratio(aspect_ratio)
    size = _ASPECT_TO_SIZE.get(normalized_ratio, "1024x1024")
    api_base, api_key = _resolve_comfly_credentials(current_user.id, db, request)
    model_id = (model or "gpt-image-2").strip() or "gpt-image-2"
    quality_id = (quality or "high").strip() or "high"

    body = {
        "prompt": prompt,
        "model": model_id,
        "n": 1,
        "quality": quality_id,
        "size": size,
        "response_format": "url",
    }
    # gpt-image-2 目前会经过不同代理/渠道，部分渠道认像素 size，部分更稳定地认比例枚举。
    # 三个字段一起带，避免 9:16 在某一层被默认回退成 1:1。
    if "gpt-image-2" in model_id or "gptimage2" in model_id.replace("-", ""):
        body["aspect_ratio"] = normalized_ratio
        body["image_size"] = normalized_ratio
    if background and background != "auto":
        body["background"] = background
    refs = [
        str(url or "").strip()
        for url in (reference_image_urls or [])
        if str(url or "").strip().startswith(("http://", "https://"))
    ][:12]
    if refs:
        body["image"] = refs

    files: List[tuple[str, tuple[str, bytes, str]]] = []
    for upload in upload_payloads:
        raw = upload.get("bytes")
        if not isinstance(raw, (bytes, bytearray)) or not raw:
            continue
        files.append(
            (
                "image",
                (
                    str(upload.get("filename") or "reference.png"),
                    bytes(raw),
                    str(upload.get("content_type") or "image/png").strip() or "image/png",
                ),
            )
        )

    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            if files:
                url = _comfly_endpoint(api_base, "/v1/images/edits")
                data = {k: str(v) for k, v in body.items() if k != "response_format"}
                if refs:
                    data["image"] = json.dumps(refs, ensure_ascii=False)
                resp = await client.post(url, headers=headers, data=data, files=files)
            else:
                url = _comfly_endpoint(api_base, "/v1/images/generations")
                resp = await client.post(url, headers={**headers, "Content-Type": "application/json"}, json=body)
    except httpx.TimeoutException as exc:
        logger.warning("[comfly_image_studio] timeout user_id=%s", current_user.id)
        raise HTTPException(status_code=504, detail="图片生成超时，请稍后重试") from exc
    except Exception as exc:
        logger.exception("[comfly_image_studio] request failed user_id=%s", current_user.id)
        raise HTTPException(status_code=500, detail=f"图片生成请求失败: {exc}") from exc

    if resp.status_code >= 400:
        detail = _pick_error_detail(resp)
        logger.warning(
            "[comfly_image_studio] upstream reject user_id=%s status=%s detail=%s",
            current_user.id,
            resp.status_code,
            detail,
        )
        raise HTTPException(status_code=resp.status_code, detail=detail)

    try:
        payload = resp.json()
    except Exception as exc:
        logger.exception("[comfly_image_studio] invalid json user_id=%s", current_user.id)
        raise HTTPException(status_code=502, detail="图片生成返回格式异常") from exc

    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=502, detail="图片生成成功，但没有返回图片结果")

    previews = [_response_preview(item) for item in rows if isinstance(item, dict)]
    previews = [item for item in previews if item.get("url") or item.get("data_url")]
    if not previews:
        raise HTTPException(status_code=502, detail="图片生成成功，但结果暂时不可预览")

    saved_assets = []
    if auto_save:
        saved_assets = await _save_image_studio_results(
            previews=previews,
            request=request,
            current_user=current_user,
            prompt=prompt,
            model=model_id,
        )

    return {
        "ok": True,
        "images": previews,
        "saved_assets": saved_assets,
        "meta": {
            "model": model_id,
            "aspect_ratio": normalized_ratio,
            "size": size,
            "quality": quality_id,
            "reference_count": len(files) + len(refs),
            "reference_url_count": len(refs),
        },
    }


async def _run_image_studio_job(
    *,
    job_id: str,
    user_id: int,
    token: str,
    install_id: str,
    payload: Dict[str, Any],
    upload_payloads: List[Dict[str, Any]],
) -> None:
    from starlette.datastructures import Headers

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/comfly-image-studio/generate/start",
        "headers": [],
        "scheme": "http",
        "server": ("127.0.0.1", 80),
        "client": ("127.0.0.1", 0),
    }
    headers = {"authorization": f"Bearer {token}"}
    if install_id:
        headers["x-installation-id"] = install_id
    request = Request({**scope, "headers": Headers(headers).raw})
    current_user = _ServerUser(id=user_id)
    update_job(job_id, status="running", stage="generating")
    auth_header = f"Bearer {token}" if token else ""
    await sync_creative_job_to_cloud(
        auth_header=auth_header,
        installation_id=install_id,
        job_id=job_id,
        feature_type="image_studio",
        provider="comfly",
        status="running",
        stage="generating",
        title="图片任务",
        prompt=str(payload.get("prompt") or ""),
        request_payload=payload,
        meta={"reference_count": len(upload_payloads or [])},
    )
    db = SessionLocal()
    try:
        result = await _generate_image_studio_core(
            request=request,
            current_user=current_user,
            db=db,
            prompt=str(payload.get("prompt") or ""),
            model=str(payload.get("model") or "gpt-image-2"),
            aspect_ratio=str(payload.get("aspect_ratio") or "1:1"),
            quality=str(payload.get("quality") or "high"),
            background=str(payload.get("background") or "auto"),
            upload_payloads=upload_payloads,
            reference_image_urls=list(payload.get("reference_image_urls") or []),
        )
        update_job(
            job_id,
            status="completed",
            stage="completed",
            result=result,
            saved_assets=result.get("saved_assets") or [],
            error=None,
        )
        await sync_creative_job_to_cloud(
            auth_header=auth_header,
            installation_id=install_id,
            job_id=job_id,
            feature_type="image_studio",
            provider="comfly",
            status="completed",
            stage="completed",
            title="图片任务",
            prompt=str(payload.get("prompt") or ""),
            request_payload=payload,
            result_payload=result,
            saved_assets=result.get("saved_assets") or [],
            meta=result.get("meta") if isinstance(result.get("meta"), dict) else {},
        )
    except HTTPException as exc:
        error = str(exc.detail or "")[:2000]
        update_job(job_id, status="failed", stage="failed", error=error)
        await sync_creative_job_to_cloud(
            auth_header=auth_header,
            installation_id=install_id,
            job_id=job_id,
            feature_type="image_studio",
            provider="comfly",
            status="failed",
            stage="failed",
            title="图片任务",
            prompt=str(payload.get("prompt") or ""),
            request_payload=payload,
            error=error,
        )
    except Exception as exc:
        logger.exception("[comfly_image_studio] async job failed job_id=%s", job_id)
        error = str(exc)[:2000]
        update_job(job_id, status="failed", stage="failed", error=error)
        await sync_creative_job_to_cloud(
            auth_header=auth_header,
            installation_id=install_id,
            job_id=job_id,
            feature_type="image_studio",
            provider="comfly",
            status="failed",
            stage="failed",
            title="图片任务",
            prompt=str(payload.get("prompt") or ""),
            request_payload=payload,
            error=error,
        )
    finally:
        db.close()


async def _upload_files_to_payloads(images: List[UploadFile]) -> List[Dict[str, Any]]:
    uploads: List[Dict[str, Any]] = []
    for upload in [item for item in (images or []) if item and (item.filename or "").strip()]:
        raw = await upload.read()
        if not raw:
            continue
        uploads.append(
            {
                "filename": upload.filename or "reference.png",
                "content_type": (upload.content_type or "image/png").strip() or "image/png",
                "bytes": raw,
            }
        )
    return uploads


def _parse_reference_image_urls(raw: str) -> List[str]:
    urls: List[str] = []
    for part in str(raw or "").replace("\n", ",").split(","):
        url = part.strip()
        if url.startswith(("http://", "https://")) and url not in urls:
            urls.append(url)
    return urls[:12]


async def _save_image_studio_results(
    *,
    previews: List[Dict[str, str]],
    request: Request,
    current_user: _ServerUser,
    prompt: str,
    model: str,
) -> List[Dict[str, Any]]:
    saved: List[Dict[str, Any]] = []
    for index, item in enumerate(previews):
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        body = SaveAssetReq(
            url=url,
            media_type="image",
            tags="auto,comfly.image_studio",
            prompt=prompt[:500] if prompt else None,
            model=(model or "")[:128] or None,
        )
        try:
            effective = await _resolve_v3_tasks_url_for_download(body.url, "image", current_user, request=request)
            base_dk = _compute_save_url_dedupe_key(body.url, effective, body.dedupe_hint_url)
            dk = _final_save_url_dedupe_key(
                base_dk,
                body.generation_task_id,
                dedupe_hint_url=body.dedupe_hint_url,
                body_url=body.url,
            )
            async with _save_url_lock_for(current_user.id, dk):
                row = await _save_asset_from_url_locked(
                    dk,
                    body,
                    request,
                    current_user,
                    effective_url_resolved=effective,
                )
            item["asset_id"] = str(row.get("asset_id") or "")
            item["source_url"] = str(row.get("source_url") or "")
            saved.append({"index": index, "source_url": url, "asset": row})
            logger.info(
                "[comfly_image_studio] save-url ok user_id=%s index=%s asset_id=%s",
                current_user.id,
                index,
                row.get("asset_id"),
            )
        except Exception:
            logger.warning(
                "[comfly_image_studio] save-url failed user_id=%s index=%s url=%s",
                current_user.id,
                index,
                url[:120],
                exc_info=True,
            )
    return saved


@router.post("/api/comfly-image-studio/generate")
async def comfly_image_studio_generate(
    request: Request,
    prompt: str = Form(""),
    model: str = Form("gpt-image-2"),
    aspect_ratio: str = Form("1:1"),
    quality: str = Form("high"),
    background: str = Form("auto"),
    reference_image_urls: str = Form(""),
    images: List[UploadFile] = File(None),
    current_user: _ServerUser = Depends(get_current_user_media_edit),
    db: Session = Depends(get_db),
):
    upload_payloads = await _upload_files_to_payloads(images or [])
    return await _generate_image_studio_core(
        request=request,
        current_user=current_user,
        db=db,
        prompt=prompt,
        model=model,
        aspect_ratio=aspect_ratio,
        quality=quality,
        background=background,
        upload_payloads=upload_payloads,
        reference_image_urls=_parse_reference_image_urls(reference_image_urls),
    )


@router.post("/api/comfly-image-studio/generate/start")
async def comfly_image_studio_generate_start(
    request: Request,
    prompt: str = Form(""),
    model: str = Form("gpt-image-2"),
    aspect_ratio: str = Form("1:1"),
    quality: str = Form("high"),
    background: str = Form("auto"),
    reference_image_urls: str = Form(""),
    images: List[UploadFile] = File(None),
    current_user: _ServerUser = Depends(get_current_user_media_edit),
):
    reference_urls = _parse_reference_image_urls(reference_image_urls)
    payload = {
        "prompt": (prompt or "").strip(),
        "model": (model or "gpt-image-2").strip() or "gpt-image-2",
        "aspect_ratio": (aspect_ratio or "1:1").strip() or "1:1",
        "quality": (quality or "high").strip() or "high",
        "background": (background or "auto").strip() or "auto",
        "reference_image_urls": reference_urls,
    }
    if not payload["prompt"]:
        raise HTTPException(status_code=400, detail="请输入图片提示词")
    upload_payloads = await _upload_files_to_payloads(images or [])
    job_id = create_job_record(user_id=current_user.id, payload=payload)
    auth = (request.headers.get("Authorization") or "").strip()
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else auth
    install_id = (request.headers.get("X-Installation-Id") or "").strip()
    asyncio.create_task(sync_creative_job_to_cloud(
        auth_header=auth,
        installation_id=install_id,
        job_id=job_id,
        feature_type="image_studio",
        provider="comfly",
        status="running",
        stage="queued",
        title="图片任务",
        prompt=str(payload.get("prompt") or ""),
        request_payload=payload,
        meta={"reference_count": len(upload_payloads or [])},
    ))

    async def _runner() -> None:
        await _run_image_studio_job(
            job_id=job_id,
            user_id=current_user.id,
            token=token,
            install_id=install_id,
            payload=payload,
            upload_payloads=upload_payloads,
        )

    task = asyncio.create_task(_runner())

    def _log_task_done(done: asyncio.Task) -> None:
        try:
            _ = done.exception()
        except asyncio.CancelledError:
            pass

    task.add_done_callback(_log_task_done)
    return {
        "ok": True,
        "async": True,
        "job_id": job_id,
        "status": "running",
        "poll_path": f"/api/comfly-image-studio/jobs/{job_id}",
    }


@router.get("/api/comfly-image-studio/jobs/{job_id}")
async def comfly_image_studio_job_status(
    job_id: str,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if int(job.get("user_id") or -1) != int(current_user.id):
        raise HTTPException(status_code=403, detail="无权查看该任务")
    return _status_response(job)
