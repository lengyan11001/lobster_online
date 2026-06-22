from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from .assets import get_asset_public_url
from .auth import _ServerUser, get_current_user_for_local
from .comfly_seedance_tvc import ComflySeedancePipelinePayload, start_seedance_tvc_pipeline_job
from .comfly_image_studio import _generate_image_studio_core

router = APIRouter()

_ROOT = Path(__file__).resolve().parents[3]
_DATA_FILE = _ROOT / "static" / "data" / "local-bestseller-10day.json"


class LocalBestsellerProfile(BaseModel):
    name: str = Field("", description="真实姓名")
    nickname: str = Field("", description="短视频昵称")
    gender: str = Field("female", description="female/male")
    identity: str = Field("女老板", description="人设身份")
    industry: str = Field("大健康", description="行业/赛道")
    city: str = Field("深圳", description="当前城市")
    province: str = Field("广东", description="省份")
    hometown: str = Field("广东潮汕", description="家乡")
    age_label: str = Field("80后", description="年龄标签")
    target_age: str = Field("607080后", description="目标共鸣人群")
    style: str = Field("真实同城生活感", description="画面风格")
    photo_asset_id: str = Field("", description="人物照片素材 ID")
    photo_url: str = Field("", description="人物照片 URL")
    source_mode: str = Field("upload", description="upload/library/video")
    uploaded_video_url: str = Field("", description="上传视频 URL")


class LocalBestsellerPlanBody(BaseModel):
    profile: LocalBestsellerProfile = Field(default_factory=LocalBestsellerProfile)
    days: int = Field(30, ge=1, le=30)


class LocalBestsellerCardOverride(BaseModel):
    id: str = ""
    day: int = Field(..., ge=1, le=30)
    title: str = ""
    stage: str = ""
    douyin: Dict[str, Any] = Field(default_factory=dict)
    videohao: Dict[str, Any] = Field(default_factory=dict)
    subtitle_text: str = ""
    ai_variant: str = ""
    scene_prompt: str = ""
    image_prompt: str = ""
    video_prompt: str = ""
    scene_asset_id: str = ""
    scene_url: str = ""
    scene_preview_url: str = ""
    scene_name: str = ""
    image_url: str = ""
    image_asset_id: str = ""
    video_url: str = ""
    video_task_id: str = ""
    scene_status: str = ""
    video_status: str = ""


class LocalBestsellerSceneBody(LocalBestsellerPlanBody):
    day: Optional[int] = Field(None, ge=1, le=30)
    model: str = Field("gpt-image-2", description="图片合成模型")
    quality: str = Field("high", description="图片质量")
    item: Optional[LocalBestsellerCardOverride] = None
    items: List[LocalBestsellerCardOverride] = Field(default_factory=list)


class LocalBestsellerVideoBody(LocalBestsellerPlanBody):
    day: Optional[int] = Field(None, ge=1, le=30)
    video_model: str = Field("grok-imagine-video-1.5-preview", description="Grok/创意分镜图生视频模型")
    item: Optional[LocalBestsellerCardOverride] = None
    items: List[LocalBestsellerCardOverride] = Field(default_factory=list)


def _load_templates() -> List[Dict[str, Any]]:
    try:
        rows = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"同城爆款模板加载失败: {exc}") from exc
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=500, detail="同城爆款模板为空")
    return [row for row in rows if isinstance(row, dict)]


def _clean_profile(profile: LocalBestsellerProfile) -> Dict[str, str]:
    data = profile.model_dump()
    defaults = LocalBestsellerProfile().model_dump()
    out: Dict[str, str] = {}
    for key, value in data.items():
        text = str(value or "").strip()
        out[key] = text or str(defaults.get(key) or "")
    if not out.get("nickname"):
        out["nickname"] = out.get("name") or "我"
    gender = str(out.get("gender") or "").strip().lower()
    out["gender"] = "male" if gender in {"male", "man", "m", "男", "男性", "先生"} else "female"
    if not out.get("identity"):
        out["identity"] = "男老板" if out["gender"] == "male" else "女老板"
    out.update(_gender_terms(out["gender"]))
    out["identity"] = _apply_gender_language(out.get("identity") or "", out)
    return out


