import io
import json
import logging
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from PIL import Image
from pathlib import Path
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.comfly_veo_exec import _resolve_comfly_credentials
from .auth import _ServerUser, get_current_user_media_edit

logger = logging.getLogger(__name__)
router = APIRouter()
_EXAMPLE_SOURCE_PATH = Path(__file__).resolve().parents[3] / "static" / "data" / "comfly-image-studio-examples.json"

_ASPECT_TO_SIZE = {
    "1:1": "1024x1024",
    "3:2": "1536x1024",
    "16:9": "1536x1024",
    "2:3": "1024x1536",
    "9:16": "1024x1536",
}

_FALLBACK_EXAMPLES = [
    {
        "id": 1050,
        "title": "3D 彩墙人物",
        "prompt": "A stylized 3D animated young woman leaning against a textured abstract wall made of layered cracked paint panels in warm yellow, coral, pink and muted purple gradients. Soft cinematic lighting, warm pastel palette, painterly textures, dreamy atmosphere, Pixar-style 3D illustration, ultra detailed.",
        "cover_image": "https://raw.githubusercontent.com/songguoxs/gpt4o-image-prompts/master/images/1050.jpeg",
        "model": "gpt-image-2",
        "tags": ["3D", "插画", "暖色"],
    },
    {
        "id": 1049,
        "title": "角色设定草图",
        "prompt": "Character sheet sketch of a subject, featuring multiple angles and expressive facial variations, drawn in pencil and ballpoint pen on a clean white background. Soft pastel palette, sharp linework, hand-drawn manga style, clear design-sheet composition.",
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
        "prompt": prompt or prompt_en or prompt_zh,
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


def _blank_image_file() -> tuple[str, bytes, str]:
    buf = io.BytesIO()
    Image.new("RGB", (1024, 1024), color="white").save(buf, format="PNG")
    return ("blank.png", buf.getvalue(), "image/png")


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


@router.post("/api/comfly-image-studio/generate")
async def comfly_image_studio_generate(
    request: Request,
    prompt: str = Form(""),
    model: str = Form("gpt-image-2"),
    aspect_ratio: str = Form("1:1"),
    quality: str = Form("high"),
    background: str = Form("auto"),
    images: List[UploadFile] = File(None),
    current_user: _ServerUser = Depends(get_current_user_media_edit),
    db: Session = Depends(get_db),
):
    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="请输入图片提示词")

    size = _ASPECT_TO_SIZE.get((aspect_ratio or "").strip(), "1024x1024")
    api_base, api_key = _resolve_comfly_credentials(current_user.id, db, request)
    url = f"{api_base.rstrip('/')}/v1/images/edits"

    data = {
        "prompt": prompt,
        "model": (model or "gpt-image-2").strip() or "gpt-image-2",
        "n": "1",
        "quality": (quality or "high").strip() or "high",
        "size": size,
    }
    if background and background != "auto":
        data["background"] = background

    files: List[tuple[str, tuple[str, bytes, str]]] = []
    valid_uploads = [item for item in (images or []) if item and (item.filename or "").strip()]
    if valid_uploads:
        for upload in valid_uploads:
            raw = await upload.read()
            if not raw:
                continue
            files.append(
                (
                    "image",
                    (
                        upload.filename or "reference.png",
                        raw,
                        (upload.content_type or "image/png").strip() or "image/png",
                    ),
                )
            )
    if not files:
        files.append(("image", _blank_image_file()))

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(url, headers=headers, data=data, files=files)
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

    return {
        "ok": True,
        "images": previews,
        "meta": {
            "model": data["model"],
            "aspect_ratio": aspect_ratio,
            "size": size,
            "quality": data["quality"],
            "reference_count": len(valid_uploads),
        },
    }
