"""Creative-film studio helpers.

This page turns selected OpenClaw personal-memory documents into an image prompt,
generates one preview image, then writes platform-specific copy that can be
published manually.
"""
from __future__ import annotations

import json
import logging
import os
import re
import asyncio
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import _ServerUser, get_current_user_for_local
from .assets import (
    SaveAssetReq,
    _compute_save_url_dedupe_key,
    _final_save_url_dedupe_key,
    _resolve_v3_tasks_url_for_download,
    _save_asset_from_url_locked,
    _save_url_lock_for,
)
from .openclaw_memory import _load_index, _read_canonical_memory_content
from .wechat_article import _extract_image_url
from ..core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_LOBSTER_SERVER_PUBLIC = "https://bhzn.top"
_PIPELINE_ID = "goal.image.pipeline"
_SOURCE_MODE = "ai_image"
_MAX_SELECTED_DOCS = 8
_MAX_MEMORY_CHARS_PER_DOC = 2400
_MAX_MEMORY_CHARS_TOTAL = 12000
_PLATFORM_REQUIREMENTS: Dict[str, Dict[str, str]] = {
    "douyin": {
        "label": "抖音",
        "requirements": (
            "抖音适合强钩子、短句、情绪推进和评论互动。输出直接可发布的图文/视频配文："
            "标题 20 字内；正文 120-260 字；开头 3 秒式钩子；包含 3-6 个高相关话题标签；"
            "避免公众号长文腔，避免夸大承诺，适合竖屏短视频或图文发布。"
        ),
    },
    "wechat": {
        "label": "公众号",
        "requirements": (
            "公众号适合有标题、有摘要、有结构、有观点的长图文。输出一篇可直接复制发布的公众号文章："
            "包含标题、摘要、正文 Markdown；正文 900-1400 字；有二级标题、自然段、列表或引用；"
            "开头说明读者痛点，中段给方法和案例，结尾给行动建议；语气专业可信，不要营销口水话。"
        ),
    },
    "shipinhao": {
        "label": "视频号",
        "requirements": (
            "视频号适合微信生态的可信表达、轻知识、真实案例和转发价值。输出可直接发布的视频号配文："
            "标题 24 字内；正文 180-360 字；前两句讲清价值；适合熟人社交转发；"
            "包含 3-5 个话题标签和一个温和互动问题；语气稳重、有温度，少用夸张网感词。"
        ),
    },
}


class CreativeFilmImageIn(BaseModel):
    memory_doc_ids: List[str] = Field(default_factory=list)
    goal: str = ""
    title: str = ""
    direct_prompt: str = ""
    image_model: str = "gpt-image-2"
    aspect_ratio: str = "9:16"


class CreativeFilmCopyIn(BaseModel):
    memory_doc_ids: List[str] = Field(default_factory=list)
    goal: str = ""
    image_prompt: str = ""
    image_url: str = ""
    platform: str = "douyin"


def _raw_token_from_request(request: Request) -> str:
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _server_proxy_base() -> str:
    return (settings.auth_server_base or "").strip().rstrip("/") or _LOBSTER_SERVER_PUBLIC


def _installation_id_from_request(request: Request, user_id: int) -> str:
    xi = (request.headers.get("X-Installation-Id") or request.headers.get("x-installation-id") or "").strip()
    return xi or f"lobster-internal-{int(user_id)}"


def _image_size_for_aspect_ratio(aspect_ratio: str) -> str:
    sizes = {
        "1:1": "1024x1024",
        "3:2": "1536x1024",
        "16:9": "1920x1080",
        "2:3": "1024x1536",
        "9:16": "1080x1920",
    }
    return sizes.get((aspect_ratio or "").strip(), "1024x1536")


