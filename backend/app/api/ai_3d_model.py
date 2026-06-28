from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from ..db import SessionLocal
from .auth import _ServerUser
from .assets import _upload_bytes_to_auth_server
from .chat import (
    _extract_image_urls_from_generate_result,
    _extract_task_id_from_result,
    _is_task_result_in_progress,
    local_mcp_url,
)
from .comfly_image_studio import _generate_image_studio_core
from ..services import ai_3d_model_store as store
from ..services import glb_assembly_service as glb_assembly
from ..services import meshy_3d_service as meshy
from ..services import model_3mf_service as model_3mf
from ..services import see_through_layer_service as see_through

logger = logging.getLogger(__name__)
router = APIRouter()


def _ai3d_local_user() -> _ServerUser:
    return _ServerUser(id=1)


_MAX_UPLOAD_BYTES = 120 * 1024 * 1024
_MAX_ZIP_BYTES = 240 * 1024 * 1024
_MAX_INPUT_IMAGES = 24
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_FILE_MEDIA_TYPES = {
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".obj": "model/obj",
    ".fbx": "application/octet-stream",
    ".usdz": "model/vnd.usdz+zip",
    ".stl": "model/stl",
    ".3mf": "model/3mf",
    ".mtl": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_RESAMPLING = getattr(Image, "Resampling", Image)
_LANCZOS = getattr(_RESAMPLING, "LANCZOS", getattr(Image, "LANCZOS", 1))
_BILINEAR = getattr(_RESAMPLING, "BILINEAR", getattr(Image, "BILINEAR", 2))
_SUTUI_GPT_IMAGE_2_MODEL = "openai/gpt-image-2"
_SUTUI_IMAGE_UNDERSTAND_MODEL = "openai/gpt-5.5"
_VIEW_PROMPT_VERSION = "hd-multiview-v18-hard-surface-3plus2"
_AI3D_DEFAULT_MAX_PARTS = 24
_AI3D_ABSOLUTE_MAX_PARTS = 24
_AI3D_IMAGE_STAGE_QUALITY = "high"
_AI3D_IMAGE_STAGE_RESOLUTION = "4K"
_AI3D_IMAGE_STAGE_OUTPUT_FORMAT = "png"
_AI3D_IMAGE_STAGE_PIXEL_SIZES = {
    "1:1": "2048x2048",
    "4:3": "2880x2160",
    "3:4": "2160x2880",
    "3:2": "3072x2048",
    "2:3": "2048x3072",
    "16:9": "3840x2160",
    "9:16": "2160x3840",
}
_STANDARD_MULTI_VIEW_SHEET_VIEWS = ["front", "front_left_45", "front_right_45", "side", "back"]
_CHARACTER_MULTI_VIEW_SHEET_VIEWS = ["front", "front_left_45", "front_right_45", "side", "back"]
_MESHY_MULTI_IMAGE_MAX = 4
_MESHY_BASE_VIEW_ROLES = ["front", "front_right_45", "front_left_45", "side", "back"]
_VIEW_ROLE_LABELS = {
    "front": "正视图",
    "front_left_45": "左前45°视图",
    "front_right_45": "右前45°视图",
    "side": "侧视图",
    "back": "背视图",
}
_VIEW_ROLE_ORDER = {role: idx for idx, role in enumerate(_CHARACTER_MULTI_VIEW_SHEET_VIEWS)}

_CHARACTER_PART_PRESETS = [
    {"role": "full_body", "label": "全身主体", "box": (0.05, 0.00, 0.95, 1.00)},
    {"role": "head_face_headwear", "label": "头部与头饰", "box": (0.25, 0.00, 0.75, 0.30)},
    {"role": "neck_shoulders_upper", "label": "颈肩上身", "box": (0.08, 0.15, 0.92, 0.48)},
    {"role": "torso_waist_core", "label": "躯干与腰部", "box": (0.14, 0.31, 0.86, 0.68)},
    {"role": "left_arm_hand", "label": "左臂与左手", "box": (0.00, 0.24, 0.56, 0.78)},
    {"role": "right_arm_hand", "label": "右臂与右手", "box": (0.44, 0.24, 1.00, 0.78)},
    {"role": "lower_body_hips", "label": "髋部与下身", "box": (0.16, 0.52, 0.84, 0.86)},
    {"role": "legs_feet", "label": "腿部与脚部", "box": (0.18, 0.72, 0.82, 1.00)},
    {"role": "face_screen_detail", "label": "面部/屏幕细节", "box": (0.32, 0.06, 0.68, 0.24)},
    {"role": "headwear_detail", "label": "头饰细节", "box": (0.24, 0.00, 0.76, 0.18)},
]
_GENERIC_REGION_PRESETS = [
    {"role": "primary_subject", "label": "完整主体", "box": (0.00, 0.00, 1.00, 1.00)},
    {"role": "center_subject", "label": "中心主体", "box": (0.12, 0.08, 0.88, 0.92)},
    {"role": "upper_subject", "label": "上方主体", "box": (0.06, 0.00, 0.94, 0.58)},
    {"role": "lower_subject", "label": "下方主体", "box": (0.05, 0.42, 0.95, 1.00)},
    {"role": "left_subject", "label": "左侧主体", "box": (0.00, 0.08, 0.58, 0.92)},
    {"role": "right_subject", "label": "右侧主体", "box": (0.42, 0.08, 1.00, 0.92)},
    {"role": "wide_base_subject", "label": "底部横向主体", "box": (0.00, 0.55, 1.00, 1.00)},
]
_REGION_CANDIDATE_ROLES = {str(item["role"]) for item in _CHARACTER_PART_PRESETS + _GENERIC_REGION_PRESETS}
_CROP_REFERENCE_MODES = {"fidelity_crop", "crop_reference_only"}
_TRUE_COMPONENT_SOURCE_MODES = {
    "user_part_package",
    "semantic_segmentation",
    "manual_component_package",
    "fidelity_source_crops",
    "semantic_image_component_sheet",
    "semantic_image_component_parts",
    "see_through_psd_layers",
}
_REMBG_SESSION = None

_CHARACTER_AI_PARTS = [
    ("head_face_headwear", "头部/面部/头饰"),
    ("neck_shoulders_upper", "颈肩/围巾/上身"),
    ("torso_waist_core", "躯干/胸腹/腰部"),
    ("belt_hip_accessory", "腰带/髋部配件"),
    ("left_arm_hand", "左臂/左手"),
    ("right_arm_hand", "右臂/右手"),
    ("lower_body_legs", "下身/腿部"),
    ("feet_or_attached_prop", "脚部/靴子/附属道具"),
]

_VIEW_TEMPLATE_COPY = {
    "auto": "自动识别资产设定，优先保持参考图主体类型、结构、材质、配色和美术风格一致",
    "character_realistic": "3D写实角色设定，保持人物五官、服饰层次、材质纹理和气质一致",
    "character_stylized": "风格化角色设定，保持轮廓、配色、发型、服饰结构一致",
    "hard_surface": "硬表面道具设定，保持机械结构、镂空、接缝和材质一致",
    "ornament_prop": "装饰道具设定，保持纹样、金属/玉石材质、厚度和边缘结构一致",
}

_PART_TEMPLATE_COPY = {
    "auto": "自动识别资产拆件，保持参考图主体结构、材质、配色和美术风格一致",
    "character_realistic": "3D写实角色资产拆件，保持人物五官、服装材质、颜色、纹样和比例一致",
    "character_stylized": "风格化角色资产拆件，保持轮廓、配色、发型和服饰结构一致",
    "hard_surface": "硬表面资产拆件，保持机械结构、镂空、接缝、厚度和材质一致",
    "ornament_prop": "复杂装饰道具拆件，保持纹样、金属/玉石材质、镂空结构、厚度和边缘一致",
}


def _is_character_template(template: str) -> bool:
    return (template or "").strip().lower() in {"character_realistic", "character_stylized"}


def _canonical_asset_template(template: str) -> str:
    value = (template or "auto").strip().lower()
    if value in {"auto", "character_realistic", "character_stylized", "hard_surface", "ornament_prop"}:
        return value
    return "auto"


def _template_from_understanding(view_understanding: Optional[Dict[str, Any]], fallback: str = "auto") -> str:
    template = _canonical_asset_template(fallback)
    if not isinstance(view_understanding, dict) or not view_understanding:
        return template
    asset_type = str(view_understanding.get("asset_type") or "").strip().lower()
    subject_text = " ".join(
        str(view_understanding.get(key) or "")
        for key in ("subject", "visual_summary", "body_structure", "head_face", "triview_prompt")
    ).lower()
    if asset_type in {"hard_surface_prop", "hard_surface", "vehicle", "machine"}:
        return "hard_surface"
    if asset_type in {"ornament", "ornament_prop", "accessory", "jewelry"}:
        return "ornament_prop"
    if asset_type in {"human_character", "humanoid_robot", "creature"}:
        return template if _is_character_template(template) else "character_realistic"
    if any(word in subject_text for word in ("building", "architecture", "architectural", "house", "tower", "shanty", "structure")):
        return "hard_surface"
    if any(word in subject_text for word in ("helmet", "weapon", "sword", "shield", "prop", "mechanical", "machine", "vehicle")):
        return "hard_surface"
    return template


def _sheet_views_for_template(template: str) -> List[str]:
    return list(_CHARACTER_MULTI_VIEW_SHEET_VIEWS if _is_character_template(template) else _STANDARD_MULTI_VIEW_SHEET_VIEWS)


def _valid_sheet_views(value: Any, *, fallback: Optional[List[str]] = None) -> List[str]:
    fallback_views = list(fallback or _STANDARD_MULTI_VIEW_SHEET_VIEWS)
    if not isinstance(value, list):
        return fallback_views
    roles: List[str] = []
    for item in value:
        role = str(item or "").strip()
        if role in _VIEW_ROLE_LABELS and role not in roles:
            roles.append(role)
    return roles if len(roles) >= 2 else fallback_views


def _sheet_view_names(roles: List[str]) -> str:
    return "、".join(_VIEW_ROLE_LABELS.get(role, role) for role in roles)


def _subject_lock_copy(template: str) -> str:
    if _is_character_template(template):
        return (
            "主体锁定规则：必须以参考图中的同一个角色为唯一主体，严格保持参考图可见的性别表达、年龄段、"
            "脸型五官、肤色、体型比例、发型/头部装饰、胡须或面部特征、服装/外壳层次、材质配色和身份气质；"
            "参考图呈现为男性时必须保持男性，呈现为女性时必须保持女性；"
            "禁止替换成新角色，禁止把男性生成成女性或把女性生成成男性，禁止生成通用美女、偶像脸、年轻化脸、"
            "不同身材或不同服装；不可见角度只能基于同一角色和同一套服装合理补全。"
        )
    return (
        "主体锁定规则：必须以参考图中的同一个资产为唯一主体，严格保持轮廓、结构、孔洞、厚度、材质、颜色、"
        "纹样、磨损和比例；禁止生成相似但不同的新设计，不得替换主体。"
    )


def _safe_name(name: str, fallback: str = "asset") -> str:
    raw = (name or "").strip().replace("\\", "/").split("/")[-1]
    stem = Path(raw).stem if raw else fallback
    suffix = Path(raw).suffix.lower()
    safe_stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in stem).strip("-_")[:80]
    if not safe_stem:
        safe_stem = fallback
    return f"{safe_stem}{suffix}"


def _is_zip_name(name: str) -> bool:
    return Path(name or "").suffix.lower() == ".zip"


def _is_image_name(name: str) -> bool:
    return Path(name or "").suffix.lower() in _IMAGE_SUFFIXES


def _content_mask_bbox(im: Image.Image) -> Tuple[Tuple[int, int, int, int], Dict[str, Any]]:
    """Find the largest non-black/non-transparent visual region on a downscaled image."""
    width, height = im.size
    if width < 8 or height < 8:
        return (0, 0, width, height), {"method": "full_image", "component_area": width * height}

    max_side = 420
    scale = min(1.0, max_side / float(max(width, height)))
    small_w = max(1, int(round(width * scale)))
    small_h = max(1, int(round(height * scale)))
    small = im.convert("RGB").resize((small_w, small_h), _BILINEAR)
    px = small.load()
    mask = bytearray(small_w * small_h)

    for y in range(small_h):
        row = y * small_w
        for x in range(small_w):
            r, g, b = px[x, y]
            bright = max(r, g, b)
            chroma = max(r, g, b) - min(r, g, b)
            if bright > 26 or (bright > 18 and chroma > 8):
                mask[row + x] = 1

    visited = bytearray(small_w * small_h)
    best_area = 0
    best_box = (0, 0, small_w, small_h)
    min_area = max(12, int(small_w * small_h * 0.0015))

    for start in range(small_w * small_h):
        if visited[start] or not mask[start]:
            continue
        stack = [start]
        visited[start] = 1
        area = 0
        min_x = small_w
        min_y = small_h
        max_x = 0
        max_y = 0
        while stack:
            idx = stack.pop()
            x = idx % small_w
            y = idx // small_w
            area += 1
            if x < min_x:
                min_x = x
            if y < min_y:
                min_y = y
            if x > max_x:
                max_x = x
            if y > max_y:
                max_y = y
            for nb in (idx - 1, idx + 1, idx - small_w, idx + small_w):
                if nb < 0 or nb >= small_w * small_h:
                    continue
                if nb == idx - 1 and x == 0:
                    continue
                if nb == idx + 1 and x == small_w - 1:
                    continue
                if visited[nb] or not mask[nb]:
                    continue
                visited[nb] = 1
                stack.append(nb)
        if area >= min_area and area > best_area:
            best_area = area
            best_box = (min_x, min_y, max_x + 1, max_y + 1)

    if best_area <= 0:
        return (0, 0, width, height), {"method": "fallback_full_image", "component_area": 0}

    inv_scale = 1.0 / scale
    left = int(best_box[0] * inv_scale)
    top = int(best_box[1] * inv_scale)
    right = int(best_box[2] * inv_scale)
    bottom = int(best_box[3] * inv_scale)
    pad_x = max(4, int((right - left) * 0.035))
    pad_y = max(4, int((bottom - top) * 0.035))
    crop = (
        max(0, left - pad_x),
        max(0, top - pad_y),
        min(width, right + pad_x),
        min(height, bottom + pad_y),
    )
    return crop, {
        "method": "largest_content_component",
        "component_area": best_area,
        "detector_size": [small_w, small_h],
        "scale": scale,
    }


def _primary_panel_crop_bbox(im: Image.Image) -> Tuple[Tuple[int, int, int, int], Dict[str, Any]]:
    """Prefer the main subject panel when a concept sheet includes right-side detail insets."""
    width, height = im.size
    if width < 800 or height < 600 or width / max(1, height) < 1.05:
        return (0, 0, width, height), {"method": "full_image_no_panel_detect"}

    max_side = 520
    scale = min(1.0, max_side / float(max(width, height)))
    small_w = max(1, int(round(width * scale)))
    small_h = max(1, int(round(height * scale)))
    small = im.convert("RGB").resize((small_w, small_h), _BILINEAR)
    px = small.load()

    densities: List[float] = []
    for x in range(small_w):
        count = 0
        for y in range(small_h):
            r, g, b = px[x, y]
            bright = max(r, g, b)
            chroma = max(r, g, b) - min(r, g, b)
            if bright < 238 or chroma > 18:
                count += 1
        densities.append(count / float(max(1, small_h)))

    start = int(small_w * 0.52)
    end = int(small_w * 0.82)
    threshold = 0.018
    best_run = (0, 0)
    run_start: Optional[int] = None
    for x in range(start, end):
        if densities[x] <= threshold:
            if run_start is None:
                run_start = x
        elif run_start is not None:
            if x - run_start > best_run[1] - best_run[0]:
                best_run = (run_start, x)
            run_start = None
    if run_start is not None and end - run_start > best_run[1] - best_run[0]:
        best_run = (run_start, end)

    min_run = max(4, int(small_w * 0.012))
    if best_run[1] - best_run[0] < min_run:
        return (0, 0, width, height), {
            "method": "full_image_no_vertical_panel_gap",
            "detector_size": [small_w, small_h],
        }

    split_small = int(round((best_run[0] + best_run[1]) / 2))
    split = max(1, min(width, int(round(split_small / scale))))
    left_region = im.crop((0, 0, split, height))
    content_box, content_meta = _content_mask_bbox(left_region)
    if (content_box[2] - content_box[0]) * (content_box[3] - content_box[1]) < int(width * height * 0.18):
        return (0, 0, width, height), {
            "method": "full_image_panel_candidate_too_small",
            "split_x": split,
            "content_meta": content_meta,
        }
    pad_x = max(12, int((content_box[2] - content_box[0]) * 0.04))
    pad_y = max(12, int((content_box[3] - content_box[1]) * 0.04))
    crop = (
        max(0, content_box[0] - pad_x),
        max(0, content_box[1] - pad_y),
        min(split, content_box[2] + pad_x),
        min(height, content_box[3] + pad_y),
    )
    return crop, {
        "method": "left_primary_panel_from_inset_sheet",
        "split_x": split,
        "gap_run": [int(best_run[0] / scale), int(best_run[1] / scale)],
        "content_meta": content_meta,
    }


def _save_primary_reference_crop(source: Path, dest: Path) -> Dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        original_size = [im.width, im.height]
        crop_box, crop_meta = _primary_panel_crop_bbox(im)
        cropped = im.crop(crop_box)
        cropped.save(dest, "JPEG", quality=94, optimize=True)
        return {
            "width": cropped.width,
            "height": cropped.height,
            "original_width": original_size[0],
            "original_height": original_size[1],
            "crop_box": list(crop_box),
            "crop_applied": [0, 0, original_size[0], original_size[1]] != list(crop_box),
            "crop_meta": crop_meta,
            "source_box": list(crop_box),
            "primary_reference_anchor": True,
        }


def _normalize_image(src: Path, dest: Path, *, auto_crop: bool = True) -> Dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        original_width, original_height = im.size
        if im.mode not in {"RGB", "L"}:
            bg = Image.new("RGB", im.size, (18, 18, 18))
            if "A" in im.getbands():
                bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").getchannel("A"))
                im = bg
            else:
                im = im.convert("RGB")
        elif im.mode == "L":
            im = im.convert("RGB")
        crop_box = (0, 0, im.width, im.height)
        crop_meta: Dict[str, Any] = {"method": "disabled"}
        if auto_crop:
            crop_box, crop_meta = _content_mask_bbox(im)
            crop_area = max(1, (crop_box[2] - crop_box[0]) * (crop_box[3] - crop_box[1]))
            full_area = max(1, im.width * im.height)
            if crop_area < int(full_area * 0.985):
                im = im.crop(crop_box)
        im.save(dest, "JPEG", quality=94, optimize=True)
        return {
            "width": im.width,
            "height": im.height,
            "original_width": original_width,
            "original_height": original_height,
            "crop_box": list(crop_box),
            "crop_applied": [0, 0, original_width, original_height] != list(crop_box),
            "crop_meta": crop_meta,
        }


def _relative_box_to_abs(width: int, height: int, box: Tuple[float, float, float, float]) -> Tuple[int, int, int, int]:
    left = int(round(width * box[0]))
    top = int(round(height * box[1]))
    right = int(round(width * box[2]))
    bottom = int(round(height * box[3]))
    crop_w = max(1, right - left)
    crop_h = max(1, bottom - top)
    pad = max(8, int(max(crop_w, crop_h) * 0.08))
    return (
        max(0, left - pad),
        max(0, top - pad),
        min(width, right + pad),
        min(height, bottom + pad),
    )


def _component_role_text(part: Dict[str, Any]) -> str:
    return " ".join(str(part.get(key) or "").lower() for key in ("role", "label", "reason"))


def _is_head_mesh_role(role_text: str) -> bool:
    return any(token in role_text for token in ("head", "face", "hat", "helmet", "crown", "headwear", "头", "脸", "帽", "盔"))


def _is_neck_or_torso_role(role_text: str) -> bool:
    return any(token in role_text for token in ("neck", "bandana", "scarf", "torso", "jacket", "shoulder", "body", "waist", "脖", "颈", "围巾", "躯干", "夹克", "肩", "身体", "腰"))


def _tighten_component_box_for_mesh(
    *,
    box: Tuple[int, int, int, int],
    source_size: Tuple[int, int],
    part: Dict[str, Any],
) -> Tuple[int, int, int, int]:
    left, top, right, bottom = box
    width, height = source_size
    role_text = _component_role_text(part)
    if _is_head_mesh_role(role_text) and not _is_neck_or_torso_role(role_text):
        crop_h = max(1, bottom - top)
        # Head-like replacement parts must not include shoulders/chest. Keep a tiny neck connector,
        # but clamp broad AI boxes before they reach Meshy and become bust models.
        bottom = min(bottom, top + int(crop_h * 0.80), int(height * 0.20))
    if "face" in role_text or "screen" in role_text or "面" in role_text or "屏幕" in role_text:
        crop_h = max(1, bottom - top)
        bottom = min(bottom, top + int(crop_h * 0.68), int(height * 0.17))
    min_h = max(16, int(height * 0.035))
    if bottom - top < min_h:
        bottom = min(height, top + min_h)
    return (max(0, left), max(0, top), min(width, max(right, left + 16)), min(height, max(bottom, top + 16)))