def _gender_terms(gender: str) -> Dict[str, str]:
    if gender == "male":
        return {
            "gender_cn": "男",
            "person_cn": "男人",
            "boss_cn": "男老板",
            "pronoun": "他",
            "young_person": "小伙",
            "sibling": "哥",
        }
    return {
        "gender_cn": "女",
        "person_cn": "女人",
        "boss_cn": "女老板",
        "pronoun": "她",
        "young_person": "姑娘",
        "sibling": "姐",
    }


def _apply_gender_language(text: str, profile: Dict[str, str]) -> str:
    out = str(text or "")
    if profile.get("gender") != "male":
        return out
    phrase_replacements = [
        ("但是可以靠男人\n的女人更幸福", "但是可以靠女人\n的男人更幸福"),
        ("但是可以靠男人的女人更幸福", "但是可以靠女人的男人更幸福"),
        ("如果一个女人能开车", "如果一个男人能开车"),
        ("你会给她", "你会给他"),
        ("女人不努力\n一辈子受委屈", "男人不努力\n一辈子没退路"),
    ]
    for old, new in phrase_replacements:
        out = out.replace(old, new)
    out = re.sub(r"但是可以靠男人\s*的女人更幸福", "但是可以靠女人\n的男人更幸福", out)
    replacements = [
        ("女老板", "男老板"),
        ("女人", "男人"),
        ("女性", "男性"),
        ("女生", "男生"),
        ("姑娘", "小伙"),
        ("小姐姐", "小哥哥"),
        ("美女", "帅哥"),
        ("婆婆", "长辈"),
        ("她", "他"),
    ]
    for old, new in replacements:
        out = out.replace(old, new)
    out = re.sub(r"但是可以靠男人\s*的男人更幸福", "但是可以靠女人\n的男人更幸福", out)
    return out


def _format_template(template: str, profile: Dict[str, str]) -> str:
    try:
        return _apply_gender_language(str(template or "").format(**profile), profile)
    except KeyError:
        return _apply_gender_language(str(template or ""), profile)


def _split_lines(text: str) -> List[str]:
    lines: List[str] = []
    for part in str(text or "").replace("。", "\n").replace("？", "？\n").replace("！", "！\n").splitlines():
        line = part.strip()
        if line:
            lines.append(line)
    return lines


def _clean_subtitle_text(text: str) -> str:
    cleaned: List[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"^(标题文案|数字人口播内容|口播内容|文案内容)\s*[：:]\s*", "", line).strip()
        line = re.sub(r"^坐标\s*[：:]\s*", "", line).strip()
        line = re.sub(r"^音乐\s*[：:]\s*.*$", "", line).strip()
        if not line or line.startswith("#"):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def _subtitle_source(row: Dict[str, Any], douyin_copy: str, videohao_copy: str, ai_variant: str) -> str:
    day = int(row.get("day") or 0)
    if day == 1:
        return _clean_subtitle_text(douyin_copy)
    if day in {3, 4, 5}:
        return _clean_subtitle_text(douyin_copy)
    return _clean_subtitle_text(ai_variant or videohao_copy or douyin_copy)


def _caption_style(row: Dict[str, Any], platform: str) -> Dict[str, Any]:
    day = int(row.get("day") or 0)
    variant = "rank" if day in {1, 3, 4, 5} and platform == "douyin" else "talking"
    if variant == "rank":
        return {
            "placement": "top_dense",
            "variant": "rank_table",
            "primary_color": "#fff200",
            "accent_color": "#ff2d2d",
            "stroke_color": "#101010",
            "font_weight": 900,
            "reference": "顶部红黄大标题 + 中间密集榜单/分栏，字要像截图里短视频模板那样压在画面上。",
        }
    return {
        "placement": "top_center",
        "variant": "yellow_black_stroke",
        "primary_color": "#fff200",
        "accent_color": "#ffffff",
        "stroke_color": "#101010",
        "font_weight": 900,
        "reference": "顶部黄字黑描边多行字幕，贴近画面上方，人物脸部避开文字，不做底部字幕条。",
    }


def _hashtags(profile: Dict[str, str], stage: str) -> List[str]:
    tags = ["同城", profile.get("city", ""), profile.get("industry", ""), "正能量"]
    if stage == "破播":
        tags.append("上热门")
    else:
        tags.append("个人IP")
    seen = set()
    out = []
    for tag in tags:
        tag = str(tag or "").strip().lstrip("#")
        if tag and tag not in seen:
            out.append("#" + tag)
            seen.add(tag)
    return out