def _image_ratio_instruction(aspect_ratio: str) -> str:
    labels = {
        "1:1": "方形 1:1 构图",
        "3:2": "横屏 3:2 构图",
        "16:9": "宽横屏 16:9 封面构图",
        "2:3": "竖屏 2:3 海报构图",
        "9:16": "竖屏 9:16 短视频封面构图",
    }
    return labels.get((aspect_ratio or "").strip(), labels["9:16"])


def _public_image_generation_error() -> str:
    return "图片生成失败，已自动重试但仍未成功，请稍后重试或切换模型。"


def _is_retryable_image_error_text(text: str) -> bool:
    msg = (text or "").lower()
    return any(
        token in msg
        for token in (
            "http 408",
            "http 409",
            "http 425",
            "http 429",
            "http 5",
            "timeout",
            "connect",
            "connection",
            "read",
            "network",
            "new_api_error",
            "unknown_error",
            "upstream",
            "上游",
            "未接收到上游响应内容",
        )
    )


def _image_response_error_detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        if isinstance(data, dict):
            detail = data.get("detail") or data.get("error") or data.get("message")
            if isinstance(detail, str) and detail.strip():
                return detail.strip()
            return json.dumps(data, ensure_ascii=False)
    except Exception:
        pass
    return (resp.text or "").strip()


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(raw[start : end + 1])
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def _selected_memory_context(user_id: int, doc_ids: List[str]) -> tuple[str, List[Dict[str, Any]]]:
    wanted = []
    seen = set()
    for raw in doc_ids or []:
        clean = re.sub(r"[^a-f0-9]", "", str(raw or "").lower())[:32]
        if clean and clean not in seen:
            seen.add(clean)
            wanted.append(clean)
    if not wanted:
        raise HTTPException(status_code=400, detail="请选择至少一份记忆资料")
    if len(wanted) > _MAX_SELECTED_DOCS:
        raise HTTPException(status_code=400, detail=f"最多选择 {_MAX_SELECTED_DOCS} 份记忆资料")

    docs = _load_index(user_id)
    by_id = {str(d.get("id") or ""): d for d in docs if isinstance(d, dict)}
    selected: List[Dict[str, Any]] = []
    parts: List[str] = []
    total = 0
    for doc_id in wanted:
        doc = by_id.get(doc_id)
        if not doc:
            continue
        text = _read_canonical_memory_content(doc, max_chars=_MAX_MEMORY_CHARS_PER_DOC)
        title = str(doc.get("title") or doc.get("filename") or doc_id).strip()
        notes = str(doc.get("notes") or "").strip()
        if not text:
            text = notes or title
        chunk = f"### {title}\n"
        if notes:
            chunk += f"备注：{notes}\n"
        chunk += text
        if total + len(chunk) > _MAX_MEMORY_CHARS_TOTAL:
            remain = max(0, _MAX_MEMORY_CHARS_TOTAL - total)
            if remain <= 200:
                break
            chunk = chunk[:remain].rstrip() + "\n..."
        total += len(chunk)
        selected.append(
            {
                "id": doc_id,
                "title": title,
                "filename": str(doc.get("filename") or ""),
                "notes": notes,
            }
        )
        parts.append(chunk)
        if total >= _MAX_MEMORY_CHARS_TOTAL:
            break

    if not selected:
        raise HTTPException(status_code=404, detail="选中的记忆资料不存在或不可读取")
    return "\n\n".join(parts).strip(), selected


