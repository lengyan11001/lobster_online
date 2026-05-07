from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import ipaddress
import math
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from PIL import Image

from ..core.config import settings
from ..db import SessionLocal, get_db
from ..models import Asset, UserComflyConfig
from ..services.viral_video_remix_job_store import create_job_record, get_job, update_job
from ..services.comfly_veo_exec import LOCAL_COMFLY_CONFIG_USER_ID
from .assets import (
    SaveAssetReq,
    _compute_save_url_dedupe_key,
    _final_save_url_dedupe_key,
    _resolve_v3_tasks_url_for_download,
    _save_asset_from_url_locked,
    _save_bytes_or_tos,
    _save_url_lock_for,
)
from .auth import _ServerUser, get_current_user_for_local

logger = logging.getLogger(__name__)
router = APIRouter()

_SUCCESS_STATUSES = {"succeeded", "success", "completed", "done"}
_FAILED_STATUSES = {"failed", "failure", "error", "cancelled", "canceled", "expired"}
_MAX_PARALLEL_SEGMENT_TASKS = 6
_UPLOADED_ASSET_CACHE: Dict[str, str] = {}
_VIDEO_DOWNLOAD_APPID = "682e90c19520118284FGb2"
_VIDEO_DOWNLOAD_API_URL = "https://watermark-api.hlyphp.top/Watermark/Index"


def _lobster_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _viral_remix_jobs_root() -> Path:
    root = _lobster_root() / "static" / "generated" / "viral_remix_jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _viral_remix_upload_cache_root() -> Path:
    root = _viral_remix_jobs_root() / "upload_cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _bundled_ffmpeg_exe() -> Optional[str]:
    p = _lobster_root() / "skills" / "comfly_veo3_daihuo_video" / "tools" / "ffmpeg" / "windows" / "ffmpeg.exe"
    if sys.platform == "win32" and p.is_file():
        return str(p)
    return shutil.which("ffmpeg")


def _bundled_ffprobe_exe() -> Optional[str]:
    p = _lobster_root() / "skills" / "comfly_veo3_daihuo_video" / "tools" / "ffmpeg" / "windows" / "ffprobe.exe"
    if sys.platform == "win32" and p.is_file():
        return str(p)
    return shutil.which("ffprobe")


def _is_local_request(request: Request) -> bool:
    host = (request.url.hostname or request.headers.get("host") or "").strip().lower()
    if host.startswith("[") and "]" in host:
        host = host[1:host.index("]")]
    if ":" in host and host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local)


def _bearer_token_from_request(request: Request) -> str:
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return auth


async def _resolve_viral_remix_user(request: Request) -> _ServerUser:
    """Local test mode: allow localhost/private-network testing without blocking on remote auth."""
    token = _bearer_token_from_request(request)
    auth_error: Optional[HTTPException] = None
    if token:
        try:
            return await get_current_user_for_local(request=request, token=token)
        except HTTPException as exc:
            auth_error = exc
            logger.info(
                "[viral_video_remix] auth unavailable, using local fallback when allowed status=%s detail=%s",
                exc.status_code,
                exc.detail,
            )
    if _is_local_request(request):
        return _ServerUser(id=LOCAL_COMFLY_CONFIG_USER_ID)
    if auth_error is not None:
        raise auth_error
    raise HTTPException(status_code=401, detail="无法验证凭证")


def _default_comfly_base() -> str:
    return ((settings.comfly_api_base or "").strip().rstrip("/")) or "https://ai.comfly.chat/v1"


def _endpoint(api_base: str, path: str) -> str:
    base = (api_base or "").strip().rstrip("/")
    if base.lower().endswith("/v1") and (path.startswith("/v1/") or path.startswith("/seedance/")):
        base = base[:-3].rstrip("/")
    return f"{base}{path}"


def _resolve_local_comfly_credentials(user_id: int, db: Session) -> tuple[str, str]:
    """Local test mode: use saved Comfly config or .env, without auth-server billing proxy."""
    ids = [int(user_id or 0), LOCAL_COMFLY_CONFIG_USER_ID]
    for uid in ids:
        row = db.query(UserComflyConfig).filter(UserComflyConfig.user_id == uid).first()
        if row and (row.api_key or "").strip():
            return ((row.api_base or "").strip().rstrip("/") or _default_comfly_base()), row.api_key.strip()
    api_key = (settings.comfly_api_key or "").strip()
    if api_key:
        return _default_comfly_base(), api_key
    raise HTTPException(
        status_code=503,
        detail="未配置本地 Comfly API Key。请先在技能商店配置 Comfly，或在 .env 设置 COMFLY_API_KEY。",
    )


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
            msg = detail.get("message") or detail.get("detail") or detail.get("error")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
    text = (resp.text or "").strip()
    return text[:800] if text else f"上游接口请求失败：HTTP {resp.status_code}"


def _image_preview(item: Dict[str, Any]) -> Dict[str, str]:
    url = str(item.get("url") or "").strip()
    b64_json = str(item.get("b64_json") or "").strip()
    data_url = ""
    if b64_json:
        payload = b64_json.split(",", 1)[-1] if b64_json.startswith("data:image") else b64_json
        data_url = f"data:image/png;base64,{payload}"
    return {"url": url, "data_url": data_url}


def _extract_file_upload_url(payload: Any) -> str:
    root = _as_dict(payload)
    data = _as_dict(root.get("data"))
    result = _as_dict(root.get("result"))
    return _first_str(
        root.get("url"),
        root.get("file_url"),
        root.get("source_url"),
        data.get("url"),
        data.get("file_url"),
        data.get("source_url"),
        result.get("url"),
        result.get("file_url"),
        result.get("source_url"),
    )


def _as_dict(obj: Any) -> Dict[str, Any]:
    return obj if isinstance(obj, dict) else {}


def _first_str(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _quote_public_url(url: str) -> str:
    """Encode non-ASCII path/query before sending URLs to upstream model APIs."""
    value = (url or "").strip()
    if not value.lower().startswith(("http://", "https://")):
        return value
    try:
        parts = urlsplit(value)
        path = quote(parts.path, safe="/%:@")
        query = quote(parts.query, safe="=&%:@,+")
        return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))
    except Exception:
        return value


def _safe_asset_suffix(filename: str, content_type: str) -> str:
    name = (filename or "").strip().lower()
    content = (content_type or "").strip().lower()
    if name.endswith(".mp4") or content == "video/mp4":
        return ".mp4"
    if name.endswith(".mov") or "quicktime" in content:
        return ".mov"
    if name.endswith(".webm") or "webm" in content:
        return ".webm"
    if name.endswith(".png") or content == "image/png":
        return ".png"
    if name.endswith((".jpg", ".jpeg")) or content == "image/jpeg":
        return ".jpg"
    return Path(name).suffix[:12] or ".bin"


