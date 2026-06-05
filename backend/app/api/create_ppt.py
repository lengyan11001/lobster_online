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
from ..services.ppt_master_runner import (
    export_ppt_master_project,
    normalize_ppt_master_plan,
    write_ppt_master_project,
)


logger = logging.getLogger(__name__)
router = APIRouter()

_IMAGE_FULLPAGE_BATCH_SIZE = 10
_IMAGE_FULLPAGE_MAX_RETRIES = 3
_IMAGE_FULLPAGE_RETRY_BASE_DELAY_SEC = 1.5
_IMAGE_FULLPAGE_MAX_SLIDES = 30
_AI_OUTLINE_TIMEOUT_SEC = 180.0
_PPT_MASTER_TIMEOUT_SEC = 240.0
_PPT_MASTER_EXPORT_TIMEOUT_SEC = 900.0
_PPT_MASTER_MAX_SLIDES = 20
_PPT_MASTER_MAX_IMAGES = 6
_PPT_MASTER_VISUAL_SPEC_TIMEOUT_SEC = 180.0
_PPT_MASTER_AI_SVG_TIMEOUT_SEC = 180.0
_PPT_MASTER_AI_SVG_MAX_PAGES = 6
_PPTX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


class CreatePptPayload(BaseModel):
    mode: str = Field("outline", description="outline/markdown/ai/image_fullpage")
    engine: str = Field("ppt_master", description="ppt_master/image_fullpage/legacy")
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
    generate_images: bool = Field(True, description="Whether ppt_master may generate supporting images")
    render_mode: str = Field("hybrid_ai_svg", description="ppt_master render mode: hybrid_ai_svg/script")


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


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, re.IGNORECASE)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw[start : end + 1])
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _ppt_outline_system_prompt() -> str:
    return (
        "你是专业的 PPT 内容策划师。请根据用户主题生成可直接渲染为 PPT 的 Markdown 大纲。"
        "只输出 Markdown，不要解释、不要代码块、不要 JSON。"
        "格式必须使用：第一行 # 演示文稿标题；每页用 ### 页面标题；页面内容用 - 要点。"
        "可以少量使用 ## 章节页，但总页数要接近用户要求。"
        "每页 3-5 个短要点，中文表达清楚，避免空泛套话。"
    )


def _ppt_master_plan_system_prompt() -> str:
    return (
        "你是专业 PPT 内容策划师和视觉设计师。请根据用户主题生成可直接渲染为 PPT 的结构化 JSON。"
        "只输出 JSON，不要 Markdown，不要代码块，不要解释。"
        "先搭建清晰的叙事主线，再拆成页面；每页必须有明确结论，不要只罗列信息。"
        "每页文字要克制、真实、适合商务汇报；避免编造夸张数据，没有数据时用定性判断。"
        "slides 每项字段：layout/role/title/subtitle/claim/bullets/metrics/visual_prompt/visual_style/notes。"
        "layout 只能用 title、section、content、image_right、quote、ending、data、comparison、process。"
        "role 可用 cover、chapter、insight、evidence、data、comparison、process、quote、closing。"
        "claim 是该页一句话结论；metrics 是 0-3 个短指标或标签；visual_prompt 用于需要 AI 配图的页面，描述画面内容，不要写 UI 操作说明。"
        "visual_style 描述页面视觉节奏，如 full_bleed、split_image、card_grid、kpi_strip、data_tiles、comparison、timeline、quote_focus。"
    )


