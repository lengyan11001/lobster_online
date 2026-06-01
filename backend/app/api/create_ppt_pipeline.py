from __future__ import annotations

import asyncio
import logging
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..core.config import settings
from ..db import SessionLocal
from ..models import Asset
from .assets import ASSETS_DIR
from .auth import _ServerUser, get_current_user_media_edit
from .goal_video_pipeline import _extract_json_object, _safe_str

logger = logging.getLogger(__name__)

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parents[3]
_VENDOR_DIR = _BASE_DIR / "backend" / "vendor"
if str(_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VENDOR_DIR))

DEFAULT_PLANNING_MODEL = "gpt-5.4"
DEFAULT_THEME = "business"
PPT_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


class CreatePptPipelinePayload(BaseModel):
    action: str = Field("run_pipeline", description="run_pipeline")
    prompt: str = ""
    topic: str = ""
    slide_count: int = Field(10, ge=3, le=30)
    theme: str = DEFAULT_THEME
    language: str = "zh-CN"
    audience: str = "business"
    style: str = "professional, clear, modern business presentation"
    planning_model: Optional[str] = None


class CreatePptPipelineBody(BaseModel):
    payload: CreatePptPipelinePayload


PPT_OUTLINE_SYSTEM_PROMPT = """你是一位专业的商业演示文稿策划师和PPT信息架构师。
你的任务是根据用户主题，生成一份结构清晰、可直接渲染为 PPTX 的结构化大纲。

要求：
1. 只输出 JSON 对象，不要 Markdown，不要解释。
2. 使用中文，内容要适合商务汇报、路演、培训或方案介绍。
3. 每页文字要克制，标题清楚，避免一页堆太多字。
4. slide_type 只能使用：title、section、content、two_column、chart、table、quote、ending。
5. content 页用 3-5 个要点；two_column 页用左右两组要点；chart/table 只有在内容天然适合数据表达时使用。
6. 不要编造夸张数据；没有真实数据时使用定性表达，不要硬造数字。
7. 最后一页必须是 ending。

输出 JSON 格式：
{
  "title": "演示文稿标题",
  "author": "",
  "language": "zh-CN",
  "slide_size": "16:9",
  "slides": [
    {
      "slide_type": "title",
      "title": "封面标题",
      "subtitle": "副标题"
    },
    {
      "slide_type": "content",
      "title": "页面标题",
      "elements": [
        {"element_type": "text", "text": "要点", "style": {"bullet": true, "bullet_level": 0}}
      ],
      "notes": "可选演讲备注"
    }
  ]
}"""


PPT_OUTLINE_USER_TEMPLATE = """请生成一份 {slide_count} 页的 PPT 大纲。

主题/需求：
{topic}

目标受众：{audience}
风格偏好：{style}
语言：{language}

补充要求：
- 封面、目录/结构、主体内容、总结页要完整。
- 页面之间要有叙事递进，不要只是罗列。
- 每页标题要像真实汇报页标题，不要空泛。
- 直接返回可解析 JSON。"""


def _raw_token_from_request(request: Request) -> str:
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _installation_id_from_request(request: Request, user_id: int) -> str:
    xi = (request.headers.get("X-Installation-Id") or request.headers.get("x-installation-id") or "").strip()
    return xi or f"lobster-internal-{int(user_id)}"


def _goal_text(pl: CreatePptPipelinePayload) -> str:
    return _safe_str(pl.prompt or pl.topic, 3000)


def _safe_filename_stem(value: Any, fallback: str = "presentation") -> str:
    text = str(value or "").strip() or fallback
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", "_", text).strip("._ ")
    return (text or fallback)[:80]


def _normalize_slide_count(value: Any, fallback: int = 10) -> int:
    try:
        n = int(value)
    except Exception:
        n = fallback
    return max(3, min(30, n))


