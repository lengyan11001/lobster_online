from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..core.config import settings
from ..services.create_video_job_store import (
    append_create_video_progress,
    create_video_job,
    get_create_video_job,
    update_create_video_job,
)
from .auth import _ServerUser, get_current_user_media_edit
from .goal_video_pipeline import (
    _collect_saved_assets,
    _collect_urls,
    _extract_json_object,
    _first_asset_id,
    PipelinePartialResultError,
    _pre_deduct_pipeline_total,
    _record_pipeline_total,
    _refund_pipeline_total,
    _installation_id_from_request,
    _raw_token_from_request,
    _retry_async,
    _safe_str,
    _submit_and_wait_generation,
    _with_video_no_text_constraint,
)

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_PLANNING_MODEL = "gpt-5.4"
DEFAULT_IMAGE_MODEL = "openai/gpt-image-2"
DEFAULT_VIDEO_MODEL = "fal-ai/veo3.1/image-to-video"

SCRIPT_PROMPT_TEMPLATE = """你是一位专业的视频脚本编剧。请根据以下需求，创作一份结构化的视频故事脚本。

## 视频需求
- 视频类型：{video_type}
- 目标受众：{target_audience}
- 时长要求：{duration}s
- 核心传达信息：{core_message}
- 风格偏好：{style}

## 输出要求
请严格按照以下 JSON 格式输出，不要添加任何其他内容：

{{
  "title": "视频标题",
  "core_storyline": "核心故事线概述（2-3句话概括整体叙事脉络）",
  "scenes": [
    {{
      "scene_id": 1,
      "scene_description": "场景画面内容描述（人物/景物/动作）",
      "narration": "旁白/台词文案",
      "transition": "与下一场景的转场方式（如：淡入淡出/闪切/运镜转场）",
      "duration_estimate": "该段预计时长（如：5s）"
    }}
  ],
  "rhythm_notes": "节奏卡点提示（哪里需要高潮、哪里需要留白等）",
  "total_duration": "预计总时长"
}}

## 创作原则
1. 场景拆分要细致，每个场景时长控制在3-10秒，确保画面丰富
2. 旁白文案要精炼有力，符合目标受众的语言习惯
3. 转场设计要有节奏感，避免单调
4. 确保所有场景时长的总和符合要求的总时长
5. 场景之间要有明确的逻辑递进关系
6. 不要设计任何需要画面出现字幕、文字、字母、数字、logo、水印或可读字符的内容
"""

STORYBOARD_PROMPT_TEMPLATE = """你是一位专业的分镜师和AI图像提示词专家。请根据以下视频脚本，为每个场景生成详细的分镜图提示词。

## 视频脚本
标题：{title}
核心故事线：{core_storyline}
整体风格偏好：{style}
节奏提示：{rhythm_notes}

## 场景列表
{scenes_text}

## 输出要求
请严格按照以下 JSON 格式输出，不要添加任何其他内容：

{{
  "title": "视频标题",
  "style_overview": "整体风格概述（确保所有分镜在色调、画风、光影上保持一致）",
  "prompts": [
    {{
      "scene_id": 1,
      "visual_content": "场景画面内容的详细描述：具体的人物外貌/服装/姿态、景物细节、动作瞬间",
      "style": "画面风格：色调、画风、光影效果",
      "composition": "构图要求：近景/中景/全景/特写，以及画面主体位置",
      "camera_movement": "镜头运动：推/拉/摇/移/固定",
      "prompt_text": "完整的英文图像生成提示词，可直接用于图像生成模型，需要包含所有视觉要素"
    }}
  ]
}}

## 提示词编写原则
1. 视觉一致性：所有分镜的 prompt_text 中必须包含统一的风格关键词，确保人物形象、色调、画风前后一致
2. 具体化：避免模糊描述，用具体的视觉元素替代抽象概念
3. 英文 prompt_text：必须用英文编写，遵循图像生成模型的最佳实践，关键词用逗号分隔，重要元素前置
4. 构图精准：每个场景必须有明确的景别和构图，不要遗漏
5. 镜头语言：镜头运动要与场景情绪匹配
6. 人物统一：如果多个场景出现同一人物，在 prompt_text 中使用一致的描述词
7. prompt_text 必须明确禁止 readable text, letters, numbers, logo, watermark, captions, subtitles, signs
"""