def _remember_uploaded_asset(source_url: str, local_path: Path) -> None:
    if not source_url or not local_path.is_file():
        return
    value = source_url.strip()
    quoted = _quote_public_url(value)
    _UPLOADED_ASSET_CACHE[value] = str(local_path)
    _UPLOADED_ASSET_CACHE[quoted] = str(local_path)


def _cached_uploaded_asset_path(source_url: str) -> Optional[Path]:
    value = (source_url or "").strip()
    for key in (value, _quote_public_url(value)):
        cached = _UPLOADED_ASSET_CACHE.get(key)
        if cached:
            path = Path(cached)
            if path.is_file() and path.stat().st_size > 0:
                return path
    return None


async def _save_remote_result_to_asset(
    *,
    url: str,
    media_type: str,
    tags: str,
    prompt: str,
    model: str,
    user_id: int,
    request: Optional[Request] = None,
    generation_task_id: str = "",
) -> Optional[Dict[str, Any]]:
    value = _quote_public_url(url)
    if not value.lower().startswith(("http://", "https://")):
        return None
    body = SaveAssetReq(
        url=value,
        media_type=media_type,
        tags=tags,
        prompt=(prompt or "")[:2000] or None,
        model=(model or "")[:128] or None,
        generation_task_id=(generation_task_id or "")[:128] or None,
    )
    try:
        current_user = _ServerUser(id=int(user_id or 0))
        effective = await _resolve_v3_tasks_url_for_download(body.url, media_type, current_user, request=request)
        base_dk = _compute_save_url_dedupe_key(body.url, effective, body.dedupe_hint_url)
        dk = _final_save_url_dedupe_key(
            base_dk,
            body.generation_task_id,
            dedupe_hint_url=body.dedupe_hint_url,
            body_url=body.url,
        )
        async with _save_url_lock_for(current_user.id, dk):
            return await _save_asset_from_url_locked(dk, body, request, current_user, effective_url_resolved=effective)
    except Exception:
        logger.exception("[viral_video_remix] save remote result to asset failed url=%s", value[:240])
        return None