def _save_square_crop(source: Image.Image, box: Tuple[int, int, int, int], dest: Path, *, size: int = 1024) -> Dict[str, Any]:
    crop = source.crop(box)
    side = max(crop.width, crop.height, 16)
    canvas = Image.new("RGB", (side, side), (34, 34, 32))
    canvas.paste(crop, ((side - crop.width) // 2, (side - crop.height) // 2))
    if side != size:
        canvas = canvas.resize((size, size), _LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.suffix.lower() == ".png":
        canvas.save(dest, "PNG", optimize=True)
    else:
        canvas.save(dest, "JPEG", quality=94, optimize=True)
    return {"width": canvas.width, "height": canvas.height, "source_box": list(box)}


def _decompose_character_image(src: Path, out_dir: Path, *, max_parts: int = _AI3D_DEFAULT_MAX_PARTS) -> List[Dict[str, Any]]:
    max_parts = max(1, min(_AI3D_ABSOLUTE_MAX_PARTS, int(max_parts or _AI3D_DEFAULT_MAX_PARTS)))
    parts: List[Dict[str, Any]] = []
    with Image.open(src) as im:
        source = ImageOps.exif_transpose(im).convert("RGB")
        for idx, preset in enumerate(_CHARACTER_PART_PRESETS[:max_parts], start=1):
            role = str(preset["role"])
            dest = out_dir / f"{idx:02d}_{role}.jpg"
            box = _relative_box_to_abs(source.width, source.height, preset["box"])
            meta = _save_square_crop(source, box, dest)
            parts.append({
                "index": idx,
                "role": role,
                "label": preset["label"],
                "path": dest,
                "width": meta["width"],
                "height": meta["height"],
                "source_box": meta["source_box"],
            })
    return parts


def _decompose_generic_subject_image(src: Path, out_dir: Path, *, max_parts: int = _AI3D_DEFAULT_MAX_PARTS) -> List[Dict[str, Any]]:
    max_parts = max(1, min(_AI3D_ABSOLUTE_MAX_PARTS, int(max_parts or _AI3D_DEFAULT_MAX_PARTS)))
    parts: List[Dict[str, Any]] = []
    seen_boxes: set[Tuple[int, int, int, int]] = set()
    with Image.open(src) as im:
        source = ImageOps.exif_transpose(im).convert("RGB")
        for idx, preset in enumerate(_GENERIC_REGION_PRESETS[:max_parts], start=1):
            role = str(preset["role"])
            dest = out_dir / f"{idx:02d}_{role}.jpg"
            box = _relative_box_to_abs(source.width, source.height, preset["box"])
            key = tuple(int(v) for v in box)
            if key in seen_boxes:
                continue
            seen_boxes.add(key)
            meta = _save_square_crop(source, box, dest)
            parts.append({
                "index": len(parts) + 1,
                "role": role,
                "label": preset["label"],
                "path": dest,
                "width": meta["width"],
                "height": meta["height"],
                "source_box": meta["source_box"],
            })
    return parts


def _generated_region_inputs_from_image(
    *,
    job_id: str,
    source_input: Dict[str, Any],
    src: Path,
    out_dir: Path,
    max_parts: int,
    source_label: str,
) -> List[Dict[str, Any]]:
    parts = _decompose_character_image(src, out_dir, max_parts=max_parts)
    generated: List[Dict[str, Any]] = []
    for part in parts:
        part_path = Path(part["path"])
        generated.append(_public_input(
            job_id=job_id,
            index=len(generated) + 1,
            filename=part_path.name,
            normalized_path=part_path,
            meta={
                "width": part["width"],
                "height": part["height"],
                "source_box": part.get("source_box"),
                "crop_applied": False,
            },
            role=str(part.get("role") or "part"),
            label=f"{source_label}{part.get('label') or part_path.stem}",
            source_filename=str(source_input.get("filename") or src.name),
            generated=True,
        ))
    return generated


def _copy_selected_candidate_as_source(
    *,
    job_id: str,
    candidate: Dict[str, Any],
    source_input: Dict[str, Any],
    index: int,
) -> Dict[str, Any]:
    src = Path(str(candidate.get("normalized_path") or ""))
    if not src.exists():
        raise RuntimeError("候选图文件不存在")
    dest = store.job_dir(job_id) / "normalized" / "selected_primary_reference.jpg"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        image = ImageOps.exif_transpose(im).convert("RGB")
        image.save(dest, "JPEG", quality=94, optimize=True)
        meta = {
            "width": image.width,
            "height": image.height,
            "original_width": source_input.get("original_width") or source_input.get("width"),
            "original_height": source_input.get("original_height") or source_input.get("height"),
            "crop_box": candidate.get("crop_box"),
            "crop_applied": True,
            "source_box": candidate.get("source_box") or candidate.get("crop_box"),
        }
    selected = _public_input(
        job_id=job_id,
        index=1,
        filename=dest.name,
        normalized_path=dest,
        meta=meta,
        role="source",
        label=f"已选主体：{candidate.get('label') or candidate.get('role') or index}",
        source_filename=str(source_input.get("source_filename") or source_input.get("filename") or candidate.get("filename") or ""),
        generated=False,
    )
    selected["selected_from_candidate_index"] = index
    selected["selected_from_candidate_role"] = str(candidate.get("role") or "")
    selected["selected_from_candidate_label"] = str(candidate.get("label") or "")
    _copy_extra_input_fields(
        selected,
        candidate,
        (
            "ai_recommended",
            "subject_candidate_index",
            "subject_type",
            "suitability_score",
            "subject_reason",
            "subject_risk",
            "must_keep",
            "forbidden_changes",
            "triview_prompt",
        ),
    )
    return selected


def _region_inputs_from_part_plan(
    *,
    job_id: str,
    source_input: Dict[str, Any],
    src: Path,
    out_dir: Path,
    part_plan: List[Dict[str, Any]],
    source_label: str,
) -> List[Dict[str, Any]]:
    generated: List[Dict[str, Any]] = []
    with Image.open(src) as im:
        source = ImageOps.exif_transpose(im).convert("RGB")
        for idx, part in enumerate(part_plan, start=1):
            box = part.get("box")
            if not isinstance(box, tuple) and not isinstance(box, list):
                continue
            try:
                rel_box = tuple(float(v) for v in list(box)[:4])
            except Exception:
                continue
            role = str(part.get("role") or f"part_{idx:02d}")
            dest = out_dir / f"{idx:02d}_{role}.jpg"
            abs_box = _relative_box_to_abs(source.width, source.height, rel_box)  # type: ignore[arg-type]
            meta = _save_square_crop(source, abs_box, dest)
            generated.append(_public_input(
                job_id=job_id,
                index=len(generated) + 1,
                filename=dest.name,
                normalized_path=dest,
                meta={
                    "width": meta["width"],
                    "height": meta["height"],
                    "source_box": meta.get("source_box"),
                    "crop_applied": False,
                },
                role=role,
                label=f"{source_label}{part.get('label') or role}",
                source_filename=str(source_input.get("filename") or src.name),
                generated=True,
            ))
            generated[-1]["part_reason"] = str(part.get("reason") or "")
    return generated


def _region_inputs_from_subject_plan(
    *,
    job_id: str,
    source_input: Dict[str, Any],
    src: Path,
    out_dir: Path,
    subject_plan: Dict[str, Any],
) -> List[Dict[str, Any]]:
    candidates = subject_plan.get("candidates") if isinstance(subject_plan.get("candidates"), list) else []
    generated: List[Dict[str, Any]] = []
    with Image.open(src) as im:
        source = ImageOps.exif_transpose(im).convert("RGB")
        for idx, candidate in enumerate(candidates, start=1):
            if not isinstance(candidate, dict):
                continue
            subject_candidate_index = int(candidate.get("index") or idx)
            box = candidate.get("box")
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                continue
            try:
                rel_box = tuple(float(v) for v in list(box)[:4])
            except Exception:
                continue
            role_raw = str(candidate.get("role") or f"subject_{idx:02d}").strip().lower()
            role = re.sub(r"[^a-z0-9_\-]+", "_", role_raw).strip("_-")[:48] or f"subject_{idx:02d}"
            dest = out_dir / f"{idx:02d}_{role}.jpg"
            abs_box = _relative_box_to_abs(source.width, source.height, rel_box)  # type: ignore[arg-type]
            meta = _save_square_crop(source, abs_box, dest)
            item = _public_input(
                job_id=job_id,
                index=len(generated) + 1,
                filename=dest.name,
                normalized_path=dest,
                meta={
                    "width": meta["width"],
                    "height": meta["height"],
                    "source_box": meta.get("source_box"),
                    "crop_applied": False,
                },
                role=role,
                label=str(candidate.get("label") or role),
                source_filename=str(source_input.get("filename") or src.name),
                generated=True,
            )
            item["subject_candidate_index"] = subject_candidate_index
            item["ai_recommended"] = int(subject_plan.get("recommended_index") or 0) == subject_candidate_index or bool(candidate.get("recommended"))
            item["subject_type"] = str(candidate.get("subject_type") or "")
            item["suitability_score"] = int(candidate.get("suitability_score") or 0)
            item["subject_reason"] = str(candidate.get("reason") or "")
            item["subject_risk"] = str(candidate.get("risk") or "")
            item["must_keep"] = candidate.get("must_keep") if isinstance(candidate.get("must_keep"), list) else []
            item["forbidden_changes"] = candidate.get("forbidden_changes") if isinstance(candidate.get("forbidden_changes"), list) else []
            item["triview_prompt"] = str(candidate.get("triview_prompt") or "")
            generated.append(item)
    return generated


def _best_component_region_inputs(job_id: str, preprocessing: Dict[str, Any], root: Path) -> List[Dict[str, Any]]:
    triview_inputs = preprocessing.get("triview_inputs") if isinstance(preprocessing.get("triview_inputs"), list) else []
    front = next((item for item in triview_inputs if isinstance(item, dict) and str(item.get("role") or "") == "front"), None)
    source_inputs = preprocessing.get("source_inputs") if isinstance(preprocessing.get("source_inputs"), list) else []
    source = front if isinstance(front, dict) else (source_inputs[0] if source_inputs and isinstance(source_inputs[0], dict) else None)
    ai_plan = preprocessing.get("component_ai_plan") if isinstance(preprocessing.get("component_ai_plan"), dict) else {}
    ai_parts = ai_plan.get("parts") if isinstance(ai_plan.get("parts"), list) else []
    if not ai_parts or not isinstance(source, dict):
        return []
    source_path = Path(str(source.get("normalized_path") or ""))
    if not source_path.exists():
        return []
    out_dir = root / "components" / "ai_planned_parts"
    return _region_inputs_from_part_plan(
        job_id=job_id,
        source_input=source,
        src=source_path,
        out_dir=out_dir,
        part_plan=ai_parts,
        source_label="AI拆件：",
    )


def _make_identity_reference_board(job_id: str, preprocessing: Dict[str, Any], dest: Path) -> Optional[Dict[str, Any]]:
    source_inputs = preprocessing.get("source_inputs") if isinstance(preprocessing.get("source_inputs"), list) else []
    region_inputs = preprocessing.get("region_candidate_inputs") if isinstance(preprocessing.get("region_candidate_inputs"), list) else []
    by_role = {str(item.get("role") or ""): item for item in region_inputs if isinstance(item, dict)}
    chosen: List[Tuple[str, Path]] = []
    for label, item in [
        ("全身原图主体", source_inputs[0] if source_inputs else None),
        ("头部/面部/头饰", by_role.get("head_face_headwear")),
        ("颈肩与上身", by_role.get("neck_shoulders_upper")),
        ("躯干与腰部", by_role.get("torso_waist_core")),
        ("手臂与手部", by_role.get("left_arm_hand") or by_role.get("right_arm_hand")),
        ("下身与腿部", by_role.get("lower_body_hips")),
        ("脚部/靴子/附属道具", by_role.get("legs_feet")),
    ]:
        if not isinstance(item, dict):
            continue
        path = Path(str(item.get("normalized_path") or ""))
        if path.exists():
            chosen.append((label, path))
    if not chosen:
        return None
    cols = 3
    cell = 640
    rows = (len(chosen) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * cell, rows * cell), (235, 235, 230))
    for idx, (_, path) in enumerate(chosen):
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((cell - 32, cell - 32), _LANCZOS)
            col = idx % cols
            row = idx // cols
            x = col * cell + (cell - im.width) // 2
            y = row * cell + (cell - im.height) // 2
            canvas.paste(im, (x, y))
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, "JPEG", quality=94, optimize=True)
    return {"width": canvas.width, "height": canvas.height, "reference_count": len(chosen)}


def _view_generation_plan(*, asset_template: str, reference_strength: str, description: str = "") -> Dict[str, Any]:
    template = (asset_template or "auto").strip().lower()
    template = _canonical_asset_template(template)
    base = _VIEW_TEMPLATE_COPY.get(template, _VIEW_TEMPLATE_COPY["auto"])
    lock_copy = _subject_lock_copy(template)
    strength = (reference_strength or "high").strip().lower()
    strength_copy = {
        "strict": "参考图强度极高，除不可见角度补全外，不允许改性别、年龄、脸型、体型、服饰、配色、材质或身份设定",
        "high": "参考图强度高，保持原画主体设定，只补全不可见角度，不改性别、年龄、脸型、体型和服装",
        "balanced": "参考图强度中等，允许为多视角补合理结构，但必须保持同一主体身份和可见性别表达",
        "creative": "参考图强度较低，允许更强的设计整理，但仍必须保持同一主体，不得改性别或换角色",
    }.get(strength, "参考图强度高，保持原画主体设定，只补全不可见角度，不改性别、年龄、脸型、体型和服装")
    desc = (description or "").strip()
    prompt_seed = f"{base}。{strength_copy}。{lock_copy}"
    if desc:
        prompt_seed += f"角色/资产描述：{desc[:1200]}。"
    sheet_views = _sheet_views_for_template(template)
    if _is_character_template(template):
        prompt_seed += (
            "多视角一致性规则：正面、左前45°、右前45°、侧面、背面必须是同一角色/同一机器人/同一资产、同一身高比例、"
            "同一头部/脸部/屏幕、同一服装或外壳结构、同一材质、同一配色、同一磨损和同一配件；只改变观察角度，不改变设计。"
            "参考图中可见的帽子、头部、脸/屏幕、衣服/外壳、腰带、手套、靴子、武器或道具必须逐项保留；"
            "参考图没有的大型配件、花纹、颜色、盔甲、建筑结构或新风格不得新增。"
            "角色必须使用 neutral A-pose：双脚平行落地，双臂自然略离躯干，手掌完整可见，手部不要贴住腰带、衣摆、腿部或身体，"
            "便于绑定和 3D 重建。白底或中性灰底，正交视角，无遮挡，全身完整，避免文字、水印、UI。"
        )
        sheet_label = "高清角色五视角板"
        stage_note = "角色五视角由图片模型生成，确认 3D 后才调用 Meshy。"
        sheet_prompt = (
            prompt_seed
            + " 参考图是唯一身份来源；先理解参考图里的主体类型、轮廓、结构、材质、配色、服装/配件/道具，再补齐不可见角度。"
            "生成完整五视角角色板：从左到右依次为正视图、左前45°视图、右前45°视图、标准侧视图、背视图。"
            "这是保真视角补全，不是重新设计概念图；不得把主体改成其他时代、物种、职业、服装体系、机械结构、建筑结构或风格。"
            "不得添加参考图不存在的关键配件，不得删除参考图里的关键配件，不得改变主色、材质、磨损、脸部/屏幕表情或轮廓。"
            "五栏主体等高、居中、完整全身、正交视角；同一比例、同一结构、同一材质、同一配色，只改变观察角度。"
            "A-pose 要稳定一致，手臂与身体保持清晰缝隙，左右手完整、对称、不要残缺或融合。"
            "中性浅灰或白色背景，五栏之间留清晰空白，避免文字、水印、UI。"
        )
    else:
        prompt_seed += (
            "五视角推断一致性规则：参考图主视角、左前45°、右前45°、侧面、背面必须是同一资产、同一比例、同一结构、"
            "同一材质和配色；侧面和背面允许基于主图合理推断，但只能延续主图已经可见的屋顶层级、墙体、管线、机械件、材质和美术风格。"
            "必须保持参考图的美术风格和渲染方式：参考图是风格化概念图就保持风格化概念图，参考图是写实照片才保持写实；"
            "禁止把风格化资产写实化、摄影化、翻新或重做材质。"
            "白底或中性灰底，正交视角，无遮挡，物体完整，避免文字、水印、UI。"
        )
        sheet_label = "高清资产五视角板"
        stage_note = "资产五视角由图片模型基于主图推断生成；确认 3D 后才调用 Meshy。"
        sheet_prompt = (
            prompt_seed
            + " 参考图是唯一身份来源；先理解参考图里的主体类型、轮廓、结构、材质、配色、配件/道具，再做完整转台视角补全。"
            "生成资产五视角板：从左到右依次为参考图主视角、左前45°视图、右前45°视图、标准侧视图、背视图。"
            "这是保真视角补全，不是重新设计概念图；不得把主体改成其他物种、职业、建筑类型、机械结构或风格；"
            "不得添加参考图不存在的大型配件、底座、岩石、管线、花纹或材质，不得删除图片理解结果中识别出的可见标志性结构和小细节。"
            "五栏主体等高、居中、完整物体视图、正交或等距转台视角；同一比例、同一结构、同一材质、同一配色、同一美术风格，只改变观察角度。"
            "侧面和背面可以推断隐藏结构，但必须像主图这栋资产转过去看到的合理延续；不要编出另一栋宽矩形工厂、新锅炉门、巨型罐体、新底座或新立面。"
            "中性浅灰或白色背景，五栏之间留清晰空白，避免文字、水印、UI。"
        )
    views = [{
        "view": "triview_sheet",
        "label": sheet_label,
        "prompt": sheet_prompt,
        "sheet_views": sheet_views,
    }]
    for role in sheet_views:
        views.append({
            "view": role,
            "label": _VIEW_ROLE_LABELS.get(role, role),
            "prompt": prompt_seed + f" 由{sheet_label}裁切得到。",
        })
    return {
        "asset_template": template,
        "reference_strength": strength,
        "image_model": "",
        "image_resolution": _AI3D_IMAGE_STAGE_RESOLUTION,
        "image_quality": _AI3D_IMAGE_STAGE_QUALITY,
        "output_format": _AI3D_IMAGE_STAGE_OUTPUT_FORMAT,
        "prompt_version": _VIEW_PROMPT_VERSION,
        "stage_provider": "image_model",
        "uses_meshy": False,
        "stage_note": stage_note,
        "views": views,
    }


def _apply_generic_reference_triview_prompts(
    plan: Dict[str, Any],
    *,
    asset_template: str,
    reference_strength: str,
    description: str = "",
    view_understanding: Optional[Dict[str, Any]] = None,
) -> None:
    template = _canonical_asset_template(asset_template)
    if template == "auto":
        template = _canonical_asset_template(str(plan.get("asset_template") or "auto"))
    sheet_views = _sheet_views_for_template(template)
    is_character = _is_character_template(template)
    strength = (reference_strength or "high").strip().lower()
    strength_rule = {
        "strict": "Reference strength is strict: only infer hidden sides; do not redesign visible identity, silhouette, colors, materials, or outfit.",
        "high": "Reference strength is high: keep the source design, only complete the unseen views in a physically consistent way.",
        "balanced": "Reference strength is balanced: complete missing side/back structure while keeping the same asset identity.",
        "creative": "Reference strength is creative but must still keep the same primary subject and recognizable design.",
    }.get(strength, "Reference strength is high: keep the source design, only complete the unseen views in a physically consistent way.")
    asset_hint = {
        "character_realistic": "realistic humanoid character or humanoid mechanical asset",
        "character_stylized": "stylized character or humanoid asset",
        "hard_surface": "hard-surface mechanical asset",
        "ornament_prop": "ornament, prop, accessory, or hard-surface asset",
        "auto": "asset in the reference image",
    }.get(template, "asset in the reference image")
    desc = (description or "").strip()
    desc_rule = f" User description, if present, is secondary to the image: {desc[:1200]}." if desc else ""
    understood_rule = ""
    if isinstance(view_understanding, dict) and view_understanding:
        understood_parts = []
        for key in (
            "asset_type",
            "subject",
            "visual_summary",
            "body_structure",
            "head_face",
            "clothing_accessories",
            "mechanical_parts",
            "materials",
            "colors",
            "props",
            "must_keep",
            "forbidden_changes",
            "triview_prompt",
        ):
            value = _compact_text_value(view_understanding.get(key), max_chars=520 if key == "triview_prompt" else 260)
            if value:
                understood_parts.append(f"{key}: {value}")
        if understood_parts:
            understood_rule = " Image understanding result to obey: " + " | ".join(understood_parts[:12]) + ". "
    dynamic_detail_rule = ""
    if isinstance(view_understanding, dict) and view_understanding:
        detail_candidates = []
        for key in ("visual_summary", "body_structure", "mechanical_parts", "props", "must_keep", "triview_prompt"):
            value = _compact_text_value(view_understanding.get(key), max_chars=520 if key == "triview_prompt" else 320)
            if value:
                detail_candidates.append(f"{key}: {value}")
        if detail_candidates:
            dynamic_detail_rule = (
                "Preserve and consistently carry around only the distinctive small details that are visibly present or explicitly identified in the image understanding result: "
                + " | ".join(detail_candidates[:6])
                + ". Do not add detail categories that are not present in the reference. "
            )
    if is_character:
        detail_lock = (
            "Preserve the exact visible character or humanoid asset: species or robot type, head/face/screen, hat or headwear, hairstyle if any, "
            "clothing, armor, mechanical joints, body proportions, silhouette, leather/metal/plastic/fabric materials, colors, weathering, decals, belt, gloves, boots, props, and all distinctive details. "
            "If the subject is a robot, keep it a robot; do not turn it into a human. If the subject is human, keep the same human identity. "
            "Do not convert the subject into an unrelated historical, fantasy, sci-fi, fashion, beauty, mascot, or occupational archetype. "
            "Do not change gender, species, costume system, color scheme, or core design. "
        )
    else:
        detail_lock = (
            "Preserve the exact visible asset: overall silhouette, footprint, proportions, construction layers, roof or outer shell, panels, pipes, vents, tanks, antennae, rails, doors, windows, holes, seams, thickness, decals, props, base shape, materials, colors, weathering, and all distinctive details. "
            "Preserve the original art/rendering style exactly: if the reference is stylized concept art or an illustration, keep the stylized painterly concept-art look and do not make it photorealistic; if it is a photo, keep it photographic. "
            "Do not convert the asset into a different building type, vehicle, weapon, robot, castle, spaceship, fantasy item, or generic prop. "
            "Do not add large new bases, rocks, vegetation, ornaments, pipes, panels, structural tiers, or accessories that are absent from the reference. "
        )
    seed = (
        f"Create a high fidelity 4K orthographic multi-view reference sheet for the same {asset_hint} shown in the reference image. "
        "The reference image is the only source of identity. "
        f"{detail_lock}"
        f"{strength_rule} "
        "Use only neutral white or light gray background, no text, no watermark, no UI, no arrows, no labels. "
        f"{understood_rule}{desc_rule}"
    )
    if is_character:
        sheet_prompt = (
            seed
            + " Output one 16:9 image containing exactly five full-body views of the same character or humanoid asset, left to right: "
            "front view, front-left 45 degree three-quarter view, front-right 45 degree three-quarter view, strict side view, back view. "
            "This is a faithful view-completion sheet, not a redesign or concept variant. Do not add large accessories, patterns, armor, building parts, colors, or style elements that are absent from the reference image. "
            "Do not remove distinctive visible parts from the reference image. Preserve the exact subject class, silhouette, color palette, materials, weathering, face or screen expression, clothing or shell structure, belt, gloves, boots, weapons, props, and all distinctive details. "
            "Use a neutral A-pose suitable for rigging: feet planted and parallel, arms slightly away from the torso, hands fully visible and separated from belt, hips, clothes, thighs, and body. "
            "All five views must have the same height, same scale, same silhouette, same head/face/screen, same outfit/structure, same materials, same colors, same weathering, and consistent proportions. "
            "Keep left and right limbs symmetrical and intact. The subject must be centered in each column, fully visible from hat/head to boots/feet, with clear empty spacing between columns."
        )
        split_prompt = seed + " This view will be obtained by splitting the generated five-view A-pose sheet; keep it consistent with the sheet."
    else:
        sheet_prompt = (
            seed
            + " Output one 16:9 image containing exactly five inferred turntable views of the same asset, left to right: source/front anchor view, front-left 45 degree view, front-right 45 degree view, strict side view, inferred back view. "
            "This is a faithful view-completion sheet based on the reference image, not a redesign, not a photorealistic remake, and not a new asset inspired by the reference. "
            "Front and 45 degree views must preserve the visible design very closely. Side and back views may infer hidden structure, but must look like the same asset turned around: same compact footprint, same stacked silhouette, same construction logic, same material language, same colors, same weathering, and same stylized art style. "
            f"{dynamic_detail_rule}"
            "Do not invent a different rear facade, broad rectangular factory wall, boiler door, giant tank, new base, or new industrial layout. Continue only the visible reference structures around the hidden sides. "
            "The asset should be centered in each column, fully visible, with clear spacing between columns."
        )
        single_view_prompts = {
            "front": "source/front anchor view",
            "front_left_45": "front-left 45 degree inferred turntable view",
            "front_right_45": "front-right 45 degree inferred turntable view",
            "side": "strict side inferred turntable view",
            "back": "inferred back turntable view",
        }
    for view in plan.get("views") if isinstance(plan.get("views"), list) else []:
        if not isinstance(view, dict):
            continue
        name = str(view.get("view") or "")
        if name == "triview_sheet":
            view["prompt"] = sheet_prompt
            view["sheet_views"] = sheet_views
        elif name in set(sheet_views):
            if is_character:
                view["prompt"] = split_prompt
            else:
                view_name = single_view_prompts.get(name, _VIEW_ROLE_LABELS.get(name, name))
                view["prompt"] = (
                    seed
                    + f" Output one single 4:3 image showing only the {view_name} of this exact same asset. "
                    "Do not create a multi-column sheet. Do not crop the asset. Keep the entire asset fully visible with generous white margin on all sides. "
                    "This is a turntable continuation from the reference. Hidden sides may be inferred, but the result must still look like the same asset turned around. "
                    f"{dynamic_detail_rule}"
                    "Do not introduce a different rear facade, broad rectangular factory wall, boiler door, giant tank, new base, new machinery layout, or new building proportions. "
                    "Center the asset in the frame. Preserve the exact same compact stacked silhouette, proportions, materials, colors, weathering, distinctive details, and original art style from the reference."
                )


def _fresh_view_generation_plan(
    job: Dict[str, Any],
    *,
    image_model: str,
    view_understanding: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    stored = job.get("view_generation_plan") if isinstance(job.get("view_generation_plan"), dict) else {}
    requested_template = _canonical_asset_template(str(job.get("asset_template") or stored.get("asset_template") or "auto"))
    asset_template = _template_from_understanding(view_understanding, requested_template)
    reference_strength = str(job.get("reference_strength") or stored.get("reference_strength") or "high")
    description = str(job.get("description") or "")
    plan = _view_generation_plan(
        asset_template=asset_template,
        reference_strength=reference_strength,
        description=description,
    )
    _apply_generic_reference_triview_prompts(
        plan,
        asset_template=asset_template,
        reference_strength=reference_strength,
        description=description,
        view_understanding=view_understanding,
    )
    # One sheet keeps all inferred views in the same design space. Generating
    # hard-surface side/back views independently drifts into unrelated assets.
    plan["view_generation_mode"] = "sheet"
    plan["front_view_policy"] = "source_anchor_for_character_generated_sheet_front_for_hard_surface"
    plan["image_model"] = _canonical_image_model(image_model or str(stored.get("image_model") or job.get("image_model") or _SUTUI_GPT_IMAGE_2_MODEL))
    return plan


def _apply_retry_feedback_to_view_plan(plan: Dict[str, Any], preprocessing: Dict[str, Any]) -> None:
    verification = preprocessing.get("triview_consistency_verification") if isinstance(preprocessing.get("triview_consistency_verification"), dict) else {}
    issues = verification.get("issues") if isinstance(verification.get("issues"), list) else []
    clean_issues = [_compact_text_value(issue, max_chars=220) for issue in issues if str(issue or "").strip()]
    if not clean_issues:
        return
    feedback = (
        " Previous QA failure to avoid on this retry: "
        + " | ".join(clean_issues[:5])
        + ". Correct these issues while preserving only the reference-visible identity and dynamically identified key details. "
    )
    for view in plan.get("views") if isinstance(plan.get("views"), list) else []:
        if isinstance(view, dict) and isinstance(view.get("prompt"), str):
            view["prompt"] = view["prompt"] + feedback
    plan["retry_feedback"] = clean_issues[:5]


def _component_slots_from_plan(preprocessing: Dict[str, Any], *, max_parts: int = _AI3D_DEFAULT_MAX_PARTS) -> List[Tuple[str, str]]:
    ai_plan = preprocessing.get("component_ai_plan") if isinstance(preprocessing.get("component_ai_plan"), dict) else {}
    parts = ai_plan.get("parts") if isinstance(ai_plan.get("parts"), list) else []
    slots: List[Tuple[str, str]] = []
    seen: set[str] = set()
    limit = max(1, min(_AI3D_ABSOLUTE_MAX_PARTS, int(max_parts or _AI3D_DEFAULT_MAX_PARTS)))
    for idx, item in enumerate(parts[:limit], start=1):
        if not isinstance(item, dict):
            continue
        output_strategy = str(item.get("output_strategy") or item.get("strategy") or "").strip().lower()
        if output_strategy in {"texture", "reference", "assembly_reference", "do_not_split", "keep_in_base"}:
            continue
        role_raw = str(item.get("role") or f"part_{idx:02d}").strip().lower()
        role = re.sub(r"[^a-z0-9_\-]+", "_", role_raw).strip("_-")[:48] or f"part_{idx:02d}"
        if role in seen:
            role = f"{role}_{idx:02d}"
        seen.add(role)
        label = str(item.get("label") or role).strip()[:40] or role
        slots.append((role, label))
    if slots:
        return slots
    return list(_CHARACTER_AI_PARTS[: max(1, min(len(_CHARACTER_AI_PARTS), limit))])


def _component_sheet_prompt(
    *,
    asset_template: str,
    reference_strength: str,
    description: str = "",
    part_slots: Optional[List[Tuple[str, str]]] = None,
) -> str:
    template = _canonical_asset_template(asset_template)
    base = _PART_TEMPLATE_COPY.get(template, _PART_TEMPLATE_COPY["auto"])
    lock_copy = _subject_lock_copy(template)
    strength = (reference_strength or "high").strip().lower()
    strength_copy = {
        "strict": "参考图强度极高，优先锁定原画，不允许重设计，只补足被遮挡的合理边缘",
        "high": "参考图强度高，保持原画设定，只为独立部件补必要结构",
        "balanced": "参考图强度中等，允许为部件完整性补合理结构，但不得换主体或改性别",
        "creative": "参考图强度较低，允许更强设计整理，但不得换主体或改性别",
    }.get(strength, "参考图强度高，保持原画设定，只为独立部件补必要结构")
    slots = part_slots or list(_CHARACTER_AI_PARTS)
    labels = "、".join(label for _, label in slots)
    desc = (description or "").strip()
    prompt = (
        f"{base}。{strength_copy}。{lock_copy}"
        f"请严格按视觉拆件规划，把参考图中的主体拆成 {len(slots)} 个相互独立的部件：{labels}。"
        "每个部件都必须来自同一个参考主体；人物不得改性别、人种、年龄、脸型、发型、胡须、体型、服装、配色或材质；道具不得改结构、纹样、材质或用途。"
        "输出一张干净的部件分离板：按部件数量自适应网格，从左到右、从上到下依次放置上述部件；未使用格子保持空白。"
        "每个格子只放一个完整独立部件，部件居中、完整、无遮挡，格子之间留足空隙。"
        "背景使用统一中性浅灰或白色，避免阴影粘连，避免任何文字、编号、水印、UI、箭头、说明线。"
        "不要输出整个人物合照，不要把多个部件粘在同一个格子里，不要补成新角色或新物体。"
    )
    if desc:
        prompt += f"角色/资产描述：{desc[:1200]}。"
    return prompt


def _component_part_prompt(
    *,
    asset_template: str,
    reference_strength: str,
    role: str,
    label: str,
    reason: str = "",
    description: str = "",
) -> str:
    template = _canonical_asset_template(asset_template)
    base = _PART_TEMPLATE_COPY.get(template, _PART_TEMPLATE_COPY["auto"])
    lock_copy = _subject_lock_copy(template)
    strength = (reference_strength or "high").strip().lower()
    strict_copy = {
        "strict": "参考强度极高：必须忠实提取红框标出的原图部件，只允许清理背景和补齐极少被遮挡边缘",
        "high": "参考强度高：必须忠实提取红框标出的原图部件，只允许必要的边缘补全",
        "balanced": "参考强度中高：以红框原图部件为准，不允许重设计",
        "creative": "即使设置为 creative，本步骤仍为生产级拆件，禁止创意改款",
    }.get(strength, "参考强度高：必须忠实提取红框标出的原图部件，只允许必要的边缘补全")
    prompt = (
        f"{base}。{strict_copy}。{lock_copy}"
        f"当前只生成一个独立部件：{label}（role={role}）。"
        "输入参考图左侧/主体是完整原图并带红色定位框，右侧/局部是同一红框区域的放大裁片；"
        "请只根据红框内的真实可见内容生成该部件的干净独立资产图。"
        "这是给 3D 生成器的 mesh input，不是裁剪预览图：必须把目标部件从相邻身体、衣服、背景和遮挡上下文中干净分离出来。"
        "必须保持原图的颜色、材质、纹样、年代、身份气质和结构；"
        "禁止换脸、换性别、换物种、换时代、年轻化、加不存在的盔甲/服装/外壳/纹样/配色/饰件，禁止把原部件改成另一套设计。"
        "输出单个完整部件，居中，无遮挡，背景为纯白或浅灰；不要输出整个人物，不要输出半身 bust，不要输出多格拼板，不要文字、水印、箭头、编号、红框。"
        "如果该部件在原图被遮挡，只补齐可由原图同材质同结构推断的边缘，不得发明新设计。"
    )
    role_text = f"{role} {label}".lower()
    if _is_head_mesh_role(role_text) and not _is_neck_or_torso_role(role_text):
        prompt += (
            "头部/帽子类硬约束：只输出帽子、机器人头壳/脸屏、耳侧圆件和极短脖子接口；"
            "严禁包含围巾、肩膀、胸口、夹克、手臂或任何上半身。"
        )
    if reason:
        prompt += f"拆件理由：{reason[:240]}。"
    desc = (description or "").strip()
    if desc:
        prompt += f"用户设定仅用于身份锁定，不得覆盖原图视觉：{desc[:800]}。"
    return prompt


def _part_plan_lookup(preprocessing: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    ai_plan = preprocessing.get("component_ai_plan") if isinstance(preprocessing.get("component_ai_plan"), dict) else {}
    parts = ai_plan.get("parts") if isinstance(ai_plan.get("parts"), list) else []
    out: Dict[str, Dict[str, Any]] = {}
    for item in parts:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        if role:
            out[role] = item
    return out


def _make_component_part_reference(
    source_path: Path,
    part: Dict[str, Any],
    dest: Path,
) -> Dict[str, Any]:
    box = part.get("box")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise RuntimeError(f"部件缺少有效 box：{part.get('role') or part.get('label') or 'unknown'}")
    rel_box = tuple(float(v) for v in list(box)[:4])
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as im:
        source = ImageOps.exif_transpose(im).convert("RGB")
        width, height = source.size
        abs_box = _relative_box_to_abs(width, height, rel_box)  # type: ignore[arg-type]
        crop = source.crop(abs_box)
        full_w = 900
        crop_w = 900
        panel_h = 1100
        canvas = Image.new("RGB", (full_w + crop_w, panel_h), (244, 244, 240))

        full = source.copy()
        full.thumbnail((full_w - 64, panel_h - 64), _LANCZOS)
        full_x = (full_w - full.width) // 2
        full_y = (panel_h - full.height) // 2
        canvas.paste(full, (full_x, full_y))
        scale_x = full.width / max(1, width)
        scale_y = full.height / max(1, height)
        draw = ImageDraw.Draw(canvas)
        rect = (
            int(full_x + abs_box[0] * scale_x),
            int(full_y + abs_box[1] * scale_y),
            int(full_x + abs_box[2] * scale_x),
            int(full_y + abs_box[3] * scale_y),
        )
        for offset in range(5):
            draw.rectangle((rect[0] - offset, rect[1] - offset, rect[2] + offset, rect[3] + offset), outline=(228, 28, 28))

        side = max(crop.width, crop.height, 16)
        crop_canvas = Image.new("RGB", (side, side), (34, 34, 32))
        crop_canvas.paste(crop, ((side - crop.width) // 2, (side - crop.height) // 2))
        crop_canvas.thumbnail((crop_w - 72, panel_h - 72), _LANCZOS)
        crop_x = full_w + (crop_w - crop_canvas.width) // 2
        crop_y = (panel_h - crop_canvas.height) // 2
        canvas.paste(crop_canvas, (crop_x, crop_y))
        canvas.save(dest, "JPEG", quality=94, optimize=True)
    return {
        "width": full_w + crop_w,
        "height": panel_h,
        "source_box": list(abs_box),
        "reference_kind": "full_image_red_box_plus_crop",
    }


def _make_component_mesh_input_crop(
    source_path: Path,
    part: Dict[str, Any],
    dest: Path,
) -> Dict[str, Any]:
    box = part.get("box")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise RuntimeError(f"部件缺少有效 box：{part.get('role') or part.get('label') or 'unknown'}")
    rel_box = tuple(float(v) for v in list(box)[:4])
    with Image.open(source_path) as im:
        source = ImageOps.exif_transpose(im).convert("RGB")
        abs_box = _relative_box_to_abs(source.width, source.height, rel_box)  # type: ignore[arg-type]
        abs_box = _tighten_component_box_for_mesh(box=abs_box, source_size=source.size, part=part)
        meta = _save_square_crop(source, abs_box, dest, size=2048)
    meta["mesh_input_kind"] = "source_pixel_crop"
    meta["fidelity_source"] = True
    meta["source_width"] = source.width
    meta["source_height"] = source.height
    meta["relative_source_box"] = [
        abs_box[0] / max(1, source.width),
        abs_box[1] / max(1, source.height),
        abs_box[2] / max(1, source.width),
        abs_box[3] / max(1, source.height),
    ]
    return meta


def _component_sheet_grid(count: int) -> Tuple[int, int, int]:
    count = max(1, int(count or 1))
    if count <= 4:
        cols = count
    elif count <= 12:
        cols = 4
    else:
        cols = 6
    rows = int((count + cols - 1) // cols)
    return cols, rows, 640


def _make_generated_component_sheet(parts: List[Dict[str, Any]], dest: Path) -> Dict[str, Any]:
    cols, rows, cell = _component_sheet_grid(len(parts))
    canvas = Image.new("RGB", (cols * cell, rows * cell), (238, 238, 232))
    for idx, item in enumerate(parts[: cols * rows]):
        path = Path(str(item.get("normalized_path") or ""))
        if not path.exists():
            continue
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((cell - 56, cell - 56), _LANCZOS)
            col = idx % cols
            row = idx // cols
            x = col * cell + (cell - im.width) // 2
            y = row * cell + (cell - im.height) // 2
            canvas.paste(im, (x, y))
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, "JPEG", quality=94, optimize=True)
    return {"width": canvas.width, "height": canvas.height, "layout": f"{cols}x{rows}", "part_count": min(len(parts), cols * rows)}


def _make_fidelity_component_inputs_from_plan(
    *,
    job_id: str,
    reference_path: Path,
    source_filename: str,
    component_dir: Path,
    preprocessing: Dict[str, Any],
    part_slots: List[Tuple[str, str]],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]:
    plan_lookup = _part_plan_lookup(preprocessing)
    out_dir = component_dir / "fidelity_source_crops"
    sheet_path = component_dir / "fidelity_component_sheet.jpg"
    component_inputs: List[Dict[str, Any]] = []
    for idx, (role, label) in enumerate(part_slots, start=1):
        part_plan = plan_lookup.get(role) or {"role": role, "label": label}
        if not isinstance(part_plan.get("box"), (list, tuple)):
            continue
        dest = out_dir / f"{idx:02d}_{role}.png"
        meta = _make_component_mesh_input_crop(reference_path, part_plan, dest)
        meta["component_source_mode"] = "fidelity_source_crops"
        part_input = _public_input(
            job_id=job_id,
            index=idx,
            filename=dest.name,
            normalized_path=dest,
            meta=meta,
            role=role,
            label=label,
            source_filename=source_filename,
            generated=True,
        )
        part_input["mesh_input_kind"] = "source_pixel_crop"
        part_input["fidelity_source"] = True
        part_input["part_reason"] = str(part_plan.get("reason") or "")
        if part_plan.get("output_strategy"):
            part_input["output_strategy"] = str(part_plan.get("output_strategy") or "")
        if part_plan.get("part_type"):
            part_input["part_type"] = str(part_plan.get("part_type") or "")
        if part_plan.get("uncertainty"):
            part_input["uncertainty"] = str(part_plan.get("uncertainty") or "")
        component_inputs.append(part_input)
    if not component_inputs:
        raise RuntimeError("AI 拆件规划没有可用于原图保真裁切的部件框。")
    sheet_meta = _make_generated_component_sheet(component_inputs, sheet_path)
    sheet_input = _public_input(
        job_id=job_id,
        index=0,
        filename=sheet_path.name,
        normalized_path=sheet_path,
        meta=sheet_meta,
        role="component_sheet",
        label="原图保真裁切部件输入板",
        source_filename=source_filename,
        generated=True,
    )
    return sheet_input, component_inputs, sheet_meta


def _split_component_sheet(sheet_path: Path, out_dir: Path, *, part_slots: Optional[List[Tuple[str, str]]] = None) -> List[Dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    parts: List[Dict[str, Any]] = []
    slots = part_slots or list(_CHARACTER_AI_PARTS)
    with Image.open(sheet_path) as im:
        source = ImageOps.exif_transpose(im).convert("RGB")
        cols, rows, _ = _component_sheet_grid(len(slots))
        cell_w = source.width / cols
        cell_h = source.height / rows
        for idx, (role, label) in enumerate(slots[: cols * rows], start=1):
            col = (idx - 1) % cols
            row = (idx - 1) // cols
            left = int(round(col * cell_w))
            top = int(round(row * cell_h))
            right = int(round((col + 1) * cell_w))
            bottom = int(round((row + 1) * cell_h))
            margin_x = max(0, int(cell_w * 0.035))
            margin_y = max(0, int(cell_h * 0.035))
            box = (
                min(max(0, left + margin_x), source.width),
                min(max(0, top + margin_y), source.height),
                min(max(0, right - margin_x), source.width),
                min(max(0, bottom - margin_y), source.height),
            )
            dest = out_dir / f"{idx:02d}_{role}.jpg"
            meta = _save_square_crop(source, box, dest)
            parts.append({
                "index": idx,
                "role": role,
                "label": label,
                "path": dest,
                "width": meta["width"],
                "height": meta["height"],
                "source_box": meta["source_box"],
            })
    return parts


def _canonical_image_model(model: str) -> str:
    raw = (model or _SUTUI_GPT_IMAGE_2_MODEL).strip().lower()
    if "banana" in raw:
        return "nano-banana-2"
    if raw in {"openai/gpt-image-2", "gptimage2", "gpt-image2", "gpt-image-2", "gpt-image"}:
        return _SUTUI_GPT_IMAGE_2_MODEL
    return model.strip() or _SUTUI_GPT_IMAGE_2_MODEL


def _is_sutui_gpt_image_2(model: str) -> bool:
    return _canonical_image_model(model) == _SUTUI_GPT_IMAGE_2_MODEL


def _image_size_for_sutui(aspect_ratio: str) -> str:
    ratio = (aspect_ratio or "").strip()
    return ratio if ratio in {"1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3"} else "1:1"


def _image_pixel_size_for_stage(aspect_ratio: str) -> str:
    return _AI3D_IMAGE_STAGE_PIXEL_SIZES.get(_image_size_for_sutui(aspect_ratio), _AI3D_IMAGE_STAGE_PIXEL_SIZES["1:1"])


def _highest_quality_image_prompt(prompt: str, *, aspect_ratio: str) -> str:
    base = (prompt or "").strip()
    if "输出质量硬要求" in base:
        return base
    pixel_size = _image_pixel_size_for_stage(aspect_ratio)
    quality_copy = (
        f"输出质量硬要求：使用最高质量/production quality 图片生成，目标 4K 级超清输出（{pixel_size} 或当前模型支持的最高分辨率），"
        "细节必须清晰锐利，面部/屏幕/结构件、服装或外壳纹样、毛发/织物/金属/皮革/塑料材质边缘和轮廓都要保真；"
        "禁止低清、模糊、压缩噪点、过度平滑、糊纹理、糊脸、糊手、缺失边缘或小图放大感。"
    )
    return f"{base}\n\n{quality_copy}" if base else quality_copy


def _raw_bearer_from_request(request: Request) -> str:
    auth = (request.headers.get("Authorization") or request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _mcp_headers_from_request(request: Request) -> Dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    auth = (request.headers.get("Authorization") or request.headers.get("authorization") or "").strip()
    if auth:
        headers["Authorization"] = auth if auth.lower().startswith("bearer ") else f"Bearer {auth}"
    installation_id = (
        request.headers.get("X-Installation-Id")
        or request.headers.get("x-installation-id")
        or ""
    ).strip()
    if installation_id:
        headers["X-Installation-Id"] = installation_id
    return headers


def _parse_mcp_tool_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return json.dumps(payload, ensure_ascii=False)
    err = payload.get("error")
    if err:
        if isinstance(err, dict):
            raise RuntimeError(str(err.get("message") or err))
        raise RuntimeError(str(err))
    result = payload.get("result")
    if not isinstance(result, dict):
        return json.dumps(payload, ensure_ascii=False)
    if result.get("isError"):
        content = result.get("content")
        if isinstance(content, list):
            text = "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
            raise RuntimeError(text.strip() or "MCP 工具调用失败")
        raise RuntimeError("MCP 工具调用失败")
    content = result.get("content")
    if isinstance(content, list) and content:
        texts = [
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and (item.get("type") == "text" or item.get("text"))
        ]
        if texts:
            return "\n".join(text for text in texts if text)
    return json.dumps(result, ensure_ascii=False)


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw).strip()
    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start:end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _matches_expected_json(data: Dict[str, Any], expected_keys: Tuple[str, ...]) -> bool:
    if not expected_keys:
        return bool(data)
    return any(key in data for key in expected_keys)


def _extract_understand_output_json(text: str, *, expected_keys: Tuple[str, ...]) -> Dict[str, Any]:
    """image.understand can return direct JSON or task.get_result with result.output as JSON text."""
    outer = _extract_json_object(text)
    if not outer:
        return {}
    if _matches_expected_json(outer, expected_keys):
        return outer

    stack: List[Any] = [outer]
    seen: set[int] = set()
    text_candidates: List[str] = []
    while stack:
        item = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, dict):
            if _matches_expected_json(item, expected_keys):
                return item
            for key in ("output", "text", "content", "message"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    text_candidates.append(value.strip())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item[:100])
        elif isinstance(item, str) and item.strip():
            text_candidates.append(item.strip())

    for candidate in text_candidates:
        data = _extract_json_object(candidate)
        if isinstance(data, dict) and _matches_expected_json(data, expected_keys):
            return data
    return {}


def _normalize_ai_part_plan(data: Dict[str, Any], *, max_parts: int) -> List[Dict[str, Any]]:
    parts = data.get("parts") if isinstance(data.get("parts"), list) else []
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    limit = max(1, min(_AI3D_ABSOLUTE_MAX_PARTS, int(max_parts or _AI3D_DEFAULT_MAX_PARTS)))
    for idx, item in enumerate(parts[:limit], start=1):
        if not isinstance(item, dict):
            continue
        role_raw = str(item.get("role") or item.get("name") or f"part_{idx:02d}").strip().lower()
        role = re.sub(r"[^a-z0-9_\-]+", "_", role_raw).strip("_-")[:48] or f"part_{idx:02d}"
        if role in seen:
            role = f"{role}_{idx:02d}"
        seen.add(role)
        label = str(item.get("label") or item.get("name") or role).strip()[:40] or role
        box = item.get("box") or item.get("bbox")
        if not isinstance(box, list) or len(box) != 4:
            continue
        try:
            vals = [float(x) for x in box]
        except Exception:
            continue
        vals = [max(0.0, min(1.0, v)) for v in vals]
        if vals[2] - vals[0] < 0.05 or vals[3] - vals[1] < 0.05:
            continue
        out.append({
            "role": role,
            "label": label,
            "box": tuple(vals),  # type: ignore[arg-type]
            "reason": str(item.get("reason") or "").strip()[:160],
            "part_type": str(item.get("part_type") or item.get("type") or "").strip()[:80],
            "output_strategy": str(item.get("output_strategy") or item.get("strategy") or "3d_part").strip()[:80],
            "occlusion": str(item.get("occlusion") or "").strip()[:80],
            "uncertainty": str(item.get("uncertainty") or item.get("risk") or "").strip()[:180],
        })
    return out


def _normalize_ai_subject_candidates(data: Dict[str, Any], *, max_candidates: int) -> Dict[str, Any]:
    candidates = data.get("candidates") if isinstance(data.get("candidates"), list) else []
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    limit = max(1, min(_AI3D_ABSOLUTE_MAX_PARTS, int(max_candidates or 8)))
    for idx, item in enumerate(candidates[:limit], start=1):
        if not isinstance(item, dict):
            continue
        role_raw = str(item.get("role") or item.get("name") or f"subject_{idx:02d}").strip().lower()
        role = re.sub(r"[^a-z0-9_\-]+", "_", role_raw).strip("_-")[:48] or f"subject_{idx:02d}"
        if not role.endswith("_subject"):
            role = f"{role}_subject"
        if role in seen:
            role = f"{role}_{idx:02d}"
        seen.add(role)
        label = str(item.get("label") or item.get("name") or role).strip()[:48] or role
        box = item.get("box") or item.get("bbox")
        if not isinstance(box, list) or len(box) != 4:
            continue
        try:
            vals = [float(x) for x in box]
        except Exception:
            continue
        vals = [max(0.0, min(1.0, v)) for v in vals]
        if vals[2] - vals[0] < 0.06 or vals[3] - vals[1] < 0.06:
            continue
        try:
            score = int(float(item.get("suitability_score") or item.get("score") or 0))
        except Exception:
            score = 0
        out.append({
            "index": len(out) + 1,
            "role": role,
            "label": label,
            "box": tuple(vals),  # type: ignore[arg-type]
            "subject_type": str(item.get("subject_type") or item.get("asset_type") or "").strip()[:80],
            "suitability_score": max(0, min(100, score)),
            "recommended": bool(item.get("recommended")),
            "reason": str(item.get("reason") or "").strip()[:240],
            "risk": str(item.get("risk") or item.get("warning") or "").strip()[:240],
            "must_keep": item.get("must_keep") if isinstance(item.get("must_keep"), list) else [],
            "forbidden_changes": item.get("forbidden_changes") if isinstance(item.get("forbidden_changes"), list) else [],
            "triview_prompt": str(item.get("triview_prompt") or "").strip()[:1000],
        })
    recommended_index = int(float(data.get("recommended_index") or 0)) if str(data.get("recommended_index") or "").strip() else 0
    if not recommended_index and out:
        recommended = next((item for item in out if item.get("recommended")), None)
        if recommended:
            recommended_index = int(recommended["index"])
        else:
            recommended_index = int(max(out, key=lambda item: int(item.get("suitability_score") or 0))["index"])
    return {
        "scene_summary": _compact_text_value(data.get("scene_summary"), max_chars=500),
        "recommended_index": recommended_index,
        "candidates": out,
        "raw": data,
    }


def _compact_text_value(value: Any, *, max_chars: int = 220) -> str:
    if isinstance(value, list):
        value = ", ".join(str(item).strip() for item in value if str(item).strip())
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_chars]


def _normalize_ai_view_understanding(data: Dict[str, Any]) -> Dict[str, Any]:
    keys = [
        "asset_type",
        "subject",
        "visual_summary",
        "body_structure",
        "head_face",
        "clothing_accessories",
        "mechanical_parts",
        "materials",
        "colors",
        "props",
        "must_keep",
        "forbidden_changes",
        "triview_prompt",
    ]
    out = {key: _compact_text_value(data.get(key), max_chars=420 if key in {"visual_summary", "triview_prompt"} else 240) for key in keys}
    details = []
    for key in keys[:-1]:
        if out.get(key):
            details.append(f"{key}: {out[key]}")
    if not out.get("triview_prompt"):
        out["triview_prompt"] = "; ".join(details)[:1800]
    out["raw"] = data
    return out


def _view_understanding_from_subject_candidate(subject_plan: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    must_keep = candidate.get("must_keep") if isinstance(candidate.get("must_keep"), list) else []
    forbidden = candidate.get("forbidden_changes") if isinstance(candidate.get("forbidden_changes"), list) else []
    subject_type = str(candidate.get("subject_type") or "").strip()
    label = str(candidate.get("label") or candidate.get("role") or "selected subject").strip()
    prompt = str(candidate.get("triview_prompt") or "").strip()
    if not prompt:
        prompt = (
            f"faithful multi-view 3D asset of the selected subject: {label}; "
            f"preserve visible details: {', '.join(str(x) for x in must_keep[:12])}; "
            f"avoid changes: {', '.join(str(x) for x in forbidden[:8])}"
        )
    data = {
        "asset_type": subject_type or "other",
        "subject": label,
        "visual_summary": str(subject_plan.get("scene_summary") or label),
        "body_structure": str(candidate.get("reason") or ""),
        "head_face": "",
        "clothing_accessories": "",
        "mechanical_parts": "",
        "materials": "",
        "colors": "",
        "props": "",
        "must_keep": must_keep,
        "forbidden_changes": forbidden,
        "triview_prompt": prompt,
    }
    out = _normalize_ai_view_understanding(data)
    out.update({
        "provider": "image.understand",
        "model": _SUTUI_IMAGE_UNDERSTAND_MODEL,
        "source": "subject_candidate_plan",
        "subject_type": subject_type,
        "suitability_score": candidate.get("suitability_score"),
        "risk": str(candidate.get("risk") or ""),
    })
    return out


async def _call_mcp_tool(request: Request, name: str, arguments: Dict[str, Any], *, timeout_seconds: float) -> str:
    body = {
        "jsonrpc": "2.0",
        "id": f"ai3d-{store.new_job_id()[:8]}",
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    async with httpx.AsyncClient(timeout=timeout_seconds, trust_env=False) as client:
        resp = await client.post(local_mcp_url(), json=body, headers=_mcp_headers_from_request(request))
    if resp.status_code >= 400:
        raise RuntimeError(f"MCP HTTP {resp.status_code}: {(resp.text or '')[:800]}")
    try:
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError(f"MCP 返回非 JSON: {(resp.text or '')[:800]}") from exc
    return _parse_mcp_tool_text(payload)


async def _ai_triview_understanding(
    *,
    job_id: str,
    request: Request,
    reference_path: Path,
    preprocessing: Dict[str, Any],
    asset_template: str,
    description: str,
) -> Dict[str, Any]:
    reference_url = await _reference_image_public_url(
        job_id=job_id,
        request=request,
        reference_path=reference_path,
        preprocessing=preprocessing,
    )
    prompt = (
        "You are a visual analyst preparing a faithful 3D asset multi-view generation prompt. "
        "Analyze the single main subject in the reference image. Do not invent a new character, costume, era, species, or style. "
        "Describe only what is visible, and mark uncertain hidden-side details as inferred rather than redesigning them. "
        "Do not translate clothing, accessories, props, architecture, or cultural cues into an unrelated archetype, profession, dynasty, fantasy class, or game faction. "
        "For character assets, the downstream image model will generate five views: front, front-left 45 degree, front-right 45 degree, side, and back. "
        "Return strict JSON only, no Markdown. Use concise but specific visual words. "
        "JSON schema: {"
        "\"asset_type\":\"humanoid_robot|human_character|creature|hard_surface_prop|ornament|other\","
        "\"subject\":\"one sentence naming exactly what the subject is\","
        "\"visual_summary\":\"high signal summary of the visible design\","
        "\"body_structure\":\"body proportions, silhouette, limbs, mechanical or organic structure\","
        "\"head_face\":\"head, face, screen, hat, hair, helmet, facial identity\","
        "\"clothing_accessories\":\"clothing, armor, belt, gloves, boots, scarf, cape, accessories\","
        "\"mechanical_parts\":\"visible mechanical joints, panels, pistons, robotic parts, hard-surface details\","
        "\"materials\":\"visible materials such as leather, metal, plastic, fabric, fur, cloth\","
        "\"colors\":\"dominant color palette\","
        "\"props\":\"held or attached props\","
        "\"must_keep\":[\"specific visible features that must remain identical\"],"
        "\"forbidden_changes\":[\"changes that would break identity\"],"
        "\"triview_prompt\":\"a compact multi-view prompt fragment that should be inserted into image generation to keep this exact subject\""
        "}. "
        f"Asset template selected by user: {asset_template or 'auto'}. "
    )
    desc = (description or "").strip()
    if desc:
        prompt += f"User description, secondary to the image: {desc[:1000]}"
    args = {
        "capability_id": "image.understand",
        "payload": {
            "model": _SUTUI_IMAGE_UNDERSTAND_MODEL,
            "prompt": prompt,
            "image_url": reference_url,
        },
    }
    text = await _call_mcp_tool(request, "invoke_capability", args, timeout_seconds=8 * 60.0)
    text = await _poll_understand_result(request, text)
    data = _extract_understand_output_json(text, expected_keys=("subject", "visual_summary", "triview_prompt"))
    if not data:
        raise RuntimeError(f"image.understand did not return usable multi-view analysis JSON: {text[:500]}")
    out = _normalize_ai_view_understanding(data)
    out.update({
        "provider": "image.understand",
        "model": _SUTUI_IMAGE_UNDERSTAND_MODEL,
        "reference_url": reference_url,
    })
    return out


async def _ai_subject_candidates(
    *,
    job_id: str,
    request: Request,
    reference_path: Path,
    preprocessing: Dict[str, Any],
    asset_template: str,
    description: str,
    max_candidates: int,
) -> Dict[str, Any]:
    reference_url = await _reference_image_public_url(
        job_id=job_id,
        request=request,
        reference_path=reference_path,
        preprocessing=preprocessing,
    )
    prompt = (
        "You are the visual decision layer for an automated 2D-to-3D pipeline. "
        "Analyze the uploaded image as Codex would: identify every plausible 3D-generation subject, decide the best default subject, and extract the details that downstream multi-view generation must preserve. "
        "Do not make the user choose by guesswork. Prefer one complete, coherent subject over random crop regions. "
        "If the image is a concept sheet with insets, recommend the main complete asset panel, not detail insets. "
        "If the image contains a large environment plus smaller vehicles/props, list them separately and recommend the subject that appears most central/complete unless the smaller subject is clearly the intended standalone asset. "
        "For each candidate, give a tight but complete bbox around the visible subject, a suitability score for single-image-to-3D, and risks such as occlusion, hidden backside uncertainty, too small, embedded in scene, or likely to be misread. "
        "Extract type-specific identity locks: shape, silhouette, materials, colors, markings/text/signs, small details, accessories, pipes, antennas, flags, weapons, clothing, face/screen, etc. Do not hard-code any object type; describe what is actually visible. "
        "Return strict JSON only, no Markdown. Schema: {"
        "\"scene_summary\":\"short description of the whole image\","
        "\"recommended_index\":1,"
        "\"candidates\":[{"
        "\"role\":\"english_snake_case_subject\","
        "\"label\":\"Chinese short label\","
        "\"subject_type\":\"character|vehicle|architecture|prop|ornament|hard_surface|scene|other\","
        "\"box\":[x1,y1,x2,y2],"
        "\"suitability_score\":0-100,"
        "\"recommended\":true|false,"
        "\"reason\":\"why this is or is not a good 3D subject\","
        "\"risk\":\"main risk for faithful multiview/3D\","
        "\"must_keep\":[\"visible details that must remain identical\"],"
        "\"forbidden_changes\":[\"changes that would break identity\"],"
        "\"triview_prompt\":\"compact prompt fragment for generating faithful multiviews of exactly this candidate\""
        "}]} "
        "Coordinates are relative 0-1 in the uploaded image. Include 1-6 candidates only. "
        "The recommended candidate should be the safest default for automated 3D. If no candidate is reliable, still choose the least bad one and explain the risk."
        f" User-selected asset template: {asset_template or 'auto'}. "
    )
    desc = (description or "").strip()
    if desc:
        prompt += f"User description, secondary to image: {desc[:1000]}"
    args = {
        "capability_id": "image.understand",
        "payload": {
            "model": _SUTUI_IMAGE_UNDERSTAND_MODEL,
            "prompt": prompt,
            "image_url": reference_url,
        },
    }
    text = await _call_mcp_tool(request, "invoke_capability", args, timeout_seconds=8 * 60.0)
    text = await _poll_understand_result(request, text)
    data = _extract_understand_output_json(text, expected_keys=("candidates", "recommended_index", "scene_summary"))
    plan = _normalize_ai_subject_candidates(data, max_candidates=max_candidates)
    if not plan.get("candidates"):
        raise RuntimeError(f"视觉理解未返回可用主体候选 JSON：{text[:500]}")
    plan.update({
        "provider": "image.understand",
        "model": _SUTUI_IMAGE_UNDERSTAND_MODEL,
        "reference_url": reference_url,
    })
    return plan


async def _ai_component_plan(
    *,
    job_id: str,
    request: Request,
    reference_path: Path,
    preprocessing: Dict[str, Any],
    asset_template: str,
    description: str,
    max_parts: int,
) -> Dict[str, Any]:
    reference_url = await _reference_image_public_url(
        job_id=job_id,
        request=request,
        reference_path=reference_path,
        preprocessing=preprocessing,
    )
    prompt = (
        "你是3D资产制作的拆件规划师。请分析图片中的单个主体，为后续高质量3D生成规划真实可拆部件。"
        "不要重新设计主体，不要更换性别/人种/服装/材质，只基于图中可见结构。"
        "拆件数量由画面决定，不要凑固定数量；宁愿少拆也不要拆坏。"
        "只有边界清楚、能完整移动、对3D重建有收益的独立硬部件才标记为3d_part。"
        "平面纹样、贴花、文字、污渍、连续表面细节标记为texture；重复模块标记为module；"
        "只表达装配关系的组合件标记为assembly_reference；遮挡严重、边缘融合太深、无法完整分离的元素标记为keep_in_base或do_not_split。"
        "不要把一个完整对象切碎，不要为了数量拆出破碎透明区域、锯齿边、白边、黑边或拼贴感。"
        "输出严格 JSON，不要 Markdown，不要解释。JSON 结构："
        "{\"asset_type\":\"character|prop|hard_surface|architecture|ornament|other\","
        "\"strategy\":\"multi_view|part_batch|base_plus_selected_parts\","
        "\"parts\":[{\"role\":\"英文snake_case\",\"label\":\"中文短标签\","
        "\"box\":[x1,y1,x2,y2],\"part_type\":\"unique|shared|variant|module|texture|assembly_reference|effect|unknown\","
        "\"output_strategy\":\"3d_part|multi_view_part|texture|module|assembly_reference|keep_in_base|do_not_split\","
        "\"occlusion\":\"none|minor|heavy\",\"uncertainty\":\"不确定点或不强拆理由\","
        "\"reason\":\"为什么要拆或为什么不强拆\"}]}"
        "box 是相对整张图的 0-1 坐标，必须包住可见部件并留少量边缘。"
        f"最多列出 {max(1, min(_AI3D_ABSOLUTE_MAX_PARTS, int(max_parts or _AI3D_DEFAULT_MAX_PARTS)))} 个有价值条目；"
        "复杂角色优先真正独立件如头饰、腰带扣、武器、靴子、配饰；连续衣服、袖子、下摆、皮肤和软布料通常 keep_in_base，不强行拆。"
        "道具、建筑或器物按真实结构拆，如主体、屋顶、管线、塔、烟囱、底座、可分离外挂件、重复模块；融合太深的保留在主体。"
        f"资产模板：{asset_template or 'auto'}。"
    )
    desc = (description or "").strip()
    if desc:
        prompt += f"用户设定：{desc[:1000]}。"
    args = {
        "capability_id": "image.understand",
        "payload": {
            "model": _SUTUI_IMAGE_UNDERSTAND_MODEL,
            "prompt": prompt,
            "image_url": reference_url,
        },
    }
    text = await _call_mcp_tool(request, "invoke_capability", args, timeout_seconds=8 * 60.0)
    text = await _poll_understand_result(request, text)
    data = _extract_understand_output_json(text, expected_keys=("parts",))
    parts = _normalize_ai_part_plan(data, max_parts=max_parts)
    if not parts:
        raise RuntimeError(f"视觉模型未返回可用拆件 JSON：{text[:500]}")
    return {
        "provider": "image.understand",
        "model": _SUTUI_IMAGE_UNDERSTAND_MODEL,
        "reference_url": reference_url,
        "asset_type": str(data.get("asset_type") or ""),
        "strategy": str(data.get("strategy") or ""),
        "parts": parts,
        "raw": data,
    }


async def _ai_verify_component_sheet(
    *,
    job_id: str,
    request: Request,
    reference_path: Path,
    sheet_path: Path,
    preprocessing: Dict[str, Any],
    part_slots: List[Tuple[str, str]],
) -> Dict[str, Any]:
    reference_url = await _reference_image_public_url(
        job_id=job_id,
        request=request,
        reference_path=reference_path,
        preprocessing=preprocessing,
    )
    sheet_url = await _reference_image_public_url(
        job_id=job_id,
        request=request,
        reference_path=sheet_path,
        preprocessing=preprocessing,
    )
    labels = "、".join(label for _, label in part_slots)
    prompt = (
        "你是3D资产制作质检。请比较第一张原始参考图和第二张 AI 部件分离板。"
        "目标是高质量 3D 生产，宁可失败也不要放过换脸、换衣服、换人种、换物种、换结构、漏关键部件或格子混部件。"
        "输出严格 JSON，不要 Markdown。结构："
        "{\"passed\":true|false,\"score\":0-100,\"issues\":[\"问题\"],\"part_match_count\":数字,\"identity_changed\":true|false,\"design_changed\":true|false}"
        f"期望部件包括：{labels}。"
        "判定规则：人物必须保持同一身份/性别/人种/年龄/脸型/发型/胡须/服装/配色/材质；道具必须保持同一结构/纹样/材质。"
        "每格应是一个独立部件，不是整图随便裁切，也不是新画的另一套设计。"
        "只有 score>=82、identity_changed=false、design_changed=false、关键部件基本齐全时才 passed=true。"
    )
    args = {
        "capability_id": "image.understand",
        "payload": {
            "model": _SUTUI_IMAGE_UNDERSTAND_MODEL,
            "prompt": prompt,
            "image_urls": [reference_url, sheet_url],
        },
    }
    text = await _call_mcp_tool(request, "invoke_capability", args, timeout_seconds=8 * 60.0)
    text = await _poll_understand_result(request, text)
    data = _extract_understand_output_json(text, expected_keys=("passed",))
    if not isinstance(data, dict) or "passed" not in data:
        raise RuntimeError(f"视觉质检未返回可用 JSON：{text[:500]}")
    return {
        "provider": "image.understand",
        "model": _SUTUI_IMAGE_UNDERSTAND_MODEL,
        "reference_url": reference_url,
        "sheet_url": sheet_url,
        "passed": bool(data.get("passed")),
        "score": int(float(data.get("score") or 0)),
        "issues": data.get("issues") if isinstance(data.get("issues"), list) else [],
        "part_match_count": int(float(data.get("part_match_count") or 0)),
        "identity_changed": bool(data.get("identity_changed")),
        "design_changed": bool(data.get("design_changed")),
        "raw": data,
    }


async def _ai_verify_multiview_consistency(
    *,
    job_id: str,
    request: Request,
    reference_path: Path,
    generated_inputs: List[Dict[str, Any]],
    review_sheet_path: Path,
    preprocessing: Dict[str, Any],
    asset_template: str,
) -> Dict[str, Any]:
    review_meta = _make_multiview_review_sheet(reference_path, generated_inputs, review_sheet_path)
    review_url = await _reference_image_public_url(
        job_id=job_id,
        request=request,
        reference_path=review_sheet_path,
        preprocessing=preprocessing,
    )
    is_character = _is_character_template(asset_template)
    threshold = 86 if is_character else 78
    view_understanding = preprocessing.get("triview_ai_understanding") if isinstance(preprocessing.get("triview_ai_understanding"), dict) else {}
    detail_context = ""
    if isinstance(view_understanding, dict) and view_understanding:
        detail_parts = []
        for key in ("subject", "visual_summary", "body_structure", "mechanical_parts", "props", "must_keep", "forbidden_changes", "triview_prompt"):
            value = _compact_text_value(view_understanding.get(key), max_chars=420 if key == "triview_prompt" else 260)
            if value:
                detail_parts.append(f"{key}: {value}")
        if detail_parts:
            detail_context = " Image understanding context for visible key details: " + " | ".join(detail_parts[:8]) + ". "
    if is_character:
        prompt = (
            "You are a strict production QA reviewer for a 2D-to-3D character pipeline. "
            "The image is a review board: the top row is the original reference / primary visible design, and the lower cells are AI-generated candidate views. "
            "Decide whether the generated views preserve the same character or humanoid identity well enough to be sent into 3D reconstruction. "
            "Do not require hidden backsides to be identical to the unseen reference, but visible identity must remain the same. "
            "Fail if the generated views redesign the subject, change species, gender presentation, face/screen identity, outfit/shell structure, main silhouette, proportions, material language, color palette, weathering style, key accessories, or original art style. "
            f"{detail_context}"
            "Return strict JSON only: "
            "{\"passed\":true|false,\"score\":0-100,\"issues\":[\"issue\"],\"identity_changed\":true|false,\"design_changed\":true|false,\"style_changed\":true|false,\"missing_key_details\":[\"detail\"],\"recommended_action\":\"accept|regenerate|use_source_only|need_real_multiview\"}. "
            f"Only pass when score>={threshold}, identity_changed=false, design_changed=false, and style_changed=false. Be strict."
        )
    else:
        prompt = (
            "You are a production QA reviewer for a 2D-to-3D hard-surface/prop/architecture pipeline. "
            "The image is a review board: the top row is the original reference / primary visible design, and the lower cells are AI-generated candidate views. "
            "The side and back views are allowed to be inferred from a single reference image; do not fail only because hidden rear details differ from the unseen original. "
            "Pass only if the generated views still look like the same asset turned around: same primary subject type, compact footprint, major silhouette, construction logic, material language, color palette, weathering, and original art/rendering style. "
            "Fail if a view becomes a different asset, a broad rectangular factory facade, a different building/prop class, a new base, a new large machinery layout, or a different art style. "
            "Check that visible key details identified from the reference are preserved when they should remain visible or plausibly continue around the form; do not require object categories that were not present in the reference. "
            f"{detail_context}"
            "For concept sheets with detail insets, preserve the primary subject instead of recombining inset details into a new design. "
            "Return strict JSON only: "
            "{\"passed\":true|false,\"score\":0-100,\"issues\":[\"issue\"],\"identity_changed\":true|false,\"design_changed\":true|false,\"style_changed\":true|false,\"missing_key_details\":[\"detail\"],\"recommended_action\":\"accept|regenerate|use_source_only|need_real_multiview\"}. "
            f"Only pass when score>={threshold}, identity_changed=false, design_changed=false, and style_changed=false."
        )
    args = {
        "capability_id": "image.understand",
        "payload": {
            "model": _SUTUI_IMAGE_UNDERSTAND_MODEL,
            "prompt": prompt,
            "image_url": review_url,
        },
    }
    text = await _call_mcp_tool(request, "invoke_capability", args, timeout_seconds=8 * 60.0)
    text = await _poll_understand_result(request, text)
    data = _extract_understand_output_json(text, expected_keys=("passed", "score", "issues"))
    if not isinstance(data, dict) or not any(key in data for key in ("passed", "score", "issues")):
        raise RuntimeError(f"多视角一致性复核未返回可用 JSON：{text[:500]}")
    score = int(float(data.get("score") or 0))
    identity_changed = bool(data.get("identity_changed"))
    design_changed = bool(data.get("design_changed"))
    style_changed = bool(data.get("style_changed"))
    passed = bool(data.get("passed")) and score >= threshold and not identity_changed and not design_changed and not style_changed
    return {
        "provider": "image.understand",
        "model": _SUTUI_IMAGE_UNDERSTAND_MODEL,
        "review_url": review_url,
        "review_sheet_path": str(review_sheet_path),
        "review_sheet_meta": review_meta,
        "passed": passed,
        "score": score,
        "threshold": threshold,
        "issues": data.get("issues") if isinstance(data.get("issues"), list) else [],
        "identity_changed": identity_changed,
        "design_changed": design_changed,
        "style_changed": style_changed,
        "missing_key_details": data.get("missing_key_details") if isinstance(data.get("missing_key_details"), list) else [],
        "recommended_action": str(data.get("recommended_action") or ("accept" if passed else "regenerate")),
        "raw": data,
    }


def _extract_generated_image_preview(result_text: str) -> Dict[str, str]:
    for row in _extract_image_urls_from_generate_result(result_text):
        url = str(row.get("url") or "").strip() if isinstance(row, dict) else ""
        if url:
            return {"url": url, "data_url": ""}
    raw = (result_text or "").strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except Exception:
            data = None
        if isinstance(data, dict):
            stack: List[Any] = [data]
            seen: set[int] = set()
            while stack:
                item = stack.pop()
                if id(item) in seen:
                    continue
                seen.add(id(item))
                if isinstance(item, dict):
                    for key in ("url", "image_url", "output_url", "public_url"):
                        value = item.get(key)
                        if isinstance(value, str) and value.strip().startswith(("http://", "https://")):
                            return {"url": value.strip(), "data_url": ""}
                    b64 = item.get("b64_json") or item.get("base64")
                    if isinstance(b64, str) and b64.strip():
                        payload = b64.split(",", 1)[-1]
                        return {"url": "", "data_url": f"data:image/png;base64,{payload}"}
                    stack.extend(list(item.values()))
                elif isinstance(item, list):
                    stack.extend(item[:100])
                elif isinstance(item, str) and item.strip().startswith(("http://", "https://")):
                    return {"url": item.strip(), "data_url": ""}
    return {}


def _extract_understand_task_id(result_text: str) -> str:
    task_id = _extract_task_id_from_result(result_text)
    if task_id:
        return task_id
    data = _extract_json_object(result_text)
    stack: List[Any] = [data]
    seen: set[int] = set()
    while stack:
        item = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, dict):
            value = item.get("task_id") or item.get("id")
            if isinstance(value, str) and value.strip():
                return value.strip()
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item[:100])
    return ""


async def _poll_understand_result(request: Request, submit_text: str) -> str:
    if not _is_task_result_in_progress(submit_text):
        return submit_text
    task_id = _extract_understand_task_id(submit_text)
    if not task_id:
        return submit_text
    poll_args = {
        "capability_id": "task.get_result",
        "payload": {"task_id": task_id, "capability_id": "image.understand"},
    }
    final_text = submit_text
    waited = 0
    while waited <= 8 * 60:
        final_text = await _call_mcp_tool(request, "invoke_capability", poll_args, timeout_seconds=10 * 60.0)
        if not _is_task_result_in_progress(final_text):
            return final_text
        await asyncio.sleep(8)
        waited += 8
    raise RuntimeError(f"视觉理解任务查询超时，task_id={task_id}")


async def _reference_image_public_url(
    *,
    job_id: str,
    request: Request,
    reference_path: Path,
    preprocessing: Dict[str, Any],
) -> str:
    cache_key = str(reference_path.resolve())
    cache_map = preprocessing.get("reference_public_urls") if isinstance(preprocessing.get("reference_public_urls"), dict) else {}
    cached = cache_map.get(cache_key)
    if isinstance(cached, str) and cached.strip().startswith(("http://", "https://")):
        return cached.strip()
    token = _raw_bearer_from_request(request)
    if not token:
        raise RuntimeError("速推图片生成需要登录 Bearer，用于上传参考图并调用 image.generate")
    ref_bytes = reference_path.read_bytes()
    public_url, diag = await _upload_bytes_to_auth_server(
        ref_bytes,
        reference_path.name,
        "image/jpeg",
        request,
        timeout=120.0,
    )
    if not public_url:
        raise RuntimeError(f"参考图上传到速推可访问地址失败：{diag}")
    cache_map[cache_key] = public_url
    preprocessing["reference_public_urls"] = cache_map
    preprocessing["reference_public_url"] = public_url
    store.update_job(job_id, preprocessing=preprocessing)
    return public_url


async def _generate_sutui_image_stage(
    *,
    job_id: str,
    request: Request,
    prompt: str,
    model: str,
    aspect_ratio: str,
    reference_path: Path,
    preprocessing: Dict[str, Any],
) -> Dict[str, Any]:
    reference_url = await _reference_image_public_url(
        job_id=job_id,
        request=request,
        reference_path=reference_path,
        preprocessing=preprocessing,
    )
    ratio = _image_size_for_sutui(aspect_ratio)
    pixel_size = _image_pixel_size_for_stage(aspect_ratio)
    payload = {
        "prompt": _highest_quality_image_prompt(prompt, aspect_ratio=aspect_ratio),
        "model": _canonical_image_model(model),
        "image_url": reference_url,
        "image_size": ratio,
        "aspect_ratio": ratio,
        "size": pixel_size,
        "pixel_size": pixel_size,
        "resolution": _AI3D_IMAGE_STAGE_RESOLUTION,
        "resolution_level": _AI3D_IMAGE_STAGE_RESOLUTION,
        "quality": _AI3D_IMAGE_STAGE_QUALITY,
        "image_quality": _AI3D_IMAGE_STAGE_QUALITY,
        "quality_preset": "highest",
        "render_quality": "production",
        "output_quality": 100,
        "num_images": 1,
        "output_format": _AI3D_IMAGE_STAGE_OUTPUT_FORMAT,
    }
    submit_args = {"capability_id": "image.generate", "payload": payload}
    submit_text = await _call_mcp_tool(request, "invoke_capability", submit_args, timeout_seconds=25 * 60.0)
    preview = _extract_generated_image_preview(submit_text)
    if preview:
        return {
            "images": [preview],
            "provider": "sutui",
            "model": payload["model"],
            "quality": _AI3D_IMAGE_STAGE_QUALITY,
            "resolution": _AI3D_IMAGE_STAGE_RESOLUTION,
            "output_format": _AI3D_IMAGE_STAGE_OUTPUT_FORMAT,
            "size": pixel_size,
            "reference_url": reference_url,
            "raw_result": submit_text,
        }
    task_id = _extract_task_id_from_result(submit_text)
    if not task_id:
        raise RuntimeError(f"速推 image.generate 未返回图片或 task_id：{submit_text[:800]}")
    poll_args = {
        "capability_id": "task.get_result",
        "payload": {"task_id": task_id, "capability_id": "image.generate"},
    }
    final_text = submit_text
    max_wait = 25 * 60
    waited = 0
    while waited <= max_wait:
        store.update_job(job_id, stage=f"polling_sutui_image_{task_id[:12]}", error=None)
        final_text = await _call_mcp_tool(request, "invoke_capability", poll_args, timeout_seconds=35 * 60.0)
        if not _is_task_result_in_progress(final_text):
            break
        await asyncio.sleep(15)
        waited += 15
    if _is_task_result_in_progress(final_text):
        raise RuntimeError(f"速推 image.generate 查询超时，task_id={task_id}")
    preview = _extract_generated_image_preview(final_text)
    if not preview:
        raise RuntimeError(f"速推 image.generate 已结束但未返回图片：{final_text[:800]}")
    return {
        "images": [preview],
        "provider": "sutui",
        "model": payload["model"],
        "quality": _AI3D_IMAGE_STAGE_QUALITY,
        "resolution": _AI3D_IMAGE_STAGE_RESOLUTION,
        "output_format": _AI3D_IMAGE_STAGE_OUTPUT_FORMAT,
        "size": pixel_size,
        "task_id": task_id,
        "reference_url": reference_url,
        "raw_result": final_text,
    }


async def _generate_image_stage_core(
    *,
    job_id: str,
    request: Request,
    current_user: _ServerUser,
    db: Any,
    prompt: str,
    model: str,
    aspect_ratio: str,
    ref_payload: Dict[str, Any],
    reference_path: Path,
    preprocessing: Dict[str, Any],
) -> Dict[str, Any]:
    model_id = _canonical_image_model(model)
    prompt = _highest_quality_image_prompt(prompt, aspect_ratio=aspect_ratio)
    if _is_sutui_gpt_image_2(model_id):
        return await _generate_sutui_image_stage(
            job_id=job_id,
            request=request,
            prompt=prompt,
            model=model_id,
            aspect_ratio=aspect_ratio,
            reference_path=reference_path,
            preprocessing=preprocessing,
        )
    return await _generate_image_studio_core(
        request=request,
        current_user=current_user,
        db=db,
        prompt=prompt,
        model=model_id,
        aspect_ratio=aspect_ratio,
        quality=_AI3D_IMAGE_STAGE_QUALITY,
        background="auto",
        upload_payloads=[ref_payload],
        reference_image_urls=[],
        size_override=_image_pixel_size_for_stage(aspect_ratio),
        auto_save=False,
        timeout_seconds=600.0,
        max_attempts=3,
    )


async def _save_generated_preview_image(preview: Dict[str, str], dest: Path) -> Dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    data_url = str(preview.get("data_url") or "").strip()
    url = str(preview.get("url") or "").strip()
    tmp = dest.with_suffix(".tmp")
    if data_url:
        payload = data_url.split(",", 1)[-1]
        raw = base64.b64decode(payload)
        tmp.write_bytes(raw)
    elif url:
        await meshy.download_url(url, tmp)
    else:
        raise RuntimeError("image generation returned no downloadable image")
    with Image.open(tmp) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        if dest.suffix.lower() == ".png":
            im.save(dest, "PNG", optimize=True)
        else:
            im.save(dest, "JPEG", quality=94, optimize=True)
        width, height = im.width, im.height
    try:
        tmp.unlink(missing_ok=True)
    except Exception:
        pass
    return {"width": width, "height": height}


def _image_file_payload(path: Path, *, content_type: str = "image/jpeg") -> Dict[str, Any]:
    return {
        "filename": path.name,
        "content_type": content_type,
        "bytes": path.read_bytes(),
    }


def _copy_reference_front_view(source: Path, dest: Path) -> Dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.save(dest, "JPEG", quality=94, optimize=True)
        return {"width": im.width, "height": im.height}


def _split_side_back_sheet(sheet_path: Path, out_dir: Path, *, sheet_views: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    roles = _valid_sheet_views(sheet_views)
    labels = [(role, _VIEW_ROLE_LABELS.get(role, role)) for role in roles]
    results: List[Dict[str, Any]] = []
    with Image.open(sheet_path) as im:
        source = ImageOps.exif_transpose(im).convert("RGB")
        width, height = source.size
        cols = len(labels)
        gap = max(0, int(width * 0.010))
        for idx, (role, label) in enumerate(labels, start=1):
            left = int(round(width * (idx - 1) / cols)) + gap
            right = int(round(width * idx / cols)) - gap
            crop = source.crop((max(0, left), 0, min(width, right), height))
            dest = out_dir / f"{idx:02d}_{role}.jpg"
            crop.save(dest, "JPEG", quality=94, optimize=True)
            results.append({
                "index": idx,
                "role": role,
                "label": label,
                "path": dest,
                "width": crop.width,
                "height": crop.height,
                "source_box": [max(0, left), 0, min(width, right), height],
            })
    return results


def _make_triview_reference_sheet(job_id: str, triview_inputs: List[Dict[str, Any]], dest: Path) -> Optional[Dict[str, Any]]:
    usable = []
    for item in triview_inputs:
        role = str(item.get("role") or "")
        path = Path(str(item.get("normalized_path") or ""))
        if role in _VIEW_ROLE_ORDER and path.exists():
            usable.append((_VIEW_ROLE_ORDER[role], role, str(item.get("label") or _VIEW_ROLE_LABELS.get(role, role)), path))
    usable.sort(key=lambda row: row[0])
    if len(usable) < 2:
        return None
    cell_w = 720
    cell_h = 1080
    label_h = 54
    canvas = Image.new("RGB", (cell_w * len(usable), cell_h + label_h), (238, 238, 232))
    for col, (_, role, label, path) in enumerate(usable):
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((cell_w - 36, cell_h - 36), _LANCZOS)
            x = col * cell_w + (cell_w - im.width) // 2
            y = label_h + (cell_h - im.height) // 2
            canvas.paste(im, (x, y))
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, "JPEG", quality=94, optimize=True)
    return {"width": canvas.width, "height": canvas.height, "view_count": len(usable)}


def _make_unlabeled_reference_board(paths: List[Path], dest: Path, *, cell: Tuple[int, int] = (900, 900)) -> Optional[Dict[str, Any]]:
    usable = [path for path in paths if isinstance(path, Path) and path.is_file()]
    if not usable:
        return None
    cell_w, cell_h = cell
    cols = len(usable)
    canvas = Image.new("RGB", (cell_w * cols, cell_h), (238, 238, 232))
    for col, path in enumerate(usable):
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((cell_w - 42, cell_h - 42), _LANCZOS)
            x = col * cell_w + (cell_w - im.width) // 2
            y = (cell_h - im.height) // 2
            canvas.paste(im, (x, y))
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, "JPEG", quality=94, optimize=True)
    return {"width": canvas.width, "height": canvas.height, "reference_count": len(usable)}


def _make_multiview_review_sheet(reference_path: Path, generated_inputs: List[Dict[str, Any]], dest: Path) -> Dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    role_order = {role: idx for idx, role in enumerate(_CHARACTER_MULTI_VIEW_SHEET_VIEWS)}
    items = [
        item for item in generated_inputs
        if isinstance(item, dict)
        and str(item.get("role") or "") in role_order
        and Path(str(item.get("normalized_path") or "")).is_file()
    ]
    items.sort(key=lambda item: role_order.get(str(item.get("role") or ""), 99))
    cell_w = 640
    cell_h = 520
    cols = 3
    rows = 1 + max(1, (len(items) + cols - 1) // cols)
    canvas = Image.new("RGB", (cols * cell_w, rows * cell_h), (246, 246, 244))
    draw = ImageDraw.Draw(canvas)

    def paste_fit(path: Path, cell_x: int, cell_y: int, label: str, span_cols: int = 1) -> None:
        area_w = cell_w * span_cols
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((area_w - 36, cell_h - 62), _LANCZOS)
            x = cell_x + (area_w - im.width) // 2
            y = cell_y + 38 + (cell_h - 62 - im.height) // 2
            canvas.paste(im, (x, y))
        draw.text((cell_x + 14, cell_y + 12), label[:80], fill=(28, 32, 36))

    paste_fit(reference_path, 0, 0, "REFERENCE SOURCE / PRIMARY VISIBLE DESIGN", span_cols=cols)
    for idx, item in enumerate(items):
        row = 1 + idx // cols
        col = idx % cols
        role = str(item.get("role") or "")
        paste_fit(Path(str(item.get("normalized_path") or "")), col * cell_w, row * cell_h, f"GENERATED {role}")
    canvas.save(dest, "JPEG", quality=94, optimize=True)
    return {
        "width": canvas.width,
        "height": canvas.height,
        "generated_count": len(items),
        "roles": [str(item.get("role") or "") for item in items],
    }


def _make_fidelity_component_sheet(parts: List[Dict[str, Any]], dest: Path) -> Dict[str, Any]:
    cols = 4
    rows = 2
    cell = 640
    canvas = Image.new("RGB", (cols * cell, rows * cell), (238, 238, 232))
    for idx, item in enumerate(parts[: cols * rows]):
        path = Path(str(item.get("normalized_path") or ""))
        if not path.exists():
            continue
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((cell - 36, cell - 36), _LANCZOS)
            col = idx % cols
            row = idx // cols
            x = col * cell + (cell - im.width) // 2
            y = row * cell + (cell - im.height) // 2
            canvas.paste(im, (x, y))
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, "JPEG", quality=94, optimize=True)
    return {"width": canvas.width, "height": canvas.height}


def _alpha_bbox(im: Image.Image, *, threshold: int = 8) -> Optional[Tuple[int, int, int, int]]:
    if im.mode != "RGBA":
        return None
    alpha = im.getchannel("A").point(lambda value: 255 if value > threshold else 0)
    return alpha.getbbox()


def _postprocess_alpha_matte(im: Image.Image) -> Image.Image:
    rgba = im.convert("RGBA")
    alpha = rgba.getchannel("A")
    alpha = alpha.filter(ImageFilter.MedianFilter(size=3))
    hard = alpha.point(lambda value: 255 if value > 24 else 0)
    hard = hard.filter(ImageFilter.MaxFilter(size=3)).filter(ImageFilter.MinFilter(size=3))
    smooth = hard.filter(ImageFilter.GaussianBlur(radius=0.9))
    rgba.putalpha(smooth)
    return rgba


def _get_rembg_session() -> Any:
    global _REMBG_SESSION
    if _REMBG_SESSION is not None:
        return _REMBG_SESSION
    try:
        from rembg import new_session  # type: ignore
    except Exception as exc:
        raise RuntimeError("rembg 未安装，无法执行真实抠图拆件") from exc
    _REMBG_SESSION = new_session("u2net")
    return _REMBG_SESSION


def _remove_background_to_alpha(source_path: Path, dest_path: Path) -> Dict[str, Any]:
    try:
        from rembg import remove  # type: ignore
    except Exception as exc:
        raise RuntimeError("rembg 未安装，无法执行真实抠图拆件") from exc
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    session = _get_rembg_session()
    with Image.open(source_path) as im:
        source = ImageOps.exif_transpose(im).convert("RGBA")
    result = remove(
        source,
        session=session,
        alpha_matting=True,
        alpha_matting_foreground_threshold=240,
        alpha_matting_background_threshold=10,
        alpha_matting_erode_size=8,
        post_process_mask=True,
    )
    if not isinstance(result, Image.Image):
        result = Image.open(result).convert("RGBA")
    result = _postprocess_alpha_matte(ImageOps.exif_transpose(result).convert("RGBA"))
    bbox = _alpha_bbox(result)
    if not bbox:
        raise RuntimeError(f"抠图没有得到可用前景：{source_path.name}")
    pad = max(14, int(max(bbox[2] - bbox[0], bbox[3] - bbox[1]) * 0.08))
    padded_box = (
        max(0, bbox[0] - pad),
        max(0, bbox[1] - pad),
        min(result.width, bbox[2] + pad),
        min(result.height, bbox[3] + pad),
    )
    cropped = result.crop(padded_box)
    side = max(cropped.width, cropped.height, 512)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.alpha_composite(cropped, ((side - cropped.width) // 2, (side - cropped.height) // 2))
    canvas.save(dest_path, "PNG")
    alpha = canvas.getchannel("A")
    fg_pixels = sum(1 for value in alpha.getdata() if value > 8)
    fg_ratio = fg_pixels / float(max(1, canvas.width * canvas.height))
    if fg_ratio < 0.015:
        raise RuntimeError(f"抠图前景过少，可能不是有效部件：{source_path.name}")
    return {
        "width": canvas.width,
        "height": canvas.height,
        "source_box": list(padded_box),
        "alpha_foreground_ratio": round(fg_ratio, 4),
        "crop_applied": True,
    }


def _make_alpha_component_sheet(parts: List[Dict[str, Any]], dest: Path) -> Dict[str, Any]:
    cols = 4
    rows = 2
    cell = 640
    canvas = Image.new("RGBA", (cols * cell, rows * cell), (238, 238, 232, 255))
    checker_light = (248, 250, 252, 255)
    checker_dark = (226, 232, 240, 255)
    for idx, item in enumerate(parts[: cols * rows]):
        path = Path(str(item.get("normalized_path") or ""))
        if not path.exists():
            continue
        col = idx % cols
        row = idx // cols
        x0 = col * cell
        y0 = row * cell
        for yy in range(y0, y0 + cell, 40):
            for xx in range(x0, x0 + cell, 40):
                color = checker_light if ((xx - x0) // 40 + (yy - y0) // 40) % 2 == 0 else checker_dark
                canvas.paste(color, (xx, yy, min(xx + 40, x0 + cell), min(yy + 40, y0 + cell)))
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGBA")
            im.thumbnail((cell - 48, cell - 48), _LANCZOS)
            x = x0 + (cell - im.width) // 2
            y = y0 + (cell - im.height) // 2
            canvas.alpha_composite(im, (x, y))
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, "PNG")
    return {"width": canvas.width, "height": canvas.height}


_SEE_THROUGH_ROLE_LABELS = {
    "headwear": "头部/帽子/头饰",
    "face": "脸部/面板",
    "neckwear": "围巾/颈部配饰",
    "topwear": "上衣/胸腹外层",
    "handwear": "手臂/手套",
    "bottomwear": "腰部/下装",
    "legwear": "腿部",
    "footwear": "靴子/脚部",
    "objects": "随身道具/配件",
    "wings": "翅膀/背部附属件",
    "tail": "尾部附属件",
    "eyewear": "眼镜/面部附件",
    "earwear": "耳饰/耳侧附件",
    "front hair": "前发",
    "back hair": "后发",
}
_SEE_THROUGH_ROLE_PRIORITY = [
    "headwear",
    "face",
    "neckwear",
    "topwear",
    "handwear",
    "bottomwear",
    "legwear",
    "footwear",
    "objects",
    "wings",
    "tail",
    "eyewear",
    "earwear",
    "front hair",
    "back hair",
]
_SEE_THROUGH_TINY_DETAIL_ROLES = {
    "eyebrow",
    "eyelash",
    "irides",
    "eyewhite",
    "ears",
    "nose",
    "mouth",
    "neck",
}


def _safe_component_role_name(value: str, fallback: str) -> str:
    role = re.sub(r"[^a-z0-9_\-]+", "_", (value or "").strip().lower()).strip("_-")
    return role[:56] or fallback


def _see_through_artifact(job_id: str, path_value: str, *, label: str, kind: str) -> Optional[Dict[str, Any]]:
    path = Path(str(path_value or ""))
    if not path.is_file():
        return None
    try:
        url = _job_file_url(job_id, path)
    except Exception:
        return None
    return {
        "kind": kind,
        "label": label,
        "filename": path.name,
        "size": path.stat().st_size,
        "url": url,
    }


def _center_see_through_layer(layer_path: Path, dest: Path, *, source_box: Any, frame_size: Any) -> Dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(layer_path) as im:
        layer = ImageOps.exif_transpose(im).convert("RGBA")
    alpha = layer.getchannel("A")
    bbox = alpha.getbbox()
    if not bbox:
        raise RuntimeError(f"see-through 图层没有有效 alpha：{layer_path.name}")
    pad = max(12, int(max(bbox[2] - bbox[0], bbox[3] - bbox[1]) * 0.08))
    crop_box = (
        max(0, bbox[0] - pad),
        max(0, bbox[1] - pad),
        min(layer.width, bbox[2] + pad),
        min(layer.height, bbox[3] + pad),
    )
    cropped = layer.crop(crop_box)
    side = max(cropped.width, cropped.height, 768)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.alpha_composite(cropped, ((side - cropped.width) // 2, (side - cropped.height) // 2))
    if canvas.width > 2048 or canvas.height > 2048:
        canvas.thumbnail((2048, 2048), _LANCZOS)
    canvas.save(dest, "PNG")
    out_alpha = canvas.getchannel("A")
    fg_pixels = sum(1 for value in out_alpha.getdata() if value > 8)
    fg_ratio = fg_pixels / float(max(1, canvas.width * canvas.height))
    frame_h = 0
    frame_w = 0
    if isinstance(frame_size, list) and len(frame_size) >= 2:
        try:
            frame_h = int(frame_size[0] or 0)
            frame_w = int(frame_size[1] or 0)
        except Exception:
            frame_h = frame_w = 0
    if not frame_w:
        frame_w = layer.width
    if not frame_h:
        frame_h = layer.height
    box = source_box if isinstance(source_box, list) and len(source_box) >= 4 else list(bbox)
    rel_box = [
        float(box[0]) / max(1, frame_w),
        float(box[1]) / max(1, frame_h),
        float(box[2]) / max(1, frame_w),
        float(box[3]) / max(1, frame_h),
    ]
    return {
        "width": canvas.width,
        "height": canvas.height,
        "source_box": [int(float(v)) for v in box[:4]],
        "source_width": frame_w,
        "source_height": frame_h,
        "relative_source_box": rel_box,
        "crop_box": list(crop_box),
        "alpha_matting": True,
        "alpha_foreground_ratio": round(fg_ratio, 5),
        "crop_applied": True,
        "mesh_input_kind": "see_through_layer_crop",
    }


def _select_see_through_layers(layers: List[Dict[str, Any]], *, max_parts: int) -> List[Dict[str, Any]]:
    by_tag = {str(item.get("tag") or ""): item for item in layers if isinstance(item, dict)}
    selected: List[Dict[str, Any]] = []
    for tag in _SEE_THROUGH_ROLE_PRIORITY:
        item = by_tag.get(tag)
        if item:
            selected.append(item)
    extras = [
        item for item in layers
        if isinstance(item, dict)
        and str(item.get("tag") or "") not in _SEE_THROUGH_TINY_DETAIL_ROLES
        and item not in selected
    ]
    extras.sort(key=lambda item: str(item.get("tag") or ""))
    selected.extend(extras)
    return selected[: max(1, min(_AI3D_ABSOLUTE_MAX_PARTS, int(max_parts or _AI3D_DEFAULT_MAX_PARTS)))]


async def _run_see_through_component_split(
    *,
    job_id: str,
    job: Dict[str, Any],
    preprocessing: Dict[str, Any],
    source_for_plan: Dict[str, Any],
    reference_path: Path,
) -> None:
    root = store.job_dir(job_id)
    max_parts = int(preprocessing.get("max_parts") or _AI3D_DEFAULT_MAX_PARTS)
    component_dir = root / "components" / "see_through"
    raw_dir = component_dir / "raw"
    mesh_dir = component_dir / "mesh_inputs"
    sheet_path = component_dir / "component_see_through_sheet.png"
    store.update_job(
        job_id,
        status="splitting_parts",
        stage="running_see_through_layer_split",
        progress=28,
        preprocessing=preprocessing,
        error=None,
    )
    result = await see_through.run_layer_decomposition(
        source_path=reference_path,
        save_dir=raw_dir,
        resolution=1280,
        timeout_seconds=3600,
    )
    layers = result.get("layers") if isinstance(result.get("layers"), list) else []
    selected_layers = _select_see_through_layers(layers, max_parts=max_parts)
    if len(selected_layers) < 4:
        raise RuntimeError(f"see-through 有效语义层不足：{len(selected_layers)}")
    component_inputs: List[Dict[str, Any]] = []
    part_slots: List[Tuple[str, str]] = []
    seen_roles: set[str] = set()
    frame_size = result.get("frame_size")
    for idx, layer in enumerate(selected_layers, start=1):
        tag = str(layer.get("tag") or f"part_{idx:02d}")
        role = _safe_component_role_name(tag, f"part_{idx:02d}")
        if role in seen_roles:
            role = f"{role}_{idx:02d}"
        seen_roles.add(role)
        label = _SEE_THROUGH_ROLE_LABELS.get(tag, tag)
        layer_path = Path(str(layer.get("path") or ""))
        if not layer_path.is_file():
            continue
        dest = mesh_dir / f"{idx:02d}_{role}.png"
        meta = _center_see_through_layer(
            layer_path,
            dest,
            source_box=layer.get("xyxy"),
            frame_size=frame_size,
        )
        part_input = _public_input(
            job_id=job_id,
            index=idx,
            filename=dest.name,
            normalized_path=dest,
            meta=meta,
            role=role,
            label=label,
            source_filename=str(source_for_plan.get("filename") or ""),
            generated=True,
        )
        part_input.update({
            "see_through_layer": True,
            "see_through_tag": tag,
            "depth_median": layer.get("depth_median"),
            "part_id": layer.get("part_id"),
            "mesh_input_kind": "see_through_layer_crop",
            "component_source_mode": "see_through_psd_layers",
            "raw_layer_url": _job_file_url(job_id, layer_path),
            "depth_url": _job_file_url(job_id, Path(str(layer.get("depth_path")))) if layer.get("depth_path") and Path(str(layer.get("depth_path"))).is_file() else "",
        })
        component_inputs.append(part_input)
        part_slots.append((role, label))
    if len(component_inputs) < 4:
        raise RuntimeError(f"see-through 可用部件输入图不足：{len(component_inputs)}")
    sheet_meta = _make_alpha_component_sheet(component_inputs, sheet_path)
    sheet_input = _public_input(
        job_id=job_id,
        index=0,
        filename=sheet_path.name,
        normalized_path=sheet_path,
        meta=sheet_meta,
        role="component_sheet",
        label="See-through PSD 语义分层部件板",
        source_filename=str(source_for_plan.get("filename") or ""),
        generated=True,
    )
    artifacts = [
        item for item in [
            _see_through_artifact(job_id, str(result.get("psd_path") or ""), label="See-through PSD 分层文件", kind="psd"),
            _see_through_artifact(job_id, str(result.get("depth_psd_path") or ""), label="See-through Depth PSD", kind="psd_depth"),
            _see_through_artifact(job_id, str(result.get("json_path") or ""), label="See-through 图层元数据", kind="json"),
            _see_through_artifact(job_id, str(result.get("reconstruction_path") or ""), label="See-through 重建预览", kind="preview"),
            _see_through_artifact(job_id, str(result.get("source_preview_path") or ""), label="See-through 输入预览", kind="preview"),
        ]
        if item
    ]
    gate_passed, gate_meta = _component_sheet_quality_gate(component_inputs, sheet_meta, part_slots=part_slots)
    gate_meta["source"] = "see_through_psd_layers"
    gate_meta["raw_layer_count"] = len(layers)
    preprocessing["component_quality_gate"] = "passed" if gate_passed else "failed"
    preprocessing["component_quality_gate_meta"] = gate_meta
    preprocessing["component_slots"] = [{"role": role, "label": label} for role, label in part_slots]
    preprocessing["component_sheet"] = sheet_input
    preprocessing["component_inputs"] = component_inputs
    preprocessing["component_inputs_partial"] = component_inputs
    preprocessing["component_sheet_partial"] = sheet_input
    preprocessing["component_source_mode"] = "see_through_psd_layers"
    preprocessing["component_mesh_input_mode"] = "see_through_layer_crops"
    preprocessing["component_split_generated"] = True
    preprocessing["see_through_result"] = {
        "resolution": result.get("resolution"),
        "frame_size": frame_size,
        "raw_layer_count": len(layers),
        "selected_layer_count": len(component_inputs),
        "artifacts": artifacts,
    }
    if not gate_passed:
        raise RuntimeError(f"see-through 部件质量门未通过：{json.dumps(gate_meta, ensure_ascii=False)}")
    notes = list(job.get("quality_notes") or [])
    notes.append("已使用 see-through 生成 PSD/2.5D 语义分层，并将透明图层转成居中部件输入图；未使用 GPT Image 2 重绘拆件。")
    notes.append("PSD、Depth PSD、原始透明层和重建预览会随当前任务资源包一起下载。")
    store.update_job(
        job_id,
        status="preprocessed",
        stage="component_split_completed",
        progress=100,
        inputs=component_inputs,
        mode="part_batch",
        strategy="part_batch",
        provider="meshy",
        final_3d_provider="meshy",
        image_stage_provider="see_through",
        preprocessing=preprocessing,
        quality_notes=notes,
    )


def _alpha_component_inputs(job_id: str, preprocessing: Dict[str, Any], root: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    region_inputs = _best_component_region_inputs(job_id, preprocessing, root)
    if not region_inputs:
        raise RuntimeError("找不到区域候选图，请先完成预处理")
    role_order = [
        "head_face_headwear",
        "neck_shoulders_upper",
        "torso_waist_core",
        "left_arm_hand",
        "right_arm_hand",
        "lower_body_hips",
        "legs_feet",
        "full_body",
    ]
    by_role = {str(item.get("role") or ""): item for item in region_inputs if isinstance(item, dict)}
    selected = [by_role[role] for role in role_order if role in by_role]
    limit = max(1, min(_AI3D_ABSOLUTE_MAX_PARTS, int(preprocessing.get("max_parts") or _AI3D_DEFAULT_MAX_PARTS)))
    if len(selected) < 4:
        selected = region_inputs[:limit]
    out_dir = root / "components" / "alpha_parts"
    component_inputs: List[Dict[str, Any]] = []
    failures: List[str] = []
    for item in selected[:limit]:
        src = Path(str(item.get("normalized_path") or ""))
        if not src.exists():
            continue
        role = str(item.get("role") or f"part_{len(component_inputs) + 1:02d}")
        dest = out_dir / f"{len(component_inputs) + 1:02d}_{role}.png"
        try:
            meta = _remove_background_to_alpha(src, dest)
        except Exception as exc:
            failures.append(f"{role}: {exc}")
            continue
        component_inputs.append(_public_input(
            job_id=job_id,
            index=len(component_inputs) + 1,
            filename=dest.name,
            normalized_path=dest,
            meta={
                **meta,
                "alpha_matting": True,
                "source_box": item.get("source_box") or meta.get("source_box"),
            },
            role=role,
            label=str(item.get("label") or role),
            source_filename=str(item.get("source_filename") or ""),
            generated=True,
        ))
    if len(component_inputs) < 3:
        detail = "；".join(failures[:3]) if failures else "有效透明部件少于 3 个"
        raise RuntimeError(f"透明语义部件生成不足：{detail}")
    sheet_path = root / "components" / "component_alpha_sheet.png"
    sheet_meta = _make_alpha_component_sheet(component_inputs, sheet_path)
    sheet_input = _public_input(
        job_id=job_id,
        index=0,
        filename=sheet_path.name,
        normalized_path=sheet_path,
        meta=sheet_meta,
        role="component_alpha_sheet",
        label="透明部件板",
        source_filename=str((selected[0] if selected else {}).get("source_filename") or ""),
        generated=True,
    )
    sheet_input["alpha_matting"] = True
    for item in component_inputs:
        item["alpha_matting"] = True
        item["component_source_mode"] = "rembg_alpha_matting"
    return sheet_input, component_inputs


def _fidelity_reference_inputs(job_id: str, preprocessing: Dict[str, Any], root: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    region_inputs = _best_component_region_inputs(job_id, preprocessing, root)
    if not region_inputs:
        raise RuntimeError("找不到参考裁切候选图，请先完成预处理")
    role_order = [
        "head_face_headwear",
        "neck_shoulders_upper",
        "torso_waist_core",
        "left_arm_hand",
        "right_arm_hand",
        "lower_body_hips",
        "legs_feet",
        "full_body",
    ]
    by_role = {str(item.get("role") or ""): item for item in region_inputs if isinstance(item, dict)}
    selected = [by_role[role] for role in role_order if role in by_role]
    limit = max(1, min(_AI3D_ABSOLUTE_MAX_PARTS, int(preprocessing.get("max_parts") or _AI3D_DEFAULT_MAX_PARTS)))
    if len(selected) < 4:
        selected = region_inputs[:limit]
    sheet_path = root / "components" / "component_reference_sheet.jpg"
    source_filename = str((selected[0] if selected else {}).get("source_filename") or "")
    sheet_meta = _make_fidelity_component_sheet(selected, sheet_path)
    sheet_input = _public_input(
        job_id=job_id,
        index=0,
        filename=sheet_path.name,
        normalized_path=sheet_path,
        meta=sheet_meta,
        role="component_reference_sheet",
        label="裁切参考板（非真实拆件）",
        source_filename=source_filename,
        generated=True,
    )
    reference_inputs: List[Dict[str, Any]] = []
    for item in selected[:limit]:
        part_path = Path(str(item.get("normalized_path") or ""))
        if not part_path.exists():
            continue
        reference_inputs.append(_public_input(
            job_id=job_id,
            index=len(reference_inputs) + 1,
            filename=part_path.name,
            normalized_path=part_path,
            meta={
                "width": item.get("width"),
                "height": item.get("height"),
                "source_box": item.get("source_box"),
                "crop_applied": False,
            },
            role=str(item.get("role") or "component_reference"),
            label=str(item.get("label") or part_path.stem),
            source_filename=str(item.get("source_filename") or ""),
            generated=False,
        ))
    return sheet_input, reference_inputs


async def _save_upload_file(file: UploadFile, target: Path, *, max_bytes: int) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with target.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(status_code=413, detail=f"{file.filename or 'file'} is too large")
            out.write(chunk)
    if total <= 0:
        raise HTTPException(status_code=400, detail=f"{file.filename or 'file'} is empty")
    return total


def _extract_zip_images(zip_path: Path, work_dir: Path) -> List[Path]:
    out_dir = work_dir / "zip_images"
    out_dir.mkdir(parents=True, exist_ok=True)
    images: List[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        infos = [info for info in zf.infolist() if not info.is_dir() and _is_image_name(info.filename)]
        infos.sort(key=lambda info: info.filename.lower())
        for idx, info in enumerate(infos[:_MAX_INPUT_IMAGES], start=1):
            if info.file_size > _MAX_UPLOAD_BYTES:
                continue
            raw_name = _safe_name(info.filename, fallback=f"zip-image-{idx:02d}")
            raw_path = out_dir / f"{idx:02d}-{raw_name}"
            with zf.open(info) as src, raw_path.open("wb") as dest:
                dest.write(src.read())
            images.append(raw_path)
    return images


def _output_url(job_id: str, filename: str) -> str:
    return f"/api/ai-3d-model/jobs/{job_id}/files/{filename}"


def _job_file_url(job_id: str, path: Path) -> str:
    rel = path.resolve().relative_to(store.job_dir(job_id).resolve()).as_posix()
    return _output_url(job_id, rel)


def _quality_notes(*, strategy: str, image_count: int, quality: str) -> List[str]:
    notes = []
    if image_count == 1:
        notes.append("单图会补猜背面和厚度，复杂镂空/翅膀/反光金属建议改用多视角或拆件包。")
    if strategy == "part_batch":
        notes.append("拆件包会逐个部件生成模型，适合头盔、翅膀、饰件等复杂组合资产，后续需要在 Blender/ZBrush 拼装修形。")
    elif image_count >= 2:
        notes.append("多视角同一物体会比单图更稳定，建议图片分别覆盖正面、侧面、背面或顶部。")
    if quality == "production":
        notes.append("生产质量模式默认开启贴图、PBR 和重拓扑；本流程不提供低精度 3D 分支。")
    return notes


def _preprocess_notes(
    *,
    source_count: int,
    generated_part_count: int,
    preprocess_only: bool,
    source_inputs: Optional[List[Dict[str, Any]]] = None,
) -> List[str]:
    notes: List[str] = []
    for item in source_inputs or []:
        width = int(item.get("width") or 0)
        height = int(item.get("height") or 0)
        if width and height and (width < 800 or height < 1200):
            notes.append("主体裁切后的有效分辨率偏低，高清还原会依赖模型补细节；建议尽量提供原图、高清图或多角度图。")
            break
    if source_count == 1 and generated_part_count:
        notes.append("已从单张图生成区域裁切候选图；这不是语义拆件，仅用于选择主体、定位和辅助生成。")
        notes.append("如需高质量自动流程，优先生成高清多视角后走 Multi-Image to 3D。")
        notes.append("复杂场景请先选择真正要生成的主体；不要直接用整张场景图生成多视角。")
        notes.append("真正拆件必须来自用户拆件包、硬表面/饰件拆件板或高质量分割模型；角色服装默认走多视角，不强行拆袖子和下摆。")
    if preprocess_only:
        notes.append("当前为仅预处理预览，不会调用 3D 生成模型，也不会消耗 Meshy credits。")
        notes.append("多视角和 AI 部件分离属于图片模型阶段；只有点击“确认输入并生成 3D”后才调用 Meshy。")
    return notes


def _input_roles(inputs: Any) -> List[str]:
    if not isinstance(inputs, list):
        return []
    return [str(item.get("role") or "") for item in inputs if isinstance(item, dict)]


def _looks_like_region_candidates(inputs: Any) -> bool:
    roles = [role for role in _input_roles(inputs) if role]
    return bool(roles) and all(role in _REGION_CANDIDATE_ROLES for role in roles)


def _looks_like_triview_inputs(inputs: Any) -> bool:
    roles = [role for role in _input_roles(inputs) if role in _VIEW_ROLE_ORDER]
    role_set = set(roles)
    if {"front", "side", "back"}.issubset(role_set):
        return True
    return "front" in role_set and len(role_set) >= 2


def _multiview_quality_gate_error(job: Dict[str, Any]) -> str:
    preprocessing = job.get("preprocessing") if isinstance(job.get("preprocessing"), dict) else {}
    if not preprocessing.get("triview_generated"):
        return ""
    if preprocessing.get("triview_quality_gate") in {"passed", "partial_pass"}:
        return ""
    verification = preprocessing.get("triview_consistency_verification")
    if isinstance(verification, dict):
        return (
            f"多视角一致性复核未通过：score={verification.get('score')}, "
            f"issues={verification.get('issues')}。请重新生成多视角或提供真实多视角图。"
        )
    return "当前多视角由旧流程生成，未经过一致性复核；请重新生成多视角后再生成 3D。"


def _has_crop_reference_components(job: Dict[str, Any]) -> bool:
    preprocessing = job.get("preprocessing") if isinstance(job.get("preprocessing"), dict) else {}
    mode = str(preprocessing.get("component_reference_mode") or "")
    if mode in _CROP_REFERENCE_MODES:
        return True
    inputs = job.get("inputs") if isinstance(job.get("inputs"), list) else []
    if preprocessing.get("component_split_generated") and _looks_like_region_candidates(inputs):
        return True
    return bool(preprocessing.get("generated_part_count") and _looks_like_region_candidates(inputs) and len(preprocessing.get("source_inputs") or []) == 1)


def _has_true_component_source(job: Dict[str, Any]) -> bool:
    preprocessing = job.get("preprocessing") if isinstance(job.get("preprocessing"), dict) else {}
    source_mode = str(preprocessing.get("component_source_mode") or "")
    if source_mode in _TRUE_COMPONENT_SOURCE_MODES:
        return True
    if str(preprocessing.get("component_quality_gate") or "") != "passed":
        return False
    inputs = job.get("inputs") if isinstance(job.get("inputs"), list) else []
    if _has_crop_reference_components(job):
        return False
    if str(job.get("strategy") or "") != "part_batch":
        return True
    source_inputs = preprocessing.get("source_inputs") if isinstance(preprocessing.get("source_inputs"), list) else []
    return len(inputs) >= 2 and len(source_inputs) >= 2


def _component_quality_gate(preprocessing: Dict[str, Any], component_inputs: List[Dict[str, Any]]) -> Tuple[bool, Dict[str, Any]]:
    plan_ok = preprocessing.get("component_plan_source") == "image.understand"
    ratios = [float(item.get("alpha_foreground_ratio") or 0) for item in component_inputs if isinstance(item, dict)]
    weak = [item.get("role") for item in component_inputs if float(item.get("alpha_foreground_ratio") or 0) < 0.10]
    good_count = sum(1 for value in ratios if value >= 0.10)
    passed = bool(plan_ok and len(component_inputs) >= 4 and good_count >= max(4, int(len(component_inputs) * 0.75)) and not weak)
    return passed, {
        "plan_ok": plan_ok,
        "component_count": len(component_inputs),
        "good_alpha_count": good_count,
        "weak_roles": weak,
        "min_alpha_foreground_ratio": min(ratios) if ratios else 0,
    }


def _component_sheet_quality_gate(
    component_inputs: List[Dict[str, Any]],
    sheet_meta: Dict[str, Any],
    *,
    part_slots: Optional[List[Tuple[str, str]]] = None,
) -> Tuple[bool, Dict[str, Any]]:
    roles = [str(item.get("role") or "") for item in component_inputs if isinstance(item, dict)]
    valid_roles = [role for role in roles if role and role != "component_sheet"]
    sizes = [
        (int(item.get("width") or 0), int(item.get("height") or 0))
        for item in component_inputs
        if isinstance(item, dict)
    ]
    min_side = min([min(w, h) for w, h in sizes if w and h] or [0])
    expected_slots = part_slots or list(_CHARACTER_AI_PARTS)
    missing_roles = [role for role, _ in expected_slots if role not in valid_roles]
    min_required = max(1, min(len(expected_slots), 6))
    passed = bool(
        len(component_inputs) >= min_required
        and len(set(valid_roles)) >= min_required
        and min_side >= 512
        and int(sheet_meta.get("width") or 0) >= 1400
        and int(sheet_meta.get("height") or 0) >= 700
    )
    return passed, {
        "component_count": len(component_inputs),
        "expected_count": len(expected_slots),
        "unique_roles": len(set(valid_roles)),
        "missing_roles": missing_roles,
        "min_component_side": min_side,
        "sheet_width": sheet_meta.get("width"),
        "sheet_height": sheet_meta.get("height"),
    }


def _safe_frontend_generation_inputs(job: Dict[str, Any], preprocessing: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates = [
        preprocessing.get("triview_inputs"),
        preprocessing.get("source_inputs"),
        job.get("inputs"),
    ]
    for candidate in candidates:
        if not isinstance(candidate, list) or not candidate:
            continue
        if _looks_like_region_candidates(candidate) and not _looks_like_triview_inputs(candidate):
            continue
        return candidate
    return []


def _public_input(
    *,
    job_id: str,
    index: int,
    filename: str,
    normalized_path: Path,
    meta: Dict[str, Any],
    role: str = "input",
    label: str = "",
    source_filename: str = "",
    generated: bool = False,
) -> Dict[str, Any]:
    rel = normalized_path.resolve().relative_to(store.job_dir(job_id).resolve()).as_posix()
    return {
        "index": index,
        "role": role,
        "label": label or filename,
        "filename": filename,
        "source_filename": source_filename or filename,
        "generated": generated,
        "normalized_path": str(normalized_path),
        "preview_url": _output_url(job_id, rel),
        "width": meta.get("width"),
        "height": meta.get("height"),
        "original_width": meta.get("original_width"),
        "original_height": meta.get("original_height"),
        "crop_box": meta.get("crop_box"),
        "crop_applied": bool(meta.get("crop_applied")),
        "source_box": meta.get("source_box"),
        "source_width": meta.get("source_width"),
        "source_height": meta.get("source_height"),
        "relative_source_box": meta.get("relative_source_box"),
        "alpha_matting": bool(meta.get("alpha_matting")),
        "alpha_foreground_ratio": meta.get("alpha_foreground_ratio"),
    }


def _copy_extra_input_fields(target: Dict[str, Any], source: Dict[str, Any], fields: Tuple[str, ...]) -> Dict[str, Any]:
    for field in fields:
        if field in source:
            target[field] = source.get(field)
    return target


def _step_status(job: Dict[str, Any], step: str) -> str:
    status = str(job.get("status") or "")
    stage = str(job.get("stage") or "")
    preprocessing = job.get("preprocessing") if isinstance(job.get("preprocessing"), dict) else {}
    outputs = job.get("outputs") if isinstance(job.get("outputs"), dict) else {}
    if step == "upload":
        return "done" if preprocessing.get("source_inputs") or job.get("inputs") else "pending"
    if step == "candidates":
        if int(preprocessing.get("generated_part_count") or 0) > 0:
            return "done"
        if len(preprocessing.get("source_inputs") or []) == 1:
            return "pending"
        return "skipped"
    if step == "triview":
        if preprocessing.get("triview_generated") or stage == "triview_completed":
            return "done"
        if stage == "triview_failed":
            return "failed"
        if status == "generating_views" or stage.startswith("generating_") or stage == "queued_triview":
            return "running"
        return "pending"
    if step == "components":
        if preprocessing.get("component_split_generated") or stage == "component_split_completed":
            return "done"
        if stage == "component_references_ready" or preprocessing.get("component_reference_mode") == "crop_reference_only":
            return "blocked"
        if stage == "component_split_failed" or preprocessing.get("component_quality_gate") == "failed":
            return "failed"
        if status == "splitting_parts" or stage.startswith("splitting_") or stage == "queued_component_split":
            return "running"
        return "pending"
    if step == "base_model":
        base_outputs = outputs.get("base") if isinstance(outputs.get("base"), dict) else {}
        if (base_outputs.get("files") if isinstance(base_outputs, dict) else None) or stage == "base_model_ready":
            return "done"
        if stage == "base_model_failed":
            return "failed"
        if stage in {"queued_base_model", "generating_base_model"}:
            return "running"
        return "pending"
    if step == "parts_3d":
        parts = outputs.get("parts") if isinstance(outputs.get("parts"), list) else []
        if parts or stage == "parts_3d_ready":
            return "done"
        if stage == "parts_3d_failed":
            return "failed"
        if stage == "queued_part_models" or stage == "generating_part_models" or stage.startswith("generating_part_") or stage.endswith("_already_done"):
            return "running"
        if preprocessing.get("component_split_generated"):
            return "pending"
        return "skipped"
    if step == "assembly":
        assembly = outputs.get("assembly") if isinstance(outputs.get("assembly"), dict) else {}
        if status == "succeeded" and assembly:
            return "done"
        if stage == "assembly_failed" or assembly.get("status") == "failed":
            return "failed"
        if stage in {"queued_part_assembly", "assembling_parts"}:
            return "running"
        parts = outputs.get("parts") if isinstance(outputs.get("parts"), list) else []
        if parts:
            return "pending"
        return "skipped"
    if step == "mesh":
        if status == "succeeded":
            return "done"
        if status == "failed":
            return "failed"
        if status == "running" or stage.startswith("generating_mesh") or stage.startswith("generating_part_") or stage.startswith("assembling"):
            return "running"
        if outputs.get("files") or outputs.get("parts"):
            return "done"
        return "pending"
    return "pending"


def _job_steps(job: Dict[str, Any]) -> List[Dict[str, Any]]:
    preprocessing = job.get("preprocessing") if isinstance(job.get("preprocessing"), dict) else {}
    source_inputs = preprocessing.get("source_inputs") if isinstance(preprocessing.get("source_inputs"), list) else []
    region_inputs = preprocessing.get("region_candidate_inputs") if isinstance(preprocessing.get("region_candidate_inputs"), list) else []
    triview_inputs = preprocessing.get("triview_inputs") if isinstance(preprocessing.get("triview_inputs"), list) else []
    if not triview_inputs:
        triview_inputs = preprocessing.get("triview_inputs_partial") if isinstance(preprocessing.get("triview_inputs_partial"), list) else []
    component_inputs = preprocessing.get("component_inputs") if isinstance(preprocessing.get("component_inputs"), list) else []
    component_reference_inputs = preprocessing.get("component_reference_inputs") if isinstance(preprocessing.get("component_reference_inputs"), list) else []
    component_sheet = preprocessing.get("component_sheet") if isinstance(preprocessing.get("component_sheet"), dict) else None
    component_sheet_partial = preprocessing.get("component_sheet_partial") if isinstance(preprocessing.get("component_sheet_partial"), dict) else None
    component_inputs_partial = preprocessing.get("component_inputs_partial") if isinstance(preprocessing.get("component_inputs_partial"), list) else []
    component_failed_preview_sheet = preprocessing.get("component_failed_preview_sheet") if isinstance(preprocessing.get("component_failed_preview_sheet"), dict) else None
    component_failed_preview_inputs = preprocessing.get("component_failed_preview_inputs") if isinstance(preprocessing.get("component_failed_preview_inputs"), list) else []
    component_reference_sheet = preprocessing.get("component_reference_sheet") if isinstance(preprocessing.get("component_reference_sheet"), dict) else None
    see_through_result = preprocessing.get("see_through_result") if isinstance(preprocessing.get("see_through_result"), dict) else {}
    see_through_artifacts = see_through_result.get("artifacts") if isinstance(see_through_result.get("artifacts"), list) else []
    component_status = _step_status(job, "components")
    triview_roles = [str(item.get("role") or "") for item in triview_inputs if isinstance(item, dict)]
    triview_summary = (
        f"{_sheet_view_names(triview_roles)}；这一步不调用 Meshy"
        if triview_roles
        else "角色五视角；硬表面/建筑默认参考主视角、左前45°、右前45°近邻视角；这一步不调用 Meshy"
    )
    components_passed = bool(
        preprocessing.get("component_split_generated")
        or str(job.get("stage") or "") == "component_split_completed"
        or preprocessing.get("component_quality_gate") == "passed"
    )
    component_summary = "只生成 2D 部件输入图；完整 3D 底模是主干，部件是后续可选增强"
    component_title = "部件输入图/拆件规划"
    component_groups = []
    if preprocessing.get("component_quality_gate") == "failed" or str(job.get("stage") or "") == "component_split_failed":
        component_summary = "部件输入图质量门未通过；已停止，不会使用低质量兜底进入 part_batch"
    elif components_passed:
        if str(preprocessing.get("component_source_mode") or "") == "see_through_psd_layers":
            component_summary = "See-through PSD 语义分层已准备；可继续生成 3D 部件"
        elif str(preprocessing.get("component_source_mode") or "") == "fidelity_source_crops":
            component_summary = "AI 重绘拆件未通过时已切换为原图像素保真裁切；可试生成 3D 部件，但融合区域建议保留在底模"
        else:
            component_summary = "2D 部件输入图已准备；可单独生成 3D 部件，确认后再与底模合成"
    if components_passed and (component_sheet or component_inputs):
        source_mode = str(preprocessing.get("component_source_mode") or "")
        component_groups.append({
            "title": "原图保真裁切部件输入图" if source_mode == "fidelity_source_crops" else "2D 部件输入图",
            "summary": "使用原图像素裁切，避免 AI 重绘换设计；这不是 3D 部件，需要下一步单独生成" if source_mode == "fidelity_source_crops" else "已通过质量门；这不是 3D 部件，需要下一步单独生成",
            "items": ([component_sheet] if component_sheet else []) + component_inputs,
        })
    if components_passed and see_through_artifacts:
        component_groups.append({
            "title": "See-through PSD 分层资源",
            "summary": "PSD、Depth、元数据和重建预览会进入批量下载",
            "items": see_through_artifacts,
        })
    if components_passed and (component_reference_sheet or component_reference_inputs):
        component_groups.append({
            "title": "裁切参考",
            "summary": "定位参考，不是 3D 部件",
            "items": ([component_reference_sheet] if component_reference_sheet else []) + component_reference_inputs,
        })
    if not components_passed and (component_failed_preview_sheet or component_failed_preview_inputs or component_sheet_partial or component_inputs_partial):
        preview_sheet = component_failed_preview_sheet or component_sheet_partial
        preview_inputs = component_failed_preview_inputs or component_inputs_partial
        component_groups.append({
            "title": "未通过复核的部件预览",
            "summary": "仅用于排查，不会进入 Meshy 3D",
            "items": ([preview_sheet] if preview_sheet else []) + preview_inputs,
        })
    if not components_passed and component_reference_inputs:
        component_groups.append({
            "title": "逐部件定位参考",
            "summary": "原图红框 + 局部裁片，用于约束图片模型",
            "items": component_reference_inputs,
        })
    outputs = job.get("outputs") if isinstance(job.get("outputs"), dict) else {}
    output_files = outputs.get("files") if isinstance(outputs.get("files"), list) else []
    output_parts = outputs.get("parts") if isinstance(outputs.get("parts"), list) else []
    base_outputs = outputs.get("base") if isinstance(outputs.get("base"), dict) else {}
    base_files = base_outputs.get("files") if isinstance(base_outputs.get("files"), list) else []
    steps = [
        {
            "key": "upload",
            "title": "上传与主体裁切",
            "status": _step_status(job, "upload"),
            "summary": f"已保存 {len(source_inputs or job.get('inputs') or [])} 张参考图",
            "items": source_inputs or job.get("inputs") or [],
        },
        {
            "key": "candidates",
            "title": "区域裁切候选",
            "status": _step_status(job, "candidates"),
            "summary": "仅用于定位和预览，不会直接当作真实部件进 3D",
            "items": region_inputs,
        },
        {
            "key": "triview",
            "title": "图片模型多视角",
            "status": _step_status(job, "triview"),
            "summary": triview_summary,
            "items": triview_inputs,
        },
        {
            "key": "base_model",
            "title": "多视角 3D 底模",
            "status": _step_status(job, "base_model"),
            "summary": "先用多视角生成完整底模；拆件只作为后续增强，不再 parts-only 拼装",
            "items": base_files,
        },
        {
            "key": "components",
            "title": component_title,
            "status": component_status,
            "summary": component_summary,
            "items": [],
            "groups": component_groups,
        },
    ]
    if str(job.get("strategy") or "") == "part_batch" or components_passed:
        steps.extend([
            {
                "key": "parts_3d",
                "title": "3D 部件生成",
                "status": _step_status(job, "parts_3d"),
                "summary": f"已生成/复用 {len(output_parts)} 个 3D 部件" if output_parts else "按部件输入图逐个生成；未变化的部件会复用",
                "items": [],
                "parts": output_parts,
            },
            {
                "key": "assembly",
                "title": "底模替换合成",
                "status": _step_status(job, "assembly"),
                "summary": f"输出 {len(output_files)} 个最终文件" if output_files else "只读取多视角底模和已有 3D 部件，不重新消耗 Meshy 生成额度",
                "items": output_files,
            },
        ])
    else:
        steps.append({
            "key": "mesh",
            "title": "Meshy 3D 生成",
            "status": _step_status(job, "mesh"),
            "summary": f"输出 {len(output_files)} 个文件" if output_files else "确认多视角后生成完整 3D 底模",
            "items": output_files,
        })
    if job.get("error"):
        current = str(job.get("stage") or "")
        for step in steps:
            if step["status"] == "failed" or step["key"] in current:
                step["error"] = str(job.get("error") or "")
                break
    return steps


def _public_job(job: Dict[str, Any]) -> Dict[str, Any]:
    job = _normalize_legacy_component_job(job)
    public = store.public_job(job)
    public["steps"] = _job_steps(job)
    return public


def _normalize_legacy_component_job(job: Dict[str, Any]) -> Dict[str, Any]:
    preprocessing = job.get("preprocessing") if isinstance(job.get("preprocessing"), dict) else {}
    if not preprocessing:
        return job
    legacy_preview = (
        str(job.get("stage") or "") == "component_preview_ready"
        or preprocessing.get("component_quality_gate") == "preview_only"
        or str(preprocessing.get("component_source_mode") or "") == "rembg_alpha_matting"
    )
    if legacy_preview:
        job = dict(job)
        preprocessing = dict(preprocessing)
        preprocessing.pop("component_sheet", None)
        preprocessing.pop("component_inputs", None)
        preprocessing.pop("component_split_generated", None)
        preprocessing["component_quality_gate"] = "failed"
        safe_inputs = _safe_frontend_generation_inputs(job, preprocessing)
        job["inputs"] = safe_inputs
        job["status"] = "preprocessed"
        job["stage"] = "component_split_failed"
        job["mode"] = "multi-image-to-3d" if _looks_like_triview_inputs(safe_inputs) else "preprocess-preview"
        job["strategy"] = "multi_view" if _looks_like_triview_inputs(safe_inputs) else "candidate_preview"
        job["error"] = job.get("error") or "旧版低质量拆件结果已废弃：不会使用 rembg、矩形裁切或预览图兜底进入 part_batch。"
        job["preprocessing"] = preprocessing
        return job
    looks_bad = (
        str(job.get("strategy") or "") == "part_batch"
        and preprocessing.get("component_split_generated")
        and str(preprocessing.get("component_reference_mode") or "") in _CROP_REFERENCE_MODES
        and _looks_like_region_candidates(job.get("inputs"))
    )
    if not looks_bad:
        return job
    job = dict(job)
    preprocessing = dict(preprocessing)
    if isinstance(preprocessing.get("component_sheet"), dict) and not isinstance(preprocessing.get("component_reference_sheet"), dict):
        sheet = dict(preprocessing["component_sheet"])
        sheet["role"] = "component_reference_sheet"
        sheet["label"] = "裁切参考板（非真实拆件）"
        preprocessing["component_reference_sheet"] = sheet
    if isinstance(preprocessing.get("component_inputs"), list) and not isinstance(preprocessing.get("component_reference_inputs"), list):
        preprocessing["component_reference_inputs"] = [dict(item) for item in preprocessing["component_inputs"] if isinstance(item, dict)]
    preprocessing.pop("component_sheet", None)
    preprocessing.pop("component_inputs", None)
    preprocessing.pop("component_split_generated", None)
    preprocessing["component_reference_mode"] = "crop_reference_only"
    safe_inputs = _safe_frontend_generation_inputs(job, preprocessing)
    job["inputs"] = safe_inputs
    job["status"] = "preprocessed"
    job["stage"] = "component_references_ready"
    job["mode"] = "multi-image-to-3d" if _looks_like_triview_inputs(safe_inputs) else "preprocess-preview"
    job["strategy"] = "multi_view" if _looks_like_triview_inputs(safe_inputs) else "candidate_preview"
    job["error"] = None
    job["preprocessing"] = preprocessing
    return job


async def _download_task_outputs(job_id: str, task_resp: Dict[str, Any], out_dir: Path, prefix: str = "") -> Dict[str, Any]:
    outputs: Dict[str, Any] = {"files": [], "provider_response": task_resp}
    model_urls = task_resp.get("model_urls") if isinstance(task_resp.get("model_urls"), dict) else {}
    if not model_urls and task_resp.get("model_url"):
        model_urls = {"glb": task_resp.get("model_url")}
    for fmt, url in model_urls.items():
        fmt_safe = "".join(ch for ch in str(fmt).lower() if ch.isalnum()) or "model"
        filename = f"{prefix}model.{fmt_safe}" if prefix else f"model.{fmt_safe}"
        path = out_dir / filename
        await meshy.download_url(str(url), path)
        outputs["files"].append({
            "kind": "model",
            "format": fmt_safe,
            "filename": filename,
            "size": path.stat().st_size,
            "url": _job_file_url(job_id, path),
        })
    if task_resp.get("thumbnail_url"):
        filename = f"{prefix}preview.png" if prefix else "preview.png"
        path = out_dir / filename
        await meshy.download_url(str(task_resp.get("thumbnail_url")), path)
        outputs["files"].append({
            "kind": "preview",
            "format": "png",
            "filename": filename,
            "size": path.stat().st_size,
            "url": _job_file_url(job_id, path),
        })
    glb = out_dir / (f"{prefix}model.glb" if prefix else "model.glb")
    if glb.exists():
        outputs["mesh_metrics"] = meshy.inspect_glb(glb)
    return outputs


def _glb_file_entry(files: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(files, list):
        return None
    for file in files:
        if not isinstance(file, dict):
            continue
        fmt = str(file.get("format") or "").lower()
        filename = str(file.get("filename") or "").lower()
        if fmt == "glb" or filename.endswith(".glb"):
            return file
    return None


def _files_include_format(files: Any, fmt: str) -> bool:
    wanted = str(fmt or "").strip().lower().lstrip(".")
    if not wanted or not isinstance(files, list):
        return False
    for file in files:
        if not isinstance(file, dict):
            continue
        file_fmt = str(file.get("format") or "").lower().lstrip(".")
        filename = str(file.get("filename") or "").lower()
        if file_fmt == wanted or filename.endswith(f".{wanted}"):
            return True
    return False


def _wants_3mf(target_formats: Any) -> bool:
    return any(str(fmt or "").strip().lower().lstrip(".") == "3mf" for fmt in (target_formats or []))


def _meshy_target_formats_for_request(target_formats: Any) -> List[str]:
    out: List[str] = []
    wants_3mf = _wants_3mf(target_formats)
    for item in target_formats or ["glb"]:
        fmt = str(item or "").strip().lower().lstrip(".")
        if not fmt or fmt == "3mf":
            continue
        if fmt not in out:
            out.append(fmt)
    if wants_3mf and "stl" not in out:
        out.append("stl")
    return out or ["glb"]


def _model_label_without_format(file: Dict[str, Any], fallback: str = "3D 模型") -> str:
    label = str(file.get("label") or file.get("filename") or fallback).strip()
    return re.sub(r"\s+(GLB|STL|OBJ)$", "", label, flags=re.IGNORECASE) or fallback


def _report_path_for_3mf(dest_3mf: Path) -> Path:
    return dest_3mf.with_name(dest_3mf.name + ".check.json")


def _upsert_file_entry(files: List[Dict[str, Any]], entry: Dict[str, Any]) -> None:
    url = str(entry.get("url") or "")
    filename = str(entry.get("filename") or "")
    for idx, item in enumerate(files):
        if not isinstance(item, dict):
            continue
        if (url and str(item.get("url") or "") == url) or (filename and str(item.get("filename") or "") == filename):
            files[idx] = entry
            return
    files.append(entry)


def _remove_file_entry_by_url(files: List[Dict[str, Any]], url: str) -> None:
    if not url:
        return
    files[:] = [item for item in files if not (isinstance(item, dict) and str(item.get("url") or "") == url)]


def _cached_3mf_report(source_path: Path, report_path: Path) -> Optional[Dict[str, Any]]:
    if not source_path.is_file() or not report_path.is_file():
        return None
    try:
        if report_path.stat().st_mtime < source_path.stat().st_mtime:
            return None
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    try:
        cached_source = Path(str(data.get("source") or "")).resolve()
        if cached_source != source_path.resolve():
            return None
    except Exception:
        return None
    return data


def _append_3mf_export_for_model(
    *,
    job_id: str,
    files: List[Dict[str, Any]],
    source_file: Dict[str, Any],
    source_path: Path,
) -> Optional[Dict[str, Any]]:
    if not source_path.is_file():
        return None
    dest_3mf = source_path.with_suffix(".3mf")
    report_path = _report_path_for_3mf(dest_3mf)
    source_format = str(source_file.get("format") or source_path.suffix.lstrip(".") or "").lower()
    label_base = _model_label_without_format(source_file)
    report = _cached_3mf_report(source_path, report_path)
    if report is None or (report.get("passed") and not dest_3mf.is_file()):
        report = model_3mf.export_glb_to_3mf(
            source_path,
            dest_3mf,
            report_path=report_path,
            label=f"{label_base} 3MF",
        )
    report_entry = {
        "kind": "validation",
        "format": "json",
        "filename": report_path.name,
        "label": f"{label_base} 3MF 检查报告",
        "three_mf_report": True,
        "three_mf_status": report.get("status"),
        "three_mf_passed": bool(report.get("passed")),
        "three_mf_source_format": source_format,
        "size": report_path.stat().st_size if report_path.is_file() else 0,
        "url": _job_file_url(job_id, report_path),
    }
    _upsert_file_entry(files, report_entry)
    if not report.get("passed") or not dest_3mf.is_file():
        try:
            stale_url = _job_file_url(job_id, dest_3mf)
        except Exception:
            stale_url = ""
        _remove_file_entry_by_url(files, stale_url)
        return report
    entry = {
        "kind": "model",
        "format": "3mf",
        "filename": dest_3mf.name,
        "label": f"{label_base} 3MF",
        "three_mf_export": True,
        "three_mf_status": "exported",
        "three_mf_source_format": source_format,
        "source_model": source_file.get("url"),
        "size": dest_3mf.stat().st_size,
        "url": _job_file_url(job_id, dest_3mf),
    }
    for key in ("base_model", "assembled", "part_index", "source"):
        if key in source_file:
            entry[key] = source_file.get(key)
    _upsert_file_entry(files, entry)
    return report


def _model_file_entries_for_3mf(files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_priority = {"stl": 0, "glb": 1, "obj": 2}
    candidates: List[Tuple[int, int, Dict[str, Any]]] = []
    for idx, file in enumerate(files):
        if not isinstance(file, dict):
            continue
        fmt = str(file.get("format") or "").lower()
        filename = str(file.get("filename") or "").lower()
        if not fmt:
            suffix = Path(filename).suffix.lower().lstrip(".")
            fmt = suffix
        if fmt in by_priority:
            candidates.append((by_priority[fmt], idx, file))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in candidates]


def _ensure_3mf_exports_for_files(
    *,
    job_id: str,
    files: Any,
    target_formats: Any,
) -> List[Dict[str, Any]]:
    if not isinstance(files, list):
        return []
    if not _wants_3mf(target_formats):
        return []
    reports: List[Dict[str, Any]] = []
    source_files = _model_file_entries_for_3mf(list(files))
    if not source_files:
        return reports
    source_file = source_files[0]
    source_path = _path_from_job_file_url(job_id, str(source_file.get("url") or ""))
    if not source_path:
        return reports
    report = _append_3mf_export_for_model(job_id=job_id, files=files, source_file=source_file, source_path=source_path)
    if report:
        reports.append(report)
    return reports


def _is_3mf_related_file(file: Dict[str, Any]) -> bool:
    fmt = str(file.get("format") or "").lower()
    filename = str(file.get("filename") or "").lower()
    return fmt == "3mf" or filename.endswith(".3mf") or bool(file.get("three_mf_report")) or filename.endswith(".3mf.check.json")


def _3mf_file_entries_for_scope(job: Dict[str, Any], scope: str) -> List[Dict[str, Any]]:
    outputs = job.get("outputs") if isinstance(job.get("outputs"), dict) else {}
    scope = (scope or "all").strip().lower()
    entries: List[Dict[str, Any]] = []

    def collect(files: Any) -> None:
        if not isinstance(files, list):
            return
        for file in files:
            if isinstance(file, dict) and _is_3mf_related_file(file):
                entries.append(file)

    if scope in {"base", "all"}:
        base_outputs = outputs.get("base") if isinstance(outputs.get("base"), dict) else {}
        collect(base_outputs.get("files"))
    if scope in {"parts", "all"}:
        parts = outputs.get("parts") if isinstance(outputs.get("parts"), list) else []
        if not parts and isinstance(job.get("subtasks"), list):
            parts = job.get("subtasks") or []
        for part in parts:
            if isinstance(part, dict):
                collect(part.get("files"))
    if scope in {"final", "assembled", "assembly", "all"}:
        collect(outputs.get("files"))
    seen: set[str] = set()
    unique: List[Dict[str, Any]] = []
    for entry in entries:
        key = str(entry.get("url") or entry.get("filename") or "")
        if key and key not in seen:
            seen.add(key)
            unique.append(entry)
    return unique


def _export_3mf_for_scope(job_id: str, job: Dict[str, Any], scope: str) -> Dict[str, Any]:
    scope = (scope or "all").strip().lower()
    if scope not in {"base", "parts", "final", "assembled", "assembly", "all"}:
        raise HTTPException(status_code=400, detail="scope must be base, parts, final or all")
    outputs = job.get("outputs") if isinstance(job.get("outputs"), dict) else {}
    outputs = dict(outputs)
    reports: List[Dict[str, Any]] = []
    touched = False

    if scope in {"base", "all"}:
        base_outputs = _base_outputs_for_current_triview(job_id, job, outputs)
        if not _base_glb_path(job_id, base_outputs):
            if scope == "base":
                raise HTTPException(status_code=409, detail="当前任务还没有可导出 3MF 的多视角 3D 底模")
        elif isinstance(base_outputs, dict):
            base_outputs = dict(base_outputs)
            files = base_outputs.get("files")
            if not isinstance(files, list):
                files = []
                base_outputs["files"] = files
            reports.extend(_ensure_3mf_exports_for_files(job_id=job_id, files=files, target_formats=["3mf"]))
            outputs["base"] = base_outputs
            touched = True

    if scope in {"parts", "all"}:
        parts = outputs.get("parts") if isinstance(outputs.get("parts"), list) else []
        if not parts and isinstance(job.get("subtasks"), list):
            parts = job.get("subtasks") or []
        if not parts:
            if scope == "parts":
                raise HTTPException(status_code=409, detail="当前任务还没有可导出 3MF 的 3D 部件")
        next_parts: List[Dict[str, Any]] = []
        any_part_glb = False
        for part in parts:
            if not isinstance(part, dict):
                continue
            item = dict(part)
            files = item.get("files")
            if not isinstance(files, list):
                files = []
                item["files"] = files
            if _glb_file_entry(files):
                any_part_glb = True
                reports.extend(_ensure_3mf_exports_for_files(job_id=job_id, files=files, target_formats=["3mf"]))
            next_parts.append(item)
        if scope == "parts" and not any_part_glb:
            raise HTTPException(status_code=409, detail="当前 3D 部件里没有 GLB，不能导出 3MF")
        if next_parts:
            outputs["parts"] = next_parts
            job["subtasks"] = next_parts
            touched = True

    if scope in {"final", "assembled", "assembly", "all"}:
        files = outputs.get("files")
        if not isinstance(files, list):
            files = []
            outputs["files"] = files
        if not _glb_file_entry(files):
            if scope in {"final", "assembled", "assembly"}:
                raise HTTPException(status_code=409, detail="当前任务还没有可导出 3MF 的最终 GLB")
        else:
            reports.extend(_ensure_3mf_exports_for_files(job_id=job_id, files=files, target_formats=["3mf"]))
            touched = True

    if not touched:
        raise HTTPException(status_code=409, detail="当前任务没有可导出 3MF 的 GLB 文件")
    job["outputs"] = outputs
    job.setdefault("target_formats", [])
    target_formats = list(job.get("target_formats") or [])
    if "3mf" not in [str(fmt).lower().lstrip(".") for fmt in target_formats]:
        target_formats.append("3mf")
        job["target_formats"] = target_formats
    store.save_job(job)
    entries = _3mf_file_entries_for_scope(job, scope)
    passed_count = sum(1 for item in entries if str(item.get("format") or "").lower() == "3mf")
    return {
        "job": job,
        "reports": reports,
        "files": entries,
        "passed_count": passed_count,
        "failed_count": max(0, len([r for r in reports if not r.get("passed")])),
    }


def _3mf_download_paths_for_scope(job_id: str, job: Dict[str, Any], scope: str) -> List[Path]:
    paths: List[Path] = []
    seen: set[str] = set()
    for file in _3mf_file_entries_for_scope(job, scope):
        if not isinstance(file, dict):
            continue
        path = _path_from_job_file_url(job_id, str(file.get("url") or ""))
        if not path or not path.is_file():
            continue
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            paths.append(path)
    return paths


def _path_from_job_file_url(job_id: str, url: str) -> Optional[Path]:
    marker = f"/api/ai-3d-model/jobs/{job_id}/files/"
    if marker not in str(url or ""):
        return None
    rel = str(url).split(marker, 1)[1].split("?", 1)[0].strip("/\\")
    if not rel:
        return None
    root = store.job_dir(job_id).resolve()
    path = (root / rel).resolve()
    if not str(path).startswith(str(root)) or not path.is_file():
        return None
    return path


def _downloadable_job_paths(job_id: str, job: Dict[str, Any]) -> List[Path]:
    root = store.job_dir(job_id).resolve()
    paths: List[Path] = []
    seen: set[str] = set()
    redundant_archives = {
        "outputs.rar",
        "outputs.zip",
        "output.rar",
        "output.zip",
        "result.rar",
        "result.zip",
        "results.rar",
        "results.zip",
    }

    def add(path: Optional[Path]) -> None:
        if not path:
            return
        try:
            resolved = path.resolve()
        except Exception:
            return
        key = str(resolved)
        try:
            rel = resolved.relative_to(root).as_posix()
        except Exception:
            return
        if rel.startswith("downloads/"):
            return
        if rel.startswith("outputs/") and resolved.name.lower() in redundant_archives:
            return
        if rel.startswith("outputs/assembled/assembled_screenshot_"):
            return
        if not str(resolved).startswith(str(root)) or not resolved.is_file() or key in seen:
            return
        seen.add(key)
        paths.append(resolved)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key in ("url", "preview_url"):
                add(_path_from_job_file_url(job_id, str(value.get(key) or "")))
            for child in value.values():
                if isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit({
        "inputs": job.get("inputs"),
        "outputs": job.get("outputs"),
        "subtasks": job.get("subtasks"),
        "preprocessing": job.get("preprocessing"),
    })
    for rel_dir in ("normalized", "outputs"):
        folder = (root / rel_dir).resolve()
        if folder.is_dir() and str(folder).startswith(str(root)):
            for path in folder.rglob("*"):
                add(path)
    add(store.job_manifest_path(job_id))
    return paths


def _archive_manifest_path(archive: Path) -> Path:
    return archive.with_suffix(".manifest.json")


def _archive_signature(root: Path, paths: List[Path]) -> List[Dict[str, Any]]:
    signature: List[Dict[str, Any]] = []
    for path in sorted(paths, key=lambda item: str(item)):
        try:
            stat = path.stat()
            rel = path.resolve().relative_to(root).as_posix()
        except Exception:
            continue
        signature.append({
            "path": rel,
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        })
    return signature


def _archive_is_current(archive: Path, paths: List[Path]) -> bool:
    if not archive.is_file():
        return False
    try:
        root = archive.resolve().parents[1]
    except Exception:
        return False
    manifest_path = _archive_manifest_path(archive)
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if manifest.get("signature") != _archive_signature(root, paths):
        return False
    try:
        archive_mtime = archive.stat().st_mtime
    except Exception:
        return False
    for path in paths:
        try:
            if path.stat().st_mtime > archive_mtime:
                return False
        except Exception:
            return False
    return True


def _assembly_reference_frame(job: Dict[str, Any]) -> Tuple[int, int]:
    preprocessing = job.get("preprocessing") if isinstance(job.get("preprocessing"), dict) else {}
    for key in ("triview_inputs", "triview_inputs_partial"):
        items = preprocessing.get(key)
        if not isinstance(items, list):
            continue
        front = next((item for item in items if isinstance(item, dict) and str(item.get("role") or "") == "front"), None)
        if isinstance(front, dict) and int(front.get("width") or 0) > 0 and int(front.get("height") or 0) > 0:
            return int(front.get("width") or 0), int(front.get("height") or 0)
    component_inputs = preprocessing.get("component_inputs") if isinstance(preprocessing.get("component_inputs"), list) else []
    boxes = [item.get("source_box") for item in component_inputs if isinstance(item, dict)]
    max_x = max([float(box[2]) for box in boxes if isinstance(box, list) and len(box) >= 4] or [0.0])
    max_y = max([float(box[3]) for box in boxes if isinstance(box, list) and len(box) >= 4] or [0.0])
    if max_x > 0 and max_y > 0:
        return int(max_x), int(max_y)
    inputs = job.get("inputs") if isinstance(job.get("inputs"), list) else []
    first = inputs[0] if inputs and isinstance(inputs[0], dict) else {}
    return int(first.get("width") or 1024), int(first.get("height") or 1024)


def _component_meta_by_index(job: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    preprocessing = job.get("preprocessing") if isinstance(job.get("preprocessing"), dict) else {}
    candidates: List[Dict[str, Any]] = []
    for key in ("component_inputs", "component_inputs_partial"):
        value = preprocessing.get(key)
        if isinstance(value, list):
            candidates.extend([item for item in value if isinstance(item, dict)])
    if not candidates and isinstance(job.get("inputs"), list):
        candidates.extend([item for item in job["inputs"] if isinstance(item, dict)])
    out: Dict[int, Dict[str, Any]] = {}
    for item in candidates:
        try:
            index = int(item.get("index") or 0)
        except Exception:
            index = 0
        if index > 0 and index not in out:
            out[index] = item
    return out


def _component_input_fingerprint(image_path: Path, meta: Optional[Dict[str, Any]] = None) -> str:
    payload = meta if isinstance(meta, dict) else {}
    digest = hashlib.sha256()
    if image_path.is_file():
        digest.update(image_path.read_bytes())
    digest.update(json.dumps({
        "role": payload.get("role"),
        "label": payload.get("label"),
        "source_box": payload.get("source_box"),
        "crop_box": payload.get("crop_box"),
        "width": payload.get("width"),
        "height": payload.get("height"),
        "mesh_input_mode": payload.get("mesh_input_mode"),
        "fidelity_source": payload.get("fidelity_source"),
    }, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
    return digest.hexdigest()


def _part_glb_exists(job_id: str, part_index: int, part: Dict[str, Any]) -> bool:
    glb_file = _glb_file_entry(part.get("files"))
    glb_path = _path_from_job_file_url(job_id, str(glb_file.get("url") if glb_file else ""))
    if glb_path and glb_path.is_file():
        return True
    expected = store.job_dir(job_id) / "outputs" / f"part_{part_index:02d}" / f"part_{part_index:02d}_model.glb"
    return expected.is_file()


def _cached_part_tasks_by_index(job_id: str, job: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    existing_by_index: Dict[int, Dict[str, Any]] = {}
    for item in job.get("subtasks") if isinstance(job.get("subtasks"), list) else []:
        if not isinstance(item, dict):
            continue
        try:
            part_index = int(item.get("part_index") or 0)
        except Exception:
            continue
        files = item.get("files") if isinstance(item.get("files"), list) else []
        if part_index > 0 and files and _part_glb_exists(job_id, part_index, item):
            existing_by_index[part_index] = dict(item)
    return existing_by_index


def _component_source_frame(meta_by_index: Dict[int, Dict[str, Any]]) -> Tuple[int, int]:
    widths: List[int] = []
    heights: List[int] = []
    for meta in meta_by_index.values():
        if not isinstance(meta, dict):
            continue
        try:
            source_width = int(meta.get("source_width") or 0)
            source_height = int(meta.get("source_height") or 0)
        except Exception:
            source_width = source_height = 0
        if source_width > 0:
            widths.append(source_width)
        if source_height > 0:
            heights.append(source_height)
        box = meta.get("source_box")
        if isinstance(box, list) and len(box) >= 4:
            try:
                widths.append(int(float(box[2])))
                heights.append(int(float(box[3])))
            except Exception:
                pass
    return max(widths or [1]), max(heights or [1])


def _box_overlap_ratio(a: Any, b: Any) -> Tuple[float, float]:
    if not (isinstance(a, list) and isinstance(b, list) and len(a) >= 4 and len(b) >= 4):
        return 0.0, 0.0
    ax1, ay1, ax2, ay2 = [float(v) for v in a[:4]]
    bx1, by1, bx2, by2 = [float(v) for v in b[:4]]
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    overlap = iw * ih
    area_a = max(1.0, (ax2 - ax1) * (ay2 - ay1))
    height_a = max(1.0, ay2 - ay1)
    return overlap / area_a, ih / height_a


def _component_mesh_preflight_issues(meta_by_index: Dict[int, Dict[str, Any]]) -> List[str]:
    issues: List[str] = []
    _, frame_h = _component_source_frame(meta_by_index)
    for index, meta in sorted(meta_by_index.items()):
        if not isinstance(meta, dict):
            continue
        mesh_kind = str(meta.get("mesh_input_kind") or "")
        if mesh_kind != "source_pixel_crop" and not meta.get("fidelity_source"):
            continue
        role_text = " ".join(str(meta.get(key) or "").lower() for key in ("role", "label", "part_reason"))
        box = meta.get("source_box")
        if not (isinstance(box, list) and len(box) >= 4):
            issues.append(f"部件 {index} 缺少 source_box，不能安全送入 3D")
            continue
        try:
            y1, y2 = float(box[1]), float(box[3])
        except Exception:
            issues.append(f"部件 {index} source_box 非法，不能安全送入 3D")
            continue
        if _is_head_mesh_role(role_text) and not _is_neck_or_torso_role(role_text):
            if y2 / max(1, frame_h) > 0.205:
                issues.append(f"部件 {index} 是头/帽/脸局部件，但裁切框下边界过低，容易生成半身 bust；请重新生成部件输入图")
            for other_index, other in meta_by_index.items():
                if other_index == index or not isinstance(other, dict):
                    continue
                other_text = " ".join(str(other.get(key) or "").lower() for key in ("role", "label", "part_reason"))
                if not _is_neck_or_torso_role(other_text):
                    continue
                _, height_ratio = _box_overlap_ratio(box, other.get("source_box"))
                if height_ratio > 0.20:
                    issues.append(f"部件 {index} 与颈部/围巾/上身部件重叠过多，不能作为独立头部件送入 3D")
                    break
    return issues


def _assembly_part_inputs(job_id: str, job: Dict[str, Any], subtasks: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int, int, List[int]]:
    meta_by_index = _component_meta_by_index(job)
    frame_width, frame_height = _assembly_reference_frame(job)
    parts: List[Dict[str, Any]] = []
    missing: List[int] = []
    for subtask in sorted(subtasks, key=lambda item: int(item.get("part_index") or 0)):
        if not isinstance(subtask, dict):
            continue
        try:
            index = int(subtask.get("part_index") or 0)
        except Exception:
            index = 0
        if index <= 0:
            continue
        glb_file = _glb_file_entry(subtask.get("files"))
        glb_path = _path_from_job_file_url(job_id, str(glb_file.get("url") if glb_file else ""))
        if not glb_path:
            expected = store.job_dir(job_id) / "outputs" / f"part_{index:02d}" / f"part_{index:02d}_model.glb"
            glb_path = expected if expected.is_file() else None
        if not glb_path:
            missing.append(index)
            continue
        meta = meta_by_index.get(index, {})
        parts.append({
            "part_index": index,
            "role": str(meta.get("role") or subtask.get("role") or f"part_{index:02d}"),
            "label": str(meta.get("label") or subtask.get("source") or f"part_{index:02d}"),
            "source": str(subtask.get("source") or meta.get("filename") or glb_path.name),
            "source_box": meta.get("source_box"),
            "glb_path": glb_path,
        })
    expected_count = len(meta_by_index) or len(subtasks)
    for index in range(1, expected_count + 1):
        if not any(int(part.get("part_index") or 0) == index for part in parts) and index not in missing:
            missing.append(index)
    return parts, frame_width, frame_height, sorted(set(missing))


def _triview_image_items(job: Dict[str, Any]) -> List[Tuple[str, Path]]:
    preprocessing = job.get("preprocessing") if isinstance(job.get("preprocessing"), dict) else {}
    triview_inputs = preprocessing.get("triview_inputs") if isinstance(preprocessing.get("triview_inputs"), list) else []
    items = [item for item in triview_inputs if isinstance(item, dict) and str(item.get("role") or "") in _VIEW_ROLE_ORDER]
    items.sort(key=lambda item: _VIEW_ROLE_ORDER.get(str(item.get("role") or ""), 99))
    paths: List[Tuple[str, Path]] = []
    for item in items:
        role = str(item.get("role") or "")
        path = Path(str(item.get("normalized_path") or ""))
        if path.is_file():
            paths.append((role, path))
    return paths


def _triview_image_paths(job: Dict[str, Any]) -> List[Path]:
    return [path for _, path in _triview_image_items(job)]


def _meshy_base_image_items(job: Dict[str, Any]) -> List[Tuple[str, Path]]:
    items = _triview_image_items(job)
    preprocessing = job.get("preprocessing") if isinstance(job.get("preprocessing"), dict) else {}
    usable_roles_raw = preprocessing.get("triview_usable_roles")
    if isinstance(usable_roles_raw, list):
        usable_roles = {str(role) for role in usable_roles_raw if str(role) in _VIEW_ROLE_ORDER}
        if usable_roles:
            filtered = [(role, path) for role, path in items if role in usable_roles]
            if len(filtered) >= 2:
                items = filtered
    if len(items) <= _MESHY_MULTI_IMAGE_MAX:
        return items
    by_role = {role: path for role, path in items}
    selected: List[Tuple[str, Path]] = []
    for role in _MESHY_BASE_VIEW_ROLES:
        path = by_role.get(role)
        if path and path.is_file():
            selected.append((role, path))
    if len(selected) >= 2:
        return selected[:_MESHY_MULTI_IMAGE_MAX]
    return items[:_MESHY_MULTI_IMAGE_MAX]


def _meshy_base_image_paths(job: Dict[str, Any]) -> List[Path]:
    return [path for _, path in _meshy_base_image_items(job)]


def _triview_input_fingerprint(job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    all_items = _triview_image_items(job)
    items = _meshy_base_image_items(job)
    if len(items) < 2:
        return None
    digest = hashlib.sha256()
    digest.update(_VIEW_PROMPT_VERSION.encode("utf-8"))
    roles: List[str] = []
    for role, path in items:
        roles.append(role)
        digest.update(role.encode("utf-8"))
        digest.update(path.name.encode("utf-8", errors="ignore"))
        digest.update(path.read_bytes())
    return {
        "signature": digest.hexdigest(),
        "prompt_version": _VIEW_PROMPT_VERSION,
        "roles": roles,
        "view_count": len(roles),
        "all_roles": [role for role, _ in all_items],
        "all_view_count": len(all_items),
    }


def _base_model_outputs_from_disk(job_id: str, *, expected_signature: str = "") -> Optional[Dict[str, Any]]:
    out_dir = store.job_dir(job_id) / "outputs" / "base"
    glb = out_dir / "base_model.glb"
    if not glb.is_file():
        return None
    meta_path = out_dir / "base_model.meta.json"
    meta: Dict[str, Any] = {}
    if meta_path.is_file():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
        except Exception:
            meta = {}
    if expected_signature and str(meta.get("input_signature") or "") != expected_signature:
        return None
    files: List[Dict[str, Any]] = [{
        "kind": "model",
        "format": "glb",
        "filename": glb.name,
        "label": "完整多视角底模 GLB",
        "base_model": True,
        "size": glb.stat().st_size,
        "url": _job_file_url(job_id, glb),
    }]
    stl = out_dir / "base_model.stl"
    if stl.is_file():
        files.append({
            "kind": "model",
            "format": "stl",
            "filename": stl.name,
            "label": "完整多视角底模 STL",
            "base_model": True,
            "size": stl.stat().st_size,
            "url": _job_file_url(job_id, stl),
        })
    three_mf = out_dir / "base_model.3mf"
    if three_mf.is_file():
        files.append({
            "kind": "model",
            "format": "3mf",
            "filename": three_mf.name,
            "label": "完整多视角底模 3MF",
            "base_model": True,
            "three_mf_export": True,
            "size": three_mf.stat().st_size,
            "url": _job_file_url(job_id, three_mf),
        })
    three_mf_report = _report_path_for_3mf(three_mf)
    if three_mf_report.is_file():
        files.append({
            "kind": "validation",
            "format": "json",
            "filename": three_mf_report.name,
            "label": "完整多视角底模 3MF 检查报告",
            "base_model": True,
            "three_mf_report": True,
            "size": three_mf_report.stat().st_size,
            "url": _job_file_url(job_id, three_mf_report),
        })
    preview = out_dir / "base_preview.png"
    if preview.is_file():
        files.append({
            "kind": "preview",
            "format": "png",
            "filename": preview.name,
            "label": "完整多视角底模预览",
            "base_model": True,
            "size": preview.stat().st_size,
            "url": _job_file_url(job_id, preview),
        })
    return {
        "provider_task_id": None,
        "consumed_credits": 0,
        "files": files,
        "mesh_metrics": meshy.inspect_glb(glb),
        "input_signature": meta.get("input_signature"),
        "input_roles": meta.get("input_roles"),
        "prompt_version": meta.get("prompt_version"),
        "reused": True,
    }


def _base_outputs_for_current_triview(
    job_id: str,
    job: Dict[str, Any],
    outputs: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    fingerprint = _triview_input_fingerprint(job)
    expected_signature = str((fingerprint or {}).get("signature") or "")
    if isinstance(outputs, dict):
        base_outputs = outputs.get("base") if isinstance(outputs.get("base"), dict) else None
        if base_outputs and (not expected_signature or str(base_outputs.get("input_signature") or "") == expected_signature):
            return base_outputs
    return _base_model_outputs_from_disk(job_id, expected_signature=expected_signature)


async def _run_or_reuse_base_model(
    *,
    job_id: str,
    job: Dict[str, Any],
    quality: str,
    target_formats: List[str],
) -> Tuple[Optional[Dict[str, Any]], int]:
    all_triview_paths = _triview_image_paths(job)
    meshy_paths = _meshy_base_image_paths(job)
    fingerprint = _triview_input_fingerprint(job)
    gate_error = _multiview_quality_gate_error(job)
    if gate_error:
        raise RuntimeError(gate_error)
    existing = _base_model_outputs_from_disk(
        job_id,
        expected_signature=str((fingerprint or {}).get("signature") or ""),
    )
    if existing:
        if not (_wants_3mf(target_formats) and not _files_include_format(existing.get("files"), "stl")):
            three_mf_reports = _ensure_3mf_exports_for_files(
                job_id=job_id,
                files=existing.get("files"),
                target_formats=target_formats,
            )
            if three_mf_reports:
                existing["three_mf_exports"] = three_mf_reports
            return existing, 0
    if len(meshy_paths) < 2:
        raise RuntimeError("高精度 3D 必须先生成多视角图；没有多视角图时不会进入低精度 parts-only 拼装。")
    out_dir = store.job_dir(job_id) / "outputs" / "base"
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = await _run_single_meshy_task(
        job_id=job_id,
        image_paths=meshy_paths,
        mode="multi-image-to-3d",
        quality=quality,
        target_formats=target_formats,
        out_dir=out_dir,
        prefix="base_",
    )
    if fingerprint:
        meta_path = out_dir / "base_model.meta.json"
        meta = {
            "input_signature": fingerprint.get("signature"),
            "input_roles": fingerprint.get("roles"),
            "all_input_roles": fingerprint.get("all_roles"),
            "prompt_version": fingerprint.get("prompt_version"),
            "view_count": fingerprint.get("view_count"),
            "all_view_count": fingerprint.get("all_view_count") or len(all_triview_paths),
            "created_at": store.now_iso(),
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs["input_signature"] = meta["input_signature"]
        outputs["input_roles"] = meta["input_roles"]
        outputs["prompt_version"] = meta["prompt_version"]
    for file in outputs.get("files") or []:
        if not isinstance(file, dict):
            continue
        file["base_model"] = True
        if file.get("kind") == "model":
            file["label"] = f"完整多视角底模 {str(file.get('format') or '').upper()}".strip()
        elif file.get("kind") == "preview":
            file["label"] = "完整多视角底模预览"
    return outputs, int(outputs.get("consumed_credits") or 0)


def _base_glb_path(job_id: str, base_outputs: Optional[Dict[str, Any]]) -> Optional[Path]:
    if isinstance(base_outputs, dict):
        glb = _glb_file_entry(base_outputs.get("files"))
        if glb:
            path = _path_from_job_file_url(job_id, str(glb.get("url") or ""))
            if path:
                return path
    expected = store.job_dir(job_id) / "outputs" / "base" / "base_model.glb"
    return expected if expected.is_file() else None


def _assemble_part_outputs(
    job_id: str,
    job: Dict[str, Any],
    subtasks: List[Dict[str, Any]],
    *,
    base_outputs: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    outputs: Dict[str, Any] = {"parts": subtasks}
    if base_outputs:
        outputs["base"] = base_outputs
    metrics: Dict[str, Any] = {}
    parts, frame_width, frame_height, missing = _assembly_part_inputs(job_id, job, subtasks)
    base_path = _base_glb_path(job_id, base_outputs)
    if not base_path:
        raise RuntimeError("高精度拼装缺少完整多视角底模；请先生成多视角图并生成 base_model.glb。")
    if len(parts) < 1:
        outputs["assembly"] = {"status": "skipped", "reason": "not_enough_part_glbs", "missing_parts": missing}
        return outputs, metrics
    out_dir = store.job_dir(job_id) / "outputs" / "assembled"
    dest_glb = out_dir / "assembled_model.glb"
    dest_plan = out_dir / "assembly_plan.json"
    try:
        assembled = glb_assembly.assemble_parts(
            parts=parts,
            frame_width=frame_width,
            frame_height=frame_height,
            dest_glb=dest_glb,
            dest_plan=dest_plan,
            base_glb_path=base_path,
        )
        metrics = meshy.inspect_glb(dest_glb)
        files = [
            {
                "kind": "model",
                "format": "glb",
                "filename": dest_glb.name,
                "label": "完整自动组装 GLB",
                "assembled": True,
                "size": dest_glb.stat().st_size,
                "url": _job_file_url(job_id, dest_glb),
            },
            {
                "kind": "metadata",
                "format": "json",
                "filename": dest_plan.name,
                "label": "自动组装坐标计划",
                "size": dest_plan.stat().st_size,
                "url": _job_file_url(job_id, dest_plan),
            },
        ]
        base_files = base_outputs.get("files") if isinstance(base_outputs, dict) and isinstance(base_outputs.get("files"), list) else []
        base_model_files = [dict(file) for file in base_files if isinstance(file, dict) and file.get("kind") == "model"]
        files[1:1] = base_model_files
        three_mf_reports = _ensure_3mf_exports_for_files(
            job_id=job_id,
            files=files,
            target_formats=job.get("target_formats") or [],
        )
        if three_mf_reports:
            outputs["three_mf_exports"] = three_mf_reports
        outputs["files"] = files
        outputs["assembly"] = {
            "status": "base_guided_completed" if not missing else "base_guided_completed_with_missing_parts",
            "mode": "base_guided_overlay",
            "base_model": True,
            "missing_parts": missing,
            "frame_width": frame_width,
            "frame_height": frame_height,
            "part_count": len(parts),
            "plan": assembled.get("plan") or {},
            "mesh_metrics": metrics,
        }
    except Exception as exc:
        logger.exception("[ai_3d_model] part assembly failed job_id=%s", job_id)
        outputs["assembly"] = {
            "status": "failed",
            "error": str(exc),
            "missing_parts": missing,
            "part_count": len(parts),
        }
    return outputs, metrics


async def _run_single_meshy_task(
    *,
    job_id: str,
    image_paths: List[Path],
    mode: str,
    quality: str,
    target_formats: List[str],
    out_dir: Path,
    prefix: str = "",
) -> Dict[str, Any]:
    meshy_target_formats = _meshy_target_formats_for_request(target_formats)
    if mode == "multi-image-to-3d":
        create_resp = await meshy.create_multi_image_to_3d_task(
            image_paths,
            quality=quality,
            target_formats=meshy_target_formats,
        )
    else:
        create_resp = await meshy.create_image_to_3d_task(
            image_paths[0],
            quality=quality,
            target_formats=meshy_target_formats,
        )
    provider_task_id = str(create_resp.get("result") or create_resp.get("id") or "").strip()
    if not provider_task_id:
        raise RuntimeError(f"Meshy did not return a task id: {create_resp}")
    final_resp = await meshy.poll_task(provider_task_id, mode=mode, timeout_seconds=1200, interval_seconds=8)
    if str(final_resp.get("status") or "").upper() != "SUCCEEDED":
        raise RuntimeError(final_resp.get("task_error") or f"Meshy task {provider_task_id} failed: {final_resp.get('status')}")
    outputs = await _download_task_outputs(job_id, final_resp, out_dir, prefix=prefix)
    three_mf_reports = _ensure_3mf_exports_for_files(
        job_id=job_id,
        files=outputs.get("files"),
        target_formats=target_formats,
    )
    if three_mf_reports:
        outputs["three_mf_exports"] = three_mf_reports
    outputs["provider_task_id"] = provider_task_id
    outputs["consumed_credits"] = int(final_resp.get("consumed_credits") or 0)
    return outputs


async def _run_job_background(job_id: str) -> None:
    job = store.load_job(job_id)
    if not job:
        return
    strategy = str(job.get("strategy") or "auto")
    subtasks: List[Dict[str, Any]] = []
    total_credits = 0
    outputs: Dict[str, Any] = {}
    metrics: Dict[str, Any] = {}
    base_outputs: Optional[Dict[str, Any]] = None
    try:
        if strategy == "part_batch" and not _has_true_component_source(job):
            raise RuntimeError("当前输入只是区域裁切/参考候选，不是真实可进 3D 的拆件包。角色请先生成高清多视角走 Multi-Image to 3D；硬表面/饰件请上传拆件包或使用拆件流程。")
        store.update_job(job_id, status="running", stage="submitting", started_at=store.now_iso(), progress=8)
        image_paths = [Path(item["normalized_path"]) for item in job.get("inputs", []) if item.get("normalized_path")]
        if not image_paths:
            raise RuntimeError("no normalized input images")
        quality = str(job.get("quality") or "production").strip().lower()
        if quality not in {"high", "production"}:
            quality = "production"
        target_formats = list(job.get("target_formats") or ["glb", "fbx", "obj", "usdz", "3mf"])
        out_dir = store.job_dir(job_id) / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        if strategy == "part_batch":
            if len(_triview_image_paths(job)) < 2:
                raise RuntimeError("高精度拆件增强必须先生成多视角图；系统不会走低精度 parts-only 拼装。")
            store.update_job(job_id, stage="generating_base_model", progress=10, consumed_credits=total_credits)
            base_outputs, base_credits = await _run_or_reuse_base_model(
                job_id=job_id,
                job=job,
                quality=quality,
                target_formats=target_formats,
            )
            total_credits += int(base_credits or 0)
            store.update_job(
                job_id,
                stage="base_model_ready",
                progress=18,
                outputs={"base": base_outputs or {}},
                consumed_credits=total_credits,
            )
            existing_by_index: Dict[int, Dict[str, Any]] = {}
            for item in job.get("subtasks") if isinstance(job.get("subtasks"), list) else []:
                if not isinstance(item, dict):
                    continue
                try:
                    part_index = int(item.get("part_index") or 0)
                except Exception:
                    continue
                part_dir = out_dir / f"part_{part_index:02d}"
                glb_path = part_dir / f"part_{part_index:02d}_model.glb"
                files = item.get("files") if isinstance(item.get("files"), list) else []
                if part_index > 0 and glb_path.exists() and files:
                    existing_by_index[part_index] = dict(item)
            for idx, image_path in enumerate(image_paths, start=1):
                existing = existing_by_index.get(idx)
                if existing:
                    existing = dict(existing)
                    three_mf_reports = _ensure_3mf_exports_for_files(
                        job_id=job_id,
                        files=existing.get("files"),
                        target_formats=target_formats,
                    )
                    if three_mf_reports:
                        existing["three_mf_exports"] = three_mf_reports
                    subtasks.append(existing)
                    store.update_job(
                        job_id,
                        stage=f"part_{idx}_already_done",
                        progress=min(92, 8 + int(idx / max(1, len(image_paths)) * 82)),
                        subtasks=subtasks,
                        outputs={"parts": subtasks},
                        consumed_credits=total_credits,
                    )
                    continue
                store.update_job(
                    job_id,
                    stage=f"generating_part_{idx}",
                    progress=min(92, 8 + int((idx - 1) / max(1, len(image_paths)) * 82)),
                )
                part_dir = out_dir / f"part_{idx:02d}"
                part_dir.mkdir(parents=True, exist_ok=True)
                outputs = await _run_single_meshy_task(
                    job_id=job_id,
                    image_paths=[image_path],
                    mode="image-to-3d",
                    quality=quality,
                    target_formats=target_formats,
                    out_dir=part_dir,
                    prefix=f"part_{idx:02d}_",
                )
                total_credits += int(outputs.get("consumed_credits") or 0)
                subtasks.append({
                    "part_index": idx,
                    "source": image_path.name,
                    "provider_task_id": outputs.get("provider_task_id"),
                    "files": outputs.get("files") or [],
                    "mesh_metrics": outputs.get("mesh_metrics") or {},
                    "consumed_credits": outputs.get("consumed_credits") or 0,
                })
                store.update_job(job_id, subtasks=subtasks, outputs={"parts": subtasks}, consumed_credits=total_credits)
            store.update_job(job_id, stage="assembling_parts", progress=94, subtasks=subtasks, outputs={"base": base_outputs or {}, "parts": subtasks}, consumed_credits=total_credits)
            outputs, metrics = _assemble_part_outputs(job_id, job, subtasks, base_outputs=base_outputs)
        else:
            meshy_input_paths = _meshy_base_image_paths(job) if _looks_like_triview_inputs(job.get("inputs")) else image_paths[:_MESHY_MULTI_IMAGE_MAX]
            if not meshy_input_paths:
                meshy_input_paths = image_paths[:_MESHY_MULTI_IMAGE_MAX]
            gate_error = _multiview_quality_gate_error(job)
            if gate_error:
                raise RuntimeError(gate_error)
            mode = "multi-image-to-3d" if len(meshy_input_paths) >= 2 else "image-to-3d"
            store.update_job(job_id, stage="generating_mesh", progress=18, mode=mode)
            outputs = await _run_single_meshy_task(
                job_id=job_id,
                image_paths=meshy_input_paths,
                mode=mode,
                quality=quality,
                target_formats=target_formats,
                out_dir=out_dir,
            )
            total_credits = int(outputs.get("consumed_credits") or 0)
            metrics = outputs.get("mesh_metrics") or {}
        store.update_job(
            job_id,
            status="succeeded",
            stage="completed",
            progress=100,
            finished_at=store.now_iso(),
            outputs=outputs,
            mesh_metrics=metrics,
            provider_task_id=outputs.get("provider_task_id"),
            consumed_credits=total_credits,
        )
    except Exception as exc:
        logger.exception("[ai_3d_model] job failed job_id=%s", job_id)
        patch: Dict[str, Any] = {
            "status": "failed",
            "stage": "failed",
            "progress": 100,
            "error": str(exc),
            "finished_at": store.now_iso(),
        }
        if strategy == "part_batch" and subtasks:
            partial_outputs, partial_metrics = _assemble_part_outputs(job_id, job, subtasks, base_outputs=base_outputs)
            assembly = partial_outputs.get("assembly") if isinstance(partial_outputs.get("assembly"), dict) else {}
            if assembly.get("status") in {
                "completed",
                "completed_with_missing_parts",
                "base_guided_completed",
                "base_guided_completed_with_missing_parts",
            }:
                patch.update({
                    "status": "succeeded",
                    "stage": "completed_with_missing_parts",
                    "error": f"部分部件生成失败，已使用 {len(subtasks)} 个已完成部件自动组装：{exc}",
                    "outputs": partial_outputs,
                    "mesh_metrics": partial_metrics,
                    "subtasks": subtasks,
                    "consumed_credits": total_credits,
                })
            else:
                patch.update({
                    "subtasks": subtasks,
                    "outputs": partial_outputs,
                    "mesh_metrics": partial_metrics,
                    "consumed_credits": total_credits,
                })
        store.update_job(job_id, **patch)


async def _run_part_models_background(job_id: str) -> None:
    job = store.load_job(job_id)
    if not job:
        return
    subtasks: List[Dict[str, Any]] = []
    total_credits = 0
    base_outputs: Optional[Dict[str, Any]] = None
    try:
        if str(job.get("strategy") or "") != "part_batch":
            raise RuntimeError("当前任务不是部件增强流程。请先生成部件输入图。")
        if not _has_true_component_source(job):
            raise RuntimeError("当前输入不是可用于 3D 部件生成的真实部件输入图。")
        preprocessing = job.get("preprocessing") if isinstance(job.get("preprocessing"), dict) else {}
        if not preprocessing.get("component_split_generated"):
            raise RuntimeError("必须先生成 2D 部件输入图，才能生成 3D 部件。")
        outputs_before = job.get("outputs") if isinstance(job.get("outputs"), dict) else {}
        base_outputs = _base_outputs_for_current_triview(job_id, job, outputs_before)
        if not _base_glb_path(job_id, base_outputs):
            raise RuntimeError("缺少完整多视角 3D 底模。请先生成多视角底模，再生成 3D 部件。")
        image_paths = [Path(item["normalized_path"]) for item in job.get("inputs", []) if isinstance(item, dict) and item.get("normalized_path")]
        image_paths = [path for path in image_paths if path.is_file()]
        if not image_paths:
            raise RuntimeError("没有可用于 3D 部件生成的 2D 部件输入图。")
        quality = str(job.get("quality") or "production").strip().lower()
        if quality not in {"high", "production"}:
            quality = "production"
        target_formats = list(job.get("target_formats") or ["glb", "fbx", "obj", "usdz", "3mf"])
        out_dir = store.job_dir(job_id) / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        meta_by_index = _component_meta_by_index(job)
        preflight_issues = _component_mesh_preflight_issues(meta_by_index)
        if preflight_issues:
            raise RuntimeError("3D 部件生成前检查未通过：" + "；".join(preflight_issues))
        existing_by_index = _cached_part_tasks_by_index(job_id, job)
        store.update_job(
            job_id,
            status="running",
            stage="generating_part_models",
            progress=12,
            started_at=store.now_iso(),
            finished_at=None,
            error=None,
            outputs={"base": base_outputs or {}},
        )
        for idx, image_path in enumerate(image_paths, start=1):
            meta = meta_by_index.get(idx, {})
            fingerprint = _component_input_fingerprint(image_path, meta)
            existing = existing_by_index.get(idx)
            if existing and existing.get("input_fingerprint") == fingerprint:
                reused = dict(existing)
                three_mf_reports = _ensure_3mf_exports_for_files(
                    job_id=job_id,
                    files=reused.get("files"),
                    target_formats=target_formats,
                )
                if three_mf_reports:
                    reused["three_mf_exports"] = three_mf_reports
                reused["reused"] = True
                subtasks.append(reused)
                total_credits = int((base_outputs or {}).get("consumed_credits") or 0) + sum(int(item.get("consumed_credits") or 0) for item in subtasks)
                store.update_job(
                    job_id,
                    stage=f"part_{idx}_already_done",
                    progress=min(92, 18 + int(idx / max(1, len(image_paths)) * 70)),
                    subtasks=subtasks,
                    outputs={"base": base_outputs or {}, "parts": subtasks},
                    consumed_credits=total_credits,
                )
                continue
            store.update_job(
                job_id,
                stage=f"generating_part_{idx}",
                progress=min(92, 18 + int((idx - 1) / max(1, len(image_paths)) * 70)),
            )
            part_dir = out_dir / f"part_{idx:02d}"
            part_dir.mkdir(parents=True, exist_ok=True)
            part_outputs = await _run_single_meshy_task(
                job_id=job_id,
                image_paths=[image_path],
                mode="image-to-3d",
                quality=quality,
                target_formats=target_formats,
                out_dir=part_dir,
                prefix=f"part_{idx:02d}_",
            )
            subtask = {
                "part_index": idx,
                "role": str(meta.get("role") or f"part_{idx:02d}"),
                "label": str(meta.get("label") or image_path.stem),
                "source": image_path.name,
                "source_box": meta.get("source_box"),
                "input_fingerprint": fingerprint,
                "provider_task_id": part_outputs.get("provider_task_id"),
                "files": part_outputs.get("files") or [],
                "mesh_metrics": part_outputs.get("mesh_metrics") or {},
                "consumed_credits": part_outputs.get("consumed_credits") or 0,
            }
            subtasks.append(subtask)
            total_credits = int((base_outputs or {}).get("consumed_credits") or 0) + sum(int(item.get("consumed_credits") or 0) for item in subtasks)
            store.update_job(
                job_id,
                subtasks=subtasks,
                outputs={"base": base_outputs or {}, "parts": subtasks},
                consumed_credits=total_credits,
            )
        total_credits = int((base_outputs or {}).get("consumed_credits") or 0) + sum(int(item.get("consumed_credits") or 0) for item in subtasks)
        store.update_job(
            job_id,
            status="preprocessed",
            stage="parts_3d_ready",
            progress=100,
            finished_at=store.now_iso(),
            subtasks=subtasks,
            outputs={"base": base_outputs or {}, "parts": subtasks},
            mesh_metrics={},
            consumed_credits=total_credits,
            provider_task_id=None,
            error=None,
        )
    except Exception as exc:
        logger.exception("[ai_3d_model] part models failed job_id=%s", job_id)
        store.update_job(
            job_id,
            status="failed",
            stage="parts_3d_failed",
            progress=100,
            finished_at=store.now_iso(),
            error=str(exc),
            subtasks=subtasks or (job.get("subtasks") if isinstance(job.get("subtasks"), list) else []),
            outputs={"base": base_outputs or {}, "parts": subtasks} if subtasks else {"base": base_outputs or {}},
            consumed_credits=total_credits,
        )


async def _run_part_assembly_background(job_id: str) -> None:
    job = store.load_job(job_id)
    if not job:
        return
    try:
        outputs_before = job.get("outputs") if isinstance(job.get("outputs"), dict) else {}
        base_outputs = _base_outputs_for_current_triview(job_id, job, outputs_before)
        subtasks = job.get("subtasks") if isinstance(job.get("subtasks"), list) else []
        if not subtasks and isinstance(outputs_before.get("parts"), list):
            subtasks = outputs_before.get("parts") or []
        if not _base_glb_path(job_id, base_outputs):
            raise RuntimeError("缺少完整多视角 3D 底模，不能做最终替换合成。")
        has_part_glb = False
        for item in subtasks:
            if not isinstance(item, dict):
                continue
            try:
                part_index = int(item.get("part_index") or 0)
            except Exception:
                part_index = 0
            if part_index > 0 and _part_glb_exists(job_id, part_index, item):
                has_part_glb = True
                break
        if not has_part_glb:
            raise RuntimeError("没有可用的 3D 部件 GLB。请先点击“生成 3D 部件”。")
        store.update_job(
            job_id,
            status="running",
            stage="assembling_parts",
            progress=94,
            started_at=store.now_iso(),
            finished_at=None,
            error=None,
            outputs={"base": base_outputs or {}, "parts": subtasks},
            subtasks=subtasks,
        )
        outputs, metrics = _assemble_part_outputs(job_id, job, subtasks, base_outputs=base_outputs)
        assembly = outputs.get("assembly") if isinstance(outputs.get("assembly"), dict) else {}
        if assembly.get("status") in {"failed", "skipped"}:
            store.update_job(
                job_id,
                status="failed",
                stage="assembly_failed",
                progress=100,
                finished_at=store.now_iso(),
                error=assembly.get("error") or assembly.get("reason") or "最终合成失败",
                outputs=outputs,
                mesh_metrics=metrics,
                subtasks=subtasks,
            )
            return
        store.update_job(
            job_id,
            status="succeeded",
            stage="completed",
            progress=100,
            finished_at=store.now_iso(),
            outputs=outputs,
            mesh_metrics=metrics,
            subtasks=subtasks,
            consumed_credits=int(job.get("consumed_credits") or 0),
            provider_task_id=None,
            error=None,
        )
    except Exception as exc:
        logger.exception("[ai_3d_model] part assembly failed job_id=%s", job_id)
        store.update_job(
            job_id,
            status="failed",
            stage="assembly_failed",
            progress=100,
            finished_at=store.now_iso(),
            error=str(exc),
        )


async def _run_base_model_background(job_id: str) -> None:
    job = store.load_job(job_id)
    if not job:
        return
    try:
        quality = str(job.get("quality") or "production").strip().lower()
        if quality not in {"high", "production"}:
            quality = "production"
        target_formats = list(job.get("target_formats") or ["glb", "fbx", "obj", "usdz", "3mf"])
        if "3mf" not in [str(fmt).lower().lstrip(".") for fmt in target_formats]:
            target_formats.append("3mf")
            job["target_formats"] = target_formats
        store.update_job(
            job_id,
            status="running",
            stage="generating_base_model",
            progress=12,
            started_at=store.now_iso(),
            error=None,
            target_formats=target_formats,
        )
        base_outputs, base_credits = await _run_or_reuse_base_model(
            job_id=job_id,
            job=job,
            quality=quality,
            target_formats=target_formats,
        )
        store.update_job(
            job_id,
            status="preprocessed",
            stage="base_model_ready",
            progress=100,
            finished_at=store.now_iso(),
            outputs={"base": base_outputs or {}},
            mesh_metrics=(base_outputs or {}).get("mesh_metrics") or {},
            provider_task_id=(base_outputs or {}).get("provider_task_id"),
            consumed_credits=int(base_credits or 0),
            error=None,
        )
    except Exception as exc:
        logger.exception("[ai_3d_model] base model failed job_id=%s", job_id)
        store.update_job(
            job_id,
            status="failed",
            stage="base_model_failed",
            progress=100,
            finished_at=store.now_iso(),
            error=str(exc),
        )


async def _run_triview_background(
    *,
    job_id: str,
    request: Request,
    current_user: _ServerUser,
    model: str,
) -> None:
    job = store.load_job(job_id)
    if not job:
        return
    db = SessionLocal()
    try:
        stored_plan = job.get("view_generation_plan") if isinstance(job.get("view_generation_plan"), dict) else {}
        model_id = _canonical_image_model(model or str(stored_plan.get("image_model") or job.get("image_model") or _SUTUI_GPT_IMAGE_2_MODEL))
        plan = _fresh_view_generation_plan(job, image_model=model_id)
        views = plan.get("views") if isinstance(plan.get("views"), list) else []
        if not views:
            raise RuntimeError("多视角模板为空")
        preprocessing = job.get("preprocessing") if isinstance(job.get("preprocessing"), dict) else {}
        source_inputs = preprocessing.get("source_inputs") if isinstance(preprocessing.get("source_inputs"), list) else []
        reference_input = source_inputs[0] if source_inputs else (job.get("inputs") or [{}])[0]
        reference_path = Path(str(reference_input.get("normalized_path") or ""))
        if not reference_path.exists():
            raise RuntimeError("找不到预处理后的参考图")
        ref_payload = _image_file_payload(reference_path)
        out_dir = store.job_dir(job_id) / "triview"
        out_dir.mkdir(parents=True, exist_ok=True)
        regenerate_for_understanding = bool(preprocessing.get("force_regenerate_triview"))
        view_understanding = preprocessing.get("triview_ai_understanding") if isinstance(preprocessing.get("triview_ai_understanding"), dict) else {}
        if regenerate_for_understanding or not view_understanding or view_understanding.get("prompt_version") != _VIEW_PROMPT_VERSION:
            store.update_job(job_id, status="generating_views", stage="planning_triview_with_ai", progress=6, preprocessing=preprocessing, error=None)
            view_understanding = await _ai_triview_understanding(
                job_id=job_id,
                request=request,
                reference_path=reference_path,
                preprocessing=preprocessing,
                asset_template=str(job.get("asset_template") or "auto"),
                description=str(job.get("description") or ""),
            )
            view_understanding["prompt_version"] = _VIEW_PROMPT_VERSION
            preprocessing["triview_ai_understanding"] = view_understanding
            store.update_job(job_id, preprocessing=preprocessing)
        plan = _fresh_view_generation_plan(job, image_model=model_id, view_understanding=view_understanding)
        _apply_retry_feedback_to_view_plan(plan, preprocessing)
        resolved_asset_template = str(plan.get("asset_template") or job.get("asset_template") or "auto")
        preprocessing["resolved_asset_template"] = resolved_asset_template
        primary_reference_path = reference_path
        primary_ref_payload = ref_payload
        primary_reference_input: Optional[Dict[str, Any]] = None
        if not _is_character_template(resolved_asset_template):
            primary_dest = out_dir / "01_source_reference_anchor.jpg"
            primary_meta = _save_primary_reference_crop(reference_path, primary_dest)
            primary_reference_path = primary_dest
            primary_ref_payload = _image_file_payload(primary_dest)
            primary_reference_input = _public_input(
                job_id=job_id,
                index=1,
                filename=primary_dest.name,
                normalized_path=primary_dest,
                meta=primary_meta,
                role="front",
                label="原图主视角锚点",
                source_filename=str(reference_input.get("filename") or ""),
                generated=False,
            )
            preprocessing["primary_reference_anchor"] = primary_reference_input
        views = plan.get("views") if isinstance(plan.get("views"), list) else []
        if not views:
            raise RuntimeError("triview generation plan is empty")
        identity_reference_path = out_dir / "identity_reference_board.jpg"
        identity_reference_meta = _make_identity_reference_board(job_id, preprocessing, identity_reference_path)
        if identity_reference_meta:
            preprocessing["triview_identity_reference"] = _public_input(
                job_id=job_id,
                index=0,
                filename=identity_reference_path.name,
                normalized_path=identity_reference_path,
                meta=identity_reference_meta,
                role="triview_identity_reference",
                label="多视角身份参考板",
                source_filename=str(reference_input.get("filename") or ""),
                generated=True,
            )
            triview_reference_path = primary_reference_path
            triview_ref_payload = primary_ref_payload
        else:
            triview_reference_path = primary_reference_path
            triview_ref_payload = primary_ref_payload
        regenerate_all = bool(preprocessing.get("force_regenerate_triview"))
        existing_generated = [] if regenerate_all else preprocessing.get("triview_inputs")
        generated: List[Dict[str, Any]] = list(existing_generated) if isinstance(existing_generated, list) else []
        if regenerate_all:
            generated = []
        sheet_dest = out_dir / "triview_sheet.jpg"

        if not generated or regenerate_all:
            sheet_item = next((item for item in views if str(item.get("view") or "") == "triview_sheet"), None)
            if not sheet_item:
                raise RuntimeError("高清多视角模板为空")
            sheet_views = _valid_sheet_views(sheet_item.get("sheet_views"), fallback=_sheet_views_for_template(str(plan.get("asset_template") or "")))
            sheet_label = str(sheet_item.get("label") or "高清多视角板")
            generated = []
            use_sheet_generation = str(plan.get("view_generation_mode") or "sheet") == "sheet"
            hard_surface_grouped_sheet = use_sheet_generation and not _is_character_template(str(plan.get("asset_template") or ""))
            if hard_surface_grouped_sheet:
                first_roles = [role for role in ("front", "front_left_45", "front_right_45") if role in sheet_views]
                second_roles = [role for role in ("side", "back") if role in sheet_views]
                if len(first_roles) < 2 or not second_roles:
                    raise RuntimeError("硬表面多视角分组模板为空")
                first_prompt = (
                    str(sheet_item.get("prompt") or "")
                    + " Generate only the first grouped sheet now: exactly three large views, left to right: source/front view, front-left 45 degree view, front-right 45 degree view. "
                    "Do not include side or back in this image. Use the full canvas space; each view must be large, not narrow, not squeezed, and not a tiny strip. "
                    "Keep all three views in the same generated style, scale, projection, and design."
                )
                second_prompt = (
                    str(sheet_item.get("prompt") or "")
                    + " Generate only the second grouped sheet now: exactly two large views, left to right: strict side view and inferred back view. "
                    "Use the provided reference board, which contains the original source and the approved front/45-degree sheet. "
                    "Do not include front or 45-degree views in this image. Use the full canvas space; each view must be large, not narrow, not squeezed, and not a tiny strip. "
                    "The side/back must look like the same asset from the first grouped sheet turned around, not a new wider facade or another construction."
                )

                store.update_job(job_id, status="generating_views", stage="generating_front_45_sheet", progress=10, preprocessing=preprocessing)
                first_sheet_dest = out_dir / "triview_sheet_front45.jpg"
                first_result = await _generate_image_stage_core(
                    job_id=job_id,
                    request=request,
                    current_user=current_user,
                    db=db,
                    prompt=first_prompt,
                    model=model_id,
                    aspect_ratio="16:9",
                    ref_payload=triview_ref_payload,
                    reference_path=triview_reference_path,
                    preprocessing=preprocessing,
                )
                first_images = first_result.get("images") if isinstance(first_result.get("images"), list) else []
                if not first_images:
                    raise RuntimeError("前三视角板生成没有返回图片")
                first_sheet_meta = await _save_generated_preview_image(first_images[0], first_sheet_dest)
                preprocessing["triview_front45_sheet"] = _public_input(
                    job_id=job_id,
                    index=0,
                    filename=first_sheet_dest.name,
                    normalized_path=first_sheet_dest,
                    meta=first_sheet_meta,
                    role="triview_front45_sheet",
                    label="前三视角板",
                    source_filename=str(reference_input.get("filename") or ""),
                    generated=True,
                )
                store.update_job(job_id, status="generating_views", stage="splitting_front_45_sheet", progress=42, preprocessing=preprocessing)
                split_first = _split_side_back_sheet(first_sheet_dest, out_dir, sheet_views=first_roles)

                sideback_reference_dest = out_dir / "side_back_reference_board.jpg"
                sideback_reference_meta = _make_unlabeled_reference_board(
                    [primary_reference_path, first_sheet_dest],
                    sideback_reference_dest,
                    cell=(1200, 900),
                )
                if not sideback_reference_meta:
                    raise RuntimeError("侧背视角参考板生成失败")
                preprocessing["side_back_reference_board"] = _public_input(
                    job_id=job_id,
                    index=0,
                    filename=sideback_reference_dest.name,
                    normalized_path=sideback_reference_dest,
                    meta=sideback_reference_meta,
                    role="side_back_reference_board",
                    label="侧背视角参考板",
                    source_filename=str(reference_input.get("filename") or ""),
                    generated=True,
                )

                store.update_job(job_id, status="generating_views", stage="generating_side_back_sheet", progress=52, preprocessing=preprocessing)
                sideback_dest = out_dir / "triview_sheet_side_back.jpg"
                sideback_result = await _generate_image_stage_core(
                    job_id=job_id,
                    request=request,
                    current_user=current_user,
                    db=db,
                    prompt=second_prompt,
                    model=model_id,
                    aspect_ratio="16:9",
                    ref_payload=_image_file_payload(sideback_reference_dest),
                    reference_path=sideback_reference_dest,
                    preprocessing=preprocessing,
                )
                sideback_images = sideback_result.get("images") if isinstance(sideback_result.get("images"), list) else []
                if not sideback_images:
                    raise RuntimeError("侧背视角板生成没有返回图片")
                sideback_meta = await _save_generated_preview_image(sideback_images[0], sideback_dest)
                preprocessing["triview_side_back_sheet"] = _public_input(
                    job_id=job_id,
                    index=0,
                    filename=sideback_dest.name,
                    normalized_path=sideback_dest,
                    meta=sideback_meta,
                    role="triview_side_back_sheet",
                    label="侧背视角板",
                    source_filename=str(reference_input.get("filename") or ""),
                    generated=True,
                )
                store.update_job(job_id, status="generating_views", stage="splitting_side_back_sheet", progress=76, preprocessing=preprocessing)
                split_second = _split_side_back_sheet(sideback_dest, out_dir, sheet_views=second_roles)
                grouped_parts = list(split_first) + list(split_second)
                for idx, part in enumerate(grouped_parts, start=1):
                    role = str(part.get("role") or "")
                    part_path = Path(str(part["path"]))
                    generated.append(_public_input(
                        job_id=job_id,
                        index=idx,
                        filename=part_path.name,
                        normalized_path=part_path,
                        meta={
                            "width": part.get("width"),
                            "height": part.get("height"),
                            "source_box": part.get("source_box"),
                        },
                        role=role,
                        label=str(part.get("label") or _VIEW_ROLE_LABELS.get(role, role)),
                        source_filename=str(reference_input.get("filename") or ""),
                        generated=True,
                    ))
            elif use_sheet_generation:
                store.update_job(job_id, status="generating_views", stage="generating_triview_sheet", progress=10, preprocessing=preprocessing)
                result = await _generate_image_stage_core(
                    job_id=job_id,
                    request=request,
                    current_user=current_user,
                    db=db,
                    prompt=str(sheet_item.get("prompt") or ""),
                    model=model_id,
                    aspect_ratio="16:9",
                    ref_payload=triview_ref_payload,
                    reference_path=triview_reference_path,
                    preprocessing=preprocessing,
                )
                images = result.get("images") if isinstance(result.get("images"), list) else []
                if not images:
                    raise RuntimeError("高清多视角生成没有返回图片")
                sheet_meta = await _save_generated_preview_image(images[0], sheet_dest)
                sheet_input = _public_input(
                    job_id=job_id,
                    index=0,
                    filename=sheet_dest.name,
                    normalized_path=sheet_dest,
                    meta=sheet_meta,
                    role="triview_sheet",
                    label=sheet_label,
                    source_filename=str(reference_input.get("filename") or ""),
                    generated=True,
                )
                preprocessing["triview_sheet"] = sheet_input
                store.update_job(job_id, status="generating_views", stage="splitting_triview_sheet", progress=72)
                split_views = _split_side_back_sheet(sheet_dest, out_dir, sheet_views=sheet_views)
                # Characters benefit from preserving the exact source front.
                # Hard-surface sheet mode should keep all five generated views
                # from the same sheet so style, scale, and projection match.
                use_reference_front_anchor = _is_character_template(str(plan.get("asset_template") or ""))
                for part in split_views:
                    role = str(part.get("role") or "")
                    part_path = Path(str(part["path"]))
                    part_meta = {
                        "width": part.get("width"),
                        "height": part.get("height"),
                        "source_box": part.get("source_box"),
                    }
                    label = str(part.get("label") or "")
                    if use_reference_front_anchor and role == "front":
                        front_dest = out_dir / "01_front_reference_anchor.jpg"
                        front_source = primary_reference_path if isinstance(primary_reference_input, dict) else reference_path
                        part_meta = _copy_reference_front_view(front_source, front_dest)
                        part_meta["source_box"] = [0, 0, part_meta.get("width"), part_meta.get("height")]
                        part_meta["reference_front_anchor"] = True
                        part_path = front_dest
                        label = "原图主视角锚点"
                    generated.append(_public_input(
                        job_id=job_id,
                        index=int(part["index"]),
                        filename=part_path.name,
                        normalized_path=part_path,
                        meta=part_meta,
                        role=role,
                        label=label,
                        source_filename=str(reference_input.get("filename") or ""),
                        generated=not bool(part_meta.get("reference_front_anchor")),
                    ))
            else:
                preprocessing.pop("triview_sheet", None)
                view_lookup = {str(item.get("view") or ""): item for item in views if isinstance(item, dict)}
                for idx, role in enumerate(sheet_views, start=1):
                    view_item = view_lookup.get(role) or {}
                    label = str(view_item.get("label") or _VIEW_ROLE_LABELS.get(role, role))
                    if role == "front" and isinstance(primary_reference_input, dict):
                        generated.append(dict(primary_reference_input, index=idx, role=role, label="原图主视角锚点"))
                        continue
                    progress = min(90, 10 + int((idx - 1) / max(1, len(sheet_views)) * 78))
                    store.update_job(
                        job_id,
                        status="generating_views",
                        stage=f"generating_view_{role}",
                        progress=progress,
                        preprocessing=preprocessing,
                    )
                    result = await _generate_image_stage_core(
                        job_id=job_id,
                        request=request,
                        current_user=current_user,
                        db=db,
                        prompt=str(view_item.get("prompt") or sheet_item.get("prompt") or ""),
                        model=model_id,
                        aspect_ratio="4:3",
                        ref_payload=triview_ref_payload,
                        reference_path=triview_reference_path,
                        preprocessing=preprocessing,
                    )
                    images = result.get("images") if isinstance(result.get("images"), list) else []
                    if not images:
                        raise RuntimeError(f"{label}生成没有返回图片")
                    view_dest = out_dir / f"{idx:02d}_{role}.jpg"
                    view_meta = await _save_generated_preview_image(images[0], view_dest)
                    generated.append(_public_input(
                        job_id=job_id,
                        index=idx,
                        filename=view_dest.name,
                        normalized_path=view_dest,
                        meta=view_meta,
                        role=role,
                        label=label,
                        source_filename=str(reference_input.get("filename") or ""),
                        generated=True,
                    ))
            for item in generated:
                for view in plan.get("views", []):
                    if isinstance(view, dict) and str(view.get("view") or "") == str(item.get("role") or ""):
                        view["preview_url"] = item["preview_url"]
                        view["filename"] = item["filename"]
            plan["generated_inputs"] = generated
            preprocessing["triview_inputs_partial"] = generated
            store.update_job(
                job_id,
                status="generating_views",
                stage="generated_triview_views",
                progress=92,
                inputs=generated,
                preprocessing=preprocessing,
                view_generation_plan=plan,
                asset_template=resolved_asset_template,
                image_model=model_id,
                error=None,
            )
        store.update_job(
            job_id,
            status="generating_views",
            stage="verifying_multiview_consistency",
            progress=94,
            inputs=generated,
            preprocessing=preprocessing,
            view_generation_plan=plan,
            asset_template=resolved_asset_template,
            image_model=model_id,
            error=None,
        )
        verification = await _ai_verify_multiview_consistency(
            job_id=job_id,
            request=request,
            reference_path=primary_reference_path,
            generated_inputs=generated,
            review_sheet_path=out_dir / "multiview_review_sheet.jpg",
            preprocessing=preprocessing,
            asset_template=resolved_asset_template,
        )
        preprocessing["triview_consistency_verification"] = verification
        preprocessing["triview_quality_gate"] = "passed" if verification.get("passed") else "failed"
        if not verification.get("passed"):
            generated_roles_for_gate = [str(item.get("role") or "") for item in generated if isinstance(item, dict)]
            reliable_roles = [role for role in ("front", "front_left_45", "front_right_45") if role in generated_roles_for_gate]
            if not _is_character_template(resolved_asset_template) and len(reliable_roles) >= 2:
                preprocessing["triview_quality_gate"] = "partial_pass"
                preprocessing["triview_usable_roles"] = reliable_roles
                preprocessing["triview_excluded_roles"] = [
                    role for role in generated_roles_for_gate
                    if role in _VIEW_ROLE_ORDER and role not in reliable_roles
                ]
                preprocessing["triview_partial_pass_reason"] = (
                    "侧面/背面一致性不足，已自动排除；Meshy 只使用通过保真度更高的主视角/45度视角。"
                )
                plan["generated_inputs"] = generated
                plan["usable_roles_for_3d"] = reliable_roles
                plan["excluded_roles_for_3d"] = preprocessing["triview_excluded_roles"]
                generated_roles = [str(item.get("role") or "") for item in generated if isinstance(item, dict)]
                preprocessing.pop("force_regenerate_triview", None)
                preprocessing["triview_generated"] = True
                preprocessing["triview_inputs"] = generated
                preprocessing["triview_inputs_partial"] = generated
                preprocessing["triview_sheet_views"] = generated_roles
                meshy_roles = [role for role, _ in _meshy_base_image_items({"preprocessing": preprocessing})]
                notes = list(job.get("quality_notes") or [])
                notes.append("已生成五个候选视角；侧面/背面复核未通过，系统会自动排除坏视角，不送入 Meshy。")
                notes.append(f"确认生成 3D 时将使用：{_sheet_view_names(meshy_roles)}。")
                store.update_job(
                    job_id,
                    status="preprocessed",
                    stage="triview_completed",
                    progress=100,
                    inputs=generated,
                    mode="multi-image-to-3d",
                    strategy="multi_view",
                    provider="meshy",
                    final_3d_provider="meshy",
                    image_stage_provider="image_model",
                    preprocessing=preprocessing,
                    view_generation_plan=plan,
                    asset_template=resolved_asset_template,
                    image_model=model_id,
                    quality_notes=notes,
                    error=None,
                )
                return
            store.update_job(
                job_id,
                status="preprocessed",
                stage="triview_failed",
                progress=100,
                inputs=generated,
                preprocessing=preprocessing,
                view_generation_plan=plan,
                asset_template=resolved_asset_template,
                image_model=model_id,
                error=(
                    f"多视角一致性复核未通过：score={verification.get('score')}, "
                    f"issues={verification.get('issues')}"
                ),
            )
            raise RuntimeError(
                f"多视角一致性复核未通过：score={verification.get('score')}, "
                f"issues={verification.get('issues')}。系统不会把这批图送入 3D。"
            )
        plan["image_model"] = model_id
        plan["stage_provider"] = "image_model"
        plan["uses_meshy"] = False
        plan["stage_note"] = "多视角由图片模型生成，确认 3D 后才调用 Meshy。"
        plan["generated_inputs"] = generated
        generated_roles = [str(item.get("role") or "") for item in generated if isinstance(item, dict)]
        preprocessing.pop("force_regenerate_triview", None)
        preprocessing["triview_generated"] = True
        preprocessing["triview_inputs"] = generated
        preprocessing["triview_inputs_partial"] = generated
        preprocessing["triview_sheet_views"] = generated_roles
        meshy_roles = [role for role, _ in _meshy_base_image_items({"preprocessing": {"triview_inputs": generated}})]
        notes = list(job.get("quality_notes") or [])
        notes.append("多视角图已由图片模型生成；这一步未调用 Meshy，也未消耗 Meshy 3D credits。")
        notes.append(
            f"已生成{_sheet_view_names(generated_roles)}；确认后将按 Meshy 多图接口限制，"
            f"使用{_sheet_view_names(meshy_roles)}生成 3D 底模。"
        )
        store.update_job(
            job_id,
            status="preprocessed",
            stage="triview_completed",
            progress=100,
            inputs=generated,
            mode="multi-image-to-3d",
            strategy="multi_view",
            provider="meshy",
            final_3d_provider="meshy",
            image_stage_provider="image_model",
            preprocessing=preprocessing,
            view_generation_plan=plan,
            asset_template=resolved_asset_template,
            image_model=model_id,
            quality_notes=notes,
        )
    except Exception as exc:
        logger.exception("[ai_3d_model] triview failed job_id=%s", job_id)
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        latest = store.load_job(job_id) or job
        latest_preprocessing = latest.get("preprocessing") if isinstance(latest.get("preprocessing"), dict) else {}
        partial_count = len(latest_preprocessing.get("triview_inputs_partial") or [])
        latest_preprocessing.pop("force_regenerate_triview", None)
        notes = list(latest.get("quality_notes") or [])
        notes.append(
            f"多视角生成失败：{detail}。任务和已生成视图已保留"
            f"{f'（已生成 {partial_count} 张）' if partial_count else ''}；为保证一致性，系统不会自动切换模型或改用低参考强度生成。"
        )
        store.update_job(
            job_id,
            status="preprocessed",
            stage="triview_failed",
            progress=100,
            error=str(detail),
            preprocessing=latest_preprocessing,
            quality_notes=notes,
        )
    finally:
        db.close()


async def _run_component_split_background(
    *,
    job_id: str,
    request: Request,
    current_user: _ServerUser,
    model: str,
) -> None:
    job = store.load_job(job_id)
    if not job:
        return
    db = SessionLocal()
    try:
        preprocessing = job.get("preprocessing") if isinstance(job.get("preprocessing"), dict) else {}
        root = store.job_dir(job_id)
        model_id = _canonical_image_model(model or str(job.get("image_model") or _SUTUI_GPT_IMAGE_2_MODEL))
        source_for_plan = None
        triview_inputs = preprocessing.get("triview_inputs") if isinstance(preprocessing.get("triview_inputs"), list) else []
        for item in triview_inputs:
            if isinstance(item, dict) and str(item.get("role") or "") == "front":
                source_for_plan = item
                break
        if not isinstance(source_for_plan, dict):
            raise RuntimeError("高精度拆件必须先生成多视角图；拆件规划只使用正面锚点图，不回退到单图低精度裁切。")
        reference_path = Path(str(source_for_plan.get("normalized_path") or ""))
        if not reference_path.exists():
            raise RuntimeError("找不到可用于生成拆件板的参考图。")
        preprocessing.pop("component_split_generated", None)
        preprocessing.pop("component_sheet", None)
        preprocessing.pop("component_inputs", None)
        preprocessing.pop("component_reference_sheet", None)
        preprocessing.pop("component_reference_inputs", None)
        preprocessing.pop("component_reference_mode", None)
        if _is_character_template(str(job.get("asset_template") or "")):
            await _run_see_through_component_split(
                job_id=job_id,
                job=job,
                preprocessing=preprocessing,
                source_for_plan=source_for_plan,
                reference_path=reference_path,
            )
            return
        store.update_job(job_id, status="splitting_parts", stage="planning_components_with_ai", progress=12, error=None)
        ai_plan = await _ai_component_plan(
            job_id=job_id,
            request=request,
            reference_path=reference_path,
            preprocessing=preprocessing,
            asset_template=str(job.get("asset_template") or "auto"),
            description=str(job.get("description") or ""),
            max_parts=int(preprocessing.get("max_parts") or _AI3D_DEFAULT_MAX_PARTS),
        )
        preprocessing["component_ai_plan"] = ai_plan
        preprocessing["component_plan_source"] = "image.understand"
        store.update_job(job_id, preprocessing=preprocessing)
        if preprocessing.get("component_plan_source") != "image.understand":
            raise RuntimeError("AI 拆件规划失败，已停止；不会使用模板裁切或 rembg 兜底。")
        store.update_job(job_id, status="splitting_parts", stage="generating_semantic_component_parts", progress=32, preprocessing=preprocessing, error=None)
        part_slots = _component_slots_from_plan(preprocessing, max_parts=int(preprocessing.get("max_parts") or _AI3D_DEFAULT_MAX_PARTS))
        preprocessing["component_slots"] = [{"role": role, "label": label} for role, label in part_slots]
        component_dir = root / "components" / "semantic_parts"
        refs_dir = component_dir / "part_references"
        outputs_dir = component_dir / "parts"
        mesh_inputs_dir = component_dir / "mesh_inputs"
        component_dir.mkdir(parents=True, exist_ok=True)
        sheet_path = component_dir / "component_sheet.jpg"
        plan_lookup = _part_plan_lookup(preprocessing)
        component_inputs: List[Dict[str, Any]] = []
        part_reference_inputs: List[Dict[str, Any]] = []
        max_generate = len(part_slots)
        for idx, (role, label) in enumerate(part_slots[:max_generate], start=1):
            part_plan = plan_lookup.get(role) or {"role": role, "label": label}
            ref_path = refs_dir / f"{idx:02d}_{role}_reference.jpg"
            ref_meta = _make_component_part_reference(reference_path, part_plan, ref_path)
            ref_input = _public_input(
                job_id=job_id,
                index=idx,
                filename=ref_path.name,
                normalized_path=ref_path,
                meta=ref_meta,
                role=f"{role}_reference",
                label=f"{label}定位参考",
                source_filename=str(source_for_plan.get("filename") or ""),
                generated=True,
            )
            part_reference_inputs.append(ref_input)
            preprocessing["component_reference_inputs"] = part_reference_inputs
            preprocessing["component_inputs_partial"] = component_inputs
            store.update_job(
                job_id,
                status="splitting_parts",
                stage=f"preparing_semantic_component_part_{idx:02d}",
                progress=34 + int((idx - 1) / max(1, max_generate) * 34),
                inputs=component_inputs,
                preprocessing=preprocessing,
                error=None,
            )
            prompt = _component_part_prompt(
                asset_template=str(job.get("asset_template") or "auto"),
                reference_strength=str(job.get("reference_strength") or "high"),
                role=role,
                label=label,
                reason=str(part_plan.get("reason") or ""),
                description=str(job.get("description") or ""),
            )
            progress = 34 + int((idx - 1) / max(1, max_generate) * 34)
            store.update_job(
                job_id,
                status="splitting_parts",
                stage=f"generating_semantic_component_part_{idx:02d}",
                progress=progress,
                preprocessing={
                    **preprocessing,
                    "component_reference_inputs": part_reference_inputs,
                    "component_inputs_partial": component_inputs,
                },
                error=None,
            )
            result = await _generate_image_stage_core(
                job_id=job_id,
                request=request,
                current_user=current_user,
                db=db,
                prompt=prompt,
                model=model_id,
                aspect_ratio="1:1",
                ref_payload=_image_file_payload(ref_path),
                reference_path=ref_path,
                preprocessing=preprocessing,
            )
            images = result.get("images") if isinstance(result.get("images"), list) else []
            if not images:
                raise RuntimeError(f"图片模型没有返回可用部件：{label}")
            part_path = outputs_dir / f"{idx:02d}_{role}.png"
            part_meta = await _save_generated_preview_image(images[0], part_path)
            part_input = _public_input(
                job_id=job_id,
                index=idx,
                filename=part_path.name,
                normalized_path=part_path,
                meta={
                    "width": part_meta.get("width"),
                    "height": part_meta.get("height"),
                    "source_box": ref_meta.get("source_box"),
                    "crop_applied": False,
                    "mesh_input_kind": "ai_generated_isolated_part",
                },
                role=role,
                label=label,
                source_filename=ref_path.name,
                generated=True,
            )
            part_input["mesh_input_kind"] = "ai_generated_isolated_part"
            part_input["part_reason"] = str(part_plan.get("reason") or "")
            component_inputs.append(part_input)
            partial_sheet_meta = _make_generated_component_sheet(component_inputs, sheet_path)
            partial_sheet = _public_input(
                job_id=job_id,
                index=0,
                filename=sheet_path.name,
                normalized_path=sheet_path,
                meta=partial_sheet_meta,
                role="component_sheet",
                label="AI 逐部件拼板",
                source_filename=str(source_for_plan.get("filename") or ""),
                generated=True,
            )
            preprocessing["component_reference_inputs"] = part_reference_inputs
            preprocessing["component_inputs_partial"] = component_inputs
            preprocessing["component_sheet_partial"] = partial_sheet
            store.update_job(
                job_id,
                status="splitting_parts",
                stage=f"generated_semantic_component_part_{idx:02d}",
                progress=34 + int(idx / max(1, max_generate) * 34),
                inputs=component_inputs,
                preprocessing=preprocessing,
                error=None,
            )
        if len(component_inputs) < max(1, min(len(part_slots), 6)):
            raise RuntimeError(f"逐部件生成数量不足：{len(component_inputs)}/{len(part_slots)}")
        sheet_meta = _make_generated_component_sheet(component_inputs, sheet_path)
        sheet_input = _public_input(
            job_id=job_id,
            index=0,
            filename=sheet_path.name,
            normalized_path=sheet_path,
            meta=sheet_meta,
            role="component_sheet",
            label="GPT Image 2 孤立部件输入板",
            source_filename=str(source_for_plan.get("filename") or ""),
            generated=True,
        )
        preprocessing["component_reference_inputs"] = part_reference_inputs
        preprocessing["component_inputs_partial"] = component_inputs
        preprocessing["component_sheet_partial"] = sheet_input
        preprocessing["component_source_mode"] = "semantic_image_component_parts"
        preprocessing["component_mesh_input_mode"] = "ai_generated_isolated_parts"
        store.update_job(job_id, status="splitting_parts", stage="verifying_semantic_component_parts", progress=72, preprocessing=preprocessing, inputs=component_inputs)
        uses_source_pixel_inputs = (
            preprocessing.get("component_mesh_input_mode") == "source_pixel_crop"
            or all(isinstance(item, dict) and item.get("fidelity_source") for item in component_inputs)
        )
        if uses_source_pixel_inputs:
            verify = {
                "passed": True,
                "score": 100,
                "skipped": True,
                "reason": "source_pixel_crop inputs preserve source pixels; AI redraw verification is not applicable",
            }
        else:
            verify = await _ai_verify_component_sheet(
                job_id=job_id,
                request=request,
                reference_path=reference_path,
                sheet_path=sheet_path,
                preprocessing=preprocessing,
                part_slots=part_slots,
            )
        preprocessing["component_ai_verification"] = verify
        if not verify.get("passed"):
            preprocessing["component_failed_preview_inputs"] = component_inputs
            preprocessing["component_failed_preview_sheet"] = sheet_input
            preprocessing["component_ai_redraw_quality_gate"] = "failed"
            store.update_job(
                job_id,
                status="splitting_parts",
                stage="fallback_to_fidelity_source_crops",
                progress=78,
                inputs=component_inputs,
                preprocessing=preprocessing,
                error=None,
            )
            fidelity_sheet, fidelity_inputs, fidelity_sheet_meta = _make_fidelity_component_inputs_from_plan(
                job_id=job_id,
                reference_path=reference_path,
                source_filename=str(source_for_plan.get("filename") or ""),
                component_dir=component_dir,
                preprocessing=preprocessing,
                part_slots=part_slots,
            )
            gate_passed, gate_meta = _component_sheet_quality_gate(fidelity_inputs, fidelity_sheet_meta, part_slots=part_slots)
            preprocessing["component_quality_gate"] = "passed" if gate_passed else "failed"
            preprocessing["component_quality_gate_meta"] = gate_meta
            if not gate_passed:
                raise RuntimeError(
                    f"AI 逐部件复核未通过：score={verify.get('score')}, issues={verify.get('issues')}。"
                    f"已尝试切换原图保真裁切，但质量门仍未通过：{json.dumps(gate_meta, ensure_ascii=False)}。"
                )
            preprocessing["component_sheet"] = fidelity_sheet
            preprocessing["component_inputs"] = fidelity_inputs
            preprocessing["component_source_mode"] = "fidelity_source_crops"
            preprocessing["component_mesh_input_mode"] = "source_pixel_crop"
            preprocessing["component_split_generated"] = True
            notes = list(job.get("quality_notes") or [])
            notes.append(
                f"AI 重绘拆件复核未通过 score={verify.get('score')}，已自动切换为原图像素保真裁切部件输入；失败的重绘板仅保留为排查预览。"
            )
            notes.append("保真裁切避免换设计，但不是完美语义抠图；融合太深的部件仍建议保留在完整底模。")
            store.update_job(
                job_id,
                status="preprocessed",
                stage="component_split_completed",
                progress=100,
                inputs=fidelity_inputs,
                mode="part_batch",
                strategy="part_batch",
                provider="meshy",
                final_3d_provider="meshy",
                image_stage_provider="image_model",
                preprocessing=preprocessing,
                image_model=model_id,
                quality_notes=notes,
            )
            return
        gate_passed, gate_meta = _component_sheet_quality_gate(component_inputs, sheet_meta, part_slots=part_slots)
        preprocessing["component_quality_gate"] = "passed" if gate_passed else "failed"
        preprocessing["component_quality_gate_meta"] = gate_meta
        if not gate_passed:
            raise RuntimeError(f"AI 部件板质量门未通过：{json.dumps(gate_meta, ensure_ascii=False)}")
        preprocessing["component_sheet"] = sheet_input
        preprocessing["component_inputs"] = component_inputs
        preprocessing["component_source_mode"] = "semantic_image_component_parts"
        preprocessing["component_mesh_input_mode"] = "ai_generated_isolated_parts"
        preprocessing["component_split_generated"] = True
        notes = list(job.get("quality_notes") or [])
        notes.append("已使用 GPT Image 2 根据红框参考生成干净孤立部件输入图；裁剪图只用于定位参考，不直接送入 3D。")
        notes.append("当前步骤只生成 2D 部件输入图；下一步可单独生成 Meshy 3D 部件，确认后再与多视角底模合成。")
        store.update_job(
            job_id,
            status="preprocessed",
            stage="component_split_completed",
            progress=100,
            inputs=component_inputs,
            mode="part_batch",
            strategy="part_batch",
            provider="meshy",
            final_3d_provider="meshy",
            image_stage_provider="image_model",
            preprocessing=preprocessing,
            image_model=model_id,
            quality_notes=notes,
        )
    except Exception as exc:
        logger.exception("[ai_3d_model] component split failed job_id=%s", job_id)
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        latest = store.load_job(job_id) or job
        latest_preprocessing = latest.get("preprocessing") if isinstance(latest.get("preprocessing"), dict) else {}
        latest_preprocessing.pop("component_split_generated", None)
        latest_preprocessing.pop("component_sheet", None)
        latest_preprocessing.pop("component_inputs", None)
        partial_components = latest_preprocessing.get("component_inputs_partial")
        partial_sheet = latest_preprocessing.get("component_sheet_partial")
        if isinstance(partial_components, list) and partial_components:
            latest_preprocessing["component_failed_preview_inputs"] = partial_components
        if isinstance(partial_sheet, dict):
            latest_preprocessing["component_failed_preview_sheet"] = partial_sheet
        latest_preprocessing["component_quality_gate"] = "failed"
        safe_inputs = _safe_frontend_generation_inputs(latest, latest_preprocessing)
        next_strategy = "multi_view" if _looks_like_triview_inputs(safe_inputs) else "candidate_preview"
        next_mode = "multi-image-to-3d" if next_strategy == "multi_view" else "preprocess-preview"
        notes = list(latest.get("quality_notes") or [])
        notes.append(
            f"真实部件分离失败：{detail}。系统不会使用模板裁切、rembg 或低质量结果兜底进入 part_batch。"
        )
        store.update_job(
            job_id,
            status="preprocessed",
            stage="component_split_failed",
            progress=100,
            error=str(detail),
            inputs=safe_inputs,
            mode=next_mode,
            strategy=next_strategy,
            preprocessing=latest_preprocessing,
            quality_notes=notes,
        )
    finally:
        db.close()


@router.get("/api/ai-3d-model/config")
async def ai_3d_model_config(_: _ServerUser = Depends(_ai3d_local_user)):
    data: Dict[str, Any] = {
        "configured": meshy.is_configured(),
        "provider": "meshy",
        "provider_label": "Meshy 3D",
        "final_3d_provider": "meshy",
        "image_stage_provider": "image_model",
        "image_stage_models": [_SUTUI_GPT_IMAGE_2_MODEL, "nano-banana-2"],
        "triview_uses_meshy": False,
        "component_split_uses_meshy": False,
        "local_3mf_export": {
            "configured": model_3mf.is_available(),
            "source_format": "stl_preferred_glb_fallback",
            "quality_gate": "watertight_mesh_required",
        },
        "see_through": see_through.health(),
    }
    if data["configured"]:
        try:
            data["balance"] = (await meshy.get_balance()).get("balance")
        except Exception as exc:
            data["balance_error"] = str(exc)
    return data


@router.post("/api/ai-3d-model/jobs")
async def ai_3d_model_create_job(
    background_tasks: BackgroundTasks,
    request: Request,
    files: Optional[List[UploadFile]] = File(None),
    strategy: str = Form("auto"),
    quality: str = Form("production"),
    formats: str = Form("glb,fbx,obj,usdz,3mf"),
    title: str = Form(""),
    auto_decompose: bool = Form(True),
    max_parts: int = Form(_AI3D_DEFAULT_MAX_PARTS),
    preprocess_only: bool = Form(False),
    asset_template: str = Form("auto"),
    reference_strength: str = Form("high"),
    description: str = Form(""),
    image_model: str = Form(_SUTUI_GPT_IMAGE_2_MODEL),
    _: _ServerUser = Depends(_ai3d_local_user),
):
    strategy = (strategy or "auto").strip().lower()
    if strategy not in {"auto", "multi_view", "part_batch"}:
        raise HTTPException(status_code=400, detail="strategy must be auto, multi_view or part_batch")
    quality = (quality or "production").strip().lower()
    if quality not in {"high", "production"}:
        quality = "production"
    target_formats = [x.strip().lower().lstrip(".") for x in (formats or "glb").split(",") if x.strip()]
    if not target_formats:
        target_formats = ["glb"]
    max_parts = max(1, min(_AI3D_ABSOLUTE_MAX_PARTS, int(max_parts or _AI3D_DEFAULT_MAX_PARTS)))
    asset_template = _canonical_asset_template(asset_template)
    reference_strength = (reference_strength or "high").strip().lower()
    if reference_strength not in {"strict", "high", "balanced", "creative"}:
        reference_strength = "high"
    image_model = _canonical_image_model(image_model or _SUTUI_GPT_IMAGE_2_MODEL)
    character_template = _is_character_template(asset_template)
    upload_files = [f for f in (files or []) if f and f.filename]
    if not upload_files:
        raise HTTPException(status_code=400, detail="请上传至少一张图片或一个 zip 压缩包")

    job_id = store.new_job_id()
    root = store.job_dir(job_id)
    raw_dir = root / "raw"
    norm_dir = root / "normalized"
    raw_dir.mkdir(parents=True, exist_ok=True)
    norm_dir.mkdir(parents=True, exist_ok=True)

    raw_image_paths: List[Path] = []
    for idx, upload in enumerate(upload_files, start=1):
        filename = _safe_name(upload.filename or f"upload-{idx}", fallback=f"upload-{idx}")
        max_bytes = _MAX_ZIP_BYTES if _is_zip_name(filename) else _MAX_UPLOAD_BYTES
        raw_path = raw_dir / f"{idx:02d}-{filename}"
        await _save_upload_file(upload, raw_path, max_bytes=max_bytes)
        if _is_zip_name(filename):
            raw_image_paths.extend(_extract_zip_images(raw_path, root))
        elif _is_image_name(filename):
            raw_image_paths.append(raw_path)
        else:
            raise HTTPException(status_code=400, detail=f"不支持的文件类型：{upload.filename}")

    if not raw_image_paths:
        raise HTTPException(status_code=400, detail="没有找到可用图片。支持 jpg/jpeg/png/webp 或包含这些图片的 zip")
    raw_image_paths = raw_image_paths[:_MAX_INPUT_IMAGES]

    inputs: List[Dict[str, Any]] = []
    for idx, raw_path in enumerate(raw_image_paths, start=1):
        norm_path = norm_dir / f"input_{idx:02d}.jpg"
        try:
            meta = _normalize_image(raw_path, norm_path, auto_crop=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"图片无法读取：{raw_path.name}") from exc
        inputs.append(_public_input(
            job_id=job_id,
            index=idx,
            filename=raw_path.name,
            normalized_path=norm_path,
            meta=meta,
            role="source",
            label="主体裁切" if len(raw_image_paths) == 1 else f"参考图 {idx}",
        ))

    source_inputs = list(inputs)
    generated_part_count = 0
    region_candidate_inputs: List[Dict[str, Any]] = []
    subject_candidate_plan: Dict[str, Any] = {}
    subject_understanding_error = ""
    selected_source_input: Optional[Dict[str, Any]] = None
    if len(source_inputs) == 1 and auto_decompose:
        part_dir = norm_dir / "parts"
        try:
            source_path = Path(str(source_inputs[0]["normalized_path"]))
            try:
                subject_candidate_plan = await _ai_subject_candidates(
                    job_id=job_id,
                    request=request,
                    reference_path=source_path,
                    preprocessing={},
                    asset_template=asset_template,
                    description=description,
                    max_candidates=max_parts,
                )
                region_candidate_inputs = _region_inputs_from_subject_plan(
                    job_id=job_id,
                    source_input=source_inputs[0],
                    src=source_path,
                    out_dir=part_dir,
                    subject_plan=subject_candidate_plan,
                )
            except Exception as exc:
                subject_understanding_error = str(exc)
                logger.warning("[ai_3d_model] subject understanding fallback job_id=%s error=%s", job_id, exc)
            if not region_candidate_inputs and character_template:
                parts = _decompose_character_image(source_path, part_dir, max_parts=max_parts)
                generated_part_count = len(parts)
                part_inputs: List[Dict[str, Any]] = []
                for part in parts:
                    part_path = Path(part["path"])
                    meta = {
                        "width": part["width"],
                        "height": part["height"],
                        "source_box": part.get("source_box"),
                        "crop_applied": False,
                    }
                    part_inputs.append(_public_input(
                        job_id=job_id,
                        index=len(part_inputs) + 1,
                        filename=part_path.name,
                        normalized_path=part_path,
                        meta=meta,
                        role=str(part.get("role") or "part"),
                        label=str(part.get("label") or part_path.stem),
                        source_filename=str(source_inputs[0].get("filename") or ""),
                        generated=True,
                    ))
                region_candidate_inputs = part_inputs
            elif not region_candidate_inputs:
                parts = _decompose_generic_subject_image(source_path, part_dir, max_parts=max_parts)
                generated_part_count = len(parts)
                part_inputs = []
                for part in parts:
                    part_path = Path(part["path"])
                    meta = {
                        "width": part["width"],
                        "height": part["height"],
                        "source_box": part.get("source_box"),
                        "crop_applied": False,
                    }
                    part_inputs.append(_public_input(
                        job_id=job_id,
                        index=len(part_inputs) + 1,
                        filename=part_path.name,
                        normalized_path=part_path,
                        meta=meta,
                        role=str(part.get("role") or "part"),
                        label=str(part.get("label") or part_path.stem),
                        source_filename=str(source_inputs[0].get("filename") or ""),
                        generated=True,
                    ))
                region_candidate_inputs = part_inputs
            else:
                generated_part_count = len(region_candidate_inputs)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"区域裁切候选生成失败：{exc}") from exc
        if subject_candidate_plan and region_candidate_inputs:
            recommended_index = int(subject_candidate_plan.get("recommended_index") or 0)
            recommended_input = next(
                (
                    item for item in region_candidate_inputs
                    if int(item.get("subject_candidate_index") or item.get("index") or -1) == recommended_index
                ),
                None,
            )
            recommended_candidate = next(
                (
                    item for item in (subject_candidate_plan.get("candidates") or [])
                    if isinstance(item, dict) and int(item.get("index") or -1) == recommended_index
                ),
                None,
            )
            if isinstance(recommended_input, dict) and isinstance(recommended_candidate, dict):
                selected_source_input = _copy_selected_candidate_as_source(
                    job_id=job_id,
                    candidate=recommended_input,
                    source_input=source_inputs[0],
                    index=recommended_index,
                )
                source_inputs = [selected_source_input]

    requires_image_stage_for_quality = bool(len(source_inputs) == 1)
    hold_for_preprocess = bool(preprocess_only or requires_image_stage_for_quality)
    if region_candidate_inputs and hold_for_preprocess:
        inputs = source_inputs if selected_source_input else region_candidate_inputs
    elif region_candidate_inputs:
        inputs = source_inputs

    effective_strategy = strategy
    if hold_for_preprocess and generated_part_count:
        effective_strategy = "candidate_preview"
    elif hold_for_preprocess and effective_strategy == "auto":
        effective_strategy = "multi_view"
    elif effective_strategy == "auto":
        effective_strategy = "multi_view" if character_template else ("part_batch" if len(inputs) > 4 else "multi_view")
    elif effective_strategy == "part_batch" and character_template and len(source_inputs) == 1:
        effective_strategy = "multi_view"
    mode = (
        "preprocess-preview"
        if effective_strategy == "candidate_preview"
        else ("part_batch" if effective_strategy == "part_batch" else ("multi-image-to-3d" if len(inputs) >= 2 else "image-to-3d"))
    )
    preprocessing = {
        "auto_crop": True,
        "auto_decompose": bool(auto_decompose),
        "generated_part_count": generated_part_count,
        "max_parts": max_parts,
        "source_inputs": source_inputs,
        "region_candidate_inputs": region_candidate_inputs,
        "requires_image_stage_for_quality": requires_image_stage_for_quality,
        "preprocess_only": hold_for_preprocess,
        "component_policy": "triview_first_for_character" if character_template else "parts_allowed_for_hard_surface",
    }
    if subject_candidate_plan:
        preprocessing["subject_candidate_plan"] = subject_candidate_plan
    if subject_understanding_error:
        preprocessing["subject_understanding_error"] = subject_understanding_error
    if selected_source_input and subject_candidate_plan:
        recommended_index = int(subject_candidate_plan.get("recommended_index") or 0)
        recommended_candidate = next(
            (
                item for item in (subject_candidate_plan.get("candidates") or [])
                if isinstance(item, dict) and int(item.get("index") or -1) == recommended_index
            ),
            None,
        )
        preprocessing["selected_region_candidate_index"] = recommended_index
        preprocessing["selected_region_candidate_role"] = str(selected_source_input.get("selected_from_candidate_role") or "")
        preprocessing["selected_region_candidate_label"] = str(selected_source_input.get("selected_from_candidate_label") or "")
        preprocessing["selected_by_ai"] = True
        if isinstance(recommended_candidate, dict):
            preprocessing["triview_ai_understanding"] = _view_understanding_from_subject_candidate(subject_candidate_plan, recommended_candidate)
            preprocessing["triview_ai_understanding"]["prompt_version"] = _VIEW_PROMPT_VERSION
    if not hold_for_preprocess and effective_strategy == "part_batch" and len(source_inputs) >= 2 and not region_candidate_inputs:
        preprocessing["component_source_mode"] = "user_part_package"
        preprocessing["component_quality_gate"] = "passed"
        preprocessing["component_split_generated"] = True
    view_plan = _view_generation_plan(
        asset_template=asset_template,
        reference_strength=reference_strength,
        description=description,
    )
    _apply_generic_reference_triview_prompts(
        view_plan,
        asset_template=asset_template,
        reference_strength=reference_strength,
        description=description,
    )
    view_plan["image_model"] = image_model
    job = {
        "job_id": job_id,
        "status": "preprocessed" if hold_for_preprocess else "queued",
        "stage": "preprocessed" if hold_for_preprocess else "queued",
        "progress": 100 if hold_for_preprocess else 0,
        "provider": "meshy",
        "final_3d_provider": "meshy",
        "image_stage_provider": "image_model",
        "image_stage_models": [_SUTUI_GPT_IMAGE_2_MODEL, "nano-banana-2"],
        "mode": mode,
        "strategy": effective_strategy,
        "quality": quality,
        "title": title.strip() or f"3D job {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "created_at": store.now_iso(),
        "updated_at": store.now_iso(),
        "inputs": inputs,
        "target_formats": target_formats,
        "outputs": {},
        "preprocessing": preprocessing,
        "view_generation_plan": view_plan,
        "asset_template": asset_template,
        "reference_strength": reference_strength,
        "image_model": image_model,
        "description": description.strip(),
        "quality_notes": (
            _preprocess_notes(
                source_count=len(source_inputs),
                generated_part_count=generated_part_count,
                preprocess_only=hold_for_preprocess,
                source_inputs=source_inputs,
            )
            + ([] if hold_for_preprocess else _quality_notes(strategy=effective_strategy, image_count=len(inputs), quality=quality))
        ),
    }
    if not hold_for_preprocess and not meshy.is_configured():
        raise HTTPException(status_code=503, detail="Meshy API Key 未配置，请在 .env 中设置 MESHY_API_KEY")
    store.save_job(job)
    if not hold_for_preprocess:
        background_tasks.add_task(_run_job_background, job_id)
    return {"ok": True, "job": _public_job(job)}


@router.get("/api/ai-3d-model/jobs")
async def ai_3d_model_list_jobs(limit: int = 20, _: _ServerUser = Depends(_ai3d_local_user)):
    jobs = [_public_job(job) for job in store.list_jobs(limit=limit)]
    return {"ok": True, "jobs": jobs}


@router.get("/api/ai-3d-model/jobs/{job_id}")
async def ai_3d_model_get_job(job_id: str, _: _ServerUser = Depends(_ai3d_local_user)):
    job = store.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True, "job": _public_job(job)}


@router.post("/api/ai-3d-model/jobs/{job_id}/generate")
async def ai_3d_model_start_generation(
    job_id: str,
    background_tasks: BackgroundTasks,
    _: _ServerUser = Depends(_ai3d_local_user),
):
    job = store.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.get("status") not in {"preprocessed", "failed", "succeeded"}:
        raise HTTPException(status_code=409, detail="当前任务状态不能重新开始生成")
    if not job.get("inputs"):
        raise HTTPException(status_code=400, detail="任务没有可生成的输入图")
    preprocessing = job.get("preprocessing") if isinstance(job.get("preprocessing"), dict) else {}
    if (
        job.get("stage") == "preprocessed"
        and preprocessing.get("requires_image_stage_for_quality")
        and not preprocessing.get("triview_generated")
        and not preprocessing.get("component_split_generated")
    ):
        raise HTTPException(status_code=409, detail="当前只有区域裁切候选，不是真实拆件。请先生成高清多视角后走 Multi-Image to 3D，或上传真实拆件包/接入语义分割后再生成 3D。")
    if str(job.get("strategy") or "") == "part_batch" and not _has_true_component_source(job):
        raise HTTPException(status_code=409, detail="当前输入不是可用于 part_batch 的真实拆件包。矩形裁切和参考候选不会送入 Meshy；角色请改用高清多视角，硬表面/饰件请上传拆件包或使用拆件流程。")
    if str(job.get("strategy") or "") == "part_batch" and len(_triview_image_paths(job)) < 2:
        raise HTTPException(status_code=409, detail="高精度拆件增强必须先生成多视角图。请先点击“用图片模型生成多视角”，系统不会走低精度 parts-only 拼装。")
    if not meshy.is_configured():
        raise HTTPException(status_code=503, detail="Meshy API Key 未配置，请在 .env 中设置 MESHY_API_KEY")
    preprocessing["preprocess_only"] = False
    quality_notes = _preprocess_notes(
        source_count=len(preprocessing.get("source_inputs") or []),
        generated_part_count=int(preprocessing.get("generated_part_count") or 0),
        preprocess_only=False,
        source_inputs=list(preprocessing.get("source_inputs") or []),
    ) + _quality_notes(
        strategy=str(job.get("strategy") or "auto"),
        image_count=len(job.get("inputs") or []),
        quality=str(job.get("quality") or "production"),
    )
    store.update_job(
        job_id,
        status="queued",
        stage="queued",
        progress=0,
        error=None,
        outputs={},
        mesh_metrics={},
        consumed_credits=0,
        provider_task_id=None,
        preprocessing=preprocessing,
        quality_notes=quality_notes,
    )
    background_tasks.add_task(_run_job_background, job_id)
    return {"ok": True, "job": _public_job(store.load_job(job_id) or job)}


@router.post("/api/ai-3d-model/jobs/{job_id}/base-model")
async def ai_3d_model_generate_base_model(
    job_id: str,
    background_tasks: BackgroundTasks,
    _: _ServerUser = Depends(_ai3d_local_user),
):
    job = store.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.get("status") not in {"preprocessed", "failed", "succeeded"}:
        raise HTTPException(status_code=409, detail="当前任务状态不能生成多视角底模")
    if len(_triview_image_paths(job)) < 2:
        raise HTTPException(status_code=409, detail="必须先生成多视角图，才能生成多视角 3D 底模")
    gate_error = _multiview_quality_gate_error(job)
    if gate_error:
        raise HTTPException(status_code=409, detail=gate_error)
    if not meshy.is_configured():
        raise HTTPException(status_code=503, detail="Meshy API Key 未配置，请在 .env 中设置 MESHY_API_KEY")
    store.update_job(
        job_id,
        status="queued",
        stage="queued_base_model",
        progress=0,
        error=None,
        finished_at=None,
    )
    background_tasks.add_task(_run_base_model_background, job_id)
    return {"ok": True, "job": _public_job(store.load_job(job_id) or job)}


@router.post("/api/ai-3d-model/jobs/{job_id}/parts-3d")
async def ai_3d_model_generate_part_models(
    job_id: str,
    background_tasks: BackgroundTasks,
    _: _ServerUser = Depends(_ai3d_local_user),
):
    job = store.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.get("status") not in {"preprocessed", "failed", "succeeded"}:
        raise HTTPException(status_code=409, detail="当前任务状态不能生成 3D 部件")
    if str(job.get("strategy") or "") != "part_batch":
        raise HTTPException(status_code=409, detail="当前任务还不是部件增强流程，请先生成部件输入图")
    preprocessing = job.get("preprocessing") if isinstance(job.get("preprocessing"), dict) else {}
    if not preprocessing.get("component_split_generated"):
        raise HTTPException(status_code=409, detail="请先生成 2D 部件输入图，再生成 3D 部件")
    if not _has_true_component_source(job):
        raise HTTPException(status_code=409, detail="当前部件输入未通过质量门，不能送入 3D")
    outputs = job.get("outputs") if isinstance(job.get("outputs"), dict) else {}
    base_outputs = _base_outputs_for_current_triview(job_id, job, outputs)
    if not _base_glb_path(job_id, base_outputs):
        raise HTTPException(status_code=409, detail="请先生成完整多视角 3D 底模，再生成 3D 部件")
    preflight_issues = _component_mesh_preflight_issues(_component_meta_by_index(job))
    if preflight_issues:
        raise HTTPException(status_code=409, detail="3D 部件生成前检查未通过：" + "；".join(preflight_issues))
    if not meshy.is_configured():
        raise HTTPException(status_code=503, detail="Meshy API Key 未配置，请在 .env 中设置 MESHY_API_KEY")
    store.update_job(
        job_id,
        status="queued",
        stage="queued_part_models",
        progress=0,
        error=None,
        finished_at=None,
        outputs={"base": base_outputs or {}},
        mesh_metrics={},
        provider_task_id=None,
    )
    background_tasks.add_task(_run_part_models_background, job_id)
    return {"ok": True, "job": _public_job(store.load_job(job_id) or job)}


@router.post("/api/ai-3d-model/jobs/{job_id}/assemble")
async def ai_3d_model_assemble_final_model(
    job_id: str,
    background_tasks: BackgroundTasks,
    _: _ServerUser = Depends(_ai3d_local_user),
):
    job = store.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.get("status") not in {"preprocessed", "failed", "succeeded"}:
        raise HTTPException(status_code=409, detail="当前任务状态不能合成最终模型")
    if str(job.get("strategy") or "") != "part_batch":
        raise HTTPException(status_code=409, detail="当前任务不是部件增强流程")
    outputs = job.get("outputs") if isinstance(job.get("outputs"), dict) else {}
    base_outputs = _base_outputs_for_current_triview(job_id, job, outputs)
    if not _base_glb_path(job_id, base_outputs):
        raise HTTPException(status_code=409, detail="缺少完整多视角 3D 底模，不能合成最终模型")
    subtasks = job.get("subtasks") if isinstance(job.get("subtasks"), list) else []
    if not subtasks and isinstance(outputs.get("parts"), list):
        subtasks = outputs.get("parts") or []
    if not subtasks:
        raise HTTPException(status_code=409, detail="请先生成 3D 部件，再合成最终模型")
    store.update_job(
        job_id,
        status="queued",
        stage="queued_part_assembly",
        progress=0,
        error=None,
        finished_at=None,
        outputs={"base": base_outputs or {}, "parts": subtasks},
        subtasks=subtasks,
    )
    background_tasks.add_task(_run_part_assembly_background, job_id)
    return {"ok": True, "job": _public_job(store.load_job(job_id) or job)}


@router.post("/api/ai-3d-model/jobs/{job_id}/triview")
async def ai_3d_model_generate_triview(
    job_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    model: str = Form(""),
    current_user: _ServerUser = Depends(_ai3d_local_user),
):
    job = store.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.get("status") not in {"preprocessed", "failed", "succeeded"}:
        raise HTTPException(status_code=409, detail="当前任务状态不能生成多视角")
    selected_model = _canonical_image_model(model or str(job.get("image_model") or _SUTUI_GPT_IMAGE_2_MODEL))
    patch: Dict[str, Any] = {
        "status": "generating_views",
        "stage": "queued_triview",
        "progress": 0,
        "error": None,
        "finished_at": None,
        "image_model": selected_model,
        "outputs": {},
        "subtasks": [],
        "mesh_metrics": {},
        "provider_task_id": None,
        "consumed_credits": 0,
    }
    preprocessing = job.get("preprocessing") if isinstance(job.get("preprocessing"), dict) else {}
    candidate_inputs = preprocessing.get("region_candidate_inputs") if isinstance(preprocessing.get("region_candidate_inputs"), list) else []
    has_generic_subject_candidates = any(
        isinstance(item, dict) and str(item.get("role") or "").endswith("_subject")
        for item in candidate_inputs
    )
    if (
        has_generic_subject_candidates
        and not preprocessing.get("selected_region_candidate_index")
        and str(job.get("stage") or "") == "preprocessed"
    ):
        raise HTTPException(status_code=409, detail="请先在区域裁切候选中选择要生成的主体，再生成多视角。")
    if preprocessing.get("triview_generated") or job.get("stage") == "triview_completed":
        preprocessing = dict(preprocessing)
        preprocessing.pop("triview_generated", None)
        preprocessing.pop("triview_inputs", None)
        preprocessing.pop("triview_inputs_partial", None)
        preprocessing["force_regenerate_triview"] = True
        reset_inputs = preprocessing.get("source_inputs")
        if not isinstance(reset_inputs, list) or not reset_inputs:
            reset_inputs = preprocessing.get("region_candidate_inputs")
        if not isinstance(reset_inputs, list) or not reset_inputs:
            reset_inputs = job.get("inputs") if isinstance(job.get("inputs"), list) else []
        notes = list(job.get("quality_notes") or [])
        notes.append("旧多视角已清除引用，本次会使用主体锁定模板重新生成。")
        patch.update(
            inputs=reset_inputs,
            preprocessing=preprocessing,
            view_generation_plan=_fresh_view_generation_plan(job, image_model=selected_model),
            mode="preprocess-preview",
            strategy="candidate_preview",
            quality_notes=notes,
        )
    else:
        stored_plan = job.get("view_generation_plan") if isinstance(job.get("view_generation_plan"), dict) else {}
        if stored_plan.get("prompt_version") != _VIEW_PROMPT_VERSION:
            patch["view_generation_plan"] = _fresh_view_generation_plan(job, image_model=selected_model)
    store.update_job(job_id, **patch)
    background_tasks.add_task(
        _run_triview_background,
        job_id=job_id,
        request=request,
        current_user=current_user,
        model=selected_model,
    )
    return {"ok": True, "job": _public_job(store.load_job(job_id) or job)}


@router.post("/api/ai-3d-model/jobs/{job_id}/select-candidate")
async def ai_3d_model_select_candidate(
    job_id: str,
    candidate_index: int = Form(...),
    _: _ServerUser = Depends(_ai3d_local_user),
):
    job = store.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.get("status") not in {"preprocessed", "failed", "succeeded"}:
        raise HTTPException(status_code=409, detail="当前任务状态不能切换主体候选")
    preprocessing = job.get("preprocessing") if isinstance(job.get("preprocessing"), dict) else {}
    preprocessing = dict(preprocessing)
    candidates = preprocessing.get("region_candidate_inputs") if isinstance(preprocessing.get("region_candidate_inputs"), list) else []
    candidate = next(
        (item for item in candidates if isinstance(item, dict) and int(item.get("index") or -1) == int(candidate_index)),
        None,
    )
    if not isinstance(candidate, dict):
        raise HTTPException(status_code=404, detail="找不到指定裁切候选")
    old_source_inputs = preprocessing.get("source_inputs") if isinstance(preprocessing.get("source_inputs"), list) else []
    old_source = old_source_inputs[0] if old_source_inputs and isinstance(old_source_inputs[0], dict) else {}
    try:
        selected_source = _copy_selected_candidate_as_source(
            job_id=job_id,
            candidate=candidate,
            source_input=old_source,
            index=int(candidate_index),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"主体候选切换失败：{exc}") from exc
    preprocessing["source_inputs"] = [selected_source]
    subject_candidate_index = int(candidate.get("subject_candidate_index") or candidate_index)
    preprocessing["selected_region_candidate_index"] = subject_candidate_index
    preprocessing["selected_region_candidate_role"] = str(candidate.get("role") or "")
    preprocessing["selected_region_candidate_label"] = str(candidate.get("label") or "")
    preprocessing["selected_by_ai"] = bool(candidate.get("ai_recommended"))
    subject_plan = preprocessing.get("subject_candidate_plan") if isinstance(preprocessing.get("subject_candidate_plan"), dict) else {}
    plan_candidate = next(
        (
            item for item in (subject_plan.get("candidates") or [])
            if isinstance(item, dict) and int(item.get("index") or -1) == subject_candidate_index
        ),
        None,
    )
    if isinstance(plan_candidate, dict):
        preprocessing["triview_ai_understanding"] = _view_understanding_from_subject_candidate(subject_plan, plan_candidate)
        preprocessing["triview_ai_understanding"]["prompt_version"] = _VIEW_PROMPT_VERSION
    for key in (
        "triview_generated",
        "triview_inputs",
        "triview_inputs_partial",
        "triview_sheet",
        "triview_front45_sheet",
        "triview_side_back_sheet",
        "triview_identity_reference",
        "triview_consistency_verification",
        "triview_quality_gate",
        "triview_usable_roles",
        "triview_excluded_roles",
        "triview_partial_pass_reason",
        "primary_reference_anchor",
        "force_regenerate_triview",
        "component_split_generated",
        "component_sheet",
        "component_inputs",
        "component_inputs_partial",
        "component_sheet_partial",
        "component_failed_preview_inputs",
        "component_failed_preview_sheet",
        "component_quality_gate",
        "component_quality_gate_meta",
        "component_source_mode",
        "component_mesh_input_mode",
        "component_ai_verification",
        "component_ai_plan",
        "component_plan_source",
        "component_slots",
    ):
        preprocessing.pop(key, None)
    notes = list(job.get("quality_notes") or [])
    notes.append(f"已选择“{selected_source.get('label')}”作为三视图主体；旧多视角和部件结果已清理。")
    store.update_job(
        job_id,
        status="preprocessed",
        stage="preprocessed",
        progress=100,
        error=None,
        finished_at=None,
        inputs=[selected_source],
        preprocessing=preprocessing,
        view_generation_plan=_fresh_view_generation_plan(job, image_model=str(job.get("image_model") or _SUTUI_GPT_IMAGE_2_MODEL)),
        outputs={},
        subtasks=[],
        mesh_metrics={},
        provider_task_id=None,
        mode="preprocess-preview",
        strategy="candidate_preview",
        quality_notes=notes,
    )
    return {"ok": True, "job": _public_job(store.load_job(job_id) or job)}


@router.post("/api/ai-3d-model/jobs/{job_id}/components")
async def ai_3d_model_generate_components(
    job_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    model: str = Form(""),
    current_user: _ServerUser = Depends(_ai3d_local_user),
):
    job = store.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.get("status") not in {"preprocessed", "failed", "succeeded"}:
        raise HTTPException(status_code=409, detail="当前任务状态不能生成 AI 部件分离")
    selected_model = _canonical_image_model(model or str(job.get("image_model") or _SUTUI_GPT_IMAGE_2_MODEL))
    preprocessing = job.get("preprocessing") if isinstance(job.get("preprocessing"), dict) else {}
    preprocessing = dict(preprocessing)
    preprocessing["max_parts"] = max(
        _AI3D_DEFAULT_MAX_PARTS,
        min(_AI3D_ABSOLUTE_MAX_PARTS, int(preprocessing.get("max_parts") or _AI3D_DEFAULT_MAX_PARTS)),
    )
    notes = list(job.get("quality_notes") or [])
    if _is_character_template(str(job.get("asset_template") or "")):
        preprocessing["component_manual_override"] = "character_split_trial"
        trial_note = "角色拆件由用户手动触发：优先使用 see-through PSD 语义分层；若环境或质量门未通过，不会送入 part_batch 3D。"
        if trial_note not in notes:
            notes.append(trial_note)
    for key in (
        "component_split_generated",
        "component_sheet",
        "component_inputs",
        "component_reference_sheet",
        "component_reference_inputs",
        "component_reference_mode",
        "component_inputs_partial",
        "component_sheet_partial",
        "component_failed_preview_inputs",
        "component_failed_preview_sheet",
        "component_quality_gate",
        "component_quality_gate_meta",
        "component_source_mode",
        "component_mesh_input_mode",
        "component_ai_verification",
        "component_ai_plan_error",
        "component_slots",
        "see_through_result",
    ):
        preprocessing.pop(key, None)
    if str(preprocessing.get("component_plan_source") or "") != "image.understand":
        preprocessing.pop("component_plan_source", None)
        preprocessing.pop("component_ai_plan", None)
    safe_inputs = _safe_frontend_generation_inputs(job, preprocessing)
    previous_outputs = job.get("outputs") if isinstance(job.get("outputs"), dict) else {}
    preserved_base = _base_outputs_for_current_triview(job_id, job, previous_outputs)
    next_outputs = {"base": preserved_base} if preserved_base else {}
    store.update_job(
        job_id,
        status="splitting_parts",
        stage="queued_component_split",
        progress=0,
        error=None,
        finished_at=None,
        preprocessing=preprocessing,
        quality_notes=notes,
        inputs=safe_inputs or job.get("inputs") or [],
        outputs=next_outputs,
        mesh_metrics={},
        provider_task_id=None,
        mode="multi-image-to-3d" if _looks_like_triview_inputs(safe_inputs) else "preprocess-preview",
        strategy="multi_view" if _looks_like_triview_inputs(safe_inputs) else "candidate_preview",
    )
    background_tasks.add_task(
        _run_component_split_background,
        job_id=job_id,
        request=request,
        current_user=current_user,
        model=selected_model,
    )
    return {"ok": True, "job": _public_job(store.load_job(job_id) or job)}


@router.post("/api/ai-3d-model/jobs/{job_id}/3mf")
async def ai_3d_model_export_3mf(
    job_id: str,
    scope: str = Form("all"),
    _: _ServerUser = Depends(_ai3d_local_user),
):
    job = store.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.get("status") not in {"preprocessed", "failed", "succeeded"}:
        raise HTTPException(status_code=409, detail="当前任务还在运行中，等模型生成完成后再导出 3MF")
    result = _export_3mf_for_scope(job_id, job, scope)
    scope_safe = (scope or "all").strip().lower()
    model_files = [
        file for file in result.get("files") or []
        if isinstance(file, dict) and str(file.get("format") or "").lower() == "3mf"
    ]
    if len(model_files) == 1 and scope_safe in {"base", "final", "assembled", "assembly"}:
        download_url = str(model_files[0].get("url") or "")
    else:
        download_url = f"/api/ai-3d-model/jobs/{job_id}/3mf/download?scope={scope_safe}"
    return {
        "ok": True,
        "scope": scope_safe,
        "passed": bool(model_files),
        "passed_count": result.get("passed_count") or 0,
        "failed_count": result.get("failed_count") or 0,
        "download_url": download_url,
        "files": result.get("files") or [],
        "reports": result.get("reports") or [],
        "job": _public_job(result.get("job") or store.load_job(job_id) or job),
    }


@router.get("/api/ai-3d-model/jobs/{job_id}/3mf/download")
def ai_3d_model_download_3mf(job_id: str, scope: str = "all"):
    job = store.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    scope_safe = (scope or "all").strip().lower()
    if scope_safe not in {"base", "parts", "final", "assembled", "assembly", "all"}:
        raise HTTPException(status_code=400, detail="scope must be base, parts, final or all")
    paths = _3mf_download_paths_for_scope(job_id, job, scope_safe)
    if not paths:
        raise HTTPException(status_code=404, detail="当前范围暂无 3MF 或 3MF 检查报告")
    model_paths = [path for path in paths if path.suffix.lower() == ".3mf"]
    if len(paths) == 1 and model_paths:
        return FileResponse(
            str(paths[0]),
            media_type=_FILE_MEDIA_TYPES.get(paths[0].suffix.lower()),
            filename=paths[0].name,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    root = store.job_dir(job_id).resolve()
    archive_dir = root / "downloads"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive = archive_dir / f"ai3d_{scope_safe}_3mf_outputs.zip"
    tmp_archive = archive.with_suffix(".zip.tmp")
    if tmp_archive.exists():
        tmp_archive.unlink(missing_ok=True)
    with zipfile.ZipFile(tmp_archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for path in paths:
            try:
                arcname = path.resolve().relative_to(root).as_posix()
            except Exception:
                arcname = path.name
            if not arcname.startswith("downloads/"):
                zf.write(path, arcname)
    tmp_archive.replace(archive)
    return FileResponse(
        str(archive),
        media_type="application/zip",
        filename=f"{job_id}-ai3d-{scope_safe}-3mf.zip",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/api/ai-3d-model/jobs/latest/download")
def ai_3d_model_download_latest_job():
    jobs = store.list_jobs(limit=1)
    if not jobs:
        raise HTTPException(status_code=404, detail="暂无可下载任务")
    job_id = str(jobs[0].get("job_id") or "").strip()
    if not job_id:
        raise HTTPException(status_code=404, detail="暂无可下载任务")
    return ai_3d_model_download_job(job_id)


@router.get("/api/ai-3d-model/jobs/{job_id}/download")
def ai_3d_model_download_job(job_id: str):
    job = store.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    paths = _downloadable_job_paths(job_id, job)
    if not paths:
        raise HTTPException(status_code=404, detail="当前任务暂无可下载文件")
    root = store.job_dir(job_id).resolve()
    archive_dir = root / "downloads"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive = archive_dir / "ai3d_task_outputs.zip"
    archive_signature = _archive_signature(root, paths)
    if not _archive_is_current(archive, paths):
        tmp_archive = archive.with_suffix(".zip.tmp")
        if tmp_archive.exists():
            tmp_archive.unlink(missing_ok=True)
        with zipfile.ZipFile(tmp_archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            for path in paths:
                try:
                    arcname = path.resolve().relative_to(root).as_posix()
                except Exception:
                    arcname = path.name
                if not arcname.startswith("downloads/"):
                    zf.write(path, arcname)
        tmp_archive.replace(archive)
        _archive_manifest_path(archive).write_text(json.dumps({
            "job_id": job_id,
            "signature": archive_signature,
            "file_count": len(paths),
            "total_bytes": sum(path.stat().st_size for path in paths if path.is_file()),
            "archive": str(archive),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return FileResponse(
        str(archive),
        media_type="application/zip",
        filename=f"{job_id}-ai3d-outputs.zip",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/api/ai-3d-model/jobs/{job_id}/files/{file_path:path}")
def ai_3d_model_file(job_id: str, file_path: str):
    if not store.load_job(job_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    root = store.job_dir(job_id).resolve()
    path = (root / file_path).resolve()
    if (not str(path).startswith(str(root)) or not path.is_file()) and "/" not in file_path and "\\" not in file_path:
        legacy_path = (root / "outputs" / file_path).resolve()
        if str(legacy_path).startswith(str(root)) and legacy_path.is_file():
            path = legacy_path
    if not str(path).startswith(str(root)) or not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(
        str(path),
        media_type=_FILE_MEDIA_TYPES.get(path.suffix.lower()),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