VIDEO_PROMPT_TEMPLATE = """你是一位专业的视频导演和AI视频生成提示词专家。请根据以下视频脚本和分镜信息，为每个场景生成适配 VEO-3.1 模型的视频生成提示词。

## 视频脚本
标题：{title}
核心故事线：{core_storyline}
总时长：{total_duration}
节奏提示：{rhythm_notes}

## 整体视觉风格
{style_overview}

## 场景分镜详情
{scenes_detail}

## 输出要求
请严格按照以下 JSON 格式输出，不要添加任何其他内容：

{{
  "title": "视频标题",
  "total_duration": "总时长，如 60s",
  "resolution": "1920x1080",
  "frame_rate": "24fps",
  "color_grade": "整体色调/滤镜风格描述",
  "bg_music_style": "背景音乐风格描述",
  "voice_over_style": "旁白配音要求",
  "scenes": [
    {{
      "scene_id": 1,
      "duration": "该段时长，如 5s",
      "image_reference": "images/scene_01.png",
      "video_prompt": "英文视频生成提示词，描述画面中物体如何运动、镜头如何移动、光影如何变化。必须具体描述动态效果，而非静态画面。需包含：画面主体动作、镜头运动方式、光影变化、氛围营造",
      "transition": "与下一场景的转场效果",
      "narration_text": "该段旁白文本",
      "sound_design": "音效设计提示"
    }}
  ]
}}

## 视频提示词编写原则
1. 动态描述：video_prompt 必须描述运动和变化，不是静态画面
2. 镜头语言精准：使用 tracking shot, dolly zoom, crane shot, handheld pan 等专业运镜术语
3. 时长合理：每个场景的 duration 应与脚本的 duration_estimate 匹配
4. 转场衔接：transition 要考虑前后场景的情绪和节奏
5. 音画配合：sound_design 要与画面动作同步
6. 旁白分配：narration_text 直接取自脚本的旁白文案，不要增删
7. 风格统一：所有场景的 video_prompt 应包含一致的风格关键词
8. VEO适配：video_prompt 使用英文编写，描述要具体、画面感强，避免抽象概念
9. video_prompt 必须明确禁止 readable text, letters, numbers, logo, watermark, captions, subtitles, signs
"""


class CreateVideoPipelinePayload(BaseModel):
    action: str = Field("run_pipeline", description="run_pipeline/start_pipeline/poll_pipeline")
    job_id: Optional[str] = None
    prompt: str = Field("", description="Video goal, topic, or creative brief")
    topic: str = Field("", description="Compatibility alias for prompt")
    video_type: str = "brand_promo"
    target_audience: str = "general_audience"
    style: str = "premium commercial, realistic, cinematic lighting"
    duration: int = Field(8, ge=3, le=60)
    scene_count: int = Field(1, ge=1, le=6)
    aspect_ratio: str = "16:9"
    language: str = "Chinese"
    planning_model: Optional[str] = None
    image_model: Optional[str] = None
    video_model: Optional[str] = None
    precomputed_plan: Dict[str, Any] = Field(default_factory=dict)
    reference_asset_ids: List[str] = Field(default_factory=list)
    reference_image_urls: List[str] = Field(default_factory=list)
    image_retry_count: int = Field(1, ge=0, le=5)
    video_retry_count: int = Field(1, ge=0, le=5)
    poll_interval_seconds: int = Field(12, ge=5, le=60)
    image_poll_timeout_seconds: int = Field(900, ge=60, le=3600)
    video_poll_timeout_seconds: int = Field(2400, ge=120, le=7200)


class CreateVideoPipelineBody(BaseModel):
    payload: CreateVideoPipelinePayload


def _goal_text(pl: CreateVideoPipelinePayload) -> str:
    return _safe_str(pl.prompt or pl.topic, 2000)


def _normal_aspect_ratio(value: str) -> str:
    raw = (value or "").strip().lower().replace(" ", "")
    if raw in {"9:16", "portrait", "vertical"}:
        return "9:16"
    if raw in {"1:1", "square"}:
        return "1:1"
    return "16:9"


def _image_size_for_ratio(aspect_ratio: str) -> str:
    return _normal_aspect_ratio(aspect_ratio)


def _scene_duration(total_duration: int, scene_count: int) -> int:
    each = max(3, int(round(float(total_duration) / max(1, scene_count))))
    return min(10, each)


def _parse_duration_seconds(value: Any, fallback: int) -> int:
    if isinstance(value, (int, float)):
        return max(3, min(60, int(value)))
    match = re.search(r"\d+", str(value or ""))
    if not match:
        return fallback
    return max(3, min(60, int(match.group(0))))