def _ppt_master_plan_user_prompt(payload: CreatePptPayload) -> str:
    topic = (payload.topic or "").strip()
    slide_count = max(1, min(int(payload.slide_count or 10), _PPT_MASTER_MAX_SLIDES))
    language = (payload.language or "zh-CN").strip() or "zh-CN"
    instructions = (payload.instructions or "").strip()
    outline = (payload.outline_markdown or "").strip()
    parts = [
        f"主题：{topic}",
        f"页数：{slide_count}",
        f"语言：{language}",
        f"风格：{payload.theme or 'business'}",
        "输出 JSON 格式：",
        "{",
        '  "title": "PPT标题",',
        '  "subtitle": "副标题",',
        '  "slides": [',
        '    {"layout": "title", "role": "cover", "title": "封面标题", "subtitle": "封面副标题", "claim": "一句话价值判断", "bullets": [], "metrics": ["标签1", "标签2"], "visual_prompt": "封面配图提示词", "visual_style": "full_bleed", "notes": "演讲备注"},',
        '    {"layout": "content", "role": "insight", "title": "页面标题", "subtitle": "短副标题", "claim": "该页核心结论", "bullets": ["要点1", "要点2", "要点3"], "metrics": ["指标/标签1", "指标/标签2"], "visual_prompt": "配图提示词", "visual_style": "split_image", "notes": "演讲备注"},',
        '    {"layout": "data", "role": "data", "title": "数据洞察页标题", "subtitle": "数据口径", "claim": "数据说明的结论", "bullets": ["解释1", "解释2", "解释3"], "metrics": ["指标1", "指标2", "指标3"], "visual_prompt": "", "visual_style": "data_tiles", "notes": "演讲备注"},',
        '    {"layout": "comparison", "role": "comparison", "title": "对比页标题", "subtitle": "对比维度", "claim": "对比后的判断", "bullets": ["左侧要点1", "左侧要点2", "左侧要点3", "右侧建议1", "右侧建议2", "右侧建议3"], "metrics": [], "visual_prompt": "", "visual_style": "comparison", "notes": "演讲备注"},',
        '    {"layout": "process", "role": "process", "title": "路径页标题", "subtitle": "推进节奏", "claim": "下一步怎么落地", "bullets": ["步骤1", "步骤2", "步骤3", "步骤4"], "metrics": ["行动", "试点", "迭代"], "visual_prompt": "", "visual_style": "timeline", "notes": "演讲备注"}',
        "  ]",
        "}",
        "要求：",
        f"- 必须生成接近 {slide_count} 页。",
        "- 第 1 页用 title，最后 1 页用 ending。",
        "- 中间至少包含 2-3 个 section/chapter 节奏页，但不要把所有页都做成章节页。",
        "- 8 页以上时，至少穿插 1 页 data、1 页 comparison 或 process，让页面节奏有变化。",
        "- 每页 bullets 3-5 条，单条不超过 28 个中文字；claim 不超过 24 个中文字。",
        "- metrics 用短标签或可信口径，不要编造精确数值。",
        "- 适合真实汇报，标题要具体，不要空泛。",
        "- 需要配图的页面填写 visual_prompt，优先让封面、关键洞察页、对比页、结尾页有配图。",
    ]
    if outline:
        parts.append(f"用户提供的大纲：\n{outline[:6000]}")
    if instructions:
        parts.append(f"补充要求：\n{instructions[:3000]}")
    return "\n".join(parts)


def _ppt_master_plan_to_markdown(plan: Dict[str, Any]) -> str:
    lines = [f"# {plan.get('title') or 'PPT'}"]
    if str(plan.get("subtitle") or "").strip():
        lines.extend(["", str(plan.get("subtitle") or "").strip()])
    for slide in plan.get("slides") or []:
        lines.extend(["", f"### {slide.get('title') or ''}".strip()])
        subtitle = str(slide.get("subtitle") or "").strip()
        if subtitle:
            lines.append(f"- {subtitle}")
        for bullet in slide.get("bullets") or []:
            lines.append(f"- {str(bullet or '').strip()}")
    return "\n".join(lines).strip() + "\n"


def _fallback_ppt_master_plan(payload: CreatePptPayload) -> Dict[str, Any]:
    topic = (payload.topic or "").strip() or "PPT"
    count = max(3, min(int(payload.slide_count or 10), _PPT_MASTER_MAX_SLIDES))
    slides: List[Dict[str, Any]] = [
        {
            "layout": "title",
            "title": topic,
            "subtitle": "自动生成演示文稿",
            "bullets": [],
            "visual_prompt": "",
            "notes": "",
        }
    ]
    if count >= 4:
        slides.append(
            {
                "layout": "section",
                "title": "核心议题",
                "subtitle": "从背景、机会、方案和落地路径展开",
                "bullets": [],
                "visual_prompt": "",
                "notes": "",
            }
        )
    while len(slides) < count - 1:
        idx = len(slides)
        slides.append(
            {
                "layout": "content",
                "title": f"关键内容 {idx}",
                "subtitle": "围绕主题形成可执行判断",
                "bullets": [
                    f"聚焦「{topic[:18]}」的核心价值",
                    "明确目标用户和使用场景",
                    "梳理差异化优势和关键动作",
                    "形成后续落地推进路径",
                ],
                "visual_prompt": "",
                "notes": "",
            }
        )
    slides.append(
        {
            "layout": "ending",
            "title": "总结与下一步",
            "subtitle": "聚焦重点，推动落地",
            "bullets": [],
            "visual_prompt": "",
            "notes": "",
        }
    )
    return normalize_ppt_master_plan({"title": topic, "slides": slides[:count]}, topic=topic, slide_count=count)