async def _call_sutui_chat(request: Request, user: _ServerUser, *, messages: List[Dict[str, str]], temperature: float = 0.72) -> str:
    token = _raw_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="需要登录后才能调用 AI 生成")
    model = (
        os.environ.get("CREATIVE_FILM_STUDIO_MODEL")
        or getattr(settings, "lobster_orchestration_sutui_chat_model", "")
        or getattr(settings, "lobster_default_sutui_chat_model", "")
        or "deepseek-chat"
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
    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
        resp = await client.post(f"{_server_proxy_base()}/api/sutui-chat/completions", json=payload, headers=headers)
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"AI 文本生成失败 HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    data = resp.json() if resp.content else {}
    try:
        return str(data["choices"][0]["message"]["content"] or "")
    except Exception:
        return json.dumps(data, ensure_ascii=False)


async def _generate_image(request: Request, user: _ServerUser, prompt: str, model: str, aspect_ratio: str) -> str:
    token = _raw_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="需要登录后才能生成图片")
    final_prompt = (
        f"{prompt.strip()}\n\n"
        f"画面比例：{_image_ratio_instruction(aspect_ratio)}。"
        "用于内容平台首图或短视频封面，主体明确，商业质感，真实可信，不要文字、不要水印、不要按钮、不要二维码。"
    )
    payload = {
        "prompt": final_prompt,
        "model": (model or "gpt-image-2").strip() or "gpt-image-2",
        "n": 1,
        "quality": "high",
        "size": _image_size_for_aspect_ratio(aspect_ratio),
        "response_format": "url",
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "X-Installation-Id": _installation_id_from_request(request, user.id),
    }
    last_error = ""
    data: Dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=180.0, trust_env=False) as client:
        for attempt in range(1, 3):
            try:
                resp = await client.post(f"{_server_proxy_base()}/api/comfly-proxy/v1/images/generations", json=payload, headers=headers)
                if resp.status_code >= 400:
                    detail = _image_response_error_detail(resp)
                    last_error = f"HTTP {resp.status_code}: {detail[:500]}"
                    if attempt < 2 and _is_retryable_image_error_text(last_error):
                        await asyncio.sleep(0.8 * attempt)
                        continue
                    raise HTTPException(status_code=502, detail=_public_image_generation_error())
                data = resp.json() if resp.content else {}
                image_url = _extract_image_url(data)
                if image_url:
                    return image_url
                last_error = _image_response_error_detail(resp) or "图片生成未返回可预览链接"
                if attempt < 2 and _is_retryable_image_error_text(last_error):
                    await asyncio.sleep(0.8 * attempt)
                    continue
                raise HTTPException(status_code=502, detail=_public_image_generation_error())
            except HTTPException:
                raise
            except Exception as exc:
                last_error = str(exc)
                if attempt >= 2 or not _is_retryable_image_error_text(last_error):
                    raise HTTPException(status_code=502, detail=_public_image_generation_error()) from exc
                await asyncio.sleep(0.8 * attempt)
    image_url = _extract_image_url(data)
    if not image_url:
        logger.warning("[creative_film_studio] image generation failed user_id=%s err=%s", user.id, last_error[:500])
        raise HTTPException(status_code=502, detail=_public_image_generation_error())
    return image_url


async def _save_generated_image_asset(
    *,
    request: Request,
    current_user: _ServerUser,
    image_url: str,
    image_prompt: str,
    model: str,
    title: str,
) -> Dict[str, Any]:
    url = (image_url or "").strip()
    if not url:
        raise HTTPException(status_code=502, detail="图片生成成功但没有可入库链接")
    body = SaveAssetReq(
        url=url,
        media_type="image",
        name=((title or "").strip()[:80] or "文案+创意图片") + ".png",
        tags=f"auto,{_PIPELINE_ID},{_SOURCE_MODE},creative_film_studio",
        prompt=(image_prompt or "").strip()[:500] or None,
        model=(model or "").strip()[:128] or None,
        dedupe_hint_url=url,
    )
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
    logger.info(
        "[creative_film_studio] save-url ok user_id=%s asset_id=%s source_url=%s",
        current_user.id,
        row.get("asset_id"),
        str(row.get("source_url") or "")[:120],
    )
    return row


def _fallback_image_prompt(memory_context: str, goal: str) -> str:
    base = (goal or "").strip() or "根据用户选择的记忆资料生成一张内容平台封面图"
    snippet = re.sub(r"\s+", " ", memory_context or "").strip()[:500]
    return (
        f"为“{base}”生成一张高质感商业内容封面图。"
        f"参考资料要点：{snippet}。"
        "画面聚焦核心产品/服务/人物场景，干净高级，真实摄影感，适合抖音、公众号和视频号传播。"
    )


