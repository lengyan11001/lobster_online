from __future__ import annotations

import asyncio
import base64
import logging
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..core.config import settings
from ..db import SessionLocal
from ..models import Asset
from .assets import ASSETS_DIR
from .auth import _ServerUser, get_current_user_media_edit
from .comfly_image_studio import _generate_image_studio_core
from .goal_video_pipeline import _extract_json_object, _safe_str
from ..services.create_ppt_runner import create_ppt_run_dir, safe_create_ppt_name
from ..services.ppt_master_runner import (
    export_ppt_master_project,
    normalize_ppt_master_plan,
    write_ppt_master_project,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parents[3]
_VENDOR_DIR = _BASE_DIR / "backend" / "vendor"
if str(_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VENDOR_DIR))

DEFAULT_PLANNING_MODEL = "gpt-5.4"
DEFAULT_THEME = "business"
PPT_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
PPT_MASTER_MAX_SUPPORT_IMAGES = 6


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
    image_model: str = "gpt-image-2"
    image_quality: str = "high"
    image_background: str = "opaque"
    aspect_ratio: str = "16:9"
    generate_images: bool = True


class CreatePptPipelineBody(BaseModel):
    payload: CreatePptPipelinePayload


PPT_OUTLINE_SYSTEM_PROMPT = """你是一位专业的商业演示文稿策划师和PPT信息架构师。
你的任务是根据用户主题，生成一份结构清晰、可直接渲染为 PPTX 的结构化大纲。

要求：
1. 只输出 JSON 对象，不要 Markdown，不要解释。
2. 使用中文，内容要适合商务汇报、路演、培训或方案介绍。
3. 每页文字要克制，标题清楚，避免一页堆太多字。
4. slide_type 只能使用：title、section、content、two_column、chart、table、quote、ending、data、comparison、process。
5. content 页用 3-5 个要点；two_column 页用左右两组要点；chart/table 只有在内容天然适合数据表达时使用。
6. 不要编造夸张数据；没有真实数据时使用定性表达，不要硬造数字。
7. 最后一页必须是 ending。
8. 每页尽量给出一句 claim 作为页面核心结论；可选 metrics 字段放 0-3 个短指标/标签。
9. 需要配图的页面填写 visual_prompt，并用 visual_style 描述视觉节奏，例如 full_bleed、split_image、card_grid、kpi_strip、data_tiles、comparison、timeline、quote_focus。
10. 8 页以上时，至少穿插 1 页 data、1 页 comparison 或 process，让页面节奏有变化。

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
      "claim": "该页核心结论",
      "elements": [
        {"element_type": "text", "text": "要点", "style": {"bullet": true, "bullet_level": 0}}
      ],
      "metrics": ["短指标/标签"],
      "visual_prompt": "页面配图提示词",
      "visual_style": "split_image",
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
- 每页尽量有一句核心结论 claim，关键页面给出 visual_prompt 方便生成高质量配图。
- 如果页数足够，穿插数据洞察页、对比页或流程页，不要所有页面都是普通图文页。
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


def _outline_to_ppt_master_plan(outline: Dict[str, Any], pl: CreatePptPipelinePayload) -> Dict[str, Any]:
    slides: List[Dict[str, Any]] = []
    raw_slides = outline.get("slides") if isinstance(outline.get("slides"), list) else []
    for idx, slide in enumerate(raw_slides, 1):
        if not isinstance(slide, dict):
            continue
        slide_type = str(slide.get("slide_type") or "").strip().lower()
        layout = {
            "title": "title",
            "section": "section",
            "quote": "quote",
            "ending": "ending",
            "two_column": "content",
            "chart": "content",
            "table": "content",
            "data": "data",
            "comparison": "comparison",
            "process": "process",
        }.get(slide_type, "content")
        bullets: List[str] = []
        elements = slide.get("elements")
        if isinstance(elements, list):
            for elem in elements:
                if isinstance(elem, dict):
                    text = _safe_str(elem.get("text") or elem.get("title") or elem.get("content"), 120)
                else:
                    text = _safe_str(elem, 120)
                if text:
                    bullets.append(text)
        for key in ("bullets", "points", "content"):
            value = slide.get(key)
            if isinstance(value, list):
                bullets.extend(_safe_str(item, 120) for item in value if _safe_str(item, 120))
            elif isinstance(value, str) and value.strip():
                bullets.extend(line.strip(" -") for line in value.splitlines() if line.strip())
        slides.append(
            {
                "layout": layout,
                "title": _safe_str(slide.get("title") or f"第 {idx} 页", 120),
                "subtitle": _safe_str(slide.get("subtitle"), 180),
                "claim": _safe_str(slide.get("claim") or slide.get("takeaway") or slide.get("conclusion"), 120),
                "bullets": bullets[:5],
                "metrics": [
                    _safe_str(item, 32)
                    for item in (slide.get("metrics") or slide.get("tags") or slide.get("kpis") or [])
                    if _safe_str(item, 32)
                ][:3]
                if isinstance(slide.get("metrics") or slide.get("tags") or slide.get("kpis"), list)
                else [],
                "visual_prompt": _safe_str(slide.get("visual_prompt") or slide.get("image_prompt"), 700),
                "visual_style": _safe_str(slide.get("visual_style") or slide.get("style_hint"), 40),
                "notes": _safe_str(slide.get("notes"), 1200),
            }
        )
    return normalize_ppt_master_plan(
        {
            "title": _safe_str(outline.get("title") or _goal_text(pl) or "PPT", 120),
            "subtitle": _safe_str(outline.get("subtitle"), 180),
            "slides": slides,
        },
        topic=_goal_text(pl) or _safe_str(outline.get("title") or "PPT", 120),
        slide_count=_normalize_slide_count(pl.slide_count),
    )


def _plan_to_markdown(plan: Dict[str, Any]) -> str:
    lines = [f"# {plan.get('title') or 'PPT'}"]
    if str(plan.get("subtitle") or "").strip():
        lines.extend(["", str(plan.get("subtitle") or "").strip()])
    for slide in plan.get("slides") or []:
        lines.extend(["", f"### {slide.get('title') or ''}".strip()])
        if str(slide.get("subtitle") or "").strip():
            lines.append(f"- {str(slide.get('subtitle') or '').strip()}")
        for bullet in slide.get("bullets") or []:
            if str(bullet or "").strip():
                lines.append(f"- {str(bullet or '').strip()}")
    return "\n".join(lines).strip() + "\n"


async def _download_or_decode_pipeline_image(preview: Dict[str, str], dest: Path) -> str:
    data_url = str(preview.get("data_url") or "").strip()
    if data_url:
        payload = data_url.split(",", 1)[-1] if "," in data_url else data_url
        dest.write_bytes(base64.b64decode(payload))
        return str(dest)

    url = str(preview.get("url") or "").strip()
    if not url:
        raise RuntimeError("image generation returned no downloadable URL")
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".webp"}:
        dest = dest.with_suffix(suffix)
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True, trust_env=False) as client:
        resp = await client.get(url)
    if resp.status_code >= 400:
        raise RuntimeError(f"failed to download generated image: HTTP {resp.status_code}")
    dest.write_bytes(resp.content)
    return str(dest)


async def _generate_ppt_master_pipeline_image(
    *,
    index: int,
    visual_prompt: str,
    pl: CreatePptPipelinePayload,
    request: Request,
    current_user: _ServerUser,
    images_dir: Path,
) -> Optional[str]:
    prompt = _safe_str(visual_prompt, 1200)
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
                "Use a clean professional presentation visual. No readable text, no watermark, no QR code, no logo."
            ),
            model=pl.image_model or "gpt-image-2",
            aspect_ratio=pl.aspect_ratio or "16:9",
            quality=pl.image_quality or "high",
            background=pl.image_background or "opaque",
            upload_payloads=[],
            auto_save=False,
        )
        previews = result.get("images") if isinstance(result, dict) else None
        if not isinstance(previews, list) or not previews:
            return None
        return await _download_or_decode_pipeline_image(previews[0], images_dir / f"support_{index:02d}.png")
    except Exception as exc:
        logger.warning("[create_ppt_pipeline] support image failed slide=%s err=%s", index, exc)
        return None
    finally:
        db.close()


async def _generate_ppt_master_pipeline_images(
    *,
    plan: Dict[str, Any],
    pl: CreatePptPipelinePayload,
    request: Optional[Request],
    current_user: Optional[_ServerUser],
    images_dir: Path,
    progress: Optional[Callable[[str, str, Optional[Dict[str, Any]]], None]] = None,
) -> Dict[int, str]:
    if not pl.generate_images or request is None or current_user is None:
        return {}
    candidates = [
        slide
        for slide in (plan.get("slides") or [])
        if str(slide.get("visual_prompt") or "").strip()
    ][:PPT_MASTER_MAX_SUPPORT_IMAGES]
    if not candidates:
        return {}
    if progress:
        progress("image_start", "generating ppt support images", {"count": len(candidates)})
    tasks = [
        _generate_ppt_master_pipeline_image(
            index=int(slide.get("index") or 1),
            visual_prompt=str(slide.get("visual_prompt") or ""),
            pl=pl,
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
    if progress:
        progress("image_done", "ppt support images generated", {"count": len(out), "requested": len(candidates)})
    return out


async def _render_ppt_master_pipeline_pptx(
    *,
    outline: Dict[str, Any],
    pl: CreatePptPipelinePayload,
    output_path: Path,
    request: Optional[Request] = None,
    current_user: Optional[_ServerUser] = None,
    progress: Optional[Callable[[str, str, Optional[Dict[str, Any]]], None]] = None,
) -> Dict[str, Any]:
    plan = _outline_to_ppt_master_plan(outline, pl)
    run_dir = create_ppt_run_dir(_safe_str(plan.get("title") or _goal_text(pl) or "ppt_master", 80))
    images_dir = run_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    slide_images = await _generate_ppt_master_pipeline_images(
        plan=plan,
        pl=pl,
        request=request,
        current_user=current_user,
        images_dir=images_dir,
        progress=progress,
    )
    project_dir = write_ppt_master_project(
        run_dir=run_dir,
        plan=plan,
        theme=pl.theme or DEFAULT_THEME,
        source_markdown=_plan_to_markdown(plan),
        slide_images=slide_images,
    )
    await asyncio.to_thread(
        lambda: export_ppt_master_project(
            project_dir=project_dir,
            output_path=output_path,
            timeout_sec=900.0,
        )
    )
    return {
        "engine": "ppt_master",
        "project_dir": str(project_dir),
        "run_dir": str(run_dir),
        "plan": plan,
        "support_image_count": len(slide_images),
        "support_images": slide_images,
    }


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
        "local_path": str(target),
        "source_url": None,
        "url": None,
        "display_text": f"PPT 文件 · {filename}",
    }


async def run_create_ppt_pipeline(
    *,
    pl: CreatePptPipelinePayload,
    token: str,
    installation_id: str,
    user_id: int,
    progress: Optional[Callable[[str, str, Optional[Dict[str, Any]]], None]] = None,
    request: Optional[Request] = None,
    current_user: Optional[_ServerUser] = None,
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
    emit("render_start", "rendering editable pptx", {"theme": pl.theme or DEFAULT_THEME, "engine": "ppt_master"})
    render_meta: Dict[str, Any] = {"engine": "ppt_master"}
    try:
        render_meta = await _render_ppt_master_pipeline_pptx(
            outline=outline,
            pl=pl,
            output_path=tmp_path,
            request=request,
            current_user=current_user,
            progress=progress,
        )
    except Exception as exc:
        logger.warning("[create_ppt_pipeline] ppt_master failed, fallback to legacy renderer: %s", exc)
        render_meta = {"engine": "legacy", "fallback_from_engine": "ppt_master", "fallback_reason": str(exc)[:800]}
        await asyncio.to_thread(_render_pptx, outline, tmp_path)
    emit("render_done", "pptx rendered", {"path": str(tmp_path), "engine": render_meta.get("engine")})

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
        "pptx_path": asset.get("local_path"),
        "filename": asset.get("filename"),
        "asset_id": asset["asset_id"],
        "ppt_asset_id": asset["asset_id"],
        "saved_assets": [asset],
        "models": {"planning": planning_model},
        "render_meta": render_meta,
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
        request=request,
        current_user=current_user,
    )