async def _generate_ppt_master_plan_via_sutui(
    *,
    payload: CreatePptPayload,
    request: Request,
    current_user: _ServerUser,
) -> Dict[str, Any]:
    asb = (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
    token, xi = forward_chat_auth_from_request(request)
    if not asb or not token:
        return _fallback_ppt_master_plan(payload)
    installation_id = xi or _installation_id_from_request(request, current_user.id)
    model = (
        (payload.model or "").strip()
        or (getattr(settings, "lobster_orchestration_sutui_chat_model", None) or "").strip()
        or (getattr(settings, "lobster_default_sutui_chat_model", None) or "").strip()
        or "gpt-5.4"
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _ppt_master_plan_system_prompt()},
            {"role": "user", "content": _ppt_master_plan_user_prompt(payload)},
        ],
        "stream": False,
        "temperature": 0.55,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Installation-Id": installation_id,
    }
    billing_key = (getattr(settings, "lobster_mcp_billing_internal_key", None) or "").strip()
    if billing_key:
        headers["X-Lobster-Mcp-Billing"] = billing_key
    async with httpx.AsyncClient(timeout=_PPT_MASTER_TIMEOUT_SEC, trust_env=False) as client:
        resp = await client.post(f"{asb}/api/sutui-chat/completions", json=body, headers=headers)
    if resp.status_code >= 400:
        logger.warning("[create_ppt] ppt_master plan HTTP %s: %s", resp.status_code, (resp.text or "")[:800])
        return _fallback_ppt_master_plan(payload)
    try:
        data = resp.json() if resp.content else {}
    except json.JSONDecodeError:
        data = {}
    raw_plan = _extract_json_object(_extract_chat_text(data))
    if not raw_plan:
        return _fallback_ppt_master_plan(payload)
    return normalize_ppt_master_plan(
        raw_plan,
        topic=(payload.topic or "PPT"),
        slide_count=max(1, min(int(payload.slide_count or 10), _PPT_MASTER_MAX_SLIDES)),
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


async def _generate_ppt_master_support_image(
    *,
    index: int,
    visual_prompt: str,
    payload: CreatePptPayload,
    request: Request,
    current_user: _ServerUser,
    images_dir: Path,
) -> Optional[str]:
    prompt = (visual_prompt or "").strip()
    if not prompt:
        return None
    db = SessionLocal()
    try:
        result = await _generate_image_studio_core(
            request=request,
            current_user=current_user,
            db=db,
            prompt=(
                f"{prompt}\n"
                "Use a clean presentation illustration/photo style. No watermark, no QR code, no garbled text."
            ),
            model=(payload.image_model or "gpt-image-2"),
            aspect_ratio=(payload.aspect_ratio or "16:9"),
            quality=(payload.image_quality or "high"),
            background=(payload.image_background or "opaque"),
            upload_payloads=[],
            auto_save=False,
        )
        previews = result.get("images") if isinstance(result, dict) else None
        if not isinstance(previews, list) or not previews:
            return None
        return await _download_or_decode_preview(previews[0], images_dir / f"support_{index:02d}.png")
    except Exception as exc:
        logger.warning("[create_ppt] ppt_master support image failed slide=%s err=%s", index, exc)
        return None
    finally:
        db.close()


async def _generate_ppt_master_support_images(
    *,
    plan: Dict[str, Any],
    payload: CreatePptPayload,
    request: Request,
    current_user: _ServerUser,
    images_dir: Path,
) -> Dict[int, str]:
    if not payload.generate_images:
        return {}
    candidates = [
        slide
        for slide in (plan.get("slides") or [])
        if str(slide.get("visual_prompt") or "").strip()
    ][:_PPT_MASTER_MAX_IMAGES]
    if not candidates:
        return {}
    tasks = [
        _generate_ppt_master_support_image(
            index=int(slide.get("index") or 1),
            visual_prompt=str(slide.get("visual_prompt") or ""),
            payload=payload,
            request=request,
            current_user=current_user,
            images_dir=images_dir,
        )
        for slide in candidates
    ]
    paths = await asyncio.gather(*tasks)
    out: Dict[int, str] = {}
    for slide, path in zip(candidates, paths):
        if path:
            out[int(slide.get("index") or 1)] = path
    return out


def _ppt_master_visual_spec_enabled(payload: CreatePptPayload) -> bool:
    return _ppt_master_ai_svg_enabled(payload)


def _ppt_master_visual_spec_system_prompt() -> str:
    return (
        "You are a senior presentation creative director. "
        "Given a structured PPT plan, produce a concise JSON visual system for the whole deck. "
        "Output JSON only. No markdown, no explanation. "
        "The design must be theme-specific, not a fixed template, and must be practical for editable SVG/PPTX rendering."
    )


def _ppt_master_visual_spec_user_prompt(*, plan: Dict[str, Any], payload: CreatePptPayload) -> str:
    compact_slides: List[Dict[str, Any]] = []
    for slide in plan.get("slides") or []:
        compact_slides.append(
            {
                "index": slide.get("index"),
                "layout": slide.get("layout"),
                "role": slide.get("role"),
                "title": slide.get("title"),
                "subtitle": slide.get("subtitle"),
                "claim": slide.get("claim"),
                "bullets": (slide.get("bullets") or [])[:5],
                "metrics": (slide.get("metrics") or [])[:3],
                "visual_prompt": slide.get("visual_prompt"),
                "visual_style": slide.get("visual_style"),
            }
        )
    body = {
        "topic": payload.topic,
        "theme": payload.theme,
        "language": payload.language,
        "instructions": (payload.instructions or "")[:2000],
        "deck": {
            "title": plan.get("title"),
            "subtitle": plan.get("subtitle"),
            "slides": compact_slides,
        },
    }
    return (
        "Create a visual direction JSON for this PPT deck.\n"
        "Return this schema:\n"
        "{\n"
        '  "design_concept": "one concise sentence",\n'
        '  "palette": {"background": "#RRGGBB", "surface": "#RRGGBB", "ink": "#RRGGBB", "muted": "#RRGGBB", "accent": "#RRGGBB", "accent2": "#RRGGBB"},\n'
        '  "typography": {"title": "font/weight/scale guidance", "body": "font/size guidance"},\n'
        '  "motifs": ["recurring visual motif 1", "motif 2"],\n'
        '  "layout_rhythm": "how cover, section, content, data and closing pages should vary",\n'
        '  "image_policy": "when to use generated image vs shapes only",\n'
        '  "slide_directions": [\n'
        '    {"index": 1, "composition": "specific composition direction", "image_needed": true, "visual_prompt": "image prompt if needed", "svg_notes": "SVG construction notes"}\n'
        "  ]\n"
        "}\n"
        "Rules:\n"
        "- Include one slide_directions item for every slide index.\n"
        "- image_needed should be true only when an image materially improves the page.\n"
        "- visual_prompt must describe a clean presentation visual with no text/watermark/logo when image_needed is true.\n"
        "- svg_notes should guide layout, hierarchy, spacing, and motifs for editable SVG.\n\n"
        f"Input:\n{json.dumps(body, ensure_ascii=False, indent=2)}"
    )


async def _generate_ppt_master_visual_spec(
    *,
    plan: Dict[str, Any],
    payload: CreatePptPayload,
    request: Request,
    current_user: _ServerUser,
) -> Dict[str, Any]:
    if not _ppt_master_visual_spec_enabled(payload):
        return {}
    asb = (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
    token, xi = forward_chat_auth_from_request(request)
    if not asb or not token:
        return {}
    model = (
        (payload.model or "").strip()
        or (getattr(settings, "lobster_orchestration_sutui_chat_model", None) or "").strip()
        or (getattr(settings, "lobster_default_sutui_chat_model", None) or "").strip()
        or "gpt-5.4"
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _ppt_master_visual_spec_system_prompt()},
            {"role": "user", "content": _ppt_master_visual_spec_user_prompt(plan=plan, payload=payload)},
        ],
        "stream": False,
        "temperature": 0.45,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Installation-Id": xi or _installation_id_from_request(request, current_user.id),
    }
    billing_key = (getattr(settings, "lobster_mcp_billing_internal_key", None) or "").strip()
    if billing_key:
        headers["X-Lobster-Mcp-Billing"] = billing_key
    try:
        async with httpx.AsyncClient(timeout=_PPT_MASTER_VISUAL_SPEC_TIMEOUT_SEC, trust_env=False) as client:
            resp = await client.post(f"{asb}/api/sutui-chat/completions", json=body, headers=headers)
    except Exception as exc:
        logger.warning("[create_ppt] visual spec request failed: %s", exc)
        return {}
    if resp.status_code >= 400:
        logger.warning("[create_ppt] visual spec HTTP %s: %s", resp.status_code, (resp.text or "")[:800])
        return {}
    try:
        data = resp.json() if resp.content else {}
    except json.JSONDecodeError:
        return {}
    spec = _extract_json_object(_extract_chat_text(data))
    if not isinstance(spec, dict) or not spec:
        return {}
    spec["_meta"] = {"model": model, "enabled": True}
    return spec