def _photo_identity_prompt(profile: Dict[str, str]) -> str:
    gender_hint = "男性" if profile.get("gender") == "male" else "女性"
    return (
        f"必须以用户上传的人物照片为唯一身份参考，保持本人五官、脸型、发型、肤色、年龄感、性别（{gender_hint}）、气质和身材比例不变；"
        "可以更换衣服、姿态和环境，但不能变成另一个人，不能网红脸，不能磨皮过度。"
    )


def _real_life_prompt() -> str:
    return (
        "画面必须像生活中朋友用手机随手拍出来的竖屏照片，不要商业棚拍，不要AI大片感；"
        "人物动作自然，可以走路、转身、低头整理东西、侧身工作、与环境自然互动或被随手拍到，不要求正脸对着屏幕；"
        "允许灯光不完美、角度随意、构图不完美、背景有一点杂乱、轻微噪点、轻微运动模糊和真实透视；"
        "使用自然光、路灯、店内普通灯或混合光，皮肤有真实纹理，衣服和环境有生活痕迹，去除AI味。"
    )


def _naturalize_scene_prompt(text: str) -> str:
    out = str(text or "")
    out = out.replace("抬头看向镜头", "自然走动或侧身抬头，像被朋友随手拍到")
    out = out.replace("自然转头看镜头", "自然转头或继续做自己的事，不必正脸看镜头")
    out = out.replace("停下脚步看镜头", "停下脚步或继续走动，神态自然，不必正脸看镜头")
    out = out.replace("正面对镜口播", "自然走动、整理东西或被侧面随手拍到，不说话不口播")
    out = out.replace("对镜口播", "自然走动、整理东西或被侧面随手拍到，不说话不口播")
    out = out.replace("人物正面口播", "人物自然走动、整理东西或被侧面随手拍到，不说话不口播")
    out = out.replace("停下来自我介绍", "自然停留、走动或整理手边事情，不说话不口播")
    return out


def _sanitize_video_prompt_no_speech(prompt: str) -> str:
    out = str(prompt or "").strip()
    protected: Dict[str, str] = {}
    protected_terms = ["说话", "口播", "对白", "台词", "唱歌", "配音", "开口", "做明显嘴型", "唇同步"]
    token_index = 0
    for term in protected_terms:
        for prefix in ("人物不要", "不要", "人物禁止", "禁止"):
            phrase = f"{prefix}{term}"
            if phrase not in out:
                continue
            token = f"__LB_NO_SPEECH_{token_index}__"
            token_index += 1
            normalized_prefix = "人物不要" if prefix.startswith("人物") else "不要"
            protected[token] = f"{normalized_prefix}{term}"
            out = out.replace(phrase, token)
    replacements = [
        ("动作自然：走路、停下、看镜头、轻微招手或口播。", "动作自然：走路、停下、转身、低头整理东西、侧身工作或与环境自然互动；视频中间约第4-6秒要自然抬头看向镜头。"),
        ("动作自然：走路、停下、看镜头、轻微招手或口播", "动作自然：走路、停下、转身、低头整理东西、侧身工作或与环境自然互动；视频中间约第4-6秒要自然抬头看向镜头"),
        ("轻微招手或口播", "自然走动、停下、转身、整理东西或侧身工作"),
        ("招手或口播", "自然走动、整理东西或侧身工作"),
        ("自然口播", "自然动作"),
        ("正面对镜口播", "自然走动、整理东西或被侧面随手拍到"),
        ("对镜口播", "自然走动、整理东西或被侧面随手拍到"),
        ("人物正面口播", "人物自然走动、整理东西或被侧面随手拍到"),
        ("看镜头说话", "自然看向周围环境"),
        ("对着镜头说话", "自然看向周围环境"),
        ("说台词", "做自然动作"),
        ("说话", "做自然动作"),
        ("讲述", "自然行动"),
        ("开口", "嘴巴自然放松"),
        ("嘴型", "嘴巴自然放松"),
        ("唇形", "嘴巴自然放松"),
        ("唇同步", "嘴巴自然放松"),
        ("看镜头", "中间三秒自然看向镜头，其他时间自然看向周围"),
    ]
    for old, new in replacements:
        out = out.replace(old, new)
    out = out.replace("口播", "无声自然动作")
    out = out.replace("对白", "无对白")
    out = out.replace("台词", "无台词")
    out = out.replace("配音", "无配音")
    for token, phrase in protected.items():
        out = out.replace(token, phrase)
    guard = (
        "全程静默自然动作视频：人物不要说话、不要口播、不要对白、不要台词、不要唱歌、不要配音、"
        "不要做明显嘴型或唇同步，嘴巴自然放松或闭合；不要让AI生成任何字幕、文字、水印，"
        "最终字幕只由后期叠加。"
    )
    if "全程静默自然动作视频" not in out:
        out = f"{out}{guard}" if out.endswith(("。", "；", ";", ".")) else f"{out}。{guard}"
    mid_look = (
        "节奏要求：10秒视频中间约第4-6秒，人物要自然抬头看向镜头或自然扫视镜头，保持真实随手拍感；"
        "其他时间可以继续走路、整理东西、侧身工作或看向周围。"
    )
    if "第4-6秒" not in out and "中间约第4" not in out:
        out = f"{out}{mid_look}" if out.endswith(("。", "；", ";", ".")) else f"{out}。{mid_look}"
    bgm = (
        "视频必须伴随街头背景音和轻快节奏音乐，音量低，只做真实街头氛围和轻快节奏铺底；"
        "不要人声、不要旁白、不要歌词、不要任何人物发声。"
    )
    optional_bgm = "可加入轻微背景音乐或真实环境氛围感，音量低，不要人声、不要旁白、不要歌词、不要任何人物发声。"
    if optional_bgm in out:
        out = out.replace(optional_bgm, bgm)
    elif "街头背景音" not in out or "轻快节奏音乐" not in out:
        out = f"{out}{bgm}" if out.endswith(("。", "；", ";", ".")) else f"{out}。{bgm}"
    return out