def _normalize_image_plan(obj: Dict[str, Any], memory_context: str, goal: str) -> Dict[str, Any]:
    prompt = str(obj.get("image_prompt") or obj.get("prompt") or "").strip()
    if not prompt:
        prompt = _fallback_image_prompt(memory_context, goal)
    title = str(obj.get("title") or "").strip()[:80]
    if not title:
        title = (goal or "创意成片封面").strip()[:40] or "创意成片封面"
    summary = str(obj.get("summary") or "").strip()[:280]
    if not summary:
        summary = "已根据选中的记忆资料整理出可用于图片生成和平台文案的核心方向。"
    keywords = obj.get("keywords")
    if not isinstance(keywords, list):
        keywords = []
    keywords = [str(x).strip() for x in keywords if str(x).strip()][:8]
    return {
        "title": title,
        "summary": summary,
        "keywords": keywords,
        "image_prompt": prompt,
    }


def _compose_direct_image_prompt(direct_prompt: str, memory_context: str) -> str:
    prompt = (direct_prompt or "").strip()
    memory = re.sub(r"\s+", " ", memory_context or "").strip()
    if not memory:
        return prompt[:3000]
    return (
        f"{prompt[:1900]}\n\n"
        "记忆资料约束：以下资料是账号、行业、产品、身份和表达风格的底座。"
        "图片必须贴合已审核文案和配图提示，同时不能脱离这些记忆里的事实、场景和专业判断。\n"
        f"{memory[:1000]}"
    ).strip()[:3000]


def _normalize_copy(obj: Dict[str, Any], platform: str, goal: str) -> Dict[str, str]:
    title = str(obj.get("title") or "").strip()
    body = str(obj.get("body") or obj.get("copy") or "").strip()
    hashtags = obj.get("hashtags")
    if isinstance(hashtags, list):
        hashtag_text = " ".join("#" + str(x).strip().lstrip("#") for x in hashtags if str(x).strip())
    else:
        hashtag_text = str(obj.get("hashtags") or "").strip()
    full_text = str(obj.get("full_text") or "").strip()
    if not title:
        title = (goal or _PLATFORM_REQUIREMENTS[platform]["label"] + "文案").strip()[:60]
    if not body:
        body = "围绕选中的记忆资料，提炼核心卖点、使用场景和行动建议，形成一条可直接发布的内容。"
    if not full_text:
        full_text = title + "\n\n" + body
        if hashtag_text:
            full_text += "\n\n" + hashtag_text
    return {
        "title": title,
        "body": body,
        "hashtags": hashtag_text,
        "full_text": full_text,
    }


