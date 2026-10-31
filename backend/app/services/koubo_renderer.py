"""数字人koubo 客户端本地渲染器（不依赖 Remotion / CutCLI）。

流程：
1. ffprobe 读成片尺寸/时长；
2. Playwright 逐帧截图透明叠层（逐字字幕 / 数字钉子 / CTA）；
3. ffmpeg 把叠层视频流与成片合成，音轨直接 copy。

只依赖客户端已带的东西：python-playwright、deps/ffmpeg。
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[3]
FFMPEG = ROOT / "deps" / "ffmpeg" / "ffmpeg.exe"
FFPROBE = ROOT / "deps" / "ffmpeg" / "ffprobe.exe"
DATA_DIR = ROOT / "data" / "koubo"
OUTPUT_DIR = DATA_DIR / "output"

PRESETS = {
    "克制": {"cap": 0.048, "kw": 0.085, "pop": 0.06, "keyColor": "#FFE14D", "cta": 0.040},
    "标准": {"cap": 0.056, "kw": 0.100, "pop": 0.12, "keyColor": "#FFE14D", "cta": 0.046},
    "热闹": {"cap": 0.062, "kw": 0.115, "pop": 0.20, "keyColor": "#FF5A8A", "cta": 0.052},
}

_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}
html,body{margin:0;padding:0;width:__W__px;height:__H__px;background:transparent;overflow:hidden;
  font-family:"Microsoft YaHei","PingFang SC","Noto Sans SC","Source Han Sans SC",sans-serif;}
#cap{position:absolute;left:6%;right:6%;bottom:9%;text-align:center;font-weight:800;line-height:1.32;
  font-size:__CAP__px;color:#fff;-webkit-text-stroke:__STROKE__px rgba(0,0,0,.85);paint-order:stroke fill;
  text-shadow:0 2px 10px rgba(0,0,0,.45);letter-spacing:.01em;}
#cap span{display:inline-block;transition:none;}
#cap span.on{color:__KEY__;transform:translateY(-2px) scale(1.06);}
#kw{position:absolute;left:0;right:0;top:12%;text-align:center;font-weight:900;color:__KEY__;
  font-size:__KW__px;-webkit-text-stroke:__KWSTROKE__px rgba(0,0,0,.8);paint-order:stroke fill;
  text-shadow:0 6px 20px rgba(0,0,0,.35);opacity:0;}
#cta{position:absolute;left:12%;right:12%;bottom:24%;text-align:center;font-weight:800;color:#fff;
  font-size:__CTA__px;padding:2.2% 0;border-radius:14px;background:linear-gradient(135deg,#f97316,#ea580c);
  box-shadow:0 10px 30px rgba(234,88,12,.45);opacity:0;}
</style></head><body>
<div id="cap"></div><div id="kw"></div><div id="cta">点个关注，下期见</div>
<script>
window.TL = __TL__;
window.renderAt = function (t) {
  var TL = window.TL, seg = null;
  for (var i = 0; i < TL.segs.length; i++) { if (t >= TL.segs[i].start && t < TL.segs[i].end) { seg = TL.segs[i]; break; } }
  var cap = document.getElementById('cap');
  if (!seg) { cap.innerHTML = ''; }
  else {
    var idx = -1;
    for (var j = 0; j < seg.chars.length; j++) { if (seg.chars[j][1] <= t) idx = j; }
    var html = '';
    for (var k = 0; k < seg.chars.length; k++) {
      html += '<span class="' + (k === idx ? 'on' : '') + '">' + seg.chars[k][0] + '</span>';
    }
    cap.innerHTML = html;
  }
  var kw = document.getElementById('kw'), kwOn = null;
  for (var m = 0; m < TL.keywords.length; m++) {
    var x = TL.keywords[m];
    if (t >= x.start && t < x.end) { kwOn = x; break; }
  }
  if (kwOn) {
    var p = Math.min(1, (t - kwOn.start) / Math.max(0.01, kwOn.pop));
    kw.textContent = kwOn.text;
    kw.style.opacity = '1';
    kw.style.transform = 'scale(' + (0.72 + 0.28 * p) + ')';
  } else { kw.style.opacity = '0'; }
  var cta = document.getElementById('cta');
  if (TL.cta && t >= TL.cta.start) {
    var q = Math.min(1, (t - TL.cta.start) / 0.35);
    cta.style.opacity = String(q);
    cta.style.transform = 'translateY(' + (1 - q) * 14 + 'px)';
  } else { cta.style.opacity = '0'; }
};
</script></body></html>"""