def _save_local_video_to_asset(
    *,
    path: Path,
    user_id: int,
    prompt: str,
    model: str,
    tags: str,
    meta: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except Exception:
        return None
    if not raw:
        return None
    try:
        aid, fname, fsize, tos_url = _save_bytes_or_tos(raw, ".mp4", "video/mp4")
        row = Asset(
            asset_id=aid,
            user_id=int(user_id or 0),
            filename=fname,
            media_type="video",
            file_size=fsize,
            source_url=(tos_url or "").strip() or None,
            prompt=(prompt or "")[:2000] or None,
            model=(model or "")[:128] or None,
            tags=tags,
            meta=meta,
        )
        db = SessionLocal()
        try:
            db.add(row)
            db.commit()
        finally:
            db.close()
        return {
            "asset_id": aid,
            "filename": fname,
            "media_type": "video",
            "file_size": fsize,
            "source_url": (tos_url or "").strip(),
        }
    except Exception:
        logger.exception("[viral_video_remix] save local video to asset failed path=%s", path)
        return None


def _extract_task_id(payload: Any) -> str:
    root = _as_dict(payload)
    data = _as_dict(root.get("data"))
    result = _as_dict(root.get("result"))
    return _first_str(
        root.get("id"),
        root.get("task_id"),
        root.get("taskId"),
        data.get("id"),
        data.get("task_id"),
        data.get("taskId"),
        result.get("id"),
        result.get("task_id"),
        result.get("taskId"),
    )


def _extract_video_url_from_obj(obj: Any) -> str:
    if isinstance(obj, str):
        value = obj.strip()
        if value.startswith(("http://", "https://")):
            return value
        return ""
    if isinstance(obj, list):
        for item in obj:
            found = _extract_video_url_from_obj(item)
            if found:
                return found
        return ""
    if not isinstance(obj, dict):
        return ""
    for key in ("video_url", "mp4url", "url", "output", "file_url"):
        found = _extract_video_url_from_obj(obj.get(key))
        if found:
            return found
    for key in ("content", "data", "result", "outputs", "videos"):
        found = _extract_video_url_from_obj(obj.get(key))
        if found:
            return found
    return ""


def _extract_seedance_poll(payload: Any) -> tuple[str, str, str]:
    root = _as_dict(payload)
    data = _as_dict(root.get("data"))
    result = _as_dict(root.get("result"))
    content = _as_dict(root.get("content"))
    status = _first_str(root.get("status"), data.get("status"), result.get("status")).lower()
    video_url = _extract_video_url_from_obj(content) or _extract_video_url_from_obj(data) or _extract_video_url_from_obj(result)
    error_detail = ""
    if status in _FAILED_STATUSES:
        error_detail = _first_str(root.get("error"), root.get("message"), data.get("error"), data.get("message"), result.get("error"), result.get("message"))
        if not error_detail:
            error_detail = json.dumps(payload, ensure_ascii=False)[:800]
    return status, video_url, error_detail


def _character_prompt(user_prompt: str, style: str) -> str:
    style_label = {
        "colored_pencil": "colored pencil illustration, light realistic character design sheet",
        "soft_illustration": "soft editorial illustration, clean character design sheet",
        "semi_realistic": "semi-realistic portrait reference sheet, gentle stylization",
    }.get((style or "").strip(), "colored pencil illustration, light realistic character design sheet")
    base = (
        "Create a single 2x2 character reference sheet of the same person. "
        "The four panels must show front face, left side profile, right side profile, and back or three-quarter back view. "
        "Keep the same face structure, hairstyle, age, expression temperament, skin tone, and outfit logic across all panels. "
        f"Use {style_label}. Clean light background, no text, no watermark, no logo, one person only. "
        "This image will be used as a consistent character reference for video generation."
    )
    if user_prompt:
        base += f" Additional direction: {user_prompt.strip()}"
    return base


def _product_reference_prompt(user_prompt: str) -> str:
    base = (
        "Create a clean product reference image from the uploaded product photo. "
        "Keep only the target product itself on a pure white studio background. "
        "Remove hands, people, tables, shelves, scene background, shadows that obscure the product, stickers, captions, watermarks, and unrelated objects. "
        "Preserve the product identity exactly: package shape, material, color, label layout, logo placement, cap, bottle/box proportions, and visible product markings. "
        "Do not invent a different product. Do not add promotional text. Center the product with enough margin, front-facing or slightly three-quarter if that best preserves the original product."
    )
    if user_prompt:
        base += f" Additional product note: {user_prompt.strip()}"
    return base


def _product_multiview_reference_prompt(user_prompt: str, source_count: int) -> str:
    if source_count <= 1:
        return _product_reference_prompt(user_prompt)
    base = (
        "Create a single clean multi-view product reference sheet from the uploaded product photos. "
        "All panels must show the exact same real product from the provided images, preserving the real geometry and product identity. "
        "Arrange the views into one square reference board with consistent framing, neutral white studio background, and no decorative design. "
        "Remove scene background, hands, props, table edges, captions, phone watermarks, camera metadata text, and unrelated objects. "
        "Preserve the product exactly: overall shape, thickness, corner radius, seam lines, ports, buttons, plug structure, texture, finish, logo placement, label details, and color. "
        "Do not redesign the product, do not stylize it, and do not invent missing features unless minimal completion is required for consistency between the provided views. "
        "No extra text, no watermark, no brand overlay, one product only."
    )
    if user_prompt:
        base += f" Additional product note: {user_prompt.strip()}"
    return base


def _build_product_reference_contact_sheet(
    sources: List[Tuple[str, bytes, str]],
    output_path: Path,
) -> None:
    if not sources:
        raise RuntimeError("no product reference sources for contact sheet")
    canv = Image.new("RGBA", (1024, 1024), (255, 255, 255, 255))
    cols = 2
    rows = max(1, int(math.ceil(len(sources) / float(cols))))
    gap = 28
    outer = 36
    cell_w = int((1024 - outer * 2 - gap * (cols - 1)) / cols)
    cell_h = int((1024 - outer * 2 - gap * (rows - 1)) / rows)
    for idx, (_, raw, _) in enumerate(sources):
        with Image.open(io.BytesIO(raw)) as img:
            tile = img.convert("RGBA")
            tile.thumbnail((cell_w, cell_h), Image.Resampling.LANCZOS)
            x = outer + (idx % cols) * (cell_w + gap) + int((cell_w - tile.width) / 2)
            y = outer + int(idx / cols) * (cell_h + gap) + int((cell_h - tile.height) / 2)
            canv.alpha_composite(tile, (x, y))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canv.convert("RGB").save(output_path, format="PNG")


@router.post("/api/viral-video-remix/character-reference")
async def generate_character_reference(
    request: Request,
    prompt: str = Form(""),
    style: str = Form("colored_pencil"),
    image: Optional[UploadFile] = File(None),
    current_user: _ServerUser = Depends(_resolve_viral_remix_user),
    db: Session = Depends(get_db),
):
    api_base, api_key = _resolve_local_comfly_credentials(current_user.id, db)
    final_prompt = _character_prompt((prompt or "").strip(), style)
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            if image and (image.filename or "").strip():
                raw = await image.read()
                if not raw:
                    raise HTTPException(status_code=400, detail="人物参考图为空")
                url = _endpoint(api_base, "/v1/images/edits")
                resp = await client.post(
                    url,
                    headers=headers,
                    data={
                        "prompt": final_prompt,
                        "model": "gpt-image-2",
                        "quality": "high",
                        "size": "1024x1024",
                        "response_format": "url",
                    },
                    files={
                        "image": (
                            image.filename or "person.png",
                            raw,
                            (image.content_type or "image/png").strip() or "image/png",
                        )
                    },
                )
            else:
                url = _endpoint(api_base, "/v1/images/generations")
                resp = await client.post(
                    url,
                    headers={**headers, "Content-Type": "application/json"},
                    json={
                        "prompt": final_prompt,
                        "model": "gpt-image-2",
                        "n": 1,
                        "quality": "high",
                        "size": "1024x1024",
                        "response_format": "url",
                    },
                )
    except HTTPException:
        raise
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="人物四视图生成超时，请稍后重试") from exc
    except Exception as exc:
        logger.exception("[viral_video_remix] character reference failed user_id=%s", current_user.id)
        raise HTTPException(status_code=500, detail=f"人物四视图生成请求失败: {exc}") from exc

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=_pick_error_detail(resp))
    try:
        payload = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="人物四视图生成返回格式异常") from exc

    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=502, detail="人物四视图生成成功，但没有返回图片")
    previews = [_image_preview(item) for item in rows if isinstance(item, dict)]
    previews = [item for item in previews if item.get("url") or item.get("data_url")]
    if not previews:
        raise HTTPException(status_code=502, detail="人物四视图结果不可预览")
    return {"ok": True, "images": previews, "prompt": final_prompt}