async def _call_ppt_planner(
    *,
    pl: CreatePptPipelinePayload,
    token: str,
    installation_id: str,
) -> Dict[str, Any]:
    asb = (settings.auth_server_base or "").strip().rstrip("/")
    if not asb or not token:
        return _fallback_outline(pl)
    topic = _goal_text(pl)
    if not topic:
        raise RuntimeError("prompt is required")

    model = (
        (pl.planning_model or "").strip()
        or (settings.lobster_orchestration_sutui_chat_model or "").strip()
        or DEFAULT_PLANNING_MODEL
    )
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "X-Installation-Id": installation_id,
    }
    prompt = PPT_OUTLINE_USER_TEMPLATE.format(
        slide_count=_normalize_slide_count(pl.slide_count),
        topic=topic,
        audience=pl.audience,
        style=pl.style,
        language=pl.language,
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": PPT_OUTLINE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "temperature": 0.65,
    }
    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
        resp = await client.post(f"{asb}/api/sutui-chat/completions", json=body, headers=headers)
    if resp.status_code >= 400:
        raise RuntimeError(f"sutui-chat HTTP {resp.status_code}: {(resp.text or '')[:800]}")
    data = resp.json() if resp.content else {}
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        content = _safe_str(data, 4000)
    outline = _extract_json_object(content)
    return _normalize_outline(outline, pl)


def _fallback_outline(pl: CreatePptPipelinePayload) -> Dict[str, Any]:
    topic = _goal_text(pl) or "商业方案汇报"
    count = _normalize_slide_count(pl.slide_count, 8)
    core_pages = max(1, count - 3)
    slides: List[Dict[str, Any]] = [
        {"slide_type": "title", "title": topic[:60], "subtitle": "自动生成演示文稿"},
        {"slide_type": "section", "title": "核心思路"},
    ]
    for idx in range(core_pages):
        slides.append(
            {
                "slide_type": "content",
                "title": f"关键内容 {idx + 1}",
                "elements": [
                    {"element_type": "text", "text": f"围绕“{topic[:40]}”梳理核心观点", "style": {"bullet": True, "bullet_level": 0}},
                    {"element_type": "text", "text": "明确目标受众、价值主张和落地路径", "style": {"bullet": True, "bullet_level": 0}},
                    {"element_type": "text", "text": "用可执行动作承接后续推进", "style": {"bullet": True, "bullet_level": 0}},
                ],
            }
        )
    slides.append({"slide_type": "ending", "title": "谢谢"})
    return _normalize_outline({"title": topic[:80], "slides": slides[:count]}, pl)


def _normalize_outline(outline: Dict[str, Any], pl: CreatePptPipelinePayload) -> Dict[str, Any]:
    if not isinstance(outline, dict):
        outline = {}
    title = _safe_str(outline.get("title") or _goal_text(pl) or "演示文稿", 120)
    slides = outline.get("slides")
    if not isinstance(slides, list):
        slides = []
    clean_slides: List[Dict[str, Any]] = []
    allowed = {"title", "section", "content", "two_column", "chart", "table", "quote", "ending"}
    for idx, raw in enumerate(slides, 1):
        if not isinstance(raw, dict):
            continue
        st = str(raw.get("slide_type") or ("title" if idx == 1 else "content")).strip().lower()
        if st not in allowed:
            st = "content"
        item = dict(raw)
        item["slide_type"] = st
        item["title"] = _safe_str(item.get("title") or ("封面" if idx == 1 else f"第 {idx} 页"), 120)
        if "subtitle" in item:
            item["subtitle"] = _safe_str(item.get("subtitle"), 200)
        elems = item.get("elements")
        if not isinstance(elems, list):
            elems = []
        item["elements"] = elems[:8]
        clean_slides.append(item)
    if not clean_slides:
        return _fallback_outline(pl)
    if clean_slides[-1].get("slide_type") != "ending":
        clean_slides.append({"slide_type": "ending", "title": "谢谢"})
    slide_count = _normalize_slide_count(pl.slide_count)
    clean_slides = clean_slides[:slide_count]
    if clean_slides and clean_slides[-1].get("slide_type") != "ending":
        clean_slides[-1] = {"slide_type": "ending", "title": "谢谢"}
    return {
        "title": title,
        "author": _safe_str(outline.get("author"), 80),
        "language": pl.language or "zh-CN",
        "slide_size": "16:9",
        "theme_name": pl.theme or DEFAULT_THEME,
        "slides": clean_slides,
    }