def _script_scenes_text(script: Dict[str, Any]) -> str:
    rows: List[str] = []
    scenes = script.get("scenes") if isinstance(script.get("scenes"), list) else []
    for idx, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            continue
        rows.append(
            "\n".join(
                [
                    f"场景 {scene.get('scene_id') or idx}",
                    f"- 画面：{_safe_str(scene.get('scene_description'), 600)}",
                    f"- 旁白：{_safe_str(scene.get('narration'), 300)}",
                    f"- 转场：{_safe_str(scene.get('transition'), 120)}",
                    f"- 时长：{_safe_str(scene.get('duration_estimate'), 40)}",
                ]
            )
        )
    return "\n\n".join(rows)


def _video_scenes_detail(script: Dict[str, Any], storyboard: Dict[str, Any]) -> str:
    script_scenes = script.get("scenes") if isinstance(script.get("scenes"), list) else []
    storyboard_prompts = storyboard.get("prompts") if isinstance(storyboard.get("prompts"), list) else []
    by_id: Dict[str, Dict[str, Any]] = {}
    for item in storyboard_prompts:
        if isinstance(item, dict):
            by_id[str(item.get("scene_id") or "")] = item
    rows: List[str] = []
    for idx, scene in enumerate(script_scenes, 1):
        if not isinstance(scene, dict):
            continue
        sid = str(scene.get("scene_id") or idx)
        board = by_id.get(sid) or (storyboard_prompts[idx - 1] if idx - 1 < len(storyboard_prompts) and isinstance(storyboard_prompts[idx - 1], dict) else {})
        rows.append(
            "\n".join(
                [
                    f"场景 {sid}",
                    f"- 脚本画面：{_safe_str(scene.get('scene_description'), 600)}",
                    f"- 分镜视觉：{_safe_str(board.get('visual_content'), 600)}",
                    f"- 图像提示词：{_safe_str(board.get('prompt_text'), 1000)}",
                    f"- 镜头运动：{_safe_str(board.get('camera_movement'), 200)}",
                    f"- 旁白：{_safe_str(scene.get('narration'), 300)}",
                    f"- 转场：{_safe_str(scene.get('transition'), 120)}",
                    f"- 时长：{_safe_str(scene.get('duration_estimate'), 40)}",
                ]
            )
        )
    return "\n\n".join(rows)


def _extract_scenes(plan: Dict[str, Any], pl: CreateVideoPipelinePayload) -> List[Dict[str, Any]]:
    raw_scenes = plan.get("scenes")
    scenes: List[Dict[str, Any]] = []
    if isinstance(raw_scenes, list):
        for idx, item in enumerate(raw_scenes, 1):
            if not isinstance(item, dict):
                continue
            image_prompt = _safe_str(
                item.get("image_prompt")
                or item.get("visual_prompt")
                or item.get("storyboard_prompt")
                or item.get("visual_content"),
                2500,
            )
            video_prompt = _with_video_no_text_constraint(
                item.get("video_prompt") or item.get("motion_prompt") or item.get("camera_movement"),
                2500,
            )
            if not image_prompt or not video_prompt:
                continue
            scenes.append(
                {
                    "scene_id": int(item.get("scene_id") or idx),
                    "title": _safe_str(item.get("title") or f"Scene {idx}", 120),
                    "duration": int(item.get("duration") or _scene_duration(pl.duration, pl.scene_count)),
                    "image_prompt": image_prompt,
                    "video_prompt": video_prompt,
                }
            )
    return scenes[: max(1, int(pl.scene_count))]


def _fallback_plan(pl: CreateVideoPipelinePayload) -> Dict[str, Any]:
    goal = _goal_text(pl) or "a premium commercial promo video"
    count = max(1, min(6, int(pl.scene_count)))
    each = _scene_duration(pl.duration, count)
    scenes: List[Dict[str, Any]] = []
    for idx in range(1, count + 1):
        scenes.append(
            {
                "scene_id": idx,
                "title": f"Scene {idx}",
                "duration": each,
                "image_prompt": (
                    f"{goal}, {pl.video_type}, for {pl.target_audience}, {pl.style}, "
                    "realistic commercial photography, clear subject, clean composition, premium texture, no readable text or logo"
                ),
                "video_prompt": _with_video_no_text_constraint(
                    f"{goal}, slow camera push-in, natural detailed subject movement, layered light and shadow, commercial advertising texture",
                    2500,
                ),
            }
        )
    return {
        "title": _safe_str(goal, 120),
        "summary": goal,
        "scenes": scenes,
    }