@router.post("/api/viral-video-remix/product-reference")
async def generate_product_reference(
    request: Request,
    prompt: str = Form(""),
    image_url: str = Form(""),
    image_urls: List[str] = Form([]),
    image: Optional[UploadFile] = File(None),
    images: List[UploadFile] = File([]),
    current_user: _ServerUser = Depends(_resolve_viral_remix_user),
    db: Session = Depends(get_db),
):
    api_base, api_key = _resolve_local_comfly_credentials(current_user.id, db)
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    sources: List[Tuple[str, bytes, str]] = []

    try:
        if image and (image.filename or "").strip():
            raw = await image.read()
            if raw:
                sources.append(
                    (
                        image.filename or "product.png",
                        raw,
                        (image.content_type or "image/png").strip() or "image/png",
                    )
                )
        for upload in images or []:
            if not upload or not (upload.filename or "").strip():
                continue
            raw = await upload.read()
            if not raw:
                continue
            sources.append(
                (
                    upload.filename or "product.png",
                    raw,
                    (upload.content_type or "image/png").strip() or "image/png",
                )
            )

        remote_urls: List[str] = []
        if (image_url or "").strip():
            remote_urls.append((image_url or "").strip())
        for item in image_urls or []:
            value = (item or "").strip()
            if value:
                remote_urls.append(value)
        seen_remote = set()
        for raw_url in remote_urls:
            src = _quote_public_url(raw_url)
            if not src or src in seen_remote:
                continue
            seen_remote.add(src)
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                get_resp = await client.get(src, headers={"Accept": "image/*"})
            if get_resp.status_code >= 400:
                raise HTTPException(status_code=get_resp.status_code, detail=f"Product image URL unreadable: HTTP {get_resp.status_code}")
            raw = get_resp.content or b""
            if not raw:
                continue
            content_type = (get_resp.headers.get("content-type") or "image/png").split(";", 1)[0].strip() or "image/png"
            suffix = ".png" if "png" in content_type else ".jpg" if "jpeg" in content_type or "jpg" in content_type else ".img"
            sources.append((f"product-source-{len(sources) + 1}{suffix}", raw, content_type))
        if not sources:
            raise HTTPException(status_code=400, detail="Please provide one or more product images.")

        final_prompt = _product_multiview_reference_prompt((prompt or "").strip(), len(sources))
        files = [("image", (name, raw, ctype)) for name, raw, ctype in sources]
        url = _endpoint(api_base, "/v1/images/edits")
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                url,
                headers=headers,
                data={
                    "prompt": final_prompt,
                    "model": "gpt-image-2",
                    "quality": "high",
                    "size": "1024x1024",
                },
                files=files,
            )
    except HTTPException:
        raise
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Product reference generation timed out.") from exc
    except Exception as exc:
        logger.exception("[viral_video_remix] product reference failed user_id=%s", current_user.id)
        raise HTTPException(status_code=500, detail=f"Product reference generation failed: {exc}") from exc

    previews: List[Dict[str, Any]] = []
    fallback_used = False
    if resp.status_code < 400:
        try:
            payload = resp.json()
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Product reference returned invalid JSON.") from exc
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            raise HTTPException(status_code=502, detail="Product reference returned no image result.")
        previews = [_image_preview(item) for item in rows if isinstance(item, dict)]
        for item in previews:
            if item.get("url"):
                item["url"] = _quote_public_url(item["url"])
        previews = [item for item in previews if item.get("url") or item.get("data_url")]
    elif len(sources) > 1:
        fallback_used = True
        fallback_path = _viral_remix_jobs_root() / "product_reference_fallbacks" / f"{current_user.id}_{int(time.time() * 1000)}.png"
        _build_product_reference_contact_sheet(sources, fallback_path)
        uploaded_url = await _upload_local_file_to_comfly(api_base, api_key, fallback_path, "image/png")
        previews = [{"url": uploaded_url}]
    else:
        raise HTTPException(status_code=resp.status_code, detail=_pick_error_detail(resp))

    if not previews:
        raise HTTPException(status_code=502, detail="Product reference result is not previewable.")
    first_url = str(previews[0].get("url") or "").strip()
    saved_asset: Optional[Dict[str, Any]] = None
    if first_url:
        saved_asset = await _save_remote_result_to_asset(
            url=first_url,
            media_type="image",
            tags="auto,viral.video.remix,product_reference",
            prompt=final_prompt,
            model="gpt-image-2",
            user_id=current_user.id,
            request=request,
            generation_task_id=f"viral_product_reference_{int(time.time() * 1000)}",
        )
        if saved_asset:
            previews[0]["asset_id"] = saved_asset.get("asset_id") or ""
    logger.info(
        "[viral_video_remix] product reference ok user_id=%s source_count=%s fallback=%s asset_id=%s result=%s",
        current_user.id,
        len(sources),
        fallback_used,
        (saved_asset or {}).get("asset_id") or "",
        (previews[0].get("url") or previews[0].get("data_url") or "")[:240],
    )
    return {
        "ok": True,
        "images": previews,
        "prompt": final_prompt,
        "source_count": len(sources),
        "fallback_used": fallback_used,
        "asset": saved_asset,
        "asset_id": (saved_asset or {}).get("asset_id") or "",
    }


@router.post("/api/viral-video-remix/assets/upload")
async def upload_viral_video_remix_asset(
    request: Request,
    file: UploadFile = File(...),
    current_user: _ServerUser = Depends(_resolve_viral_remix_user),
    db: Session = Depends(get_db),
):
    api_base, api_key = _resolve_local_comfly_credentials(current_user.id, db)
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="上传文件为空")
    filename = (file.filename or "viral-remix-asset").strip() or "viral-remix-asset"
    content_type = (file.content_type or "application/octet-stream").strip() or "application/octet-stream"
    url = _endpoint(api_base, "/v1/files")
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                files={"file": (filename, raw, content_type)},
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Comfly 文件上传超时") from exc
    except Exception as exc:
        logger.exception("[viral_video_remix] comfly file upload failed user_id=%s", current_user.id)
        raise HTTPException(status_code=500, detail=f"Comfly 文件上传失败: {exc}") from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=_pick_error_detail(resp))
    try:
        payload = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Comfly 文件上传返回格式异常") from exc
    source_url = _extract_file_upload_url(payload)
    if not source_url:
        raise HTTPException(status_code=502, detail=f"Comfly 文件上传未返回 URL: {json.dumps(payload, ensure_ascii=False)[:500]}")
    source_url = _quote_public_url(source_url)
    try:
        digest = hashlib.sha256(raw).hexdigest()[:24]
        cache_path = _viral_remix_upload_cache_root() / f"{digest}{_safe_asset_suffix(filename, content_type)}"
        if not cache_path.exists() or cache_path.stat().st_size != len(raw):
            cache_path.write_bytes(raw)
        _remember_uploaded_asset(source_url, cache_path)
    except Exception:
        logger.exception(
            "[viral_video_remix] failed to cache uploaded asset user_id=%s source_url=%s",
            current_user.id,
            source_url[:240],
        )
    logger.info(
        "[viral_video_remix] asset upload ok user_id=%s filename=%s content_type=%s source_url=%s",
        current_user.id,
        filename,
        content_type,
        source_url[:240],
    )
    return {
        "ok": True,
        "source_url": source_url,
        "url": source_url,
        "filename": filename,
        "content_type": content_type,
        "raw": payload,
    }


class ViralShareVideoResolveBody(BaseModel):
    share_text: str = Field(..., min_length=1)