def _build_card(row: Dict[str, Any], profile: Dict[str, str]) -> Dict[str, Any]:
    day = int(row.get("day") or 0)
    stage = str(row.get("stage") or "").strip()
    douyin_copy = _format_template(str(row.get("douyin_copy") or ""), profile)
    videohao_copy = _format_template(str(row.get("videohao_copy") or ""), profile)
    ai_variant = _format_template(str(row.get("ai_variant") or ""), profile)
    scene_prompt = _naturalize_scene_prompt(_format_template(str(row.get("scene_template") or ""), profile))
    subtitle_text = _subtitle_source(row, douyin_copy, videohao_copy, ai_variant)
    style = profile.get("style") or "真实同城生活感"
    photo_url = profile.get("photo_url") or ""
    photo_asset_id = profile.get("photo_asset_id") or ""
    scene_reference_prompt = (
        "如当前Day卡片提供了场景底图参考，第二张参考图只用于保持环境、空间、光线、街景/门店/室内结构和生活现场感；"
        "人物身份必须仍以用户人物照片为准，不要把场景图里的人当成主角。"
    )
    base_scene_prompt = (
        f"{scene_prompt} 行业/人设：{profile.get('industry')}，{profile.get('identity')}，风格：{style}。"
        f"{_photo_identity_prompt(profile)}{scene_reference_prompt}{_real_life_prompt()}"
        "输出9:16竖屏生活场景照片；画面本身不要生成字幕、不要水印、不要海报排版，字幕由前端/后期叠加。"
    )
    video_prompt = (
        f"基于合成出的场景照片生成10秒竖屏视频。人物身份保持不变，衣服可随场景自然变化；"
        "人物正常走动，步伐和手臂摆动自然，可以停下、转身、低头整理东西、侧身工作或与环境自然互动；"
        "视频中间约第4-6秒，人物要自然抬头看向镜头或自然扫视镜头，像刚好发现朋友在拍；"
        "镜头像朋友拿手机边走边拍，轻微跟拍、轻微晃动、轻微推近或侧向视角变化，视角不完美但真实，像真的现场拍摄。"
        f"{_real_life_prompt()}"
        "不要出现AI感运镜，不要过度电影光，不要文字水印。"
    )
    video_prompt = _sanitize_video_prompt_no_speech(video_prompt)
    return {
        "id": f"local-day-{day:02d}",
        "day": day,
        "stage": stage,
        "title": str(row.get("title") or f"第{day}天").strip(),
        "douyin": {
            "copy": douyin_copy,
            "reference": str(row.get("douyin_reference") or "").strip(),
            "subtitle": {
                "lines": _split_lines(douyin_copy),
                "style": _caption_style(row, "douyin"),
            },
        },
        "videohao": {
            "copy": videohao_copy,
            "reference": str(row.get("videohao_reference") or "").strip(),
            "subtitle": {
                "lines": _split_lines(videohao_copy),
                "style": _caption_style(row, "videohao"),
            },
        },
        "subtitle_text": subtitle_text,
        "ai_variant": ai_variant,
        "hashtags": _hashtags(profile, stage),
        "scene_prompt": base_scene_prompt,
        "image_prompt": base_scene_prompt,
        "video_prompt": video_prompt,
        "reference": {
            "photo_asset_id": photo_asset_id,
            "photo_url": photo_url,
            "uploaded_video_url": profile.get("uploaded_video_url") or "",
        },
        "scene_asset_id": "",
        "scene_url": "",
        "scene_preview_url": "",
        "scene_name": "",
        "status": "ready",
        "scene_status": "ready",
        "image_url": "",
        "image_asset_id": "",
        "video_url": "",
        "video_task_id": "",
        "video_status": "ready",
    }


