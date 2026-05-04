from __future__ import annotations

import json
import logging
import ipaddress
from urllib.parse import quote, urlsplit, urlunsplit
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import get_db
from ..models import UserComflyConfig
from ..services.comfly_veo_exec import LOCAL_COMFLY_CONFIG_USER_ID
from .auth import _ServerUser, get_current_user_for_local

logger = logging.getLogger(__name__)
router = APIRouter()

_SUCCESS_STATUSES = {"succeeded", "success", "completed", "done"}
_FAILED_STATUSES = {"failed", "failure", "error", "cancelled", "canceled", "expired"}


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
    image: Optional[UploadFile] = File(None),
    current_user: _ServerUser = Depends(_resolve_viral_remix_user),
    db: Session = Depends(get_db),
):
    api_base, api_key = _resolve_local_comfly_credentials(current_user.id, db)
    final_prompt = _product_reference_prompt((prompt or "").strip())
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    raw = b""
    filename = "product.png"
    content_type = "image/png"

    try:
        if image and (image.filename or "").strip():
            raw = await image.read()
            filename = image.filename or filename
            content_type = (image.content_type or content_type).strip() or content_type
        else:
            src = _quote_public_url(image_url)
            if not src:
                raise HTTPException(status_code=400, detail="请先选择产品图，或填写产品图 URL")
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                get_resp = await client.get(src, headers={"Accept": "image/*"})
            if get_resp.status_code >= 400:
                raise HTTPException(status_code=get_resp.status_code, detail=f"产品图 URL 无法读取：HTTP {get_resp.status_code}")
            raw = get_resp.content or b""
            content_type = (get_resp.headers.get("content-type") or content_type).split(";", 1)[0].strip() or content_type
            suffix = ".png" if "png" in content_type else ".jpg" if "jpeg" in content_type or "jpg" in content_type else ".img"
            filename = f"product-source{suffix}"
        if not raw:
            raise HTTPException(status_code=400, detail="产品图为空")

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
                    "response_format": "url",
                },
                files={"image": (filename, raw, content_type)},
            )
    except HTTPException:
        raise
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="白底产品图生成超时，请稍后重试") from exc
    except Exception as exc:
        logger.exception("[viral_video_remix] product reference failed user_id=%s", current_user.id)
        raise HTTPException(status_code=500, detail=f"白底产品图生成失败: {exc}") from exc

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=_pick_error_detail(resp))
    try:
        payload = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="白底产品图生成返回格式异常") from exc

    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=502, detail="白底产品图生成成功，但没有返回图片")
    previews = [_image_preview(item) for item in rows if isinstance(item, dict)]
    for item in previews:
        if item.get("url"):
            item["url"] = _quote_public_url(item["url"])
    previews = [item for item in previews if item.get("url") or item.get("data_url")]
    if not previews:
        raise HTTPException(status_code=502, detail="白底产品图结果不可预览")
    logger.info(
        "[viral_video_remix] product reference ok user_id=%s source=%s result=%s",
        current_user.id,
        str((image.filename if image else image_url) or "")[:180],
        (previews[0].get("url") or previews[0].get("data_url") or "")[:240],
    )
    return {"ok": True, "images": previews, "prompt": final_prompt}


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


class ViralRemixStartBody(BaseModel):
    original_video_url: str = Field(..., min_length=8)
    character_image_url: str = ""
    product_image_url: str = Field(..., min_length=8)
    prompt: str = ""
    model: str = "doubao-seedance-2-0-260128"
    ratio: str = "9:16"
    resolution: str = "720p"
    duration: int = 5
    generate_audio: bool = True
    watermark: bool = False
    use_character_reference: bool = False


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
        n = 5
    return n if n in {5, 10} else 5


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


@router.post("/api/viral-video-remix/seedance/start")
async def start_viral_video_remix(
    body: ViralRemixStartBody,
    current_user: _ServerUser = Depends(_resolve_viral_remix_user),
    db: Session = Depends(get_db),
):
    api_base, api_key = _resolve_local_comfly_credentials(current_user.id, db)
    product_url = _quote_public_url(body.product_image_url)
    character_url = _quote_public_url(body.character_image_url)
    original_video_url = _quote_public_url(body.original_video_url)
    content: List[Dict[str, Any]] = [
        {"type": "text", "text": _remix_prompt(body)},
        {"type": "image_url", "image_url": {"url": product_url}, "role": "reference_image"},
    ]
    content_order = ["text", "product_reference_image"]
    if body.use_character_reference and character_url:
        content.append({"type": "image_url", "image_url": {"url": character_url}, "role": "reference_image"})
        content_order.append("character_reference_image")
    content.append({"type": "video_url", "video_url": {"url": original_video_url}, "role": "reference_video"})
    content_order.append("reference_video")
    request_body: Dict[str, Any] = {
        "model": _normalize_remix_model(body.model),
        "content": content,
        "ratio": (body.ratio or "9:16").strip() or "9:16",
        "resolution": _normalize_resolution(body.resolution),
        "duration": _normalize_duration(body.duration),
        "generate_audio": bool(body.generate_audio),
        "return_last_frame": False,
        "watermark": bool(body.watermark),
    }
    logger.info(
        "[viral_video_remix] submit seedance user_id=%s model_requested=%s model_used=%s content_order=%s video_url=%s character_url=%s product_url=%s prompt=%s",
        current_user.id,
        body.model,
        _normalize_remix_model(body.model),
        content_order,
        original_video_url[:160],
        character_url[:160],
        product_url[:160],
        _remix_prompt(body)[:600],
    )
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
    return {"ok": True, "task_id": task_id, "prompt": _remix_prompt(body), "raw": payload}


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