@router.post("/api/viral-video-remix/share-video/resolve")
async def resolve_viral_video_share_link(
    body: ViralShareVideoResolveBody,
    current_user: _ServerUser = Depends(_resolve_viral_remix_user),
    db: Session = Depends(get_db),
):
    share_text = (body.share_text or "").strip()
    share_url = _extract_first_http_url(share_text)
    if not share_url:
        raise HTTPException(status_code=400, detail="未识别到视频分享链接，请粘贴包含 http/https 的完整分享文案。")
    api_base, api_key = _resolve_local_comfly_credentials(current_user.id, db)
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(
                _VIDEO_DOWNLOAD_API_URL,
                params={"appid": _VIDEO_DOWNLOAD_APPID, "link": share_url},
                headers={"Accept": "application/json"},
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="视频解析接口请求超时，请稍后重试。") from exc
    except Exception as exc:
        logger.exception("[viral_video_remix] share video parse request failed user_id=%s", current_user.id)
        raise HTTPException(status_code=502, detail=f"视频解析接口请求失败: {exc}") from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=f"视频解析接口 HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="视频解析接口返回格式异常。") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="视频解析接口返回格式无效。")
    code = payload.get("code")
    if code != 1:
        error_mapping = {
            -1: "该账号已被禁用",
            -100: "已开启白名单 IP 设置，当前 IP 禁止访问",
            109: "套餐提取次数不足",
            301: "未检查到链接，请检查分享文案是否完整",
            400: "提取链接无效或暂不支持此平台",
        }
        raise HTTPException(status_code=502, detail=error_mapping.get(code, str(payload.get("msg") or "视频解析失败")))
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    direct_video_url = _quote_public_url(str(data.get("videoSrc") or "").strip())
    if not direct_video_url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=502, detail="视频解析成功，但没有返回可下载的视频地址。")

    suffix = ".mp4"
    path_suffix = Path(urlsplit(direct_video_url).path).suffix.lower()
    if path_suffix in {".mp4", ".mov", ".webm"}:
        suffix = path_suffix
    download_path = _viral_remix_upload_cache_root() / f"share_{current_user.id}_{int(time.time() * 1000)}{suffix}"
    await _download_to_file(direct_video_url, download_path, timeout_seconds=300.0)
    source_url = await _upload_local_file_to_comfly(
        api_base,
        api_key,
        download_path,
        _guess_video_content_type(direct_video_url),
    )
    _remember_uploaded_asset(source_url, download_path)
    logger.info(
        "[viral_video_remix] share video resolved user_id=%s share_url=%s source_url=%s title=%s",
        current_user.id,
        share_url[:180],
        source_url[:240],
        str(data.get("title") or "")[:120],
    )
    return {
        "ok": True,
        "share_url": share_url,
        "title": data.get("title") or "",
        "direct_video_url": direct_video_url,
        "source_url": source_url,
        "url": source_url,
        "cover_url": data.get("imageSrc") or "",
        "state": data.get("state"),
        "raw": payload,
    }


class ViralRemixStartBody(BaseModel):
    original_video_url: str = Field(..., min_length=8)
    character_image_url: str = ""
    product_image_url: str = Field(..., min_length=8)
    prompt: str = ""
    model: str = "doubao-seedance-2-0-260128"
    ratio: str = "9:16"
    resolution: str = "720p"
    duration: int = 10
    generate_audio: bool = True
    watermark: bool = False
    use_character_reference: bool = False


