"""成片画面质检：黑帧、音轨、时长、帧率、安全区/文字截断。"""
from __future__ import annotations

import json
import math
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
from PIL import Image

from . import koubo_renderer as kr

OUTPUT_DIR = kr.OUTPUT_DIR
DATA_DIR = kr.DATA_DIR
FFMPEG = kr.FFMPEG


def _layout_path(pid: str, quality: str) -> Path:
    return OUTPUT_DIR / pid / ("qa_%s.json" % quality)


def check_render(project_id: str, quality: str = "final") -> Dict[str, Any]:
    quality = "preview" if (quality or "").strip().lower() == "preview" else "final"
    path = OUTPUT_DIR / project_id / ("final_preview.mp4" if quality == "preview" else "final_packaged.mp4")
    if not path.is_file():
        return {"ok": False, "score": 0, "warnings": ["成片文件不存在"], "checks": [], "quality": quality}
    layout: Dict[str, Any] = {}
    lp = _layout_path(project_id, quality)
    if lp.is_file():
        try:
            layout = json.loads(lp.read_text(encoding="utf-8"))
        except Exception:
            layout = {}
    info = kr.probe(path)
    warnings: List[str] = list(layout.get("layout_warnings") or [])
    checks: List[Dict[str, Any]] = []

    expected_fps = 12.0 if quality == "preview" else 30.0
    if abs(float(info.get("fps") or 0) - expected_fps) > 0.6:
        warnings.append("帧率异常：%.1ffps，预期 %.0ffps" % (float(info.get("fps") or 0), expected_fps))
        checks.append({"name": "帧率", "ok": False, "detail": "%.1ffps" % float(info.get("fps") or 0)})
    else:
        checks.append({"name": "帧率", "ok": True, "detail": "%.1ffps" % float(info.get("fps") or 0)})

    expected_duration = float(layout.get("duration") or 0)
    if expected_duration > 0 and abs(float(info.get("duration") or 0) - expected_duration) > 0.8:
        warnings.append("时长异常：%.2fs，底片 %.2fs" % (float(info.get("duration") or 0), expected_duration))
        checks.append({"name": "时长", "ok": False, "detail": "%.2fs" % float(info.get("duration") or 0)})
    else:
        checks.append({"name": "时长", "ok": True, "detail": "%.2fs" % float(info.get("duration") or 0)})

    tmp = Path(tempfile.mkdtemp(prefix="koubo-qa-", dir=str(DATA_DIR)))
    black = 0
    samples = 0
    try:
        duration = float(info.get("duration") or 0)
        for frac in (0.08, 0.35, 0.65, 0.9):
            t = max(0.05, min(max(0.1, duration - 0.06), duration * frac))
            frame = tmp / ("f_%s.jpg" % str(frac).replace(".", "_"))
            r = kr._run([str(FFMPEG), "-y", "-loglevel", "error", "-ss", "%.3f" % t,
                         "-i", str(path), "-frames:v", "1", "-q:v", "2", str(frame)], timeout=60)
            if r.returncode != 0 or not frame.is_file():
                continue
            with Image.open(frame) as im:
                arr = np.asarray(im.convert("L"), dtype=np.float32)
            samples += 1
            mean_v = float(arr.mean()) if arr.size else 0.0
            std_v = float(arr.std()) if arr.size else 0.0
            if mean_v < 8 or std_v < 4:
                black += 1
                checks.append({"name": "画面", "ok": False, "detail": "%.2fs 可能黑帧/纯色 mean=%.1f std=%.1f" % (t, mean_v, std_v)})
        if samples and black:
            warnings.append("检测到 %d/%d 个疑似黑帧" % (black, samples))
        elif samples:
            checks.append({"name": "画面", "ok": True, "detail": "抽查 %d 帧未发现黑帧" % samples})
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    vol = kr._run([str(FFMPEG), "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"], timeout=120)
    vol_text = (vol.stderr or "") + "\n" + (vol.stdout or "")
    has_audio = "Audio:" in vol_text
    if not has_audio:
        warnings.append("成片没有音轨")
        checks.append({"name": "音轨", "ok": False, "detail": "无音轨"})
    else:
        mean_m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", vol_text)
        max_m = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?) dB", vol_text)
        mean_db = float(mean_m.group(1)) if mean_m else None
        max_db = float(max_m.group(1)) if max_m else None
        if max_db is not None and max_db >= -0.1:
            warnings.append("音量可能削波 max=%.1fdB" % max_db)
            checks.append({"name": "音轨", "ok": False, "detail": "可能削波 %.1fdB" % max_db})
        elif mean_db is not None and mean_db < -30:
            warnings.append("整体音量偏低 mean=%.1fdB" % mean_db)
            checks.append({"name": "音轨", "ok": False, "detail": "偏低 %.1fdB" % mean_db})
        else:
            checks.append({"name": "音轨", "ok": True, "detail": "mean=%s dB max=%s dB" % (
                ("%.1f" % mean_db) if mean_db is not None else "?", ("%.1f" % max_db) if max_db is not None else "?")})

    if layout.get("layout_checked") and not layout.get("layout_warnings"):
        checks.append({"name": "安全区", "ok": True, "detail": "抽查时间点未发现文字溢出/截断"})
    if layout.get("layout_checks"):
        for item in layout["layout_checks"]:
            if not item.get("ok"):
                checks.append({"name": "安全区", "ok": False, "detail": item.get("detail") or ""})

    score = max(0, 100 - len(warnings) * 8)
    return {
        "ok": not warnings,
        "score": score,
        "warnings": warnings[:20],
        "checks": checks[:30],
        "quality": quality,
        "info": info,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
