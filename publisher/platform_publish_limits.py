"""
各平台发布字段长度限制（与创作者后台常见规则对齐；平台改版时需人工核对）。

单位：Python str 长度（中日文一般 1 字 = 1）。

- 小红书：标题严格 ≤20 字（超出会无法提交或前端拦截）。
- 抖音：驱动内已对标题做截断；此处与驱动一致：图文标题 20、视频 30；描述+话题合并填入约 500 字。
- 今日头条：标题保守 30 字；正文/简介保守 5000 字（mp 后台以实际提示为准）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def is_image_publish(file_path: str) -> bool:
    return Path(file_path or "").suffix.lower() in _IMAGE_EXTS


def _douyin_tag_suffix(tags: str) -> str:
    g = (tags or "").strip()
    if not g:
        return ""
    parts = [t.strip() for t in g.split(",") if t.strip()]
    if not parts:
        return ""
    return " " + " ".join(f"#{t}" for t in parts)


def normalize_publish_texts(
    platform: str,
    file_path: str,
    title: str,
    description: str,
    tags: str,
) -> Tuple[str, str, str, List[str]]:
    """
    按平台裁剪 title / description（tags 原样返回；抖音会按「描述+话题」总长度再收紧描述）。

    Returns:
        (title, description, tags, warnings)
    """
    warnings: List[str] = []
    t = (title or "").strip()
    d = (description or "").strip()
    g = (tags or "").strip()
    plat = (platform or "").strip().lower()
    image = is_image_publish(file_path)

    if plat == "xiaohongshu":
        title_max, desc_max = 20, 1000
        if len(t) > title_max:
            warnings.append(f"小红书标题已超过 {title_max} 字，已截断（原 {len(t)} 字）")
            t = t[:title_max]
        if len(d) > desc_max:
            warnings.append(f"小红书描述已超过 {desc_max} 字，已截断（原 {len(d)} 字）")
            d = d[:desc_max]
        return t, d, g, warnings

    if plat == "douyin":
        title_max = 20 if image else 30
        combo_max = 500
        if len(t) > title_max:
            kind = "图文" if image else "视频"
            warnings.append(f"抖音{kind}标题已超过 {title_max} 字，已截断（原 {len(t)} 字）")
            t = t[:title_max]
        suffix = _douyin_tag_suffix(g)
        combined = d + suffix
        if len(combined) > combo_max:
            room = combo_max - len(suffix)
            if room < 0:
                room = 0
            warnings.append(
                f"抖音描述+话题总长度已超过 {combo_max} 字，已缩短描述（话题保留，原描述 {len(d)} 字）"
            )
            d = d[:room]
        return t, d, g, warnings

    if plat == "toutiao":
        title_max, desc_max = 30, 5000
        if len(t) > title_max:
            warnings.append(f"头条标题已超过 {title_max} 字，已截断（原 {len(t)} 字）")
            t = t[:title_max]
        if len(d) > desc_max:
            warnings.append(f"头条正文/简介已超过 {desc_max} 字，已截断（原 {len(d)} 字）")
            d = d[:desc_max]
        return t, d, g, warnings

    return t, d, g, warnings


def log_and_attach_warnings(result: dict, warnings: List[str]) -> dict:
    """把截断说明写入日志，并挂到发布结果上便于前端/MCP 展示。"""
    if not warnings:
        return result
    for w in warnings:
        logger.warning("[PUBLISH] %s", w)
    out = dict(result) if isinstance(result, dict) else {"ok": False, "error": str(result)}
    prev = out.get("text_normalization_warnings")
    if isinstance(prev, list):
        out["text_normalization_warnings"] = prev + warnings
    else:
        out["text_normalization_warnings"] = list(warnings)
    return out