def _visual_spec_slide_direction(visual_spec: Dict[str, Any], index: int) -> Dict[str, Any]:
    items = visual_spec.get("slide_directions") if isinstance(visual_spec, dict) else None
    if not isinstance(items, list):
        return {}
    for item in items:
        if isinstance(item, dict) and int(item.get("index") or -1) == int(index):
            return item
    return {}


def _apply_visual_spec_to_plan(plan: Dict[str, Any], visual_spec: Dict[str, Any]) -> Dict[str, Any]:
    if not visual_spec:
        return plan
    slides = plan.get("slides") if isinstance(plan.get("slides"), list) else []
    for slide in slides:
        if not isinstance(slide, dict):
            continue
        idx = int(slide.get("index") or 1)
        direction = _visual_spec_slide_direction(visual_spec, idx)
        if not direction:
            continue
        svg_notes = str(direction.get("svg_notes") or direction.get("composition") or "").strip()
        if svg_notes:
            existing = str(slide.get("visual_direction") or "").strip()
            slide["visual_direction"] = (existing + "\n" + svg_notes).strip() if existing else svg_notes[:1200]
        if not str(slide.get("visual_prompt") or "").strip() and bool(direction.get("image_needed")):
            prompt = str(direction.get("visual_prompt") or "").strip()
            if prompt:
                slide["visual_prompt"] = prompt[:700]
    return plan


