"""PPT generation API for the bundled create_ppt skill."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .auth import _ServerUser, get_current_user_media_edit
from .comfly_image_studio import _generate_image_studio_core
from ..core.config import settings
from ..db import SessionLocal
from ..services.internal_chat_client import forward_chat_auth_from_request
from ..services.create_ppt_runner import (
    create_download_token,
    create_fullpage_image_ppt,
    create_ppt_run_dir,
    resolve_download_token,
    run_create_ppt_sync,
    safe_create_ppt_name,
)


logger = logging.getLogger(__name__)
router = APIRouter()

_IMAGE_FULLPAGE_BATCH_SIZE = 10
_IMAGE_FULLPAGE_MAX_RETRIES = 3
_IMAGE_FULLPAGE_RETRY_BASE_DELAY_SEC = 1.5
_IMAGE_FULLPAGE_MAX_SLIDES = 30
_AI_OUTLINE_TIMEOUT_SEC = 180.0
_PPTX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


class CreatePptPayload(BaseModel):
    mode: str = Field("outline", description="outline/markdown/ai/image_fullpage")
    topic: str = Field("PPT", description="PPT topic")
    outline_markdown: Optional[str] = Field(None, description="Markdown outline")
    outline_file: Optional[str] = Field(None, description="Server-side outline file path")
    slide_count: int = Field(10, ge=1, le=80, description="Slide count")
    theme: str = Field("business", description="Theme name")
    language: str = Field("zh-CN", description="Language")
    instructions: Optional[str] = Field(None, description="Additional instructions")
    model: Optional[str] = Field(None, description="Text planning AI model")
    base_url: Optional[str] = Field(None, description="Reserved for compatibility")
    api_key: Optional[str] = Field(None, description="Reserved for compatibility")
    filename: Optional[str] = Field(None, description="Output PPTX filename")
    template_path: Optional[str] = Field(None, description="Optional local PPTX template path")
    slide_prompts: Optional[List[str]] = Field(None, description="Per-slide prompts for image_fullpage mode")
    image_model: str = Field("gpt-image-2", description="Image model for image_fullpage mode")
    image_quality: str = Field("high", description="Image quality for image_fullpage mode")
    image_background: str = Field("opaque", description="Image background for image_fullpage mode")
    aspect_ratio: str = Field("16:9", description="Image aspect ratio for image_fullpage mode")


_TITLE_RE = re.compile(r"^\s{0,3}#{1,3}\s+(.+?)\s*$")


def _split_outline_to_slide_prompts(outline_markdown: str, topic: str, limit: int) -> List[str]:
    text = (outline_markdown or "").strip()
    prompts: List[str] = []
    current_title = ""
    current_lines: List[str] = []

    def flush() -> None:
        nonlocal current_title, current_lines
        body = "\n".join(line.strip() for line in current_lines if line.strip()).strip()
        title = current_title.strip()
        if title or body:
            prompts.append("\n".join(part for part in [title, body] if part).strip())
        current_title = ""
        current_lines = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = _TITLE_RE.match(line)
        if match:
            flush()
            current_title = match.group(1).strip()
        elif line:
            current_lines.append(line)
    flush()

    cleaned = [item for item in prompts if item]
    if not cleaned:
        cleaned = [topic or "PPT"]
    return cleaned[: max(1, limit)]


def _image_ppt_prompt(base_prompt: str, topic: str, index: int, total: int, extra: str) -> str:
    subject = (base_prompt or topic or "presentation slide").strip()
    parts = [
        f"Create full-slide 16:9 presentation artwork for slide {index}/{total}.",
        f"Deck topic: {topic or subject}",
        f"Slide content: {subject}",
        "Business presentation style, clear hierarchy, polished composition, suitable to fill an entire PPT slide.",
        "If text appears in the image, keep it short, accurate, readable Chinese. Avoid garbled text, watermarks, QR codes, and brand logos.",
    ]
    if extra:
        parts.append(extra)
    return "\n".join(parts)


def _installation_id_from_request(request: Request, user_id: int) -> str:
    xi = (request.headers.get("X-Installation-Id") or request.headers.get("x-installation-id") or "").strip()
    return xi or f"lobster-internal-{int(user_id)}"


def _extract_chat_text(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"].strip()
        if isinstance(first.get("text"), str):
            return first["text"].strip()
    for key in ("content", "text", "message"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested = data.get("data")
    if isinstance(nested, dict):
        return _extract_chat_text(nested)
    return ""


def _clean_markdown_outline(text: str, topic: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    fenced = re.search(r"```(?:markdown|md)?\s*([\s\S]*?)\s*```", raw, re.IGNORECASE)
    if fenced:
        raw = fenced.group(1).strip()
    raw = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw.startswith("# "):
        raw = f"# {topic or 'PPT'}\n\n{raw}"
    return raw


def _ppt_outline_system_prompt() -> str:
    return (
        "你是专业的 PPT 内容策划师。请根据用户主题生成可直接渲染为 PPT 的 Markdown 大纲。"
        "只输出 Markdown，不要解释、不要代码块、不要 JSON。"
        "格式必须使用：第一行 # 演示文稿标题；每页用 ### 页面标题；页面内容用 - 要点。"
        "可以少量使用 ## 章节页，但总页数要接近用户要求。"
        "每页 3-5 个短要点，中文表达清楚，避免空泛套话。"
    )


def _ppt_outline_user_prompt(payload: CreatePptPayload) -> str:
    topic = (payload.topic or "").strip()
    slide_count = max(1, min(int(payload.slide_count or 10), 80))
    language = (payload.language or "zh-CN").strip() or "zh-CN"
    instructions = (payload.instructions or "").strip()
    parts = [
        f"主题：{topic}",
        f"页数：{slide_count}",
        f"语言：{language}",
        "输出要求：",
        f"- 生成约 {slide_count} 页 PPT。",
        "- 第 1 页通常是封面，最后 1 页可以是总结/行动建议/谢谢页。",
        "- 每个 ### 代表一页，页内用项目符号写内容。",
        "- 不要输出制作说明，不要说无法生成，不要要求用户手工粘贴。",
    ]
    if instructions:
        parts.append(f"补充要求：{instructions}")
    return "\n".join(parts)


async def _generate_ai_outline_via_sutui(
    *,
    payload: CreatePptPayload,
    request: Request,
    current_user: _ServerUser,
) -> str:
    asb = (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
    if not asb:
        raise HTTPException(status_code=503, detail="未配置 AUTH_SERVER_BASE，无法通过系统代理生成 PPT 大纲")
    token, xi = forward_chat_auth_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="PPT AI 模式需要登录态 Authorization，用于通过系统代理生成大纲")
    installation_id = xi or _installation_id_from_request(request, current_user.id)
    model = (
        (payload.model or "").strip()
        or (getattr(settings, "lobster_orchestration_sutui_chat_model", None) or "").strip()
        or (getattr(settings, "lobster_default_sutui_chat_model", None) or "").strip()
        or "deepseek-chat"
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _ppt_outline_system_prompt()},
            {"role": "user", "content": _ppt_outline_user_prompt(payload)},
        ],
        "stream": False,
        "temperature": 0.35,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Installation-Id": installation_id,
    }
    billing_key = (getattr(settings, "lobster_mcp_billing_internal_key", None) or "").strip()
    if billing_key:
        headers["X-Lobster-Mcp-Billing"] = billing_key
    for src, dst in (
        ("X-Lobster-Chat-Turn-Charged", "X-Lobster-Chat-Turn-Charged"),
        ("X-Lobster-Chat-Turn-Id", "X-Lobster-Chat-Turn-Id"),
        ("X-Lobster-LLM-Billing-Mode", "X-Lobster-LLM-Billing-Mode"),
    ):
        value = (request.headers.get(src) or request.headers.get(src.lower()) or "").strip()
        if value:
            headers[dst] = value[:128] if dst.endswith("Turn-Id") else value

    try:
        async with httpx.AsyncClient(timeout=_AI_OUTLINE_TIMEOUT_SEC, trust_env=False) as client:
            resp = await client.post(f"{asb}/api/sutui-chat/completions", json=body, headers=headers)
    except httpx.RequestError as exc:
        logger.warning("[create_ppt] sutui outline request failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"PPT 大纲生成服务暂时不可用: {exc}") from exc
    if resp.status_code >= 400:
        detail = ""
        try:
            data = resp.json()
            if isinstance(data, dict):
                detail = str(data.get("detail") or data.get("message") or data.get("error") or "")
        except Exception:
            detail = ""
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"PPT 大纲生成失败: {detail or resp.text[:800] or f'HTTP {resp.status_code}'}",
        )
    try:
        data = resp.json() if resp.content else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="PPT 大纲生成服务返回了非 JSON 内容") from exc
    outline = _clean_markdown_outline(_extract_chat_text(data), payload.topic)
    if not outline or "### " not in outline:
        raise HTTPException(status_code=502, detail="PPT 大纲生成结果格式不完整，请换个主题或稍后重试")
    return outline


async def _run_ai_image_ppt(
    *,
    payload: CreatePptPayload,
    request: Request,
    current_user: _ServerUser,
) -> Dict[str, Any]:
    outline = await _generate_ai_outline_via_sutui(
        payload=payload,
        request=request,
        current_user=current_user,
    )
    image_payload = payload.model_copy(
        update={
            "mode": "image_fullpage",
            "outline_markdown": outline,
        }
    )
    result = await _run_image_fullpage_ppt(
        payload=image_payload,
        request=request,
        current_user=current_user,
    )
    result["mode"] = "ai"
    result["render_mode"] = "image_fullpage"
    result["outline_markdown"] = outline
    result["ai_outline_model"] = (
        (payload.model or "").strip()
        or (getattr(settings, "lobster_orchestration_sutui_chat_model", None) or "").strip()
        or (getattr(settings, "lobster_default_sutui_chat_model", None) or "").strip()
        or "deepseek-chat"
    )
    return result


async def _download_or_decode_preview(preview: Dict[str, str], dest: Path) -> str:
    data_url = str(preview.get("data_url") or "").strip()
    if data_url:
        payload = data_url.split(",", 1)[-1] if "," in data_url else data_url
        dest.write_bytes(base64.b64decode(payload))
        return str(dest)

    url = str(preview.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=502, detail="Image generation returned no downloadable URL")
    try:
        parsed = urlparse(url)
        suffix = Path(parsed.path).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".webp"}:
            dest = dest.with_suffix(suffix)
        async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
            resp = await client.get(url)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to download generated image: {exc}") from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=f"Failed to download generated image: HTTP {resp.status_code}")
    dest.write_bytes(resp.content)
    return str(dest)


def _http_exception_detail(exc: HTTPException) -> str:
    detail = exc.detail
    return detail if isinstance(detail, str) else str(detail or "")


def _is_retryable_image_error(exc: Exception) -> bool:
    if isinstance(exc, HTTPException):
        status_code = int(exc.status_code or 500)
        if status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
            return True
        detail = _http_exception_detail(exc).lower()
        return any(token in detail for token in ("timeout", "timed out", "temporarily", "rate limit", "connection"))
    return True


async def _upload_pptx_via_asset_api(
    *,
    pptx_path: str,
    request: Request,
    download_url: str,
) -> Optional[Dict[str, Any]]:
    path = Path((pptx_path or "").strip())
    if not path.is_file():
        return None
    data = path.read_bytes()
    if not data:
        return None
    base = str(request.base_url).rstrip("/")
    headers: Dict[str, str] = {}
    auth = (request.headers.get("Authorization") or "").strip()
    if auth:
        headers["Authorization"] = auth
    install_id = (request.headers.get("X-Installation-Id") or request.headers.get("x-installation-id") or "").strip()
    if install_id:
        headers["X-Installation-Id"] = install_id
    async with httpx.AsyncClient(timeout=180.0, trust_env=False) as client:
        resp = await client.post(
            f"{base}/api/assets/upload",
            files={"file": (path.name, data, _PPTX_CONTENT_TYPE)},
            headers=headers,
        )
    if resp.status_code >= 400:
        detail = ""
        try:
            body = resp.json()
            detail = str(body.get("detail") or body.get("message") or "")
        except Exception:
            detail = (resp.text or "")[:800]
        raise HTTPException(status_code=resp.status_code, detail=detail or "PPT upload to asset library failed")
    row = resp.json() if resp.content else {}
    if not isinstance(row, dict) or not str(row.get("asset_id") or "").strip():
        raise HTTPException(status_code=502, detail="PPT upload to asset library returned no asset_id")
    source_url = str(row.get("source_url") or "").strip()
    return {
        "asset_id": str(row.get("asset_id") or ""),
        "filename": path.name,
        "stored_filename": str(row.get("filename") or ""),
        "media_type": str(row.get("media_type") or "document"),
        "file_size": int(row.get("file_size") or len(data)),
        "source_url": source_url or download_url,
        "url": source_url or download_url,
        "download_url": download_url,
        "local_path": str(path),
        "display_text": f"PPT 文件 · {path.name}",
        "tags": "auto,ppt,create_ppt",
    }


async def _generate_one_fullpage_slide(
    *,
    index: int,
    total: int,
    slide_prompt: str,
    payload: CreatePptPayload,
    request: Request,
    current_user: _ServerUser,
    images_dir: Path,
) -> Dict[str, Any]:
    prompt = _image_ppt_prompt(slide_prompt, payload.topic, index, total, str(payload.instructions or "").strip())
    started = time.perf_counter()
    last_error = ""
    for attempt in range(1, _IMAGE_FULLPAGE_MAX_RETRIES + 1):
        db = SessionLocal()
        try:
            result = await _generate_image_studio_core(
                request=request,
                current_user=current_user,
                db=db,
                prompt=prompt,
                model=(payload.image_model or "gpt-image-2"),
                aspect_ratio=(payload.aspect_ratio or "16:9"),
                quality=(payload.image_quality or "high"),
                background=(payload.image_background or "opaque"),
                upload_payloads=[],
                auto_save=False,
            )
            previews = result.get("images") if isinstance(result, dict) else None
            if not isinstance(previews, list) or not previews:
                raise HTTPException(status_code=502, detail=f"Slide {index} generated no image result")
            local_path = await _download_or_decode_preview(previews[0], images_dir / f"slide_{index:02d}.png")
            return {
                "index": index,
                "prompt": prompt,
                "local_path": local_path,
                "preview": previews[0],
                "meta": result.get("meta") if isinstance(result.get("meta"), dict) else {},
                "attempts": attempt,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
        except Exception as exc:
            status_code = exc.status_code if isinstance(exc, HTTPException) else 500
            last_error = _http_exception_detail(exc) if isinstance(exc, HTTPException) else str(exc)
            if attempt >= _IMAGE_FULLPAGE_MAX_RETRIES or not _is_retryable_image_error(exc):
                raise HTTPException(
                    status_code=status_code or 500,
                    detail=f"Slide {index} image generation failed after {attempt} attempt(s): {last_error}",
                ) from exc
            await asyncio.sleep(_IMAGE_FULLPAGE_RETRY_BASE_DELAY_SEC * attempt)
        finally:
            db.close()

    raise HTTPException(status_code=500, detail=f"Slide {index} image generation failed: {last_error}")


async def _generate_fullpage_slides_in_batches(
    *,
    raw_prompts: List[str],
    payload: CreatePptPayload,
    request: Request,
    current_user: _ServerUser,
    images_dir: Path,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    total = len(raw_prompts)
    for batch_start in range(0, total, _IMAGE_FULLPAGE_BATCH_SIZE):
        batch = raw_prompts[batch_start : batch_start + _IMAGE_FULLPAGE_BATCH_SIZE]
        tasks = [
            _generate_one_fullpage_slide(
                index=batch_start + offset + 1,
                total=total,
                slide_prompt=slide_prompt,
                payload=payload,
                request=request,
                current_user=current_user,
                images_dir=images_dir,
            )
            for offset, slide_prompt in enumerate(batch)
        ]
        results.extend(await asyncio.gather(*tasks))
    return sorted(results, key=lambda item: int(item.get("index") or 0))


async def _run_image_fullpage_ppt(
    *,
    payload: CreatePptPayload,
    request: Request,
    current_user: _ServerUser,
) -> Dict[str, Any]:
    max_slides = min(max(int(payload.slide_count or 1), 1), _IMAGE_FULLPAGE_MAX_SLIDES)
    raw_prompts = [str(item or "").strip() for item in (payload.slide_prompts or []) if str(item or "").strip()]
    if not raw_prompts:
        raw_prompts = _split_outline_to_slide_prompts(payload.outline_markdown or "", payload.topic, max_slides)
    raw_prompts = raw_prompts[:max_slides]
    if not raw_prompts:
        raise HTTPException(status_code=400, detail="Please provide a PPT topic or per-slide image prompts")

    run_dir = create_ppt_run_dir(payload.topic or "image_ppt")
    images_dir = run_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    generated_images = await _generate_fullpage_slides_in_batches(
        raw_prompts=raw_prompts,
        payload=payload,
        request=request,
        current_user=current_user,
        images_dir=images_dir,
    )
    filename = str(payload.filename or "").strip()
    if not filename:
        filename = f"{safe_create_ppt_name(payload.topic or 'image_ppt')}.pptx"
    if not filename.lower().endswith(".pptx"):
        filename += ".pptx"
    pptx_path = create_fullpage_image_ppt(
        image_paths=[str(item.get("local_path") or "") for item in generated_images],
        output_path=str(run_dir / filename),
        notes=raw_prompts,
    )
    out_path = Path(pptx_path)
    return {
        "ok": True,
        "mode": "image_fullpage",
        "topic": payload.topic,
        "run_dir": str(run_dir),
        "pptx_path": str(out_path),
        "filename": out_path.name,
        "size_bytes": out_path.stat().st_size if out_path.exists() else 0,
        "images": generated_images,
        "saved_assets": [],
        "generation_meta": {
            "slide_count": len(raw_prompts),
            "batch_size": _IMAGE_FULLPAGE_BATCH_SIZE,
            "max_retries": _IMAGE_FULLPAGE_MAX_RETRIES,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "total_attempts": sum(int(item.get("attempts") or 0) for item in generated_images),
            "retried_slides": [
                int(item.get("index") or 0)
                for item in generated_images
                if int(item.get("attempts") or 0) > 1
            ],
        },
    }


@router.post("/api/create-ppt/run")
async def create_ppt_run(
    payload: CreatePptPayload,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
):
    try:
        mode = (payload.mode or "").strip().lower()
        topic = (payload.topic or "").strip()
        outline = (payload.outline_markdown or "").strip()
        slide_prompts = [str(item or "").strip() for item in (payload.slide_prompts or []) if str(item or "").strip()]
        if not topic and not outline and not slide_prompts:
            raise HTTPException(status_code=400, detail="PPT generation requires topic, outline_markdown, or slide_prompts")
        if topic == "PPT" and not outline and not slide_prompts:
            raise HTTPException(status_code=400, detail="Please provide a specific PPT topic")
        if mode in {"image_fullpage", "image", "fullpage_image"}:
            result = await _run_image_fullpage_ppt(
                payload=payload,
                request=request,
                current_user=current_user,
            )
        elif mode in {"ai", "topic"}:
            result = await _run_ai_image_ppt(
                payload=payload,
                request=request,
                current_user=current_user,
            )
        else:
            result = run_create_ppt_sync(payload.model_dump())
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PPT generation failed: {exc}") from exc

    token = create_download_token(str(result.get("pptx_path") or ""))
    base = str(request.base_url).rstrip("/")
    result["download_token"] = token
    result["download_url"] = f"{base}/api/create-ppt/files/{token}"
    ppt_asset = None
    try:
        ppt_asset = await _upload_pptx_via_asset_api(
            pptx_path=str(result.get("pptx_path") or ""),
            request=request,
            download_url=result["download_url"],
        )
    except Exception as exc:
        logger.warning("[create_ppt] upload pptx via asset api failed path=%s err=%s", result.get("pptx_path"), exc)
    if ppt_asset:
        result["asset"] = ppt_asset
        saved = result.get("saved_assets") if isinstance(result.get("saved_assets"), list) else []
        result["saved_assets"] = [ppt_asset] + saved
    return {"ok": True, "result": result}


@router.get("/api/create-ppt/files/{token}")
def create_ppt_file(token: str):
    path = resolve_download_token(token)
    return FileResponse(
        path,
        media_type=_PPTX_CONTENT_TYPE,
        filename=Path(path).name,
    )