@router.post("/api/creative-film-studio/generate-image", summary="Generate image prompt and preview image from selected memory")
async def generate_creative_film_image(
    body: CreativeFilmImageIn,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    goal = (body.goal or "").strip()
    direct_prompt = (body.direct_prompt or "").strip()
    if direct_prompt:
        memory_context = ""
        selected_docs: List[Dict[str, Any]] = []
        if body.memory_doc_ids:
            memory_context, selected_docs = _selected_memory_context(current_user.id, body.memory_doc_ids)
        plan = {
            "title": ((body.title or goal or "朋友圈配图").strip()[:80] or "朋友圈配图"),
            "summary": "已按已审核文案和配图提示直接生成图片。",
            "keywords": [],
            "image_prompt": _compose_direct_image_prompt(direct_prompt, memory_context),
        }
    else:
        memory_context, selected_docs = _selected_memory_context(current_user.id, body.memory_doc_ids)
        system_prompt = (
            "你是短视频与内容平台创意策划。请基于用户选择的记忆资料，提炼一个用于 AI 图片生成的提示词。"
            "必须返回严格 JSON，不要 Markdown 代码块。字段：title、summary、keywords、image_prompt。"
            "image_prompt 要适合 gpt-image-2，描述画面主体、场景、光线、构图、风格，禁止要求图片里出现文字、水印、按钮或二维码。"
        )
        user_prompt = (
            f"用户目标：{goal or '根据记忆生成一张可用于内容平台的创意封面图'}\n\n"
            f"选中的记忆资料：\n{memory_context}\n\n"
            "请输出图片生成方案。"
        )
        plan_text = await _call_sutui_chat(
            request,
            current_user,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            temperature=0.68,
        )
        plan = _normalize_image_plan(_extract_json_object(plan_text), memory_context, goal)
    image_model = body.image_model or "gpt-image-2"
    image_url = await _generate_image(
        request,
        current_user,
        plan["image_prompt"],
        image_model,
        body.aspect_ratio or "9:16",
    )
    asset = await _save_generated_image_asset(
        request=request,
        current_user=current_user,
        image_url=image_url,
        image_prompt=plan["image_prompt"],
        model=image_model,
        title=plan["title"],
    )
    preview_url = str(asset.get("source_url") or "").strip() or image_url
    return {
        "ok": True,
        "pipeline": _PIPELINE_ID,
        "source_mode": _SOURCE_MODE,
        "selected_documents": selected_docs,
        "goal": goal,
        "title": plan["title"],
        "summary": plan["summary"],
        "keywords": plan["keywords"],
        "image_prompt": plan["image_prompt"],
        "image_url": preview_url,
        "original_image_url": image_url,
        "asset": asset,
        "saved_assets": [asset],
        "aspect_ratio": body.aspect_ratio or "9:16",
    }


@router.post("/api/creative-film-studio/generate-copy", summary="Generate platform copy from memory, prompt and image")
async def generate_creative_film_copy(
    body: CreativeFilmCopyIn,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    platform = (body.platform or "").strip().lower()
    if platform not in _PLATFORM_REQUIREMENTS:
        raise HTTPException(status_code=400, detail="不支持的平台")
    memory_context, selected_docs = _selected_memory_context(current_user.id, body.memory_doc_ids)
    spec = _PLATFORM_REQUIREMENTS[platform]
    system_prompt = (
        "你是资深内容平台运营和商业文案策划。请基于资料、图片提示词和平台要求，生成一条能直接发布的中文文案。"
        "必须返回严格 JSON，不要 Markdown 代码块。字段：title、body、hashtags、full_text。"
        "full_text 必须把标题、正文和话题整合成用户可直接复制发布的完整文本。"
        "不得编造资料里没有的硬性数据，不要承诺无法证明的效果。"
    )
    user_prompt = (
        f"平台：{spec['label']}\n"
        f"平台特点与要求：{spec['requirements']}\n\n"
        f"用户目标：{(body.goal or '').strip() or '根据记忆生成平台发布文案'}\n\n"
        f"前面根据记忆生成的图片提示词：\n{(body.image_prompt or '').strip()}\n\n"
        f"当前预览图片链接：{(body.image_url or '').strip() or '无'}\n\n"
        f"选中的记忆资料：\n{memory_context}\n\n"
        "请生成一条可直接发布的文案。"
    )
    text = await _call_sutui_chat(
        request,
        current_user,
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        temperature=0.74,
    )
    copy = _normalize_copy(_extract_json_object(text), platform, body.goal or "")
    return {
        "ok": True,
        "platform": platform,
        "platform_label": spec["label"],
        "selected_documents": selected_docs,
        "requirements": spec["requirements"],
        "copy": copy,
    }


@router.get("/api/creative-film-studio/platforms", summary="List creative-film copy platforms")
async def list_creative_film_platforms(
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del current_user
    return {
        "ok": True,
        "platforms": [
            {
                "id": key,
                "label": value["label"],
                "requirements": value["requirements"],
            }
            for key, value in _PLATFORM_REQUIREMENTS.items()
        ],
    }
