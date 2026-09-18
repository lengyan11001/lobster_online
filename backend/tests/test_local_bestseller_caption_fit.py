from __future__ import annotations

import re
import unicodedata

from backend.app.api import comfly_seedance_tvc as tvc
from backend.app.api import local_bestseller as lb

PLAY_X, PLAY_Y = 1080, 1920
MARGIN_X = 52


def _units(text: str) -> float:
    total = 0.0
    for ch in str(text or ""):
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            total += 1.0
        elif ch.isspace():
            total += 0.35
        else:
            total += 0.55
    return total


def _style_fonts(ass: str) -> dict:
    fonts = {}
    for line in ass.splitlines():
        if line.startswith("Style: "):
            parts = line[len("Style: "):].split(",")
            fonts[parts[0]] = int(float(parts[2]))
    return fonts


def _events(ass: str) -> list:
    fonts = _style_fonts(ass)
    rows = []
    for line in ass.splitlines():
        if not line.startswith("Dialogue: "):
            continue
        fields = line[len("Dialogue: "):].split(",", 9)
        style = fields[3]
        text = fields[9] if len(fields) > 9 else ""
        pos = re.search(r"\\pos\((-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\)", text)
        fs = re.search(r"\\fs(\d+)", text)
        rows.append({
            "style": style,
            "text": re.sub(r"\{[^}]*\}", "", text).strip(),
            "font": int(fs.group(1)) if fs else fonts.get(style, 88),
            "x": float(pos.group(1)) if pos else None,
            "y": float(pos.group(2)) if pos else None,
        })
    return rows


def _profile() -> dict:
    return lb._clean_profile(
        lb.LocalBestsellerProfile(
            name="张想", nickname="张想", gender="female", identity="女老板",
            industry="大健康", city="深圳", province="广东",
        )
    )


def test_caption_pixels_stay_inside_the_frame_for_all_days():
    profile = _profile()
    checked = 0
    for row in lb._load_templates()[:30]:
        day = int(row.get("day"))
        card = lb._build_card(row, profile)
        for platform in ("douyin", "videohao"):
            ass = tvc._local_bestseller_ass_content(
                card[platform]["copy"], lb._caption_style(row, platform), day=day
            )
            for item in _events(ass):
                if not item["text"]:
                    continue
                checked += 1
                width = _units(item["text"]) * item["font"]
                if item["x"] is not None:
                    left = item["x"] - width / 2
                    right = item["x"] + width / 2
                    assert left >= 0 and right <= PLAY_X, (day, platform, item)
                else:
                    assert width <= PLAY_X - 2 * MARGIN_X, (day, platform, item)
                if item["y"] is not None:
                    assert item["y"] >= 0, (day, platform, item)
                    assert item["y"] + item["font"] * 1.05 <= PLAY_Y, (day, platform, item)
    assert checked > 0


def test_long_caption_line_is_wrapped_not_overflowing():
    text = "风吹广东省，你是广东哪里的？刷到就是缘分一起聊聊"
    wrapped = tvc._wrap_ass_line(text, font_size=88)
    assert len(wrapped) > 1
    assert "".join(wrapped) == text
    for piece in wrapped:
        assert tvc._ass_display_units(piece) * 88 <= 1080


def test_long_title_font_is_shrunk_to_fit():
    title = "广东各市贫富差距排行榜"
    fitted = tvc._ass_fit_font_size(title, base_size=96, margin_x=54, min_size=58)
    assert fitted < 96
    assert tvc._ass_display_units(title) * fitted <= 1080


def test_rank_table_last_row_stays_inside_frame():
    ass = tvc._local_bestseller_rank_table_ass_content("广东各市贫富差距排行榜\n湖北竟然是南方", day=3)
    rows = [item for item in _events(ass) if item["style"] == "RankList"]
    assert len(rows) == 30
    assert max(item["y"] for item in rows) + 90 * 1.05 <= PLAY_Y


def test_scene_only_day_captions_are_stacked_from_the_top():
    profile = _profile()
    rows = {int(item.get("day")): item for item in lb._load_templates()}
    card = lb._build_card(rows[1], profile)
    ass = tvc._local_bestseller_ass_content(card["douyin"]["copy"], lb._caption_style(rows[1], "videohao"), day=1)
    ys = [item["y"] for item in _events(ass) if item["y"] is not None]
    assert ys and min(ys) < 400