def _ppt_master_ai_svg_enabled(payload: CreatePptPayload) -> bool:
    mode = str(payload.render_mode or "").strip().lower()
    return mode in {"hybrid_ai_svg", "ai_svg", "hybrid", "auto"}


def _image_file_data_url(path: str) -> str:
    p = Path(path)
    if not p.is_file():
        return ""
    suffix = p.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/webp" if suffix == ".webp" else "image/png"
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def _ppt_master_ai_svg_system_prompt() -> str:
    return (
        "You are a senior presentation art director and SVG engineer. "
        "Create one editable 16:9 PPT slide as clean SVG only. "
        "Output only the SVG XML, no markdown and no explanation. "
        "Canvas must be 1280 by 720 with viewBox='0 0 1280 720'. "
        "Use <text>, <rect>, <circle>, <path>, <line>, <g>, and optionally one <image href='PPT_IMAGE_HREF'>. "
        "Do not use script, foreignObject, filters, blur effects, remote URLs, base64 images, markdown, or HTML. "
        "Use at most one <image>. Never crop the same image into multiple small panels. "
        "If the support image is used, keep it clear with opacity >= 0.9 and do not cover it with large gray translucent blocks. "
        "Keep all text readable, avoid overlap, use professional spacing, and make the layout theme-specific rather than a fixed template."
    )