async def _call_create_video_planner(
    *,
    pl: CreateVideoPipelinePayload,
    token: str,
    installation_id: str,
    chat_turn_id: str = "",
) -> Dict[str, Any]:
    asb = (settings.auth_server_base or "").strip().rstrip("/")
    if not asb or not token:
        return _fallback_plan(pl)
    goal = _goal_text(pl)
    if not goal:
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
    billing_key = (getattr(settings, "lobster_mcp_billing_internal_key", None) or "").strip()
    if billing_key and chat_turn_id:
        headers["X-Lobster-Mcp-Billing"] = billing_key
        headers["X-Lobster-Chat-Turn-Charged"] = "1"
        headers["X-Lobster-Chat-Turn-Id"] = chat_turn_id[:128]
        headers["X-Lobster-LLM-Billing-Mode"] = "turn_precharged"

    async def call_json(prompt: str, temperature: float = 0.7) -> Dict[str, Any]:
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": temperature,
        }
        async with httpx.AsyncClient(timeout=90.0, trust_env=False) as client:
            r = await client.post(f"{asb}/api/sutui-chat/completions", json=body, headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(f"sutui-chat HTTP {r.status_code}: {(r.text or '')[:800]}")
        data = r.json() if r.content else {}
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception:
            content = _safe_str(data, 4000)
        return _extract_json_object(content)

    try:
        script_prompt = SCRIPT_PROMPT_TEMPLATE.format(
            video_type=pl.video_type,
            target_audience=pl.target_audience,
            duration=pl.duration,
            core_message=goal,
            style=pl.style,
        )
        script = await call_json(script_prompt, 0.7)
        raw_script_scenes = script.get("scenes") if isinstance(script.get("scenes"), list) else []
        if not raw_script_scenes:
            raise RuntimeError("script stage returned no scenes")
        if len(raw_script_scenes) > pl.scene_count:
            script["scenes"] = raw_script_scenes[: pl.scene_count]

        storyboard_prompt = STORYBOARD_PROMPT_TEMPLATE.format(
            title=_safe_str(script.get("title") or goal, 120),
            core_storyline=_safe_str(script.get("core_storyline") or goal, 1000),
            style=pl.style,
            rhythm_notes=_safe_str(script.get("rhythm_notes"), 600),
            scenes_text=_script_scenes_text(script),
        )
        storyboard = await call_json(storyboard_prompt, 0.65)
        if not isinstance(storyboard.get("prompts"), list):
            raise RuntimeError("storyboard stage returned no prompts")

        video_prompt = VIDEO_PROMPT_TEMPLATE.format(
            title=_safe_str(script.get("title") or goal, 120),
            core_storyline=_safe_str(script.get("core_storyline") or goal, 1000),
            total_duration=_safe_str(script.get("total_duration") or f"{pl.duration}s", 80),
            rhythm_notes=_safe_str(script.get("rhythm_notes"), 600),
            style_overview=_safe_str(storyboard.get("style_overview") or pl.style, 1000),
            scenes_detail=_video_scenes_detail(script, storyboard),
        )
        video_prompts = await call_json(video_prompt, 0.65)

        storyboard_items = storyboard.get("prompts") if isinstance(storyboard.get("prompts"), list) else []
        video_items = video_prompts.get("scenes") if isinstance(video_prompts.get("scenes"), list) else []
        script_items = script.get("scenes") if isinstance(script.get("scenes"), list) else []
        by_storyboard_id = {str(item.get("scene_id") or ""): item for item in storyboard_items if isinstance(item, dict)}
        by_video_id = {str(item.get("scene_id") or ""): item for item in video_items if isinstance(item, dict)}
        scenes: List[Dict[str, Any]] = []
        for idx, script_scene in enumerate(script_items[: pl.scene_count], 1):
            if not isinstance(script_scene, dict):
                continue
            sid = str(script_scene.get("scene_id") or idx)
            storyboard_scene = by_storyboard_id.get(sid) or (storyboard_items[idx - 1] if idx - 1 < len(storyboard_items) and isinstance(storyboard_items[idx - 1], dict) else {})
            video_scene = by_video_id.get(sid) or (video_items[idx - 1] if idx - 1 < len(video_items) and isinstance(video_items[idx - 1], dict) else {})
            image_prompt = _safe_str(storyboard_scene.get("prompt_text") or storyboard_scene.get("visual_content"), 2500)
            motion_prompt = _safe_str(video_scene.get("video_prompt"), 2500)
            if not image_prompt or not motion_prompt:
                continue
            scenes.append(
                {
                    "scene_id": int(script_scene.get("scene_id") or idx),
                    "title": _safe_str(script_scene.get("scene_description") or f"Scene {idx}", 120),
                    "duration": _parse_duration_seconds(video_scene.get("duration") or script_scene.get("duration_estimate"), _scene_duration(pl.duration, pl.scene_count)),
                    "image_prompt": image_prompt,
                    "video_prompt": _with_video_no_text_constraint(motion_prompt, 2500),
                    "narration": _safe_str(video_scene.get("narration_text") or script_scene.get("narration"), 600),
                    "transition": _safe_str(video_scene.get("transition") or script_scene.get("transition"), 300),
                    "sound_design": _safe_str(video_scene.get("sound_design"), 400),
                }
            )
        plan = {
            "title": _safe_str(script.get("title") or video_prompts.get("title") or goal, 120),
            "summary": _safe_str(script.get("core_storyline") or goal, 2000),
            "rhythm_notes": _safe_str(script.get("rhythm_notes"), 800),
            "style_overview": _safe_str(storyboard.get("style_overview") or pl.style, 1000),
            "script": script,
            "storyboard": storyboard,
            "video_prompt_plan": video_prompts,
            "scenes": scenes,
        }
        scenes = _extract_scenes(plan, pl)
        if not scenes:
            raise RuntimeError("planner returned no valid scenes")
        plan["scenes"] = scenes
        plan["title"] = _safe_str(plan.get("title") or goal, 120)
        plan["summary"] = _safe_str(plan.get("summary") or goal, 2000)
        return plan
    except Exception as e:
        logger.warning("[create.video.pipeline] planner failed, using fallback plan: %s", e)
        return _fallback_plan(pl)


def _best_image_reference(result: Dict[str, Any]) -> Dict[str, str]:
    asset_id = _first_asset_id(result, "image") or _first_asset_id(result)
    urls = _collect_urls(result, want="image")
    return {"asset_id": asset_id, "image_url": urls[0] if urls else ""}


async def run_create_video_pipeline(
    *,
    pl: CreateVideoPipelinePayload,
    token: str,
    installation_id: str,
    chat_turn_id: str = "",
    progress: Optional[Callable[[str, str, Optional[Dict[str, Any]]], None]] = None,
    billing_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    goal = _goal_text(pl)
    if not goal:
        raise HTTPException(status_code=400, detail="prompt is required")

    def emit(stage: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        if progress:
            progress(stage, message, extra)

    aspect_ratio = _normal_aspect_ratio(pl.aspect_ratio)
    plan = dict(pl.precomputed_plan or {}) if isinstance(pl.precomputed_plan, dict) else {}
    if not plan:
        plan = await _retry_async(
            "plan",
            2,
            lambda: _call_create_video_planner(
                pl=pl,
                token=token,
                installation_id=installation_id,
                chat_turn_id=chat_turn_id,
            ),
            emit,
        )
    scenes = _extract_scenes(plan, pl) or _fallback_plan(pl)["scenes"]
    emit("plan_done", "plan generated", {"title": plan.get("title"), "scene_count": len(scenes)})

    image_model = (pl.image_model or "").strip() or DEFAULT_IMAGE_MODEL
    video_model = (pl.video_model or "").strip() or DEFAULT_VIDEO_MODEL
    image_size = _image_size_for_ratio(aspect_ratio)
    scene_outputs: List[Dict[str, Any]] = []
    saved_assets: List[Dict[str, Any]] = []
    video_urls_all: List[str] = []
    image_urls_all: List[str] = []

    for idx, scene in enumerate(scenes, 1):
        emit("scene_start", f"scene {idx}/{len(scenes)}", {"scene_id": scene.get("scene_id")})
        image_result: Dict[str, Any] = {}
        image_ref = {"asset_id": "", "image_url": ""}
        if pl.reference_asset_ids:
            image_ref["asset_id"] = pl.reference_asset_ids[min(idx - 1, len(pl.reference_asset_ids) - 1)]
        elif pl.reference_image_urls:
            image_ref["image_url"] = pl.reference_image_urls[min(idx - 1, len(pl.reference_image_urls) - 1)]
        else:
            image_payload: Dict[str, Any] = {
                "prompt": scene["image_prompt"],
                "model": image_model,
                "quality": "low",
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
            }
            image_result = await _retry_async(
                "image",
                pl.image_retry_count + 1,
                lambda image_payload=image_payload: _submit_and_wait_generation(
                    kind="image",
                    submit_payload=image_payload,
                    token=token,
                    installation_id=installation_id,
                    timeout_seconds=pl.image_poll_timeout_seconds,
                    interval_seconds=pl.poll_interval_seconds,
                    progress=emit,
                    pipeline_context=billing_context,
                ),
                emit,
            )
            image_ref = _best_image_reference(image_result)
            emit(
                "image_done",
                f"scene {idx} image generated",
                {
                    "scene_id": scene.get("scene_id") or idx,
                    "image_asset_id": image_ref.get("asset_id") or "",
                    "image_url": image_ref.get("image_url") or "",
                    "partial_scene": {
                        "scene_id": scene.get("scene_id") or idx,
                        "title": scene.get("title") or f"Scene {idx}",
                        "image_asset_id": image_ref.get("asset_id") or _first_asset_id(image_result, "image"),
                        "image_urls": _collect_urls(image_result, want="image") or ([image_ref["image_url"]] if image_ref.get("image_url") else []),
                        "image": image_result,
                        "video_prompt": _with_video_no_text_constraint(scene["video_prompt"], 2500),
                        "duration": int(scene.get("duration") or _scene_duration(pl.duration, len(scenes))),
                    },
                    "partial_plan": plan,
                },
            )

        video_payload: Dict[str, Any] = {
            "prompt": _with_video_no_text_constraint(scene["video_prompt"], 2500),
            "model": video_model,
            "aspect_ratio": aspect_ratio,
            "duration": int(scene.get("duration") or _scene_duration(pl.duration, len(scenes))),
        }
        if image_ref.get("asset_id"):
            video_payload["asset_id"] = image_ref["asset_id"]
        if image_ref.get("image_url"):
            video_payload["image_url"] = image_ref["image_url"]
        if not video_payload.get("asset_id") and not video_payload.get("image_url"):
            raise RuntimeError("image generation finished but no image asset/url was available for video generation")

        video_result = await _retry_async(
            "video",
            pl.video_retry_count + 1,
            lambda video_payload=video_payload: _submit_and_wait_generation(
                kind="video",
                submit_payload=video_payload,
                token=token,
                installation_id=installation_id,
                timeout_seconds=pl.video_poll_timeout_seconds,
                interval_seconds=pl.poll_interval_seconds,
                progress=emit,
                pipeline_context=billing_context,
            ),
            emit,
        )
        scene_image_urls = _collect_urls(image_result, want="image") if image_result else []
        scene_video_urls = _collect_urls(video_result, want="video")
        image_urls_all.extend(scene_image_urls)
        video_urls_all.extend(scene_video_urls)
        saved_assets.extend(image_result.get("saved_assets") or [])
        saved_assets.extend(video_result.get("saved_assets") or [])
        scene_outputs.append(
            {
                "scene_id": scene.get("scene_id") or idx,
                "title": scene.get("title") or f"Scene {idx}",
                "image_asset_id": image_ref.get("asset_id") or _first_asset_id(image_result, "image"),
                "video_asset_id": _first_asset_id(video_result, "video") or _first_asset_id(video_result),
                "image_urls": scene_image_urls or ([image_ref["image_url"]] if image_ref.get("image_url") else []),
                "video_urls": scene_video_urls,
                "image": image_result,
                "video": video_result,
            }
        )
        emit("scene_done", f"scene {idx} done", {"video_asset_id": scene_outputs[-1].get("video_asset_id")})

    final_video_asset_id = ""
    for item in reversed(scene_outputs):
        final_video_asset_id = _safe_str(item.get("video_asset_id"), 64)
        if final_video_asset_id:
            break

    return {
        "ok": True,
        "pipeline": "create_video_sutui_pipeline",
        "status": "completed",
        "models": {
            "planning": (pl.planning_model or DEFAULT_PLANNING_MODEL),
            "image": image_model,
            "video": video_model,
        },
        "plan": plan,
        "scenes": scene_outputs,
        "saved_assets": _collect_saved_assets({"saved_assets": saved_assets}),
        "video_asset_id": final_video_asset_id,
        "final_asset_id": final_video_asset_id,
        "media_urls": {
            "image": image_urls_all,
            "video": video_urls_all,
        },
        "message": "Video creation completed through the Sutui path; image and video generation used image.generate/video.generate with unified server billing.",
    }


def _create_video_billing_payload(pl: CreateVideoPipelinePayload) -> Dict[str, Any]:
    payload = pl.model_dump()
    payload["source_mode"] = "reference" if (pl.reference_asset_ids or pl.reference_image_urls) else "ai_image"
    payload["scene_count"] = max(1, min(6, int(pl.scene_count or 1)))
    payload["image_model"] = (pl.image_model or DEFAULT_IMAGE_MODEL)
    payload["video_model"] = (pl.video_model or DEFAULT_VIDEO_MODEL)
    return payload


def _create_video_partial_from_scenes(
    *,
    pl: CreateVideoPipelinePayload,
    plan: Optional[Dict[str, Any]],
    partial_scenes: List[Dict[str, Any]],
    error_message: str,
) -> Dict[str, Any]:
    images: List[str] = []
    saved: List[Dict[str, Any]] = []
    ref_asset_ids: List[str] = []
    ref_urls: List[str] = []
    for scene in partial_scenes:
        if not isinstance(scene, dict):
            continue
        aid = _safe_str(scene.get("image_asset_id"), 80)
        if aid:
            ref_asset_ids.append(aid)
        urls = scene.get("image_urls") if isinstance(scene.get("image_urls"), list) else []
        for url in urls:
            s = str(url or "").strip()
            if s:
                images.append(s)
                ref_urls.append(s)
        image = scene.get("image") if isinstance(scene.get("image"), dict) else {}
        saved.extend(image.get("saved_assets") or [])
    return {
        "ok": False,
        "pipeline": "create_video_sutui_pipeline",
        "status": "partial_image",
        "resume_available": bool(ref_asset_ids or ref_urls),
        "error": error_message[:2000],
        "models": {
            "planning": (pl.planning_model or DEFAULT_PLANNING_MODEL),
            "image": (pl.image_model or DEFAULT_IMAGE_MODEL),
            "video": (pl.video_model or DEFAULT_VIDEO_MODEL),
        },
        "plan": plan or {},
        "scenes": partial_scenes,
        "saved_assets": _collect_saved_assets({"saved_assets": saved}),
        "image_asset_id": ref_asset_ids[0] if ref_asset_ids else "",
        "final_asset_id": ref_asset_ids[0] if ref_asset_ids else "",
        "media_urls": {"image": images, "video": []},
        "resume_payload": {
            "capability_id": "create.video.pipeline",
            "prompt": _goal_text(pl),
            "topic": pl.topic,
            "video_type": pl.video_type,
            "target_audience": pl.target_audience,
            "style": pl.style,
            "duration": pl.duration,
            "scene_count": max(1, len(partial_scenes) or int(pl.scene_count or 1)),
            "aspect_ratio": pl.aspect_ratio,
            "language": pl.language,
            "planning_model": pl.planning_model,
            "image_model": pl.image_model,
            "video_model": pl.video_model,
            "precomputed_plan": plan or {},
            "reference_asset_ids": ref_asset_ids,
            "reference_image_urls": ref_urls,
        },
    }


async def run_create_video_pipeline_with_total_billing(
    *,
    pl: CreateVideoPipelinePayload,
    token: str,
    installation_id: str,
    chat_turn_id: str = "",
    progress: Optional[Callable[[str, str, Optional[Dict[str, Any]]], None]] = None,
) -> Dict[str, Any]:
    pre_payload = _create_video_billing_payload(pl)
    pre = await _pre_deduct_pipeline_total(
        capability_id="create.video.pipeline",
        payload=pre_payload,
        token=token,
        installation_id=installation_id,
    )
    credits = pre.get("credits_charged") if isinstance(pre, dict) else 0
    billing_context = {
        "precharged": True,
        "pipeline_id": f"create-video-{asyncio.current_task().get_name() if asyncio.current_task() else 'run'}",
        "capability_id": "create.video.pipeline",
    }
    captured: Dict[str, Any] = {"plan": None, "partial_scenes": []}

    def wrapped_progress(stage: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        if isinstance(extra, dict):
            if extra.get("partial_plan") and isinstance(extra.get("partial_plan"), dict):
                captured["plan"] = extra.get("partial_plan")
            if extra.get("partial_scene") and isinstance(extra.get("partial_scene"), dict):
                scenes = captured.setdefault("partial_scenes", [])
                if isinstance(scenes, list):
                    scenes.append(extra.get("partial_scene"))
        if progress:
            progress(stage, message, extra)

    try:
        result = await run_create_video_pipeline(
            pl=pl,
            token=token,
            installation_id=installation_id,
            chat_turn_id=chat_turn_id,
            progress=wrapped_progress,
            billing_context=billing_context,
        )
    except Exception as exc:
        await _refund_pipeline_total(
            capability_id="create.video.pipeline",
            credits=credits,
            token=token,
            installation_id=installation_id,
        )
        await _record_pipeline_total(
            capability_id="create.video.pipeline",
            payload=pre_payload,
            result=None,
            token=token,
            installation_id=installation_id,
            credits_charged=0,
            success=False,
            error_message=str(exc),
        )
        partial_scenes = captured.get("partial_scenes") if isinstance(captured.get("partial_scenes"), list) else []
        if partial_scenes:
            partial = _create_video_partial_from_scenes(
                pl=pl,
                plan=captured.get("plan") if isinstance(captured.get("plan"), dict) else None,
                partial_scenes=partial_scenes,
                error_message=str(exc),
            )
            partial["pipeline_billing"] = {
                "pre_deduct_applied": bool(credits),
                "credits_charged": credits or 0,
                "refunded": True,
            }
            raise PipelinePartialResultError(str(exc), partial) from exc
        raise
    result["pipeline_billing"] = {
        "pre_deduct_applied": bool(credits),
        "credits_charged": credits or 0,
        "billing_rule": pre.get("billing_rule") if isinstance(pre, dict) else "",
        "breakdown": pre.get("breakdown") if isinstance(pre, dict) else {},
    }
    await _record_pipeline_total(
        capability_id="create.video.pipeline",
        payload=pre_payload,
        result=result,
        token=token,
        installation_id=installation_id,
        credits_charged=credits,
        success=True,
    )
    return result


async def _background_runner(
    job_id: str,
    pl: CreateVideoPipelinePayload,
    token: str,
    installation_id: str,
    chat_turn_id: str = "",
) -> None:
    def progress(stage: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        append_create_video_progress(job_id, stage=stage, message=message, extra=extra)

    try:
        result = await run_create_video_pipeline_with_total_billing(
            pl=pl,
            token=token,
            installation_id=installation_id,
            chat_turn_id=chat_turn_id,
            progress=progress,
        )
    except Exception as e:
        logger.exception("[create.video.pipeline] background job failed job_id=%s", job_id)
        update_create_video_job(job_id, status="failed", stage="failed", error=str(e)[:2000])
        return
    update_create_video_job(job_id, status="completed", stage="completed", error=None, result=result)


def _log_background_task_done(task: asyncio.Task) -> None:
    try:
        _ = task.exception()
    except asyncio.CancelledError:
        pass


def start_create_video_pipeline_background_job(
    *,
    pl: CreateVideoPipelinePayload,
    token: str,
    installation_id: str,
    user_id: int,
    chat_turn_id: str = "",
) -> str:
    if not _goal_text(pl):
        raise HTTPException(status_code=400, detail="prompt is required")
    job_id = create_video_job(user_id=user_id, payload=pl.model_dump())
    task = asyncio.create_task(
        _background_runner(job_id, pl, token, installation_id, chat_turn_id=chat_turn_id)
    )
    task.add_done_callback(_log_background_task_done)
    return job_id


@router.post("/api/create-video/pipeline/run")
async def create_video_pipeline_run(
    body: CreateVideoPipelineBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
):
    pl = body.payload
    token = _raw_token_from_request(request)
    installation_id = _installation_id_from_request(request, current_user.id)
    try:
        return await run_create_video_pipeline_with_total_billing(pl=pl, token=token, installation_id=installation_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:2000]) from e


@router.post("/api/create-video/pipeline/start")
async def create_video_pipeline_start(
    body: CreateVideoPipelineBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
):
    pl = body.payload
    token = _raw_token_from_request(request)
    installation_id = _installation_id_from_request(request, current_user.id)
    job_id = start_create_video_pipeline_background_job(
        pl=pl,
        token=token,
        installation_id=installation_id,
        user_id=current_user.id,
    )
    return {"ok": True, "async": True, "job_id": job_id, "poll_path": f"/api/create-video/pipeline/jobs/{job_id}"}


@router.get("/api/create-video/pipeline/jobs/{job_id}")
async def create_video_pipeline_job_status(
    job_id: str,
    compact: bool = False,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
):
    job = get_create_video_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found or expired")
    if int(job.get("user_id") or -1) != int(current_user.id):
        raise HTTPException(status_code=403, detail="permission denied for this job")
    out: Dict[str, Any] = {
        "ok": True,
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "stage": job.get("stage"),
        "created_at_ts": job.get("created_at_ts"),
        "updated_at_ts": job.get("updated_at_ts"),
        "progress": job.get("progress") or [],
    }
    if job.get("status") == "failed":
        out["error"] = job.get("error")
    if job.get("status") == "completed" and not compact:
        out["result"] = job.get("result")
    return out