def _extract_first_http_url(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    match = re.search(r"https?://[^\s\"'<>，。！？、；]+", raw, flags=re.IGNORECASE)
    if not match:
        return ""
    return match.group(0).rstrip(").,;，。；")


def _guess_video_content_type(url: str) -> str:
    path = urlsplit(url or "").path.lower()
    if path.endswith(".webm"):
        return "video/webm"
    if path.endswith(".mov"):
        return "video/quicktime"
    return "video/mp4"


def _normalize_seedance_model(raw: str) -> str:
    value = (raw or "").strip()
    aliases = {
        "seedance-2-0-pro-250528": "doubao-seedance-2-0-260128",
        "seedance-2-0-260128": "doubao-seedance-2-0-260128",
        "seedance-2-0-fast-260128": "doubao-seedance-2-0-fast-260128",
    }
    return aliases.get(value, value or "doubao-seedance-2-0-260128")


def _normalize_remix_model(raw: str) -> str:
    """Product replacement is an edit task; force Pro for better reference obedience."""
    return "doubao-seedance-2-0-260128"


def _normalize_duration(value: int) -> int:
    try:
        n = int(value)
    except Exception:
        n = 10
    return n if n in {5, 10} else 10


def _normalize_resolution(value: str) -> str:
    normalized = (value or "720p").strip().lower()
    return normalized if normalized in {"720p", "480p"} else "720p"


def _remix_prompt(body: ViralRemixStartBody) -> str:
    extra = (body.prompt or "").strip()
    parts = [
        "将视频1里的旧产品替换成图片1里的目标产品，运镜不变。",
        "只替换商品本体：保留视频1的镜头运动、构图、人物动作、手部动作、摆放位置、节奏和场景。",
        "图片1是唯一产品身份参考，最终画面必须出现图片1的产品；不要保留视频1里的旧产品，不要生成相似但不同的产品。",
        "保持图片1产品的外观、颜色、包装形状、材质、标签布局和品牌标识位置。",
        "去掉视频1里的字幕、贴纸、水印、界面文字、营销文案和无关 logo，不要新增无关文字。",
    ]
    if body.use_character_reference and (body.character_image_url or "").strip():
        parts.append("图片2只作为人物参考；产品替换优先级高于人物参考。")
    if extra:
        parts.append(f"补充要求：{extra}")
    return " ".join(parts)


def _remix_prompt(body: ViralRemixStartBody) -> str:
    """Use an ASCII prompt here to avoid mojibake from older mixed-encoding copies."""
    extra = (body.prompt or "").strip()
    parts = [
        "Use the input video as the only source of camera motion, timing, composition, hand actions, subject blocking, and scene continuity.",
        "Replace the old product seen in the input video with the target product from the first reference image.",
        "The first reference image is the only product identity source. The final video must show that exact product, not the old product from the source video and not a similar redesigned variant.",
        "Preserve the target product exactly: package shape, materials, color palette, label layout, logo position, cap, proportions, and visible markings.",
        "Keep the original video's scene, performer motion, framing, pacing, and hand interaction whenever possible. Only the product itself should change.",
        "Remove subtitles, stickers, watermarks, UI text, marketing copy, and unrelated logos from the final video. Do not add new text overlays.",
        "Strict anti-watermark rule: never reproduce Douyin/TikTok/short-video platform watermarks, musical-note icons, account IDs, user handles, translucent corner logos, bottom-right logos, or any app interface overlays from the source video.",
        "If the source video contains a visible watermark or account text, treat it as unwanted noise and reconstruct that area as clean natural scene background instead of copying it.",
        "Material binding: the video_url item is the source motion video, and the first reference_image item is the replacement product image.",
    ]
    if body.use_character_reference and (body.character_image_url or "").strip():
        parts.append(
            "If a second reference_image is present, use it only for character identity such as face, hairstyle, and styling. "
            "Product replacement has higher priority than character reference."
        )
    if extra:
        parts.append(f"Additional instruction: {extra}")
    return " ".join(parts)


def _remix_segment_prompt(body: ViralRemixStartBody, index: int, total: int) -> str:
    base = _remix_prompt(body)
    if total <= 1:
        return base
    return (
        f"{base} This request is segment {index + 1} of {total} from one longer source video. "
        "Keep character identity, product identity, scene logic, and motion style continuous across adjacent segments. "
        "Apply the anti-watermark rule independently to this segment even if the watermark appears only in this segment."
    )


def _run_subprocess(args: List[str], *, timeout: int, error_label: str) -> None:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{error_label} timed out") from exc
    if proc.returncode == 0:
        return
    detail = (proc.stderr or proc.stdout or "").strip()
    raise RuntimeError(f"{error_label} failed: {detail[:1200]}")


def _probe_video_duration_seconds(video_path: Path) -> float:
    ffprobe = _bundled_ffprobe_exe()
    if not ffprobe:
        raise RuntimeError("ffprobe not found")
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {(proc.stderr or proc.stdout or '').strip()[:800]}")
    try:
        return max(0.0, float((proc.stdout or "").strip()))
    except Exception as exc:
        raise RuntimeError("ffprobe returned invalid duration") from exc


def _split_local_video(source_path: Path, job_dir: Path, segment_seconds: int, total_duration: float) -> List[Dict[str, Any]]:
    ffmpeg = _bundled_ffmpeg_exe()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    clips_dir = job_dir / "source_segments"
    clips_dir.mkdir(parents=True, exist_ok=True)
    segment_count = max(1, int(math.ceil(max(total_duration, 0.01) / float(segment_seconds))))
    segments: List[Dict[str, Any]] = []
    for idx in range(segment_count):
        start_seconds = float(idx * segment_seconds)
        if start_seconds >= total_duration and idx > 0:
            break
        clip_seconds = max(0.1, min(float(segment_seconds), max(total_duration - start_seconds, 0.1)))
        clip_path = clips_dir / f"segment_{idx + 1:02d}.mp4"
        _run_subprocess(
            [
                ffmpeg,
                "-y",
                "-ss",
                f"{start_seconds:.3f}",
                "-t",
                f"{clip_seconds:.3f}",
                "-i",
                str(source_path),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(clip_path),
            ],
            timeout=600,
            error_label=f"split segment {idx + 1}",
        )
        segments.append(
            {
                "index": idx,
                "segment_path": str(clip_path),
                "start_seconds": start_seconds,
                "source_clip_seconds": clip_seconds,
                "target_duration_seconds": int(segment_seconds),
                "status": "prepared",
            }
        )
    return segments


async def _download_to_file(url: str, dest: Path, *, timeout_seconds: float = 180.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cached_path = _cached_uploaded_asset_path(url)
    if cached_path:
        shutil.copyfile(cached_path, dest)
    else:
        last_error: Optional[BaseException] = None
        for attempt in range(1, 4):
            try:
                timeout = httpx.Timeout(timeout_seconds, connect=30.0, read=timeout_seconds, write=60.0, pool=30.0)
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                    async with client.stream("GET", url) as resp:
                        if resp.status_code >= 400:
                            raise RuntimeError(f"download failed HTTP {resp.status_code}: {url[:240]}")
                        with dest.open("wb") as fh:
                            async for chunk in resp.aiter_bytes(1024 * 1024):
                                if chunk:
                                    fh.write(chunk)
                break
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError, httpx.TimeoutException, RuntimeError) as exc:
                last_error = exc
                if dest.exists():
                    try:
                        dest.unlink()
                    except Exception:
                        pass
                if attempt >= 3:
                    raise RuntimeError(f"download failed after retries: {url[:240]} ({exc})") from exc
                await asyncio.sleep(1.5 * attempt)
            except Exception as exc:
                last_error = exc
                if dest.exists():
                    try:
                        dest.unlink()
                    except Exception:
                        pass
                raise
        if last_error and (not dest.exists() or dest.stat().st_size <= 0):
            raise RuntimeError(f"download failed: {url[:240]} ({last_error})") from last_error
    if not dest.is_file() or dest.stat().st_size <= 0:
        raise RuntimeError(f"download produced empty file: {url[:240]}")


async def _upload_local_file_to_comfly(api_base: str, api_key: str, path: Path, content_type: str) -> str:
    raw = path.read_bytes()
    if not raw:
        raise RuntimeError(f"empty file: {path}")
    url = _endpoint(api_base, "/v1/files")
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            files={"file": (path.name, raw, content_type)},
        )
    if resp.status_code >= 400:
        raise RuntimeError(_pick_error_detail(resp))
    try:
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError("Comfly upload returned invalid JSON") from exc
    source_url = _extract_file_upload_url(payload)
    if not source_url:
        raise RuntimeError(f"Comfly upload returned no source_url: {json.dumps(payload, ensure_ascii=False)[:400]}")
    return _quote_public_url(source_url)


async def _submit_seedance_remix_task(
    *,
    api_base: str,
    api_key: str,
    body: ViralRemixStartBody,
    original_video_url: str,
    prompt_text: str,
) -> Dict[str, Any]:
    product_url = _quote_public_url(body.product_image_url)
    character_url = _quote_public_url(body.character_image_url)
    content: List[Dict[str, Any]] = [
        {"type": "text", "text": prompt_text},
        {"type": "video_url", "video_url": {"url": _quote_public_url(original_video_url)}},
        {"type": "image_url", "image_url": {"url": product_url}, "role": "reference_image"},
    ]
    if body.use_character_reference and character_url:
        content.append({"type": "image_url", "image_url": {"url": character_url}, "role": "reference_image"})
    request_body: Dict[str, Any] = {
        "model": _normalize_remix_model(body.model),
        "prompt": prompt_text,
        "content": content,
        "ratio": (body.ratio or "9:16").strip() or "9:16",
        "resolution": _normalize_resolution(body.resolution),
        "duration": _normalize_duration(body.duration),
        "generate_audio": bool(body.generate_audio),
        "return_last_frame": False,
        "watermark": bool(body.watermark),
    }
    url = _endpoint(api_base, "/seedance/v3/contents/generations/tasks")
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=request_body,
        )
    if resp.status_code >= 400:
        raise RuntimeError(_pick_error_detail(resp))
    try:
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError("Seedance submit returned invalid JSON") from exc
    task_id = _extract_task_id(payload)
    if not task_id:
        raise RuntimeError(f"Seedance returned no task id: {json.dumps(payload, ensure_ascii=False)[:400]}")
    return {"task_id": task_id, "raw": payload}