def _ppt_master_ai_svg_user_text(
    *,
    plan: Dict[str, Any],
    slide: Dict[str, Any],
    theme: str,
    image_available: bool,
    visual_spec: Optional[Dict[str, Any]] = None,
) -> str:
    slide_json = json.dumps(slide, ensure_ascii=False, indent=2)
    deck_title = str(plan.get("title") or "PPT")
    subtitle = str(plan.get("subtitle") or "")
    idx = int(slide.get("index") or 1)
    slide_direction = _visual_spec_slide_direction(visual_spec or {}, idx)
    visual_spec_brief = ""
    if visual_spec:
        brief = {
            "design_concept": visual_spec.get("design_concept"),
            "palette": visual_spec.get("palette"),
            "typography": visual_spec.get("typography"),
            "motifs": visual_spec.get("motifs"),
            "layout_rhythm": visual_spec.get("layout_rhythm"),
            "image_policy": visual_spec.get("image_policy"),
            "this_slide_direction": slide_direction,
        }
        visual_spec_brief = json.dumps(brief, ensure_ascii=False, indent=2)[:5000]
    image_hint = (
        "A support image is attached. Analyze it as visual reference. "
        "If it helps the page, place exactly one <image href='PPT_IMAGE_HREF'> in the SVG and compose text around it. "
        "Do not copy text from the image; use it as visual mood/composition reference."
        if image_available
        else "No support image is attached. Build the page with editable SVG shapes and text only."
    )
    return (
        f"Deck title: {deck_title}\n"
        f"Deck subtitle: {subtitle}\n"
        f"Theme: {theme or 'business'}\n"
        f"Slide count: {len(plan.get('slides') or [])}\n"
        f"Slide JSON:\n{slide_json}\n\n"
        f"Deck visual direction JSON:\n{visual_spec_brief or '{}'}\n\n"
        f"Visual reference rule: {image_hint}\n\n"
        "Design requirements:\n"
        "- Make this page feel specifically designed for this topic and slide role.\n"
        "- Follow the deck visual direction so this slide belongs to the same presentation system.\n"
        "- If using the support image, use it once as a clear hero/side/background image; do not split it into several fake thumbnails.\n"
        "- Do not place text on top of the main product/object area; reserve a separate text panel or empty region.\n"
        "- Avoid blur, filter effects, heavy translucent overlays, tiny footer text, and dense annotation blocks.\n"
        "- Use Chinese text exactly from the slide JSON where appropriate; do not invent long paragraphs.\n"
        "- Keep title and claim prominent, bullets concise, and whitespace deliberate.\n"
        "- Use no more than 5 bullet items and no more than 3 metric labels.\n"
        "- All visible text must stay inside the canvas and remain editable <text> nodes.\n"
        "- Return a complete SVG only."
    )