def _run(cmd: List[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def probe(path: Path) -> Dict[str, Any]:
    r = _run([str(FFPROBE), "-v", "error", "-select_streams", "v:0",
              "-show_entries", "stream=width,height,r_frame_rate",
              "-show_entries", "format=duration", "-of", "json", str(path)], timeout=60)
    data = json.loads(r.stdout or "{}")
    st = (data.get("streams") or [{}])[0]
    num, _, den = str(st.get("r_frame_rate") or "30/1").partition("/")
    fps = (float(num) / float(den or 1)) if float(den or 1) else 30.0
    return {"w": int(st.get("width") or 720), "h": int(st.get("height") or 1280),
            "fps": fps or 30.0, "duration": float((data.get("format") or {}).get("duration") or 0)}


def _clean(s: str) -> str:
    return re.sub(r"[\s，。、：；！？,.:;!?“”\"'（）()]", "", s or "")


def build_timeline(project: Dict[str, Any], duration: float) -> Dict[str, Any]:
    segs_in = project.get("segments") or []
    total = sum(float(s.get("seconds") or 0) for s in segs_in) or duration
    k = (duration / total) if total else 1.0
    segs: List[Dict[str, Any]] = []
    t = 0.0
    for s in segs_in:
        dur = max(0.4, float(s.get("seconds") or 0) * k)
        text = str(s.get("text") or "")
        chars = [c for c in text if c.strip()]
        n = max(1, len(chars))
        step = dur / n
        entries = [[c, round(t + i * step, 3)] for i, c in enumerate(chars)]
        segs.append({"start": round(t, 3), "end": round(t + dur, 3), "text": text, "chars": entries})
        t += dur
    keywords: List[Dict[str, Any]] = []
    for seg in segs:
        for tok in re.findall(r"[¥$]?\d[\d,.]*\s*(?:%|元|块|万|亿)?", seg["text"]):
            tok = tok.strip()
            if not tok:
                continue
            hit = next((c for c in seg["chars"] if c[0] in tok), None)
            if not hit:
                continue
            start = max(seg["start"], hit[1])
            keywords.append({"text": tok, "start": start, "end": min(seg["end"], start + 1.4), "pop": 0.22})
    cta_start = max(0.0, duration - min(3.0, max(1.6, duration * 0.28)))
    return {"segs": segs, "keywords": keywords, "cta": {"start": round(cta_start, 3)}, "duration": duration}


def render(
    project: Dict[str, Any],
    preset: str,
    components: Optional[List[str]] = None,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> Path:
    pid = str(project.get("id") or "")
    src = OUTPUT_DIR / pid / "final.mp4"
    if not src.is_file():
        raise RuntimeError("还没有生成成片，无法包装")
    if not FFMPEG.is_file():
        raise RuntimeError("缺少内置 ffmpeg")

    info = probe(src)
    w, h = info["w"], info["h"]
    duration = info["duration"]
    fps = 30.0
    total_frames = max(1, int(math.ceil(duration * fps)))
    p = PRESETS.get(preset) or PRESETS["标准"]
    comps = set(components or ["caption", "keyword", "cta"])
    timeline = build_timeline(project, duration)
    if "keyword" not in comps:
        timeline["keywords"] = []
    if "cta" not in comps:
        timeline["cta"] = None
    if "caption" not in comps:
        timeline["segs"] = []

    html = (_HTML.replace("__W__", str(w)).replace("__H__", str(h))
            .replace("__CAP__", str(round(h * p["cap"])))
            .replace("__STROKE__", str(max(2, round(h * 0.004))))
            .replace("__KW__", str(round(h * p["kw"])))
            .replace("__KWSTROKE__", str(max(2, round(h * 0.005))))
            .replace("__CTA__", str(round(h * p["cta"])))
            .replace("__KEY__", p["keyColor"])
            .replace("__TL__", json.dumps(timeline, ensure_ascii=False)))

    tmp = Path(tempfile.mkdtemp(prefix="koubo-render-", dir=str(DATA_DIR)))
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
            page.set_content(html, wait_until="load")
            for i in range(total_frames):
                t = i / fps
                page.evaluate("(t) => window.renderAt(t)", t)
                page.screenshot(path=str(tmp / ("f%05d.png" % i)), omit_background=True)
                if progress and (i % 10 == 0 or i == total_frames - 1):
                    progress(i + 1, total_frames, "渲染帧 %d/%d" % (i + 1, total_frames))
            browser.close()

        out = OUTPUT_DIR / pid / "final_packaged.mp4"
        cmd = [str(FFMPEG), "-y", "-i", str(src),
               "-framerate", str(fps), "-i", str(tmp / "f%05d.png"),
               "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto",
               "-c:v", "libx264", "-crf", "18", "-preset", "medium",
               "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", str(out)]
        r = _run(cmd, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError("合成失败：" + (r.stderr or "")[-600:])
        if progress:
            progress(total_frames, total_frames, "合成完成")
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