async def _poll_seedance_until_done(api_base: str, api_key: str, task_id: str) -> Dict[str, Any]:
    url = _endpoint(api_base, f"/seedance/v3/contents/generations/tasks/{task_id}")
    last_status = "queued"
    for _ in range(240):
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
        if resp.status_code >= 400:
            raise RuntimeError(_pick_error_detail(resp))
        try:
            payload = resp.json()
        except Exception as exc:
            raise RuntimeError("Seedance poll returned invalid JSON") from exc
        status, video_url, error_detail = _extract_seedance_poll(payload)
        if status:
            last_status = status
        if status in _FAILED_STATUSES:
            raise RuntimeError(error_detail or f"Seedance task failed: {task_id}")
        if status in _SUCCESS_STATUSES and video_url:
            return {"task_id": task_id, "status": status, "video_url": _quote_public_url(video_url), "raw": payload}
        await asyncio.sleep(8)
    raise RuntimeError(f"Seedance task polling timed out: {task_id} status={last_status}")


def _merge_segment_videos(segment_paths: List[Path], output_path: Path) -> None:
    ffmpeg = _bundled_ffmpeg_exe()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    list_path = output_path.parent / "concat_list.txt"
    list_lines = ["file '" + str(path) + "'" for path in segment_paths]
    list_path.write_text("\n".join(list_lines) + "\n", encoding="utf-8")
    try:
        _run_subprocess(
            [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-c",
                "copy",
                str(output_path),
            ],
            timeout=1200,
            error_label="concat remix segments",
        )
    except RuntimeError:
        _run_subprocess(
            [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            timeout=1200,
            error_label="concat remix segments (re-encode)",
        )


def _trim_merged_video(source_path: Path, final_path: Path, duration_seconds: float) -> None:
    ffmpeg = _bundled_ffmpeg_exe()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    trimmed_path = final_path.with_name(final_path.stem + "_trimmed.mp4")
    _run_subprocess(
        [
            ffmpeg,
            "-y",
            "-i",
            str(source_path),
            "-t",
            f"{max(duration_seconds, 0.1):.3f}",
            "-c",
            "copy",
            str(trimmed_path),
        ],
        timeout=600,
        error_label="trim merged remix video",
    )
    trimmed_path.replace(final_path)


def _job_status_response(job: Dict[str, Any]) -> Dict[str, Any]:
    segments = job.get("segments")
    out: Dict[str, Any] = {
        "ok": str(job.get("status") or "") != "failed",
        "task_id": job.get("job_id"),
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "stage": job.get("stage"),
        "video_url": job.get("merged_video_url") or "",
        "error": job.get("error") or "",
        "total_segments": int(job.get("total_segments") or 0),
        "completed_segments": int(job.get("completed_segments") or 0),
        "segment_duration_seconds": int(job.get("segment_duration_seconds") or 0),
        "source_duration_seconds": float(job.get("source_duration_seconds") or 0.0),
        "created_at_ts": job.get("created_at_ts"),
        "updated_at_ts": job.get("updated_at_ts"),
        "asset_id": job.get("asset_id") or "",
    }
    asset = job.get("asset")
    if isinstance(asset, dict):
        out["asset"] = asset
    if isinstance(segments, list):
        out["segments"] = segments
    result = job.get("result")
    if isinstance(result, dict):
        out["result"] = result
    return out


async def _run_viral_remix_job(job_id: str, body: ViralRemixStartBody, api_base: str, api_key: str) -> None:
    job = get_job(job_id)
    if not job:
        return
    job_dir = Path(str(job.get("job_dir") or "")).resolve()
    work_dir = job_dir / "work"
    downloads_dir = job_dir / "downloads"
    outputs_dir = job_dir / "outputs"
    work_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    source_path = downloads_dir / "source_video.mp4"
    try:
        update_job(job_id, status="running", stage="downloading")
        await _download_to_file(_quote_public_url(body.original_video_url), source_path, timeout_seconds=300.0)
        total_duration = _probe_video_duration_seconds(source_path)
        segment_seconds = _normalize_duration(body.duration)
        update_job(
            job_id,
            source_duration_seconds=total_duration,
            segment_duration_seconds=segment_seconds,
            stage="splitting",
        )
        segments = _split_local_video(source_path, work_dir, segment_seconds, total_duration)
        update_job(job_id, total_segments=len(segments), segments=segments)
        segment_state_lock = asyncio.Lock()
        parallel_limit = max(1, min(_MAX_PARALLEL_SEGMENT_TASKS, len(segments)))
        semaphore = asyncio.Semaphore(parallel_limit)

        async def _update_segment_state(
            index: int,
            *,
            stage: Optional[str] = None,
            status: Optional[str] = None,
            extra: Optional[Dict[str, Any]] = None,
        ) -> None:
            async with segment_state_lock:
                current = get_job(job_id) or {}
                seg_meta = list(current.get("segments") or [])
                if index < len(seg_meta):
                    updated = dict(seg_meta[index])
                    if status is not None:
                        updated["status"] = status
                    if extra:
                        updated.update(extra)
                    seg_meta[index] = updated
                fields: Dict[str, Any] = {"segments": seg_meta}
                if stage:
                    fields["stage"] = stage
                fields["completed_segments"] = sum(
                    1 for item in seg_meta if str(item.get("status") or "").lower() == "completed"
                )
                update_job(job_id, **fields)

        async def _process_segment(index: int, seg: Dict[str, Any]) -> Dict[str, Any]:
            async with semaphore:
                try:
                    seg_path = Path(str(seg.get("segment_path") or "")).resolve()
                    seg_upload_url = await _upload_local_file_to_comfly(api_base, api_key, seg_path, "video/mp4")
                    await _update_segment_state(
                        index,
                        stage="segment_submitted",
                        status="submitted",
                        extra={"segment_video_url": seg_upload_url},
                    )
                    submit_result = await _submit_seedance_remix_task(
                        api_base=api_base,
                        api_key=api_key,
                        body=body,
                        original_video_url=seg_upload_url,
                        prompt_text=_remix_segment_prompt(body, index, len(segments)),
                    )
                    task_id = str(submit_result.get("task_id") or "").strip()
                    await _update_segment_state(
                        index,
                        stage="segment_running",
                        status="polling",
                        extra={"remote_task_id": task_id},
                    )
                    poll_result = await _poll_seedance_until_done(api_base, api_key, task_id)
                    output_url = str(poll_result.get("video_url") or "").strip()
                    local_output = outputs_dir / f"segment_{index + 1:02d}.mp4"
                    await _download_to_file(output_url, local_output, timeout_seconds=300.0)
                    await _update_segment_state(
                        index,
                        stage="segment_running",
                        status="completed",
                        extra={
                            "remote_task_id": task_id,
                            "result_video_url": output_url,
                            "result_video_path": str(local_output),
                        },
                    )
                    return {"index": index, "local_output": local_output, "output_url": output_url}
                except Exception as exc:
                    await _update_segment_state(
                        index,
                        stage="segment_running",
                        status="failed",
                        extra={"error": str(exc)[:1000]},
                    )
                    raise

        tasks: List[asyncio.Task[Dict[str, Any]]] = []
        async with asyncio.TaskGroup() as tg:
            for idx, seg in enumerate(segments):
                tasks.append(tg.create_task(_process_segment(idx, seg)))
        segment_results = [task.result() for task in tasks]
        segment_results.sort(key=lambda item: int(item["index"]))
        rendered_paths = [Path(str(item["local_output"])) for item in segment_results]
        rendered_urls = [str(item["output_url"]) for item in segment_results]
        update_job(job_id, stage="merging")
        merged_tmp = outputs_dir / "merged_raw.mp4"
        merged_final = job_dir / "final.mp4"
        _merge_segment_videos(rendered_paths, merged_tmp)
        _trim_merged_video(merged_tmp, merged_final, total_duration)
        relative_url = f"/static/generated/viral_remix_jobs/{job_id}/final.mp4?ts={int(time.time())}"
        saved_asset = _save_local_video_to_asset(
            path=merged_final,
            user_id=int(job.get("user_id") or 0),
            prompt=_remix_prompt(body),
            model=_normalize_remix_model(body.model),
            tags="auto,viral.video.remix,merged_final",
            meta={
                "origin": "viral_video_remix",
                "job_id": job_id,
                "source_duration_seconds": total_duration,
                "segment_duration_seconds": segment_seconds,
                "segment_video_urls": rendered_urls,
                "local_path": str(merged_final),
            },
        )
        update_job(
            job_id,
            status="completed",
            stage="completed",
            merged_video_url=relative_url,
            merged_video_path=str(merged_final),
            asset=saved_asset,
            asset_id=(saved_asset or {}).get("asset_id") or "",
            result={
                "source_duration_seconds": total_duration,
                "segment_duration_seconds": segment_seconds,
                "segment_video_urls": rendered_urls,
                "merged_video_url": relative_url,
                "asset": saved_asset,
                "asset_id": (saved_asset or {}).get("asset_id") or "",
            },
        )
    except Exception as exc:
        logger.exception("[viral_video_remix] remix job failed job_id=%s", job_id)
        update_job(job_id, status="failed", stage="failed", error=str(exc)[:2000])


@router.post("/api/viral-video-remix/seedance/start")
async def start_viral_video_remix(
    body: ViralRemixStartBody,
    current_user: _ServerUser = Depends(_resolve_viral_remix_user),
    db: Session = Depends(get_db),
):
    api_base, api_key = _resolve_local_comfly_credentials(current_user.id, db)
    safe_body = ViralRemixStartBody(
        original_video_url=_quote_public_url(body.original_video_url),
        character_image_url=_quote_public_url(body.character_image_url),
        product_image_url=_quote_public_url(body.product_image_url),
        prompt=body.prompt,
        model=body.model,
        ratio=body.ratio,
        resolution=body.resolution,
        duration=_normalize_duration(body.duration),
        generate_audio=bool(body.generate_audio),
        watermark=bool(body.watermark),
        use_character_reference=bool(body.use_character_reference),
    )
    job_id = create_job_record(
        user_id=current_user.id,
        payload=safe_body.model_dump(),
        job_dir=str(_viral_remix_jobs_root() / "pending"),
    )
    job_dir = _viral_remix_jobs_root() / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    update_job(
        job_id,
        job_dir=str(job_dir),
        status="queued",
        stage="queued",
        total_segments=0,
        completed_segments=0,
        segment_duration_seconds=int(safe_body.duration or 5),
    )
    logger.info(
        "[viral_video_remix] queued local remix job user_id=%s job_id=%s segment_seconds=%s video_url=%s character_url=%s product_url=%s",
        current_user.id,
        job_id,
        safe_body.duration,
        safe_body.original_video_url[:160],
        safe_body.character_image_url[:160],
        safe_body.product_image_url[:160],
    )
    asyncio.create_task(_run_viral_remix_job(job_id, safe_body, api_base, api_key))
    return {
        "ok": True,
        "task_id": job_id,
        "job_id": job_id,
        "status": "queued",
        "stage": "queued",
        "segment_duration_seconds": int(safe_body.duration or 10),
    }
    url = _endpoint(api_base, "/seedance/v3/contents/generations/tasks")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, headers=headers, json=request_body)
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Seedance 任务提交超时") from exc
    except Exception as exc:
        logger.exception("[viral_video_remix] seedance submit failed user_id=%s", current_user.id)
        raise HTTPException(status_code=500, detail=f"Seedance 任务提交失败: {exc}") from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=_pick_error_detail(resp))
    try:
        payload = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Seedance 提交返回格式异常") from exc
    task_id = _extract_task_id(payload)
    if not task_id:
        raise HTTPException(status_code=502, detail=f"Seedance 未返回任务 ID: {json.dumps(payload, ensure_ascii=False)[:500]}")
    return {"ok": True, "task_id": task_id, "prompt": prompt_text, "raw": payload}