async def _generate_one_ppt_master_ai_svg(
    *,
    slide: Dict[str, Any],
    plan: Dict[str, Any],
    payload: CreatePptPayload,
    request: Request,
    current_user: _ServerUser,
    image_path: Optional[str],
    visual_spec: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    index = int(slide.get("index") or 1)
    asb = (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
    token, xi = forward_chat_auth_from_request(request)
    if not asb or not token:
        return {"index": index, "ok": False, "reason": "missing_auth_server_or_token"}
    model = (
        (payload.model or "").strip()
        or (getattr(settings, "lobster_orchestration_sutui_chat_model", None) or "").strip()
        or (getattr(settings, "lobster_default_sutui_chat_model", None) or "").strip()
        or "gpt-5.4"
    )
    user_text = _ppt_master_ai_svg_user_text(
        plan=plan,
        slide=slide,
        theme=(payload.theme or "business"),
        image_available=bool(image_path),
        visual_spec=visual_spec,
    )
    user_content: Any = user_text
    if image_path:
        data_url = _image_file_data_url(image_path)
        if data_url:
            user_content = [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _ppt_master_ai_svg_system_prompt()},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
        "temperature": 0.35,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Installation-Id": xi or _installation_id_from_request(request, current_user.id),
    }
    billing_key = (getattr(settings, "lobster_mcp_billing_internal_key", None) or "").strip()
    if billing_key:
        headers["X-Lobster-Mcp-Billing"] = billing_key
    try:
        async with httpx.AsyncClient(timeout=_PPT_MASTER_AI_SVG_TIMEOUT_SEC, trust_env=False) as client:
            resp = await client.post(f"{asb}/api/sutui-chat/completions", json=body, headers=headers)
    except Exception as exc:
        logger.warning("[create_ppt] ai svg request failed slide=%s err=%s", index, exc)
        return {"index": index, "ok": False, "reason": str(exc)[:300]}
    if resp.status_code >= 400:
        logger.warning("[create_ppt] ai svg HTTP %s slide=%s: %s", resp.status_code, index, (resp.text or "")[:800])
        return {"index": index, "ok": False, "reason": f"HTTP {resp.status_code}"}
    try:
        data = resp.json() if resp.content else {}
    except json.JSONDecodeError:
        return {"index": index, "ok": False, "reason": "non_json_response"}
    svg = _extract_chat_text(data)
    if "<svg" not in svg.lower():
        return {"index": index, "ok": False, "reason": "no_svg_in_response"}
    return {"index": index, "ok": True, "svg": svg, "model": model, "used_image": bool(image_path)}


async def _generate_ppt_master_ai_svgs(
    *,
    plan: Dict[str, Any],
    payload: CreatePptPayload,
    request: Request,
    current_user: _ServerUser,
    slide_images: Dict[int, str],
    visual_spec: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not _ppt_master_ai_svg_enabled(payload):
        return {"slide_svgs": {}, "records": [], "enabled": False}
    slides = list(plan.get("slides") or [])
    scored: List[tuple[int, Dict[str, Any]]] = []
    for slide in slides:
        if not isinstance(slide, dict):
            continue
        idx = int(slide.get("index") or 1)
        role = str(slide.get("role") or "").strip().lower()
        layout = str(slide.get("layout") or "").strip().lower()
        direction = _visual_spec_slide_direction(visual_spec or {}, idx)
        score = 0
        if idx in slide_images:
            score += 100
        if layout in {"title", "ending", "data", "comparison", "process", "quote"}:
            score += 35
        if role in {"cover", "closing", "data", "comparison", "process", "quote"}:
            score += 25
        if direction:
            score += 20
        if str(slide.get("visual_direction") or "").strip():
            score += 10
        if score > 0:
            scored.append((score, slide))
    if not scored:
        return {"slide_svgs": {}, "records": [], "enabled": True, "reason": "no_candidate_slides", "selected": []}
    scored.sort(key=lambda item: (-item[0], int(item[1].get("index") or 1)))
    selected = [item[1] for item in scored[: max(1, min(_PPT_MASTER_AI_SVG_MAX_PAGES, len(scored)))]]
    tasks = [
        _generate_one_ppt_master_ai_svg(
            slide=slide,
            plan=plan,
            payload=payload,
            request=request,
            current_user=current_user,
            image_path=slide_images.get(int(slide.get("index") or 1)),
            visual_spec=visual_spec,
        )
        for slide in selected
    ]
    records = await asyncio.gather(*tasks)
    slide_svgs: Dict[int, str] = {}
    for record in records:
        if record.get("ok") and record.get("svg"):
            slide_svgs[int(record.get("index") or 1)] = str(record.get("svg") or "")
    return {
        "slide_svgs": slide_svgs,
        "records": records,
        "enabled": True,
        "selected": [
            {
                "index": int(slide.get("index") or 1),
                "layout": str(slide.get("layout") or ""),
                "role": str(slide.get("role") or ""),
                "has_image": int(slide.get("index") or 1) in slide_images,
            }
            for slide in selected
        ],
    }


async def _run_ppt_master_ppt(
    *,
    payload: CreatePptPayload,
    request: Request,
    current_user: _ServerUser,
) -> Dict[str, Any]:
    started = time.perf_counter()
    slide_count = max(1, min(int(payload.slide_count or 10), _PPT_MASTER_MAX_SLIDES))
    plan = await _generate_ppt_master_plan_via_sutui(
        payload=payload.model_copy(update={"slide_count": slide_count}),
        request=request,
        current_user=current_user,
    )
    visual_spec = await _generate_ppt_master_visual_spec(
        plan=plan,
        payload=payload,
        request=request,
        current_user=current_user,
    )
    plan = _apply_visual_spec_to_plan(plan, visual_spec)
    run_dir = create_ppt_run_dir(payload.topic or str(plan.get("title") or "ppt_master"))
    images_dir = run_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    slide_images = await _generate_ppt_master_support_images(
        plan=plan,
        payload=payload,
        request=request,
        current_user=current_user,
        images_dir=images_dir,
    )
    ai_svg_meta = await _generate_ppt_master_ai_svgs(
        plan=plan,
        payload=payload,
        request=request,
        current_user=current_user,
        slide_images=slide_images,
        visual_spec=visual_spec,
    )
    source_markdown = (payload.outline_markdown or "").strip() or _ppt_master_plan_to_markdown(plan)
    project_dir = write_ppt_master_project(
        run_dir=run_dir,
        plan=plan,
        theme=(payload.theme or "business"),
        source_markdown=source_markdown,
        slide_images=slide_images,
        slide_svgs=ai_svg_meta.get("slide_svgs") if isinstance(ai_svg_meta, dict) else {},
    )
    render_manifest: List[Dict[str, Any]] = []
    try:
        manifest_path = project_dir / "render_manifest.json"
        if manifest_path.is_file():
            parsed_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(parsed_manifest, list):
                render_manifest = [item for item in parsed_manifest if isinstance(item, dict)]
    except Exception:
        render_manifest = []
    actual_ai_svg_count = len([item for item in render_manifest if item.get("renderer") == "ai_svg"])
    filename = str(payload.filename or "").strip()
    if not filename:
        filename = f"{safe_create_ppt_name(str(plan.get('title') or payload.topic or 'ppt_master'))}.pptx"
    if not filename.lower().endswith(".pptx"):
        filename += ".pptx"
    pptx_path = run_dir / filename
    export_meta = export_ppt_master_project(
        project_dir=project_dir,
        output_path=pptx_path,
        timeout_sec=_PPT_MASTER_EXPORT_TIMEOUT_SEC,
    )
    return {
        "ok": True,
        "mode": "ai",
        "engine": "ppt_master",
        "render_mode": "svg_to_pptx",
        "topic": payload.topic,
        "run_dir": str(run_dir),
        "project_dir": str(project_dir),
        "pptx_path": str(pptx_path),
        "filename": pptx_path.name,
        "size_bytes": pptx_path.stat().st_size if pptx_path.exists() else 0,
        "plan": plan,
        "outline_markdown": source_markdown,
        "visual_spec": visual_spec,
        "support_images": slide_images,
        "ai_svg": ai_svg_meta,
        "render_manifest": render_manifest,
        "saved_assets": [],
        "generation_meta": {
            "slide_count": len(plan.get("slides") or []),
            "support_image_count": len(slide_images),
            "visual_spec_enabled": bool(visual_spec),
            "ai_svg_count": actual_ai_svg_count,
            "ai_svg_returned_count": len((ai_svg_meta or {}).get("slide_svgs") or {}) if isinstance(ai_svg_meta, dict) else 0,
            "ai_svg_enabled": bool((ai_svg_meta or {}).get("enabled")) if isinstance(ai_svg_meta, dict) else False,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            **export_meta,
        },
    }


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
            engine = (payload.engine or "ppt_master").strip().lower()
            if engine in {"legacy", "image_fullpage", "image"}:
                result = await _run_ai_image_ppt(
                    payload=payload,
                    request=request,
                    current_user=current_user,
                )
            else:
                try:
                    result = await _run_ppt_master_ppt(
                        payload=payload,
                        request=request,
                        current_user=current_user,
                    )
                except Exception as exc:
                    logger.warning("[create_ppt] ppt_master failed, fallback to image_fullpage: %s", exc)
                    result = await _run_ai_image_ppt(
                        payload=payload.model_copy(update={"engine": "image_fullpage"}),
                        request=request,
                        current_user=current_user,
                    )
                    result["fallback_from_engine"] = "ppt_master"
                    result["fallback_reason"] = str(getattr(exc, "detail", None) or exc)[:800]
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
