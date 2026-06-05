"""PPT Master adapter for Lobster's PPT capability.

This module intentionally keeps model calls out of the runner. The API layer
plans content and generates optional images through the existing server
interfaces, then this runner writes a PPT Master-compatible SVG project and
exports it with ppt-master's SVG-to-PPTX tooling.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from fastapi import HTTPException


SLIDE_W = 1280
SLIDE_H = 720


def _lobster_root() -> Path:
    return Path(__file__).resolve().parents[3]


def ppt_master_skill_dir() -> Path:
    primary = _lobster_root() / "skills" / "ppt_master"
    if primary.exists():
        return primary
    legacy = _lobster_root() / "skills" / "ppt-master"
    if legacy.exists():
        return legacy
    raise HTTPException(status_code=503, detail=f"PPT Master skill not found: {primary}")


def safe_text(value: Any, limit: int = 400) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit].strip()


def _xml(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _short_lines(items: Iterable[Any], *, max_items: int = 5, max_len: int = 62) -> List[str]:
    lines: List[str] = []
    for item in items:
        if isinstance(item, dict):
            text = safe_text(item.get("text") or item.get("title") or item.get("content"), max_len)
        else:
            text = safe_text(item, max_len)
        if text:
            lines.append(text)
        if len(lines) >= max_items:
            break
    return lines


def _coerce_short_list(value: Any, *, max_items: int = 3, max_len: int = 32) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items: Iterable[Any] = [line.strip(" -") for line in value.splitlines() if line.strip()]
    elif isinstance(value, list):
        items = value
    else:
        items = []
    return _short_lines(items, max_items=max_items, max_len=max_len)


def normalize_ppt_master_plan(raw: Dict[str, Any], *, topic: str, slide_count: int) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    title = safe_text(raw.get("title") or topic or "Presentation", 100)
    subtitle = safe_text(raw.get("subtitle") or raw.get("summary") or "", 140)
    slides_raw = raw.get("slides")
    if not isinstance(slides_raw, list):
        slides_raw = []

    slides: List[Dict[str, Any]] = []
    for idx, item in enumerate(slides_raw[: max(1, slide_count)]):
        if not isinstance(item, dict):
            item = {"title": safe_text(item, 80)}
        title_i = safe_text(item.get("title") or f"Page {idx + 1}", 90)
        subtitle_i = safe_text(item.get("subtitle") or item.get("kicker") or "", 120)
        bullets = item.get("bullets")
        if bullets is None:
            bullets = item.get("elements") or item.get("points") or item.get("content") or []
        if isinstance(bullets, str):
            bullets = [line.strip(" -") for line in bullets.splitlines() if line.strip()]
        if not isinstance(bullets, list):
            bullets = []
        layout = safe_text(item.get("layout") or item.get("slide_type") or "content", 32).lower()
        if layout not in {"title", "section", "content", "two_column", "image_right", "quote", "ending", "comparison", "data", "process"}:
            layout = "content"
        role = safe_text(item.get("role") or item.get("page_role") or "", 32).lower()
        if role not in {"cover", "chapter", "insight", "evidence", "comparison", "quote", "closing", "process", "data"}:
            role = {
                "title": "cover",
                "section": "chapter",
                "quote": "quote",
                "ending": "closing",
                "comparison": "comparison",
                "data": "data",
                "process": "process",
            }.get(layout, "insight")
        claim = safe_text(item.get("claim") or item.get("takeaway") or item.get("conclusion") or "", 120)
        metrics = _coerce_short_list(item.get("metrics") or item.get("tags") or item.get("kpis"), max_items=3, max_len=30)
        visual_style = safe_text(item.get("visual_style") or item.get("style_hint") or "", 40).lower()
        visual_prompt = safe_text(item.get("visual_prompt") or item.get("image_prompt") or "", 600)
        notes = safe_text(item.get("notes") or item.get("speaker_notes") or "", 1200)
        if not visual_prompt and layout in {"title", "content", "image_right", "ending"}:
            bullet_text = "；".join(_short_lines(bullets, max_items=3, max_len=40))
            image_subject = claim or bullet_text or subtitle or title or topic
            scene_hint = {
                "cover": "具有品牌主视觉感的封面图，留出左侧或中部文字空间",
                "insight": "能表达关键洞察的高质量概念图或真实感场景图",
                "evidence": "适合承载证据和分析的产品/市场场景图",
                "comparison": "适合对比分析的双主体构图",
                "data": "适合数据洞察页的抽象图形或行业场景图",
                "process": "适合流程推进和路径拆解的场景图",
                "closing": "具有行动号召和总结感的收尾画面",
            }.get(role, "适合商务汇报的主视觉画面")
            visual_prompt = safe_text(
                (
                    f"为一页16:9商务演示PPT生成高质量配图。主题：{title or topic}。"
                    f"核心表达：{image_subject}。"
                    f"画面方向：{scene_hint}。"
                    "画面应专业、干净、有层次、有留白，适合放入PPT页面；不要文字、不要水印、不要Logo、不要二维码。"
                ),
                600,
            )
        slides.append(
            {
                "index": idx + 1,
                "title": title_i,
                "subtitle": subtitle_i,
                "claim": claim,
                "bullets": _short_lines(bullets, max_items=5, max_len=74),
                "metrics": metrics,
                "layout": layout,
                "role": role,
                "visual_prompt": visual_prompt,
                "visual_style": visual_style,
                "notes": notes,
            }
        )

    if not slides:
        slides = [
            {
                "index": 1,
                "title": title,
                "subtitle": subtitle,
                "claim": "",
                "bullets": [],
                "metrics": [],
                "layout": "title",
                "role": "cover",
                "visual_prompt": "",
                "visual_style": "full_bleed",
                "notes": "",
            }
        ]

    if slides[0]["layout"] == "content":
        slides[0]["layout"] = "title"
    if len(slides) > 1 and slides[-1]["layout"] == "content":
        slides[-1]["layout"] = "ending"

    return {
        "title": title,
        "subtitle": subtitle,
        "slides": slides[: max(1, slide_count)],
    }


def _theme_palette(theme: str) -> Dict[str, str]:
    key = safe_text(theme, 40).lower()
    if key in {"dark", "black"}:
        return {
            "bg": "#101418",
            "panel": "#192028",
            "ink": "#F4F7FA",
            "muted": "#AAB4BF",
            "accent": "#31B7A9",
            "accent2": "#E8B84A",
            "line": "#2C3742",
        }
    if key in {"tech", "blue"}:
        return {
            "bg": "#F7FAFC",
            "panel": "#FFFFFF",
            "ink": "#172033",
            "muted": "#667085",
            "accent": "#1D7FF2",
            "accent2": "#12B981",
            "line": "#D8E1EA",
        }
    return {
        "bg": "#F6F3EE",
        "panel": "#FFFFFF",
        "ink": "#202124",
        "muted": "#667085",
        "accent": "#0F766E",
        "accent2": "#D97706",
        "line": "#DED7CC",
    }


def _visual_units(text: Any) -> float:
    total = 0.0
    for ch in str(text or ""):
        if ch.isspace():
            total += 0.35
        elif ord(ch) < 128:
            total += 0.58
        else:
            total += 1.0
    return total


def _fit_font_size(text: str, base: int, *, min_size: int = 24, max_chars: int = 18) -> int:
    length = max(_visual_units(text), 1.0)
    if length <= max_chars:
        return base
    return max(min_size, int(base * max_chars / length))


def _wrap_text(text: Any, max_chars: int, max_lines: int = 2) -> List[str]:
    raw = safe_text(text, max_chars * max_lines * 2)
    if not raw:
        return []
    chunks: List[str] = []
    cur = ""
    cur_units = 0.0
    for ch in raw:
        ch_units = _visual_units(ch)
        if cur and cur_units + ch_units > max_chars and (ch.isspace() or cur_units >= max_chars * 0.92):
            chunks.append(cur.strip())
            cur = ""
            cur_units = 0.0
        cur += ch
        cur_units += ch_units
    if cur.strip():
        chunks.append(cur.strip())
    if len(chunks) > max_lines:
        chunks = chunks[:max_lines]
        chunks[-1] = chunks[-1].rstrip(".,;:!? ") + "..."
    return chunks


def _svg_multiline_text(
    text: Any,
    *,
    x: int,
    y: int,
    size: int,
    fill: str,
    weight: int = 400,
    max_chars: int = 28,
    max_lines: int = 2,
    line_gap: Optional[int] = None,
    anchor: str = "start",
    opacity: float = 1.0,
) -> str:
    lines = _wrap_text(text, max_chars=max_chars, max_lines=max_lines)
    gap = line_gap or int(size * 1.28)
    out: List[str] = []
    for i, line in enumerate(lines):
        out.append(
            f'<text x="{x}" y="{y + i * gap}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" opacity="{opacity}" text-anchor="{anchor}" '
            f'font-family="Microsoft YaHei, PingFang SC, Arial">{_xml(line)}</text>'
        )
    return "\n".join(out)


def _svg_page_label(page: int, total: int, p: Dict[str, str], *, dark: bool = False) -> str:
    fill = "#FFFFFF" if dark else p["muted"]
    return (
        f'<text x="72" y="660" font-size="17" fill="{fill}" opacity="0.72" '
        f'font-family="Arial">{page:02d} / {total:02d}</text>'
    )


def _deck_label(plan: Dict[str, Any]) -> str:
    title = safe_text(plan.get("title") or "PRESENTATION", 36)
    return title[:28] or "PRESENTATION"


def _svg_header(title: str, subtitle: str, p: Dict[str, str], *, page: int, total: int, deck_label: str = "PRESENTATION") -> str:
    return "\n".join(
        [
            f'<text x="70" y="76" font-size="16" fill="{p["accent"]}" font-weight="700" '
            f'font-family="Microsoft YaHei, PingFang SC, Arial">{_xml(deck_label)}</text>',
            _svg_multiline_text(title, x=70, y=132, size=_fit_font_size(title, 40, min_size=30, max_chars=24), fill=p["ink"], weight=760, max_chars=27, max_lines=2),
            _svg_multiline_text(subtitle, x=72, y=190, size=20, fill=p["muted"], max_chars=44, max_lines=1),
            f'<rect x="70" y="214" width="96" height="5" fill="{p["accent"]}"/>',
            _svg_page_label(page, total, p),
        ]
    )


def _svg_bullet_cards(
    bullets: List[str],
    *,
    x: int,
    y: int,
    w: int,
    p: Dict[str, str],
    columns: int = 1,
    max_items: int = 4,
) -> str:
    items = bullets[:max_items]
    if not items:
        return ""
    gap = 20
    col_w = int((w - gap * (columns - 1)) / columns)
    card_h = 76 if columns == 1 else 112
    out: List[str] = []
    for idx, item in enumerate(items):
        col = idx % columns
        row = idx // columns
        cx = x + col * (col_w + gap)
        cy = y + row * (card_h + 14)
        tint = "#FFFFFF" if idx % 2 == 0 else "#F9FBFD"
        out.extend(
            [
                f'<rect x="{cx}" y="{cy}" width="{col_w}" height="{card_h}" rx="18" fill="{tint}" stroke="{p["line"]}" stroke-width="1"/>',
                f'<rect x="{cx}" y="{cy}" width="5" height="{card_h}" rx="2.5" fill="{p["accent"]}" opacity="{0.95 if idx == 0 else 0.45}"/>',
                f'<circle cx="{cx + 30}" cy="{cy + 34}" r="12" fill="{p["accent"]}" opacity="{0.98 if idx == 0 else 0.72}"/>',
                f'<text x="{cx + 30}" y="{cy + 39}" font-size="13" fill="#FFFFFF" text-anchor="middle" font-weight="700" font-family="Arial">{idx + 1}</text>',
                _svg_multiline_text(item, x=cx + 58, y=cy + 33, size=19 if columns == 1 else 18, fill=p["ink"], weight=650, max_chars=32 if columns == 1 else 20, max_lines=2),
            ]
        )
    return "\n".join(out)


def _svg_metric_pills(metrics: List[str], *, x: int, y: int, p: Dict[str, str], dark: bool = False, filled: bool = False) -> str:
    if not metrics:
        return ""
    fill = p["accent"] if filled else ("#FFFFFF" if dark else p["panel"])
    stroke = "#FFFFFF" if dark else p["line"]
    text_fill = "#FFFFFF" if (filled or dark) else p["ink"]
    out: List[str] = []
    cursor = x
    for metric in metrics[:3]:
        label = safe_text(metric, 28)
        width = max(106, min(248, int(34 + _visual_units(label) * 18)))
        out.extend(
            [
                f'<rect x="{cursor}" y="{y}" width="{width}" height="40" rx="20" fill="{fill}" opacity="{0.9 if filled else 0.84}" stroke="{stroke}" stroke-width="1" stroke-opacity="0.34"/>',
                f'<text x="{cursor + width / 2:.1f}" y="{y + 26}" font-size="16" fill="{text_fill}" opacity="0.95" text-anchor="middle" font-weight="750" font-family="Microsoft YaHei, PingFang SC, Arial">{_xml(label)}</text>',
            ]
        )
        cursor += width + 12
    return "\n".join(out)


def _svg_claim_band(claim: str, *, x: int, y: int, w: int, p: Dict[str, str], dark: bool = False) -> str:
    text = safe_text(claim, 90)
    if not text:
        return ""
    bg = "#081018" if dark else p["ink"]
    fg = "#FFFFFF"
    accent = p["accent2"] if dark else p["accent"]
    return "\n".join(
        [
            f'<rect x="{x}" y="{y}" width="{w}" height="76" rx="18" fill="{bg}" opacity="{0.72 if dark else 0.94}"/>',
            f'<rect x="{x}" y="{y}" width="8" height="76" rx="4" fill="{accent}"/>',
            _svg_multiline_text(text, x=x + 28, y=y + 32, size=22, fill=fg, weight=700, max_chars=max(18, int(w / 28)), max_lines=2, line_gap=28),
        ]
    )


def _svg_mini_agenda(slides: List[Dict[str, Any]], *, x: int, y: int, p: Dict[str, str], active: int) -> str:
    titles = [safe_text(s.get("title"), 28) for s in slides if safe_text(s.get("title"), 28)]
    titles = titles[:6]
    if not titles:
        return ""
    out: List[str] = []
    for idx, title in enumerate(titles):
        cy = y + idx * 46
        is_active = idx == 0
        opacity = "1" if is_active else "0.62"
        out.extend(
            [
                f'<rect x="{x - 18}" y="{cy - 29}" width="352" height="38" rx="19" fill="{p["accent"] if is_active else p["bg"]}" opacity="{0.12 if is_active else 0.0}"/>',
                f'<circle cx="{x}" cy="{cy - 9}" r="12" fill="{p["accent"]}" opacity="{opacity}"/>',
                f'<text x="{x}" y="{cy - 4}" font-size="12" fill="#FFFFFF" text-anchor="middle" font-weight="700" font-family="Arial">{idx + 1}</text>',
                f'<text x="{x + 30}" y="{cy - 2}" font-size="19" fill="{p["ink"]}" opacity="{opacity}" font-weight="{760 if is_active else 560}" font-family="Microsoft YaHei, PingFang SC, Arial">{_xml(title)}</text>',
            ]
        )
        if idx < len(titles) - 1:
            out.append(f'<line x1="{x}" y1="{cy + 5}" x2="{x}" y2="{cy + 27}" stroke="{p["line"]}" stroke-width="2"/>')
    return "\n".join(out)


def _svg_data_tiles(metrics: List[str], bullets: List[str], *, x: int, y: int, w: int, p: Dict[str, str]) -> str:
    labels = metrics[:3] or bullets[:3]
    if not labels:
        return ""
    gap = 20
    tile_w = int((w - gap * (len(labels) - 1)) / len(labels))
    out: List[str] = []
    for idx, label in enumerate(labels):
        cx = x + idx * (tile_w + gap)
        accent = p["accent"] if idx == 0 else (p["accent2"] if idx == 1 else "#64748B")
        headline = safe_text(label, 24)
        support = bullets[idx] if idx < len(bullets) else ""
        out.extend(
            [
                f'<rect x="{cx}" y="{y}" width="{tile_w}" height="178" rx="24" fill="#FFFFFF" stroke="{p["line"]}" stroke-width="1"/>',
                f'<circle cx="{cx + tile_w - 48}" cy="{y + 48}" r="34" fill="{accent}" opacity="0.13"/>',
                f'<rect x="{cx + 26}" y="{y + 28}" width="54" height="6" rx="3" fill="{accent}"/>',
                _svg_multiline_text(headline, x=cx + 26, y=y + 82, size=28, fill=p["ink"], weight=820, max_chars=max(8, int(tile_w / 24)), max_lines=2, line_gap=34),
                _svg_multiline_text(support, x=cx + 28, y=y + 142, size=16, fill=p["muted"], weight=500, max_chars=max(12, int(tile_w / 18)), max_lines=2, line_gap=22),
            ]
        )
    return "\n".join(out)


def _svg_comparison_columns(bullets: List[str], *, x: int, y: int, w: int, p: Dict[str, str]) -> str:
    left = bullets[:3]
    right = bullets[3:6] or bullets[:3]
    col_gap = 28
    col_w = int((w - col_gap) / 2)

    def col(items: List[str], *, cx: int, title: str, accent: str) -> str:
        rows: List[str] = [
            f'<rect x="{cx}" y="{y}" width="{col_w}" height="292" rx="26" fill="#FFFFFF" stroke="{p["line"]}" stroke-width="1"/>',
            f'<rect x="{cx}" y="{y}" width="{col_w}" height="78" rx="26" fill="{accent}" opacity="0.1"/>',
            f'<text x="{cx + 30}" y="{y + 50}" font-size="24" fill="{p["ink"]}" font-weight="820" font-family="Microsoft YaHei, PingFang SC, Arial">{_xml(title)}</text>',
        ]
        for idx, item in enumerate(items[:3]):
            ry = y + 112 + idx * 58
            rows.extend(
                [
                    f'<circle cx="{cx + 34}" cy="{ry - 6}" r="10" fill="{accent}" opacity="0.9"/>',
                    _svg_multiline_text(item, x=cx + 58, y=ry, size=18, fill=p["ink"], weight=620, max_chars=max(15, int(col_w / 22)), max_lines=2, line_gap=23),
                ]
            )
        return "\n".join(rows)

    return "\n".join(
        [
            col(left, cx=x, title="当前判断", accent=p["accent"]),
            col(right, cx=x + col_w + col_gap, title="建议动作", accent=p["accent2"]),
        ]
    )


def _svg_process_steps(bullets: List[str], *, x: int, y: int, w: int, p: Dict[str, str]) -> str:
    items = bullets[:4]
    if not items:
        return ""
    step_gap = int(w / max(len(items), 1))
    out: List[str] = [
        f'<line x1="{x + 56}" y1="{y + 62}" x2="{x + w - 56}" y2="{y + 62}" stroke="{p["line"]}" stroke-width="3"/>'
    ]
    for idx, item in enumerate(items):
        cx = x + int(step_gap * idx) + int(step_gap / 2)
        out.extend(
            [
                f'<circle cx="{cx}" cy="{y + 62}" r="28" fill="{p["accent"]}" opacity="{0.95 if idx == 0 else 0.72}"/>',
                f'<text x="{cx}" y="{y + 70}" font-size="20" fill="#FFFFFF" text-anchor="middle" font-weight="800" font-family="Arial">{idx + 1}</text>',
                f'<rect x="{cx - 104}" y="{y + 116}" width="208" height="126" rx="20" fill="#FFFFFF" stroke="{p["line"]}" stroke-width="1"/>',
                _svg_multiline_text(item, x=cx - 78, y=y + 158, size=19, fill=p["ink"], weight=700, max_chars=12, max_lines=3, line_gap=25),
            ]
        )
    return "\n".join(out)


def _image_element(href: str, *, x: int, y: int, w: int, h: int, opacity: float = 1.0) -> str:
    if not href:
        return ""
    return (
        f'<image href="{_xml(href)}" x="{x}" y="{y}" width="{w}" height="{h}" '
        f'preserveAspectRatio="xMidYMid slice" opacity="{opacity}"/>'
    )


def _decorative_grid(p: Dict[str, str], *, opacity: float = 0.18) -> str:
    lines: List[str] = []
    for x in range(80, 1240, 80):
        lines.append(f'<line x1="{x}" y1="0" x2="{x}" y2="720" stroke="{p["line"]}" stroke-width="1" opacity="{opacity}"/>')
    for y in range(80, 700, 80):
        lines.append(f'<line x1="0" y1="{y}" x2="1280" y2="{y}" stroke="{p["line"]}" stroke-width="1" opacity="{opacity}"/>')
    return "\n".join(lines)


def _svg_text_block(lines: List[str], *, x: int, y: int, width: int, size: int, color: str, gap: int = 44) -> str:
    out: List[str] = []
    for offset, line in enumerate(lines):
        cy = y + offset * gap
        out.append(f'<circle cx="{x + 9}" cy="{cy - 8}" r="5" fill="{color}" opacity="0.9"/>')
        out.append(
            f'<text x="{x + 28}" y="{cy}" font-size="{size}" fill="#28323D" '
            f'font-family="Microsoft YaHei, PingFang SC, Arial" data-width="{width}">{_xml(line)}</text>'
        )
    return "\n".join(out)


def _image_href(image_path: Optional[str], svg_dir: Path) -> str:
    if not image_path:
        return ""
    try:
        return os.path.relpath(str(Path(image_path).resolve()), str(svg_dir.resolve())).replace("\\", "/")
    except Exception:
        return str(image_path).replace("\\", "/")


def _strip_code_fence(text: Any) -> str:
    raw = str(text or "").strip()
    fenced = re.search(r"```(?:svg|xml)?\s*([\s\S]*?)\s*```", raw, re.IGNORECASE)
    if fenced:
        raw = fenced.group(1).strip()
    start = raw.find("<svg")
    end = raw.rfind("</svg>")
    if start >= 0 and end >= start:
        raw = raw[start : end + len("</svg>")]
    return raw.strip()


def validate_and_prepare_slide_svg(
    svg_text: Any,
    *,
    svg_dir: Path,
    allowed_image_path: Optional[str] = None,
) -> Optional[str]:
    """Return a PPT Master-safe SVG string or None if it should fall back."""
    raw = _strip_code_fence(svg_text)
    if not raw or "<svg" not in raw.lower():
        return None
    raw = re.sub(r"<!DOCTYPE[\s\S]*?>", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"<\s*(script|foreignObject)\b[\s\S]*?<\s*/\s*\1\s*>", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s+on[a-zA-Z]+\s*=\s*(['\"])[\s\S]*?\1", "", raw)
    raw = re.sub(r"\s+xmlns:xlink=(['\"])[\s\S]*?\1", "", raw)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    if not root.tag.lower().endswith("svg"):
        return None
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    root.set("width", str(SLIDE_W))
    root.set("height", str(SLIDE_H))
    root.set("viewBox", f"0 0 {SLIDE_W} {SLIDE_H}")

    allowed_href = _image_href(allowed_image_path, svg_dir) if allowed_image_path else ""
    allowed_names = {Path(allowed_href).name} if allowed_href else set()
    text_count = 0
    image_count = 0
    for elem in list(root.iter()):
        tag = elem.tag.split("}")[-1].lower()
        if tag == "text":
            text_count += 1
        if tag in {"script", "foreignobject", "filter"} or tag.startswith("fe"):
            try:
                root.remove(elem)
            except Exception:
                pass
            continue
        for key in list(elem.attrib):
            low = key.lower()
            if low.startswith("on"):
                elem.attrib.pop(key, None)
            if low == "filter" or "filter" in str(elem.attrib.get(key) or "").lower():
                elem.attrib.pop(key, None)
        if tag == "image":
            image_count += 1
            if image_count > 1:
                return None
            href_key = None
            href = ""
            for key, value in elem.attrib.items():
                if key.lower().endswith("href"):
                    href_key = key
                    href = str(value or "").strip()
                    break
            if not href_key:
                if allowed_href:
                    elem.set("href", allowed_href)
                else:
                    return None
            elif href.startswith(("http://", "https://")):
                return None
            elif href.startswith("data:"):
                return None
            elif allowed_href and Path(href).name in allowed_names:
                elem.set(href_key, allowed_href)
            elif allowed_href and href != allowed_href:
                elem.set(href_key, allowed_href)
            elif not allowed_href:
                return None
            try:
                opacity = float(elem.attrib.get("opacity", "1"))
                if opacity < 0.86:
                    elem.set("opacity", "0.92")
            except Exception:
                pass

    safe = ET.tostring(root, encoding="unicode")
    if len(safe) < 800:
        return None
    if text_count < 1:
        return None
    return safe


def render_slide_svg(
    *,
    slide: Dict[str, Any],
    plan: Dict[str, Any],
    theme: str,
    image_path: Optional[str],
    svg_dir: Path,
) -> str:
    p = _theme_palette(theme)
    title = safe_text(slide.get("title"), 100)
    subtitle = safe_text(slide.get("subtitle"), 140)
    claim = safe_text(slide.get("claim"), 120)
    bullets = _short_lines(slide.get("bullets") or [], max_items=5)
    metrics = _coerce_short_list(slide.get("metrics"), max_items=3, max_len=30)
    layout = safe_text(slide.get("layout") or "content", 32)
    role = safe_text(slide.get("role") or "", 32)
    visual_style = safe_text(slide.get("visual_style") or "", 40)
    page = int(slide.get("index") or 1)
    total = len(plan.get("slides") or [])
    deck_label = _deck_label(plan)
    image_href = _image_href(image_path, svg_dir)

    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SLIDE_W}" height="{SLIDE_H}" viewBox="0 0 {SLIDE_W} {SLIDE_H}">',
        f'<rect width="{SLIDE_W}" height="{SLIDE_H}" fill="{p["bg"]}"/>',
    ]

    if layout == "title":
        title_size = _fit_font_size(title, 62, min_size=38, max_chars=15)
        if image_href:
            parts.extend([
                _image_element(image_href, x=0, y=0, w=1280, h=720, opacity=0.96),
                '<rect width="1280" height="720" fill="#07111B" opacity="0.38"/>',
                '<rect x="0" y="0" width="720" height="720" fill="#07111B" opacity="0.72"/>',
                '<rect x="720" y="0" width="220" height="720" fill="#07111B" opacity="0.18"/>',
                _decorative_grid({"line": "#FFFFFF"}, opacity=0.055),
                f'<rect x="76" y="94" width="96" height="7" rx="3.5" fill="{p["accent"]}"/>',
                _svg_multiline_text(title, x=76, y=214, size=title_size, fill="#FFFFFF", weight=820, max_chars=11, max_lines=3, line_gap=66),
                _svg_multiline_text(subtitle or plan.get("subtitle") or "", x=80, y=430, size=25, fill="#DCE6EF", weight=450, max_chars=27, max_lines=2),
                _svg_metric_pills(metrics or ["市场洞察", "策略建议"], x=80, y=506, p=p, dark=True, filled=True),
                '<rect x="80" y="584" width="330" height="1" fill="#FFFFFF" opacity="0.28"/>',
                f'<text x="80" y="624" font-size="18" fill="#FFFFFF" opacity="0.78" font-family="Microsoft YaHei, PingFang SC, Arial">{_xml(deck_label)}</text>',
                _svg_page_label(page, total, p, dark=True),
            ])
        else:
            parts.extend([
                _decorative_grid(p, opacity=0.24),
                f'<rect x="90" y="96" width="1100" height="510" rx="28" fill="{p["panel"]}" stroke="{p["line"]}" stroke-width="1.4"/>',
                f'<rect x="90" y="96" width="18" height="510" rx="9" fill="{p["accent"]}"/>',
                _svg_multiline_text(title, x=148, y=260, size=title_size, fill=p["ink"], weight=800, max_chars=19, max_lines=2, line_gap=70),
                _svg_multiline_text(subtitle or plan.get("subtitle") or "", x=152, y=420, size=28, fill=p["muted"], max_chars=36, max_lines=2),
                _svg_metric_pills(metrics or ["自动生成", "可编辑PPT"], x=152, y=486, p=p, filled=True),
                _svg_page_label(page, total, p),
            ])
    elif layout == "section":
        chapter_slides = [
            item
            for item in (plan.get("slides") or [])
            if safe_text(item.get("layout"), 32) in {"section", "content", "image_right"}
        ]
        parts.extend([
            _decorative_grid(p, opacity=0.16),
            f'<rect x="0" y="0" width="1280" height="720" fill="{p["accent"]}" opacity="0.08"/>',
            f'<rect x="746" y="0" width="534" height="720" fill="{p["panel"]}" opacity="0.78"/>',
            f'<rect x="790" y="116" width="396" height="374" rx="28" fill="#FFFFFF" opacity="0.64" stroke="{p["line"]}" stroke-width="1"/>',
            f'<text x="92" y="166" font-size="18" fill="{p["accent"]}" font-weight="800" font-family="Arial">SECTION {page:02d}</text>',
            f'<rect x="92" y="206" width="164" height="8" fill="{p["accent"]}"/>',
            _svg_multiline_text(title, x=92, y=332, size=_fit_font_size(title, 62, min_size=38, max_chars=18), fill=p["ink"], weight=800, max_chars=20, max_lines=2, line_gap=72),
            _svg_multiline_text(subtitle, x=96, y=486, size=27, fill=p["muted"], max_chars=42, max_lines=2),
            _svg_mini_agenda(chapter_slides, x=836, y=166, p=p, active=page),
            f'<circle cx="1100" cy="178" r="68" fill="{p["accent"]}" opacity="0.16"/>',
            f'<circle cx="1142" cy="224" r="104" fill="{p["accent2"]}" opacity="0.12"/>',
            _svg_page_label(page, total, p),
        ])
    elif layout == "quote":
        quote = bullets[0] if bullets else subtitle
        if image_href:
            parts.extend([
                _image_element(image_href, x=750, y=0, w=530, h=720, opacity=0.94),
                f'<rect x="0" y="0" width="810" height="720" fill="{p["bg"]}"/>',
            ])
        parts.extend([
            f'<text x="96" y="150" font-size="86" fill="{p["accent"]}" opacity="0.22" font-family="Georgia">“</text>',
            _svg_multiline_text(quote, x=112, y=280, size=42, fill=p["ink"], weight=760, max_chars=19, max_lines=3, line_gap=58),
            f'<rect x="116" y="500" width="110" height="5" fill="{p["accent"]}"/>',
            _svg_multiline_text(title, x=116, y=552, size=25, fill=p["muted"], max_chars=30, max_lines=1),
            _svg_metric_pills(metrics, x=116, y=590, p=p),
            _svg_page_label(page, total, p),
        ])
    elif layout == "ending":
        if image_href:
            parts.extend([
                _image_element(image_href, x=0, y=0, w=1280, h=720, opacity=0.96),
                '<rect width="1280" height="720" fill="#081018" opacity="0.58"/>',
            ])
        parts.extend([
            f'<rect x="90" y="170" width="520" height="6" fill="{p["accent"]}"/>',
            _svg_multiline_text(title or "Thank You", x=90, y=292, size=_fit_font_size(title, 64, min_size=42, max_chars=14), fill="#FFFFFF" if image_href else p["ink"], weight=840, max_chars=16, max_lines=2),
            _svg_multiline_text(subtitle or "关键结论与下一步行动", x=94, y=408, size=27, fill="#DCE6EF" if image_href else p["muted"], max_chars=32, max_lines=2),
            f'<rect x="672" y="142" width="500" height="358" rx="28" fill="{p["panel"]}" opacity="0.72" stroke="{p["line"]}" stroke-width="1"/>' if bullets and not image_href else "",
            _svg_bullet_cards(bullets, x=704, y=174, w=430, p=p, columns=1, max_items=3) if bullets and not image_href else "",
            _svg_claim_band(claim, x=90, y=500, w=560, p=p, dark=bool(image_href)),
            _svg_page_label(page, total, p, dark=bool(image_href)),
        ])
    elif layout == "data" or visual_style in {"kpi_strip", "data_tiles"} or role == "data":
        if image_href:
            parts.extend([
                _image_element(image_href, x=0, y=0, w=1280, h=280, opacity=0.88),
                '<rect x="0" y="0" width="1280" height="280" fill="#071018" opacity="0.42"/>',
                _svg_multiline_text(title, x=72, y=132, size=_fit_font_size(title, 46, min_size=34, max_chars=22), fill="#FFFFFF", weight=820, max_chars=24, max_lines=2),
                _svg_multiline_text(subtitle, x=74, y=206, size=22, fill="#DCE6EF", max_chars=48, max_lines=1),
            ])
        else:
            parts.extend([
                _decorative_grid(p, opacity=0.13),
                _svg_header(title, subtitle, p, page=page, total=total, deck_label=deck_label),
            ])
        parts.extend([
            _svg_claim_band(claim, x=74, y=326, w=1130, p=p),
            _svg_data_tiles(metrics, bullets, x=74, y=430, w=1130, p=p),
            _svg_page_label(page, total, p),
        ])
    elif layout == "comparison" or visual_style in {"comparison", "compare"} or role == "comparison":
        parts.extend([
            _decorative_grid(p, opacity=0.12),
            _svg_header(title, subtitle, p, page=page, total=total, deck_label=deck_label),
            _svg_claim_band(claim, x=78, y=236, w=1120, p=p),
            _svg_comparison_columns(bullets, x=78, y=326, w=1120, p=p),
        ])
    elif layout == "process" or visual_style in {"process", "timeline", "roadmap"} or role == "process":
        parts.extend([
            _decorative_grid(p, opacity=0.12),
            _svg_header(title, subtitle, p, page=page, total=total, deck_label=deck_label),
            _svg_claim_band(claim, x=86, y=238, w=1080, p=p),
            _svg_process_steps(bullets, x=92, y=358, w=1060, p=p),
            _svg_metric_pills(metrics, x=780, y=626, p=p),
        ])
    else:
        variant = page % 3
        if image_href:
            if visual_style == "full_bleed" or variant == 0:
                parts.extend([
                    _image_element(image_href, x=0, y=0, w=1280, h=720, opacity=0.95),
                    '<rect x="0" y="0" width="1280" height="720" fill="#071018" opacity="0.34"/>',
                    f'<rect x="66" y="72" width="600" height="574" rx="26" fill="{p["panel"]}" opacity="0.95"/>',
                    _svg_header(title, subtitle, p, page=page, total=total, deck_label=deck_label),
                    _svg_claim_band(claim, x=96, y=242, w=520, p=p),
                    _svg_bullet_cards(bullets, x=96, y=336, w=520, p=p, columns=1, max_items=3),
                    _svg_metric_pills(metrics, x=96, y=570, p=p, filled=False),
                ])
            elif visual_style == "split_image" or variant == 1:
                parts.extend([
                    _svg_header(title, subtitle, p, page=page, total=total, deck_label=deck_label),
                    f'<rect x="686" y="88" width="510" height="512" rx="26" fill="{p["panel"]}" stroke="{p["line"]}" stroke-width="1.1"/>',
                    _image_element(image_href, x=710, y=112, w=462, h=464, opacity=0.98),
                    _svg_claim_band(claim, x=78, y=244, w=560, p=p),
                    _svg_bullet_cards(bullets, x=78, y=340, w=560, p=p, columns=1, max_items=3),
                    _svg_metric_pills(metrics, x=78, y=574, p=p),
                ])
            else:
                parts.extend([
                    _image_element(image_href, x=0, y=0, w=1280, h=330, opacity=0.94),
                    '<rect x="0" y="0" width="1280" height="330" fill="#071018" opacity="0.36"/>',
                    _svg_multiline_text(title, x=78, y=142, size=_fit_font_size(title, 46, min_size=32, max_chars=24), fill="#FFFFFF", weight=800, max_chars=24, max_lines=2),
                    _svg_multiline_text(subtitle, x=80, y=220, size=22, fill="#DCE6EF", max_chars=42, max_lines=1),
                    _svg_claim_band(claim, x=78, y=358, w=1120, p=p),
                    _svg_bullet_cards(bullets, x=78, y=462, w=1120, p=p, columns=2, max_items=4),
                    _svg_metric_pills(metrics, x=78, y=612, p=p),
                    _svg_page_label(page, total, p),
                ])
        else:
            parts.extend([
                _decorative_grid(p, opacity=0.16),
                _svg_header(title, subtitle, p, page=page, total=total, deck_label=deck_label),
                _svg_claim_band(claim, x=86, y=246, w=1080, p=p),
                _svg_bullet_cards(bullets, x=86, y=356, w=1080, p=p, columns=2 if len(bullets) > 3 else 1, max_items=4),
                _svg_metric_pills(metrics, x=86, y=612, p=p),
            ])

    parts.append("</svg>")
    return "\n".join(part for part in parts if part)

def write_ppt_master_project(
    *,
    run_dir: Path,
    plan: Dict[str, Any],
    theme: str,
    source_markdown: str,
    slide_images: Optional[Dict[int, str]] = None,
    slide_svgs: Optional[Dict[int, str]] = None,
) -> Path:
    project_dir = run_dir / "ppt_master_project"
    if project_dir.exists():
        shutil.rmtree(project_dir)
    for rel in ("svg_output", "svg_final", "images", "notes", "templates", "sources", "exports"):
        (project_dir / rel).mkdir(parents=True, exist_ok=True)

    (project_dir / "README.md").write_text(
        f"# {safe_text(plan.get('title'), 100)}\n\n- Canvas format: ppt169\n",
        encoding="utf-8",
    )
    (project_dir / "sources" / "source.md").write_text(source_markdown or "", encoding="utf-8")
    (project_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (project_dir / "spec_lock.md").write_text(
        "\n".join(
            [
                "# PPT Master Spec Lock",
                "",
                "- canvas: ppt169 / 1280x720",
                f"- theme: {theme or 'business'}",
                "- font_family: Microsoft YaHei, PingFang SC, Arial",
                "- output: editable native PPTX from SVG",
            ]
        ),
        encoding="utf-8",
    )

    svg_dir = project_dir / "svg_output"
    image_map: Dict[int, str] = {}
    for idx, source in (slide_images or {}).items():
        try:
            src = Path(source)
            if not src.exists() or not src.is_file():
                continue
            suffix = src.suffix.lower() if src.suffix else ".png"
            dst = project_dir / "images" / f"slide_{int(idx):02d}{suffix}"
            shutil.copy2(src, dst)
            image_map[int(idx)] = str(dst)
        except Exception:
            continue
    render_manifest: List[Dict[str, Any]] = []
    for slide in plan.get("slides") or []:
        index = int(slide.get("index") or 1)
        svg = ""
        source = "script"
        if slide_svgs and slide_svgs.get(index):
            prepared = validate_and_prepare_slide_svg(
                slide_svgs.get(index),
                svg_dir=svg_dir,
                allowed_image_path=image_map.get(index),
            )
            if prepared:
                svg = prepared
                source = "ai_svg"
        if not svg:
            svg = render_slide_svg(
                slide=slide,
                plan=plan,
                theme=theme,
                image_path=image_map.get(index),
                svg_dir=svg_dir,
            )
        (svg_dir / f"slide_{index:02d}.svg").write_text(svg, encoding="utf-8")
        render_manifest.append({"index": index, "renderer": source, "has_image": bool(image_map.get(index))})
        notes = safe_text(slide.get("notes") or "\n".join(slide.get("bullets") or []), 1600)
        if notes:
            (project_dir / "notes" / f"slide_{index:02d}.md").write_text(notes, encoding="utf-8")
    (project_dir / "render_manifest.json").write_text(
        json.dumps(render_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return project_dir


def inspect_ppt_master_project(*, project_dir: Path, pptx_path: Optional[Path] = None) -> Dict[str, Any]:
    svg_dir = project_dir / "svg_output"
    image_dir = project_dir / "images"
    svg_records: List[Dict[str, Any]] = []
    broken_images: List[Dict[str, str]] = []
    total_svg_images = 0
    total_svg_text = 0
    for svg_path in sorted(svg_dir.glob("*.svg")):
        text = svg_path.read_text(encoding="utf-8", errors="replace")
        image_hrefs = re.findall(r'<image\b[^>]*\bhref="([^"]+)"', text)
        text_count = len(re.findall(r"<text\b", text))
        total_svg_images += len(image_hrefs)
        total_svg_text += text_count
        for href in image_hrefs:
            if href.startswith(("data:", "http://", "https://")):
                continue
            target = (svg_path.parent / href).resolve()
            if not target.exists():
                broken_images.append({"svg": svg_path.name, "href": href})
        svg_records.append(
            {
                "file": svg_path.name,
                "bytes": svg_path.stat().st_size,
                "image_count": len(image_hrefs),
                "text_count": text_count,
                "has_content": svg_path.stat().st_size > 800 and text_count > 0,
            }
        )

    pptx_meta: Dict[str, Any] = {}
    if pptx_path and pptx_path.exists():
        try:
            with zipfile.ZipFile(pptx_path) as zf:
                names = zf.namelist()
                slides = [name for name in names if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
                media = [name for name in names if name.startswith("ppt/media/")]
                pptx_meta = {
                    "slide_count": len(slides),
                    "media_count": len(media),
                    "media_bytes": sum(zf.getinfo(name).file_size for name in media),
                }
        except Exception as exc:
            pptx_meta = {"error": str(exc)[:300]}

    return {
        "svg_count": len(svg_records),
        "svg_image_count": total_svg_images,
        "svg_text_count": total_svg_text,
        "project_image_count": len([p for p in image_dir.glob("*") if p.is_file()]) if image_dir.exists() else 0,
        "broken_images": broken_images,
        "empty_or_sparse_svgs": [row["file"] for row in svg_records if not row["has_content"]],
        "slides": svg_records,
        "pptx": pptx_meta,
        "ok": bool(svg_records) and not broken_images and not [row for row in svg_records if not row["has_content"]],
    }


def export_ppt_master_project(*, project_dir: Path, output_path: Path, timeout_sec: float = 600.0) -> Dict[str, Any]:
    skill_dir = ppt_master_skill_dir()
    project_dir = project_dir.resolve()
    output_path = output_path.resolve()
    finalize = skill_dir / "scripts" / "finalize_svg.py"
    exporter = skill_dir / "scripts" / "svg_to_pptx.py"
    if not finalize.exists() or not exporter.exists():
        raise HTTPException(status_code=503, detail="PPT Master export scripts are incomplete")

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    exporter_output_path = output_path
    needs_ascii_bridge = any(ord(ch) > 127 for ch in str(output_path))
    if needs_ascii_bridge:
        exporter_output_path = output_path.with_name(f"ppt_master_export_{os.getpid()}.pptx")
        if exporter_output_path.exists():
            exporter_output_path.unlink()
    commands = [
        [sys.executable, str(finalize), str(project_dir), "--quiet"],
        [sys.executable, str(exporter), str(project_dir), "-o", str(exporter_output_path), "--only", "native", "--no-notes", "-q"],
    ]
    logs: List[Dict[str, Any]] = []
    for cmd in commands:
        proc = subprocess.run(
            cmd,
            cwd=str(skill_dir),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_sec,
        )
        logs.append({"cmd": cmd, "returncode": proc.returncode, "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:]})
        if proc.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"PPT Master export failed: {(proc.stderr or proc.stdout or '')[-1200:]}",
            )
    if needs_ascii_bridge:
        if not exporter_output_path.exists() or not exporter_output_path.is_file():
            raise HTTPException(status_code=500, detail="PPT Master export finished but temporary output PPTX was not created")
        if output_path.exists():
            output_path.unlink()
        shutil.move(str(exporter_output_path), str(output_path))
    if not output_path.exists() or not output_path.is_file():
        raise HTTPException(status_code=500, detail="PPT Master export finished but output PPTX was not created")
    inspection = inspect_ppt_master_project(project_dir=project_dir, pptx_path=output_path)
    if inspection.get("broken_images"):
        raise HTTPException(status_code=500, detail=f"PPT Master export has broken image references: {inspection['broken_images'][:3]}")
    return {
        "project_dir": str(project_dir),
        "export_logs": logs,
        "ascii_output_bridge": needs_ascii_bridge,
        "inspection": inspection,
    }