@router.get("/api/viral-video-remix/seedance/tasks/{task_id}")
async def poll_viral_video_remix_task(
    task_id: str,
    current_user: _ServerUser = Depends(_resolve_viral_remix_user),
    db: Session = Depends(get_db),
):
    api_base, api_key = _resolve_local_comfly_credentials(current_user.id, db)
    safe_task = (task_id or "").strip()
    if not safe_task:
        raise HTTPException(status_code=400, detail="缺少任务 ID")
    job = get_job(safe_task)
    if job is not None:
        return _job_status_response(job)
        """
        if int(job.get("user_id") or 0) != int(current_user.id or 0):
            raise HTTPException(status_code=404, detail="未找到复刻任务")
        return _job_status_response(job)
        """
    url = _endpoint(api_base, f"/seedance/v3/contents/generations/tasks/{safe_task}")
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Seedance 任务查询超时") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Seedance 任务查询失败: {exc}") from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=_pick_error_detail(resp))
    try:
        payload = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Seedance 任务查询返回格式异常") from exc
    status, video_url, error_detail = _extract_seedance_poll(payload)
    return {
        "ok": status not in _FAILED_STATUSES,
        "task_id": safe_task,
        "status": status,
        "video_url": video_url,
        "error": error_detail,
        "raw": payload,
    }
