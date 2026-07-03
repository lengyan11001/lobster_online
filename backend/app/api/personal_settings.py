"""Personal settings and memory-document preparation helpers."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from .auth import _ServerUser, get_current_user_for_local
from .creative_film_studio import _installation_id_from_request, _raw_token_from_request, _server_proxy_base
from .openclaw_memory import (
    _MAX_UPLOAD_BYTES,
    _decode_text_payload,
    _doc_id_for,
    _limit_extracted_text,
    _load_index,
    _memory_markdown,
    _mirror_local_memory_to_server,
    _read_canonical_memory_content,
    _save_index,
    _short_title,
    _user_dir,
    _utc_now_iso,
    _write_workspace_memory,
)
from ..core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
_MEDIA_SUFFIXES = _IMAGE_SUFFIXES | _VIDEO_SUFFIXES
_MAX_IMAGE_BYTES = 12 * 1024 * 1024
_MAX_VIDEO_BYTES = 80 * 1024 * 1024
_MAX_MEMORY_SOURCE_CHARS = 80_000
_URL_RE = re.compile(r"https?://[^\s,，]+", re.I)
_DOC_TYPES = (
    ("brand_product_intro", "产品介绍"),
    ("product_service_faq", "百问百答"),
    ("short_video_scripts", "短视频口播稿"),
)
_DOC_TYPE_LABELS = {key: label for key, label in _DOC_TYPES}


class MemoryDocumentSaveBody(BaseModel):
    title: str = ""
    notes: str = ""
    documents: dict[str, str] = Field(default_factory=dict)


class RawMemorySaveBody(BaseModel):
    title: str = ""
    notes: str = ""
    content: str = ""
    target_doc_id: str = ""
    mode: str = "new"


def _safe_text(value: Any, max_len: int = 2000) -> str:
    text = str(value or "").replace("\x00", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()[:max_len]


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I).strip()
    raw = re.sub(r"\s*```$", "", raw).strip()
    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for item in candidates:
        try:
            data = json.loads(item)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {}


def _format_generated_memory(documents: dict[str, str]) -> str:
    lines: list[str] = []
    for key, label in _DOC_TYPES:
        text = _safe_text(documents.get(key), 80_000)
        if text:
            lines.append(f"# {label}\n\n{text}")
    return "\n\n---\n\n".join(lines).strip()


async def _save_single_memory_document(
    request: Request,
    current_user: _ServerUser,
    *,
    title: str,
    notes: str,
    text: str,
    filename_suffix: str = ".md",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = _limit_extracted_text(text)
    if not text:
        raise HTTPException(status_code=400, detail="没有可保存的记忆内容")
    created_at = _utc_now_iso()
    clean_title = _short_title(title, "个人记忆资料")
    filename = f"{clean_title}{filename_suffix if filename_suffix.startswith('.') else '.md'}"
    sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    doc_id = _doc_id_for(current_user.id, filename, sha256, created_at)
    base_dir = Path(__file__).resolve().parent.parent.parent.parent
    docs_dir = _user_dir(current_user.id) / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "id": doc_id,
        "user_id": current_user.id,
        "title": clean_title,
        "filename": filename,
        "notes": _safe_text(notes, 500),
        "size": len(text.encode("utf-8")),
        "sha256": sha256,
        "created_at": created_at,
        "source": "local_user",
        "memory_layer": "personal",
        "meta": meta or {"source": "personal_settings"},
    }
    markdown_path = docs_dir / f"{doc_id}.md"
    markdown_path.write_text(_memory_markdown(record, text), encoding="utf-8")
    record["canonical_path"] = markdown_path.relative_to(base_dir).as_posix()
    record["workspace_paths"] = _write_workspace_memory(record, text)
    record["cloud_mirror"] = await _mirror_local_memory_to_server(request, record, text)
    docs = [d for d in _load_index(current_user.id) if d.get("id") != doc_id]
    docs.insert(0, record)
    _save_index(current_user.id, docs)
    return record


def _find_memory_record(user_id: int, doc_id: str) -> dict[str, Any] | None:
    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "", (doc_id or "").strip())[:64]
    if not clean_id:
        return None
    docs = _load_index(user_id)
    return next((doc for doc in docs if str(doc.get("id") or "") == clean_id), None)


def _split_doc_ids(value: str) -> list[str]:
    raw = _safe_text(value, 20_000)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            items = [str(item or "") for item in parsed]
        else:
            items = [raw]
    except Exception:
        items = re.split(r"[\s,，;；]+", raw)
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean_id = re.sub(r"[^a-zA-Z0-9_-]", "", (item or "").strip())[:64]
        if clean_id and clean_id not in seen:
            seen.add(clean_id)
            out.append(clean_id)
    return out[:20]


async def _update_memory_document(
    request: Request,
    current_user: _ServerUser,
    *,
    target_doc_id: str,
    title: str,
    notes: str,
    text: str,
    mode: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "", (target_doc_id or "").strip())[:64]
    docs = _load_index(current_user.id)
    found = next((doc for doc in docs if str(doc.get("id") or "") == clean_id), None)
    if not found:
        raise HTTPException(status_code=404, detail="要扩展的记忆文件不存在")
    source = str(found.get("source") or "local_user")
    layer = str(found.get("memory_layer") or "").strip()
    if layer == "agent" or str(found.get("origin") or "") == "agent_memory":
        raise HTTPException(status_code=403, detail="代理商记忆不能在本机编辑")
    if source.startswith("cloud_"):
        raise HTTPException(status_code=403, detail="云端同步资料需要在下发端编辑")
    old_text = _read_canonical_memory_content(found, max_chars=500_000)
    new_text = _limit_extracted_text(text)
    if not new_text:
        raise HTTPException(status_code=400, detail="没有可保存的记忆内容")
    if mode == "overwrite":
        final_text = new_text
    else:
        raise HTTPException(status_code=400, detail="保存方式无效")
    found["notes"] = _safe_text(notes or found.get("notes") or "", 500)
    found["size"] = len(final_text.encode("utf-8"))
    found["sha256"] = hashlib.sha256(final_text.encode("utf-8")).hexdigest()
    found["updated_at"] = _utc_now_iso()
    found["meta"] = {**(found.get("meta") if isinstance(found.get("meta"), dict) else {}), **(meta or {})}
    canon = Path(__file__).resolve().parent.parent.parent.parent / str(found.get("canonical_path") or "")
    try:
        if not canon.is_file() or _user_dir(current_user.id).resolve() not in canon.resolve().parents:
            raise RuntimeError("canonical memory path invalid")
        canon.write_text(_memory_markdown(found, final_text), encoding="utf-8")
    except Exception as exc:
        logger.warning("[personal-settings] update memory failed doc_id=%s: %s", clean_id, exc)
        raise HTTPException(status_code=500, detail="更新个人记忆文件失败")
    found["workspace_paths"] = _write_workspace_memory(found, final_text)
    found["cloud_mirror"] = await _mirror_local_memory_to_server(request, found, final_text)
    _save_index(current_user.id, docs)
    return found


def _fallback_documents(source_text: str, url_text: str = "") -> dict[str, str]:
    text = _safe_text(source_text, 60_000)
    urls = _safe_text(url_text, 5000)
    base = text or urls or "暂无可抽取文本；请补充企业资料、产品说明、客户问答或口播稿。"
    return {
        "brand_product_intro": (
            "以下为已上传资料摘要，请人工检查后保存：\n\n"
            + base[:12_000]
        ),
        "product_service_faq": (
            "根据当前资料可先沉淀为产品/服务问答素材；建议补充客户常问问题、价格、流程、交付范围和售后口径。\n\n"
            + base[:12_000]
        ),
        "short_video_scripts": (
            "根据当前资料可先沉淀为短视频口播素材；建议补充案例、场景、痛点、结果和禁用表达。\n\n"
            + base[:12_000]
        ),
    }


async def _call_sutui_chat(
    request: Request,
    user: _ServerUser,
    *,
    messages: list[dict[str, Any]],
    temperature: float = 0.35,
    timeout: float = 180.0,
) -> str:
    token = _raw_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="需要登录后才能调用 AI 理解资料")
    model = (
        getattr(settings, "lobster_orchestration_sutui_chat_model", "")
        or getattr(settings, "lobster_default_sutui_chat_model", "")
        or "gpt-4o-mini"
    )
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "X-Installation-Id": _installation_id_from_request(request, user.id),
    }
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resp = await client.post(f"{_server_proxy_base()}/api/sutui-chat/completions", json=payload, headers=headers)
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"AI 资料理解失败 HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    data = resp.json() if resp.content else {}
    try:
        return str(data["choices"][0]["message"]["content"] or "")
    except Exception:
        return json.dumps(data, ensure_ascii=False)


def _data_url(filename: str, data: bytes, content_type: str = "") -> str:
    suffix = Path(filename or "").suffix.lower()
    mime = (content_type or "").strip()
    if not mime:
        if suffix in {".jpg", ".jpeg"}:
            mime = "image/jpeg"
        elif suffix == ".png":
            mime = "image/png"
        elif suffix == ".webp":
            mime = "image/webp"
        elif suffix == ".gif":
            mime = "image/gif"
        else:
            mime = "application/octet-stream"
    return "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")


async def _describe_image(
    request: Request,
    user: _ServerUser,
    *,
    filename: str,
    data: bytes,
    content_type: str = "",
) -> str:
    if len(data) > _MAX_IMAGE_BYTES:
        return f"图片 {filename} 超过 12MB，未调用视觉理解。"
    prompt = (
        "请理解这张图片中和企业资料/产品服务/案例/人物IP有关的信息。"
        "只输出可写入记忆库的事实描述，不要编造不可见内容。"
    )
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": _data_url(filename, data, content_type)}},
    ]
    try:
        return await _call_sutui_chat(
            request,
            user,
            messages=[
                {"role": "system", "content": "你是资料理解助手，负责把图片内容转成可用于企业 AI 记忆的事实资料。"},
                {"role": "user", "content": content},
            ],
            temperature=0.2,
            timeout=120.0,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("[personal-settings] image describe failed filename=%s: %s", filename, exc)
        return f"图片 {filename} 视觉理解失败：{exc}"


def _split_urls(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in _URL_RE.finditer(text or ""):
        url = match.group(0).strip().rstrip("。；;，,)")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= 20:
            break
    return out


def _looks_like_image_url(url: str) -> bool:
    clean = (url or "").split("?", 1)[0].split("#", 1)[0].lower()
    return any(clean.endswith(suffix) for suffix in _IMAGE_SUFFIXES)


def _looks_like_video_url(url: str) -> bool:
    clean = (url or "").split("?", 1)[0].split("#", 1)[0].lower()
    return any(clean.endswith(suffix) for suffix in _VIDEO_SUFFIXES)


async def _describe_image_url(request: Request, user: _ServerUser, url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True, trust_env=False) as client:
            resp = await client.get(url)
        if resp.status_code >= 400:
            return f"图片链接 {url} 下载失败 HTTP {resp.status_code}"
        ctype = resp.headers.get("content-type", "")
        data = resp.content or b""
        if len(data) > _MAX_IMAGE_BYTES:
            return f"图片链接 {url} 超过 12MB，未调用视觉理解。"
        return await _describe_image(request, user, filename=Path(url).name or "image-url", data=data, content_type=ctype)
    except Exception as exc:
        logger.warning("[personal-settings] image url describe failed url=%s: %s", url[:200], exc)
        return f"图片链接 {url} 视觉理解失败：{exc}"


async def _download_direct_media_url(url: str, *, max_bytes: int) -> bytes:
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True, trust_env=False) as client:
        async with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}")
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError("media too large")
                chunks.append(chunk)
    return b"".join(chunks)


def _ffmpeg_path() -> str:
    base = Path(__file__).resolve().parent.parent.parent.parent
    candidates = [
        base / "deps" / "ffmpeg" / "ffmpeg.exe",
        base / "skills" / "comfly_veo3_daihuo_video" / "tools" / "ffmpeg" / "windows" / "ffmpeg.exe",
    ]
    for item in candidates:
        if item.is_file():
            return str(item)
    return "ffmpeg"


def _extract_video_frames(data: bytes, filename: str, max_frames: int = 3) -> list[tuple[str, bytes]]:
    suffix = Path(filename or "").suffix.lower() or ".mp4"
    frames: list[tuple[str, bytes]] = []
    with tempfile.TemporaryDirectory(prefix="lobster-video-frames-") as tmp:
        tmp_dir = Path(tmp)
        src = tmp_dir / ("source" + suffix)
        src.write_bytes(data)
        out_pattern = tmp_dir / "frame_%02d.jpg"
        cmd = [
            _ffmpeg_path(),
            "-y",
            "-i",
            str(src),
            "-vf",
            f"fps=1/5,scale='min(1280,iw)':-2",
            "-frames:v",
            str(max_frames),
            "-q:v",
            "4",
            str(out_pattern),
        ]
        try:
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=45,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
        except Exception as exc:
            logger.warning("[personal-settings] ffmpeg frame extraction failed filename=%s: %s", filename, exc)
            return []
        for frame in sorted(tmp_dir.glob("frame_*.jpg"))[:max_frames]:
            try:
                frames.append((frame.name, frame.read_bytes()))
            except OSError:
                continue
    return frames


async def _describe_video(
    request: Request,
    user: _ServerUser,
    *,
    filename: str,
    data: bytes,
) -> str:
    frames = _extract_video_frames(data, filename, max_frames=3)
    if not frames:
        return (
            f"视频 {filename} 已收到，但关键帧抽取失败。"
            "请补充字幕、口播稿或关键截图，以便形成更准确的记忆。"
        )
    descriptions: list[str] = []
    for idx, (frame_name, frame_data) in enumerate(frames, start=1):
        desc = await _describe_image(
            request,
            user,
            filename=f"{filename}-{frame_name}",
            data=frame_data,
            content_type="image/jpeg",
        )
        descriptions.append(f"关键帧 {idx}：{desc}")
    return "\n\n".join(descriptions).strip()


async def _describe_video_url(request: Request, user: _ServerUser, url: str) -> str:
    try:
        data = await _download_direct_media_url(url, max_bytes=_MAX_VIDEO_BYTES)
        return await _describe_video(request, user, filename=Path(url).name or "video-url.mp4", data=data)
    except Exception as exc:
        logger.warning("[personal-settings] video url describe failed url=%s: %s", url[:200], exc)
        return (
            f"视频链接 {url} 暂未能直接抽帧理解：{exc}。"
            "如果这是平台页面链接，请补充字幕、口播稿或关键截图。"
        )


async def _collect_sources(
    request: Request,
    user: _ServerUser,
    *,
    files: list[UploadFile],
    urls: str,
    direct_intro: str,
    direct_faq: str,
    direct_scripts: str,
) -> tuple[str, list[dict[str, Any]], dict[str, str]]:
    source_parts: list[str] = []
    source_meta: list[dict[str, Any]] = []
    direct_docs = {
        "brand_product_intro": _safe_text(direct_intro, 80_000),
        "product_service_faq": _safe_text(direct_faq, 80_000),
        "short_video_scripts": _safe_text(direct_scripts, 80_000),
    }
    for key, label in _DOC_TYPES:
        if direct_docs.get(key):
            source_parts.append(f"## 已有{label}\n\n{direct_docs[key]}")
            source_meta.append({"type": "direct_document", "name": label})
    url_text = _safe_text(urls, 20_000)
    if url_text:
        links = _split_urls(url_text)
        source_parts.append("## 图片/视频/网页链接\n\n" + url_text)
        source_meta.append({"type": "links", "count": len(links)})
        for url in links:
            if _looks_like_image_url(url):
                desc = await _describe_image_url(request, user, url)
                source_parts.append(f"## 图片链接理解：{url}\n\n{desc}")
                source_meta.append({"type": "image_url", "url": url})
            elif _looks_like_video_url(url):
                desc = await _describe_video_url(request, user, url)
                source_parts.append(f"## 视频链接理解：{url}\n\n{desc}")
                source_meta.append({"type": "video_url", "url": url})
    for file in files:
        filename = (file.filename or "upload").strip() or "upload"
        suffix = Path(filename).suffix.lower()
        limit = _MAX_VIDEO_BYTES if suffix in _VIDEO_SUFFIXES else _MAX_UPLOAD_BYTES
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(status_code=413, detail=f"{filename} 文件过大")
        if suffix in _IMAGE_SUFFIXES:
            desc = await _describe_image(request, user, filename=filename, data=data, content_type=file.content_type or "")
            source_parts.append(f"## 图片理解：{filename}\n\n{desc}")
            source_meta.append({"type": "image", "filename": filename, "size": len(data)})
        elif suffix in _VIDEO_SUFFIXES:
            desc = await _describe_video(request, user, filename=filename, data=data)
            source_parts.append(f"## 视频理解：{filename}\n\n{desc}")
            source_meta.append({"type": "video", "filename": filename, "size": len(data)})
        else:
            text = _decode_text_payload(data, filename)
            source_parts.append(f"## 文档资料：{filename}\n\n{text}")
            source_meta.append({"type": "document", "filename": filename, "size": len(data)})
    source_text = "\n\n".join(source_parts).strip()
    if len(source_text) > _MAX_MEMORY_SOURCE_CHARS:
        source_text = source_text[:_MAX_MEMORY_SOURCE_CHARS].rstrip() + "\n\n[内容过长，已截断用于本次理解]"
    return source_text, source_meta, direct_docs


@router.post("/api/personal-settings/memory-documents/generate", summary="理解资料并生成记忆内容")
async def generate_memory_documents(
    request: Request,
    files: List[UploadFile] = File(default=[]),
    urls: str = Form(""),
    direct_intro: str = Form(""),
    direct_faq: str = Form(""),
    direct_scripts: str = Form(""),
    doc_type: str = Form("brand_product_intro"),
    reference_doc_ids: str = Form(""),
    target_doc_id: str = Form(""),
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    doc_type = (doc_type or "brand_product_intro").strip()
    if doc_type not in _DOC_TYPE_LABELS:
        raise HTTPException(status_code=400, detail="生成类型无效")
    reference_ids = _split_doc_ids(reference_doc_ids)
    if not reference_ids and target_doc_id:
        reference_ids = _split_doc_ids(target_doc_id)
    reference_contexts: list[str] = []
    for ref_id in reference_ids:
        target = _find_memory_record(current_user.id, ref_id)
        if not target:
            raise HTTPException(status_code=404, detail="选择的参考记忆文件不存在")
        title = _safe_text(target.get("title") or target.get("filename") or ref_id, 200)
        content = _read_canonical_memory_content(target, max_chars=60_000)
        if content:
            reference_contexts.append(f"## 参考记忆：{title}\n\n{content}")
    target_context = "\n\n---\n\n".join(reference_contexts)
    source_text, source_meta, direct_docs = await _collect_sources(
        request,
        current_user,
        files=files or [],
        urls=urls,
        direct_intro=direct_intro,
        direct_faq=direct_faq,
        direct_scripts=direct_scripts,
    )
    if not source_text:
        raise HTTPException(status_code=400, detail="请上传资料、填写链接，或粘贴资料内容")

    system = (
        "你是企业资料整理助手。你要把用户投喂的资料整理成一份可长期保存的 AI 记忆文档。"
        "只基于资料事实，不要编造价格、案例、参数、客户名。"
        "用户提供的示例只是参考结构和写法，不要把示例里的品牌名、公司名、人名照搬进输出。"
    )
    label = _DOC_TYPE_LABELS[doc_type]
    user_prompt = (
        f"请基于下面资料生成「{label}」。\n\n"
        "要求：\n"
        "1. 只输出正文，不要输出 JSON，不要 Markdown 代码块，不要解释生成过程。\n"
        "2. 如果资料不足，明确写“资料未提供”，不要猜。\n"
        "3. 保留关键名词、产品卖点、服务流程、客户痛点、禁用或慎用表达。\n"
        "4. 如果提供了参考记忆，请结合参考资料补充、修正和整理，不要简单重复。\n"
        "5. 内容用于每日 IP 日更、获客、客服等技能，不要加入无意义的导流话术。\n\n"
        + (f"参考记忆原文：\n{target_context}\n\n" if target_context else "")
        + "资料如下：\n"
        + source_text
    )
    try:
        reply = await _call_sutui_chat(
            request,
            current_user,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user_prompt}],
            temperature=0.25,
            timeout=240.0,
        )
        text = _safe_text(reply, 100_000)
        if not text:
            text = _safe_text(_fallback_documents(source_text, urls).get(doc_type), 100_000)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("[personal-settings] memory doc generation fallback: %s", exc)
        text = _safe_text(_fallback_documents(source_text, urls).get(doc_type), 100_000)
    documents = {doc_type: text}
    return {
        "ok": True,
        "doc_type": doc_type,
        "label": label,
        "documents": documents,
        "sources": source_meta,
        "raw_text": text,
    }


@router.post("/api/personal-settings/memory-documents/save", summary="保存记忆附件为 OpenClaw 个人记忆")
async def save_memory_documents(
    request: Request,
    body: MemoryDocumentSaveBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    input_docs = body.documents or {}
    rows = [(key, label, _safe_text(input_docs.get(key), 100_000)) for key, label in _DOC_TYPES]
    rows = [(key, label, text) for key, label, text in rows if text]
    if not rows:
        raise HTTPException(status_code=400, detail="没有可保存的记忆内容")
    if not (body.title or "").strip():
        raise HTTPException(status_code=400, detail="请填写文档名字")
    base_title = _short_title(body.title, "IP日更个人资料附件")
    saved: list[dict[str, Any]] = []
    for key, label, raw_text in rows:
        record = await _save_single_memory_document(
            request,
            current_user,
            title=f"{base_title}-{label}",
            notes=body.notes or f"个人设置生成：{label}",
            text=raw_text,
            meta={"source": "personal_settings", "document_type": key, "document_label": label},
        )
        saved.append(record)
    return {
        "ok": True,
        "documents": saved,
        "document": saved[0] if saved else None,
        "content_text": _format_generated_memory({key: text for key, _label, text in rows}),
    }


@router.post("/api/personal-settings/memory-documents/save-raw", summary="直接保存原始资料为一份个人记忆")
async def save_raw_memory_document(
    request: Request,
    body: RawMemorySaveBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    mode = (body.mode or "new").strip()
    meta = {"source": "personal_settings", "document_type": "raw_memory", "document_label": "原始资料", "save_mode": mode}
    if mode == "overwrite":
        record = await _update_memory_document(
            request,
            current_user,
            target_doc_id=body.target_doc_id,
            title=body.title,
            notes=body.notes or "个人设置更新的原始资料",
            text=body.content,
            mode=mode,
            meta=meta,
        )
    elif mode == "new":
        if not (body.title or "").strip():
            raise HTTPException(status_code=400, detail="请填写文档名字")
        record = await _save_single_memory_document(
            request,
            current_user,
            title=body.title or "个人记忆资料",
            notes=body.notes or "个人设置直接保存的原始资料",
            text=body.content,
            meta=meta,
        )
    else:
        raise HTTPException(status_code=400, detail="保存方式无效")
    return {"ok": True, "document": record, "documents": [record], "content_text": body.content}


@router.post("/api/personal-settings/memory-documents/save-upload", summary="直接保存上传资料为一份个人记忆")
async def save_uploaded_memory_document(
    request: Request,
    files: List[UploadFile] = File(default=[]),
    urls: str = Form(""),
    raw_text: str = Form(""),
    title: str = Form(""),
    notes: str = Form(""),
    target_doc_id: str = Form(""),
    mode: str = Form("new"),
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    parts: list[str] = []
    raw = _safe_text(raw_text, 100_000)
    if raw:
        parts.append(raw)
    url_text = _safe_text(urls, 20_000)
    if url_text:
        parts.append("## 资料链接\n\n" + url_text)
    for file in files or []:
        filename = (file.filename or "upload").strip() or "upload"
        suffix = Path(filename).suffix.lower()
        limit = _MAX_VIDEO_BYTES if suffix in _VIDEO_SUFFIXES else _MAX_UPLOAD_BYTES
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(status_code=413, detail=f"{filename} 文件过大")
        if suffix in _MEDIA_SUFFIXES:
            parts.append(f"## 媒体文件\n\n已上传：{filename}\n\n如需理解图片或视频内容，请点击 AI 理解后再保存。")
        else:
            text = _decode_text_payload(data, filename)
            parts.append(f"## 上传文件：{filename}\n\n{text}")
    content = "\n\n---\n\n".join(part for part in parts if part.strip()).strip()
    mode = (mode or "new").strip()
    meta = {"source": "personal_settings", "document_type": "uploaded_raw_memory", "document_label": "上传原始资料", "save_mode": mode}
    if mode == "overwrite":
        record = await _update_memory_document(
            request,
            current_user,
            target_doc_id=target_doc_id,
            title=title,
            notes=notes or "个人设置更新的上传资料",
            text=content,
            mode=mode,
            meta=meta,
        )
    elif mode == "new":
        if not (title or "").strip():
            raise HTTPException(status_code=400, detail="请填写文档名字")
        record = await _save_single_memory_document(
            request,
            current_user,
            title=title or "个人记忆资料",
            notes=notes or "个人设置直接保存的上传资料",
            text=content,
            meta=meta,
        )
    else:
        raise HTTPException(status_code=400, detail="保存方式无效")
    return {"ok": True, "document": record, "documents": [record], "content_text": content}


@router.get("/api/personal-settings/memory-documents/{doc_id}/preview", summary="预览个人记忆正文")
async def preview_memory_document(
    doc_id: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "", (doc_id or "").strip())[:64]
    docs = _load_index(current_user.id)
    found = next((doc for doc in docs if str(doc.get("id") or "") == clean_id), None)
    if not found:
        raise HTTPException(status_code=404, detail="资料不存在")
    return {"ok": True, "document": found, "content_text": _read_canonical_memory_content(found, max_chars=60_000)}