def _render_pptx(outline: Dict[str, Any], output_path: Path) -> None:
    from ppt_maker import create_from_model
    from ppt_maker.parser.json_parser import parse_json_dict

    model = parse_json_dict(outline)
    model.theme_name = str(outline.get("theme_name") or DEFAULT_THEME)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    create_from_model(model, str(output_path))


def _save_ppt_asset(
    *,
    user_id: int,
    pptx_path: Path,
    title: str,
    prompt: str,
    model: str,
    theme: str,
) -> Dict[str, Any]:
    data = pptx_path.read_bytes()
    asset_id = f"ppt{uuid.uuid4().hex[:9]}"
    filename = f"{asset_id}.pptx"
    target = ASSETS_DIR / filename
    target.write_bytes(data)
    row = Asset(
        asset_id=asset_id,
        user_id=int(user_id),
        filename=filename,
        media_type="document",
        file_size=len(data),
        source_url=None,
        prompt=prompt[:2000],
        model=model[:128],
        tags="scheduled,ppt,create.ppt.pipeline",
        meta={"title": title, "theme": theme, "content_type": PPT_CONTENT_TYPE},
    )
    db = SessionLocal()
    try:
        db.add(row)
        db.commit()
    finally:
        db.close()
    return {
        "asset_id": asset_id,
        "filename": filename,
        "media_type": "document",
        "file_size": len(data),
        "content_type": PPT_CONTENT_TYPE,
    }


async def run_create_ppt_pipeline(
    *,
    pl: CreatePptPipelinePayload,
    token: str,
    installation_id: str,
    user_id: int,
    progress: Optional[Callable[[str, str, Optional[Dict[str, Any]]], None]] = None,
) -> Dict[str, Any]:
    topic = _goal_text(pl)
    if not topic:
        raise HTTPException(status_code=400, detail="prompt is required")

    def emit(stage: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        if progress:
            progress(stage, message, extra)

    emit("plan_start", "generating ppt outline", {"slide_count": pl.slide_count})
    planning_model = (
        (pl.planning_model or "").strip()
        or (settings.lobster_orchestration_sutui_chat_model or "").strip()
        or DEFAULT_PLANNING_MODEL
    )
    outline = await _call_ppt_planner(pl=pl, token=token, installation_id=installation_id)
    emit("plan_done", "ppt outline generated", {"title": outline.get("title"), "slides": len(outline.get("slides") or [])})

    work_dir = ASSETS_DIR / "_generated_ppt"
    stem = _safe_filename_stem(outline.get("title") or topic, "presentation")
    tmp_path = work_dir / f"{stem}-{int(time.time())}.pptx"
    emit("render_start", "rendering pptx", {"theme": pl.theme or DEFAULT_THEME})
    await asyncio.to_thread(_render_pptx, outline, tmp_path)
    emit("render_done", "pptx rendered", {"path": str(tmp_path)})

    asset = _save_ppt_asset(
        user_id=user_id,
        pptx_path=tmp_path,
        title=str(outline.get("title") or topic),
        prompt=topic,
        model=planning_model,
        theme=pl.theme or DEFAULT_THEME,
    )
    try:
        tmp_path.unlink(missing_ok=True)
    except Exception:
        pass
    return {
        "ok": True,
        "status": "completed",
        "title": outline.get("title") or topic,
        "slide_count": len(outline.get("slides") or []),
        "outline": outline,
        "asset_id": asset["asset_id"],
        "ppt_asset_id": asset["asset_id"],
        "saved_assets": [asset],
        "models": {"planning": planning_model},
        "message": "PPT 已生成",
    }


@router.post("/api/create-ppt/pipeline/run")
async def create_ppt_pipeline_run(
    body: CreatePptPipelineBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
):
    token = _raw_token_from_request(request)
    installation_id = _installation_id_from_request(request, current_user.id)
    return await run_create_ppt_pipeline(
        pl=body.payload,
        token=token,
        installation_id=installation_id,
        user_id=int(current_user.id),
    )