def _merge_card_override(card: Dict[str, Any], override: Optional[LocalBestsellerCardOverride]) -> Dict[str, Any]:
    if override is None:
        return card
    data = override.model_dump()
    merged = dict(card)
    for key in ("title", "stage", "subtitle_text", "ai_variant", "scene_prompt", "image_prompt", "video_prompt", "scene_asset_id", "scene_url", "scene_preview_url", "scene_name", "image_url", "image_asset_id", "video_url", "video_task_id", "scene_status", "video_status"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            merged[key] = value.strip()
    if isinstance(data.get("douyin"), dict):
        merged_douyin = dict(merged.get("douyin") or {})
        incoming = data["douyin"]
        if str(incoming.get("copy") or "").strip():
            merged_douyin["copy"] = str(incoming.get("copy") or "").strip()
            merged_douyin["subtitle"] = {
                **(merged_douyin.get("subtitle") or {}),
                "lines": _split_lines(merged_douyin["copy"]),
            }
        if str(incoming.get("reference") or "").strip():
            merged_douyin["reference"] = str(incoming.get("reference") or "").strip()
        merged["douyin"] = merged_douyin
    if isinstance(data.get("videohao"), dict):
        merged_videohao = dict(merged.get("videohao") or {})
        incoming = data["videohao"]
        if str(incoming.get("copy") or "").strip():
            merged_videohao["copy"] = str(incoming.get("copy") or "").strip()
            merged_videohao["subtitle"] = {
                **(merged_videohao.get("subtitle") or {}),
                "lines": _split_lines(merged_videohao["copy"]),
            }
        if str(incoming.get("reference") or "").strip():
            merged_videohao["reference"] = str(incoming.get("reference") or "").strip()
        merged["videohao"] = merged_videohao
    merged["image_prompt"] = str(merged.get("scene_prompt") or merged.get("image_prompt") or "")
    return merged


def _merge_card_overrides(cards: List[Dict[str, Any]], overrides: List[LocalBestsellerCardOverride]) -> List[Dict[str, Any]]:
    by_day = {int(item.day): item for item in overrides or []}
    return [_merge_card_override(card, by_day.get(int(card.get("day") or 0))) for card in cards]


def _pick_card(cards: List[Dict[str, Any]], day: Optional[int]) -> Dict[str, Any]:
    if day is None:
        raise HTTPException(status_code=400, detail="请提供 day")
    for card in cards:
        if int(card.get("day") or 0) == int(day):
            return card
    raise HTTPException(status_code=404, detail=f"未找到第 {day} 天模板")


def _card_with_scene_result(card: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    images = result.get("images") if isinstance(result, dict) else []
    saved = result.get("saved_assets") if isinstance(result, dict) else []
    first = images[0] if isinstance(images, list) and images else {}
    saved_first = saved[0] if isinstance(saved, list) and saved else {}
    asset = saved_first.get("asset") if isinstance(saved_first, dict) else {}
    image_url = (
        str(first.get("url") or first.get("data_url") or "")
        if isinstance(first, dict)
        else ""
    )
    source_url = ""
    asset_id = ""
    if isinstance(asset, dict):
        source_url = str(asset.get("source_url") or "")
        asset_id = str(asset.get("asset_id") or "")
    if isinstance(first, dict):
        source_url = source_url or str(first.get("source_url") or "")
        asset_id = asset_id or str(first.get("asset_id") or "")
    card = dict(card)
    card["status"] = "scene_completed"
    card["scene_status"] = "completed"
    card["image_url"] = source_url or image_url
    card["image_asset_id"] = asset_id
    return card


def _scene_generation_prompt(card: Dict[str, Any]) -> str:
    prompt = str(card.get("scene_prompt") or card.get("image_prompt") or "").strip()
    if str(card.get("scene_url") or card.get("scene_asset_id") or "").strip() and "第二张参考图只用于保持环境" not in prompt:
        prompt += (
            "如当前Day卡片提供了场景底图参考，第二张参考图只用于保持环境、空间、光线、街景/门店/室内结构和生活现场感；"
            "人物身份必须仍以用户人物照片为准，不要把场景图里的人当成主角。"
        )
    return prompt


def _card_with_video_result(card: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    card = dict(card)
    task_id = str((result or {}).get("task_id") or (result or {}).get("job_id") or "").strip()
    card["video_status"] = "submitted" if task_id else "submitted"
    card["status"] = "video_submitted"
    card["video_task_id"] = task_id
    card["video_job_id"] = str((result or {}).get("job_id") or task_id).strip()
    card["video_poll_path"] = str((result or {}).get("poll_path") or "").strip()
    card["video_submit_result"] = result
    return card


def _seedance_grok_model(model: str) -> str:
    raw = str(model or "").strip()
    normalized = raw.lower().replace("_", "-").replace(" ", "")
    if not raw or normalized in {"grok-video-3", "yingmeng1.5plus", "影梦1.5plus"}:
        return "grok-video-3"
    return raw


def _seedance_grok_video_fallbacks() -> List[Dict[str, str]]:
    return [
        {"channel": "openmind", "model": "grok-imagine-video-1.5-preview"},
        {"channel": "yunwu", "model": "grok-video-3"},
        {"channel": "comfly", "model": "veo3.1-fast"},
    ]


async def _submit_card_video_via_seedance(
    *,
    card: Dict[str, Any],
    video_model: str,
    request: Request,
    current_user: _ServerUser,
    db: Session,
) -> Dict[str, Any]:
    image_asset_id = str(card.get("image_asset_id") or "").strip()
    image_url = ""
    if not image_asset_id:
        image_url = _resolve_card_image_url(card=card, current_user=current_user, request=request, db=db)
    raw_prompt = str(card.get("video_prompt") or "").strip()
    if not raw_prompt:
        raise HTTPException(status_code=400, detail=f"Day {card.get('day')} 缺少 Grok 10秒视频提示词")
    prompt = _sanitize_video_prompt_no_speech(raw_prompt)
    pl = ComflySeedancePipelinePayload(
        asset_id=image_asset_id or None,
        image_url=image_url or None,
        workflow_mode="direct_video",
        segment_count=1,
        segment_duration_seconds=10,
        total_duration_seconds=10,
        merge_clips=True,
        auto_save=True,
        task_text=prompt,
        video_model=_seedance_grok_model(video_model),
        video_channel="comfly",
        video_fallbacks=_seedance_grok_video_fallbacks(),
        aspect_ratio="9:16",
        generate_audio=True,
        watermark=False,
        image_model_fallback="gpt-image-2-yunwu",
    )
    return await start_seedance_tvc_pipeline_job(
        pl=pl,
        request=request,
        current_user=current_user,
        db=db,
        title=f"同城爆款 Day {card.get('day')} 视频",
        meta={
            "feature": "local_bestseller",
            "day": card.get("day"),
            "workflow_mode": "direct_video",
            "subtitle_text": str(card.get("subtitle_text") or "").strip(),
            "subtitle_style": card.get("douyin", {}).get("subtitle", {}).get("style") if isinstance(card.get("douyin"), dict) else {},
        },
    )


def _resolve_card_image_url(
    *,
    card: Dict[str, Any],
    current_user: _ServerUser,
    request: Request,
    db: Session,
) -> str:
    image_url = str(card.get("image_url") or "").strip()
    if image_url.startswith(("http://", "https://")):
        return image_url
    aid = str(card.get("image_asset_id") or "").strip()
    if aid:
        public = get_asset_public_url(aid, current_user.id, request, db)
        if public:
            return public
    scene_url = str(card.get("scene_url") or card.get("scene_preview_url") or "").strip()
    if scene_url.startswith(("http://", "https://")):
        return scene_url
    scene_aid = str(card.get("scene_asset_id") or "").strip()
    if scene_aid:
        public = get_asset_public_url(scene_aid, current_user.id, request, db)
        if public:
            return public
    raise HTTPException(status_code=400, detail=f"Day {card.get('day')} 还没有可用于视频合成的图片，请先上传/选择底图，或先合成场景图片")


def _resolve_reference_urls(
    *,
    profile: Dict[str, str],
    card: Optional[Dict[str, Any]] = None,
    current_user: _ServerUser,
    request: Request,
    db: Session,
) -> List[str]:
    urls: List[str] = []
    photo_url = str(profile.get("photo_url") or "").strip()
    if photo_url.startswith(("http://", "https://")):
        urls.append(photo_url)
    aid = str(profile.get("photo_asset_id") or "").strip()
    if aid:
        public = get_asset_public_url(aid, current_user.id, request, db)
        if not public:
            raise HTTPException(status_code=400, detail=f"人物照片素材 {aid} 暂无可用于云端合成的公网地址")
        if public not in urls:
            urls.append(public)
    if not urls:
        raise HTTPException(status_code=400, detail="请先上传人物照片，或从素材库选择一张人物照片")
    card = card or {}
    scene_url = str(card.get("scene_url") or "").strip()
    if scene_url.startswith(("http://", "https://")) and scene_url not in urls:
        urls.append(scene_url)
    scene_aid = str(card.get("scene_asset_id") or "").strip()
    if scene_aid:
        public = get_asset_public_url(scene_aid, current_user.id, request, db)
        if not public:
            raise HTTPException(status_code=400, detail=f"场景底图素材 {scene_aid} 暂无可用于云端合成的公网地址")
        if public not in urls:
            urls.append(public)
    return urls[:2]


@router.get("/api/local-bestseller/templates")
async def local_bestseller_templates(_: _ServerUser = Depends(get_current_user_for_local)):
    rows = _load_templates()
    return {"ok": True, "items": rows[:30], "total": min(len(rows), 30)}


@router.post("/api/local-bestseller/plan")
async def local_bestseller_plan(
    body: LocalBestsellerPlanBody,
    _: _ServerUser = Depends(get_current_user_for_local),
):
    profile = _clean_profile(body.profile)
    rows = _load_templates()[: int(body.days or 30)]
    cards = [_build_card(row, profile) for row in rows]
    return {
        "ok": True,
        "mode": "draft_plan",
        "days": len(cards),
        "profile": profile,
        "items": cards,
        "render_hint": f"当前接口先生成{len(cards)}天批量渲染方案；真实图片/Grok视频生成可按每张卡的 image_prompt/video_prompt 继续接入。",
    }


@router.post("/api/local-bestseller/render")
async def local_bestseller_render(
    body: LocalBestsellerPlanBody,
    _: _ServerUser = Depends(get_current_user_for_local),
):
    profile = _clean_profile(body.profile)
    rows = _load_templates()[: int(body.days or 10)]
    cards = [_build_card(row, profile) for row in rows]
    for card in cards:
        card["status"] = "queued"
        card["progress"] = 0
        card["scene_status"] = "queued"
        card["render_note"] = "已进入批量场景图片合成队列；合成完成后可继续用 Grok 生成10秒视频。"
    return {
        "ok": True,
        "job_id": "local-bestseller-preview",
        "status": "queued",
        "items": cards,
    }


@router.post("/api/local-bestseller/scene/generate")
async def local_bestseller_scene_generate(
    body: LocalBestsellerSceneBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    profile = _clean_profile(body.profile)
    rows = _load_templates()[: int(body.days or 10)]
    cards = [_build_card(row, profile) for row in rows]
    cards = _merge_card_overrides(cards, [body.item] if body.item else body.items)
    card = _pick_card(cards, body.day)
    if body.item and int(body.item.day) == int(body.day or 0):
        card = _merge_card_override(card, body.item)
    refs = _resolve_reference_urls(profile=profile, card=card, current_user=current_user, request=request, db=db)
    result = await _generate_image_studio_core(
        request=request,
        current_user=current_user,
        db=db,
        prompt=_scene_generation_prompt(card),
        model=(body.model or "gpt-image-2").strip() or "gpt-image-2",
        aspect_ratio="9:16",
        quality=(body.quality or "high").strip() or "high",
        background="auto",
        upload_payloads=[],
        reference_image_urls=refs,
        auto_save=True,
    )
    return {"ok": True, "item": _card_with_scene_result(card, result), "raw": result}


@router.post("/api/local-bestseller/scene/batch")
async def local_bestseller_scene_batch(
    body: LocalBestsellerSceneBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    profile = _clean_profile(body.profile)
    rows = _load_templates()[: int(body.days or 10)]
    cards = [_build_card(row, profile) for row in rows]
    cards = _merge_card_overrides(cards, body.items)
    semaphore = asyncio.Semaphore(10)

    async def _generate_one(card: Dict[str, Any]) -> Dict[str, Any]:
        card_db = SessionLocal()
        try:
            async with semaphore:
                result = await _generate_image_studio_core(
                    request=request,
                    current_user=current_user,
                    db=card_db,
                    prompt=_scene_generation_prompt(card),
                    model=(body.model or "gpt-image-2").strip() or "gpt-image-2",
                    aspect_ratio="9:16",
                    quality=(body.quality or "high").strip() or "high",
                    background="auto",
                    upload_payloads=[],
                    reference_image_urls=_resolve_reference_urls(profile=profile, card=card, current_user=current_user, request=request, db=db),
                    auto_save=True,
                )
                return _card_with_scene_result(card, result)
        except Exception as exc:
            failed = dict(card)
            failed["status"] = "scene_failed"
            failed["scene_status"] = "failed"
            failed["error"] = str(getattr(exc, "detail", exc))[:500]
            return failed
        finally:
            card_db.close()

    out = await asyncio.gather(*[_generate_one(card) for card in cards])
    return {"ok": True, "status": "completed", "items": out}


@router.post("/api/local-bestseller/video/generate")
async def local_bestseller_video_generate(
    body: LocalBestsellerVideoBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    profile = _clean_profile(body.profile)
    rows = _load_templates()[: int(body.days or 10)]
    cards = [_build_card(row, profile) for row in rows]
    cards = _merge_card_overrides(cards, [body.item] if body.item else body.items)
    card = _pick_card(cards, body.day)
    result = await _submit_card_video_via_seedance(
        card=card,
        video_model=body.video_model,
        request=request,
        current_user=current_user,
        db=db,
    )
    return {"ok": True, "item": _card_with_video_result(card, result), "raw": result}


@router.post("/api/local-bestseller/video/batch")
async def local_bestseller_video_batch(
    body: LocalBestsellerVideoBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    profile = _clean_profile(body.profile)
    rows = _load_templates()[: int(body.days or 10)]
    cards = [_build_card(row, profile) for row in rows]
    cards = _merge_card_overrides(cards, body.items)
    semaphore = asyncio.Semaphore(10)

    async def _submit_one(card: Dict[str, Any]) -> Dict[str, Any]:
        if not str(card.get("image_url") or card.get("image_asset_id") or card.get("scene_url") or card.get("scene_asset_id") or "").strip():
            skipped = dict(card)
            skipped["video_status"] = "ready"
            skipped["video_note"] = "未提交视频：请先上传/选择底图，或先合成场景图片"
            return skipped
        card_db = SessionLocal()
        try:
            async with semaphore:
                result = await _submit_card_video_via_seedance(
                    card=card,
                    video_model=body.video_model,
                    request=request,
                    current_user=current_user,
                    db=card_db,
                )
                return _card_with_video_result(card, result)
        except Exception as exc:
            failed = dict(card)
            failed["status"] = "video_failed"
            failed["video_status"] = "failed"
            failed["error"] = str(getattr(exc, "detail", exc))[:500]
            return failed
        finally:
            card_db.close()

    out = await asyncio.gather(*[_submit_one(card) for card in cards])
    return {"ok": True, "status": "submitted", "items": out}
