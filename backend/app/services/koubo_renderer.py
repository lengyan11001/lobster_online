"""数字人koubo 客户端本地渲染器（不依赖 Remotion / CutCLI）。

流程：
1. ffprobe 读成片尺寸/时长；
2. ffmpeg 抽帧到临时目录，起本地 HTTP 服务给浏览器读帧；
3. Playwright 逐帧渲染整帧（背景 + 跳切 + 逐字字幕 + 数字钉子 + 姓名条 + CTA）；
4. ffmpeg 用原音轨编码。

优先用 whisper-cli 的真实逐字时间；模型缺失时退回按字数均分。
"""
from __future__ import annotations

import difflib
import functools
import json
import math
import re
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[3]
FFMPEG = ROOT / "deps" / "ffmpeg" / "ffmpeg.exe"
FFPROBE = ROOT / "deps" / "ffmpeg" / "ffprobe.exe"
WHISPER = ROOT / "deps" / "whisper" / "whisper-cli.exe"
WHISPER_MODEL = ROOT / "deps" / "whisper" / "models" / "ggml-medium-q5_0.bin"
FONTS_DIR = ROOT / "deps" / "fonts"
DATA_DIR = ROOT / "data" / "koubo"
OUTPUT_DIR = DATA_DIR / "output"

FPS = 30
PRESETS = {
    "克制": {"cap": 0.048, "kw": 0.085, "pop": 0.06, "punch": 0.06, "keyColor": "#FFE14D", "cta": 0.040},
    "标准": {"cap": 0.056, "kw": 0.100, "pop": 0.12, "punch": 0.14, "keyColor": "#FFE14D", "cta": 0.046},
    "热闹": {"cap": 0.062, "kw": 0.115, "pop": 0.20, "punch": 0.22, "keyColor": "#FF5A8A", "cta": 0.052},
}
LANG_MAP = {"Mandarin Chinese": "zh", "English": "en", "Japanese": "ja", "Korean": "ko",
            "French": "fr", "Spanish": "es", "Arabic": "ar"}

_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}
html,body{margin:0;padding:0;width:__W__px;height:__H__px;background:#000;overflow:hidden;
  font-family:"Microsoft YaHei","PingFang SC","Noto Sans SC","Source Han Sans SC",sans-serif;}
#stage{position:absolute;inset:0;overflow:hidden;background:#000;}
#bg{position:absolute;left:0;top:0;width:100%;height:100%;object-fit:contain;transform-origin:50% 50%;}
#cap{position:absolute;left:6%;right:6%;bottom:9%;text-align:center;font-weight:800;line-height:1.32;
  font-size:__CAP__px;color:#fff;-webkit-text-stroke:__STROKE__px rgba(0,0,0,.85);paint-order:stroke fill;
  text-shadow:0 2px 10px rgba(0,0,0,.45);letter-spacing:.01em;}
#cap span{display:inline-block;}
#cap span.on{color:__KEY__;transform:translateY(-2px) scale(1.06);}
#kw{position:absolute;left:0;right:0;top:12%;text-align:center;font-weight:900;color:__KEY__;
  font-size:__KW__px;-webkit-text-stroke:__KWSTROKE__px rgba(0,0,0,.8);paint-order:stroke fill;
  text-shadow:0 6px 20px rgba(0,0,0,.35);opacity:0;}
#lt{position:absolute;left:5%;bottom:22%;padding:1.4% 2.4%;border-radius:12px;background:rgba(15,23,42,.72);
  color:#fff;font-size:__LT__px;font-weight:600;opacity:0;backdrop-filter:blur(4px);}
#lt small{display:block;font-size:__LTS__px;font-weight:400;color:#cbd5e1;margin-top:2px;}
#cta{position:absolute;left:12%;right:12%;bottom:24%;text-align:center;font-weight:800;color:#fff;
  font-size:__CTA__px;padding:2.2% 0;border-radius:14px;background:linear-gradient(135deg,#f97316,#ea580c);
  box-shadow:0 10px 30px rgba(234,88,12,.45);opacity:0;}
#prog{position:absolute;left:0;top:0;height:4px;width:100%;background:rgba(255,255,255,.16);}
#prog i{display:block;height:100%;background:__KEY__;width:0;}
#chapter{position:absolute;left:5%;top:5.5%;display:flex;align-items:center;gap:8px;padding:4px 10px;border-radius:8px;
  background:rgba(15,23,42,.72);color:#fff;font-size:__CH__px;font-weight:600;opacity:0;}
#chapter b{color:__KEY__;font-weight:800;}
#stack{position:absolute;right:5%;top:24%;width:42%;display:flex;flex-direction:column;align-items:flex-end;gap:6px;}
#stack .chip{background:rgba(15,23,42,.72);color:#fff;font-size:__STK__px;font-weight:600;padding:3px 9px;border-radius:8px;
  border-left:3px solid __KEY__;opacity:0;transform:translateX(10px);}
#counter{position:absolute;left:0;right:0;top:32%;text-align:center;font-weight:900;color:__KEY__;
  font-size:__CNT__px;-webkit-text-stroke:__KWSTROKE__px rgba(0,0,0,.8);paint-order:stroke fill;opacity:0;}
</style></head><body>
<div id="stage"><img id="bg" alt=""></div>
<div id="cap"></div><div id="kw"></div>
<div id="lt"><span id="ltName"></span><small id="ltSub"></small></div>
<div id="cta">点个关注，下期见</div>
<div id="prog"><i></i></div>
<div id="chapter"><b id="chNo">01</b><span id="chTitle"></span></div>
<div id="stack"></div>
<div id="counter">0</div>
<script>
window.TL = __TL__;
window.renderAt = function (t, frameSrc) {
  var TL = window.TL, bg = document.getElementById('bg');
  if (bg.getAttribute('src') !== frameSrc) bg.setAttribute('src', frameSrc);
  var seg = null;
  for (var i = 0; i < TL.segs.length; i++) { if (t >= TL.segs[i].start && t < TL.segs[i].end) { seg = TL.segs[i]; break; } }
  var zoom = 1.0;
  if (TL.punch && seg && seg.punch) zoom = seg.punch;
  bg.style.transform = 'scale(' + zoom + ')';
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
  for (var m = 0; m < TL.keywords.length; m++) { var x = TL.keywords[m]; if (t >= x.start && t < x.end) { kwOn = x; break; } }
  if (kwOn) {
    var pp = Math.min(1, (t - kwOn.start) / Math.max(0.01, kwOn.pop));
    kw.textContent = kwOn.text; kw.style.opacity = '1';
    kw.style.transform = 'scale(' + (0.72 + 0.28 * pp) + ')';
  } else { kw.style.opacity = '0'; }
  var lt = document.getElementById('lt');
  if (TL.lowerThird && t >= TL.lowerThird.start && t < TL.lowerThird.end) {
    document.getElementById('ltName').textContent = TL.lowerThird.name;
    document.getElementById('ltSub').textContent = TL.lowerThird.sub || '';
    lt.style.opacity = '1';
  } else { lt.style.opacity = '0'; }
  var cta = document.getElementById('cta');
  if (TL.cta && t >= TL.cta.start) {
    var q = Math.min(1, (t - TL.cta.start) / 0.35);
    cta.style.opacity = String(q);
    cta.style.transform = 'translateY(' + (1 - q) * 14 + 'px)';
  } else { cta.style.opacity = '0'; }
  var prog = document.querySelector('#prog i');
  if (prog) prog.style.width = Math.min(100, (t / Math.max(0.01, TL.duration)) * 100) + '%';
  var ch = document.getElementById('chapter');
  if (TL.showChapter && seg && t - seg.start < 1.8) {
    document.getElementById('chNo').textContent = seg.chapter.no;
    document.getElementById('chTitle').textContent = seg.chapter.title;
    ch.style.opacity = '1';
  } else { ch.style.opacity = '0'; }
  var stack = document.getElementById('stack');
  if (TL.showStack && seg && t >= seg.start + 0.3 && t < seg.start + 2.8 && seg.stack && seg.stack.length) {
    if (stack.getAttribute('data-seg') !== seg.start) {
      stack.setAttribute('data-seg', String(seg.start));
      stack.innerHTML = seg.stack.map(function (x) { return '<div class="chip">' + x + '</div>'; }).join('');
    }
    var chips = stack.querySelectorAll('.chip');
    for (var c = 0; c < chips.length; c++) {
      var on = t >= seg.start + 0.3 + c * 0.12;
      chips[c].style.opacity = on ? '1' : '0';
      chips[c].style.transform = on ? 'translateX(0)' : 'translateX(10px)';
    }
  } else if (stack) { stack.innerHTML = ''; stack.removeAttribute('data-seg'); }
  var counter = document.getElementById('counter');
  if (TL.showCounter && seg && seg.numbers && seg.numbers.length) {
    var n = seg.numbers[0];
    if (t >= n.start && t < n.end) {
      var rr = Math.min(1, (t - n.start) / 0.7);
      counter.textContent = n.prefix + Math.round(n.value * rr).toLocaleString() + n.suffix;
      counter.style.opacity = '1';
    } else { counter.style.opacity = '0'; }
  } else { counter.style.opacity = '0'; }
};
</script></body></html>"""


def _run(cmd: List[str], timeout: int = 900) -> subprocess.CompletedProcess:
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
    return re.sub(r"[\s，。、：；！？,.:;!?\"'（）()]", "", s or "")


def _target_ratio(project: Dict[str, Any], w: int, h: int) -> str:
    opts = project.get("options") or {}
    r = opts.get("ratio")
    if r in ("9:16", "16:9"):
        return r
    if (opts.get("host") or project.get("host")) == "me":
        return "16:9"
    return "9:16" if (h >= w) else "16:9"


def _canvas(ratio: str) -> Tuple[int, int]:
    return (720, 1280) if ratio == "9:16" else (1280, 720)


def _even_timeline(project: Dict[str, Any], duration: float) -> List[Dict[str, Any]]:
    segs_in = project.get("segments") or []
    total = sum(float(s.get("seconds") or 0) for s in segs_in) or duration
    k = (duration / total) if total else 1.0
    segs: List[Dict[str, Any]] = []
    t = 0.0
    for s in segs_in:
        dur = max(0.4, float(s.get("seconds") or 0) * k)
        chars = [c for c in str(s.get("text") or "") if c.strip()]
        step = dur / max(1, len(chars))
        entries = [[c, round(t + i * step, 3)] for i, c in enumerate(chars)]
        segs.append({"start": round(t, 3), "end": round(t + dur, 3), "text": s.get("text") or "", "chars": entries})
        t += dur
    return segs


def _whisper_chars(video: Path, tmp: Path, lang: str) -> Optional[List[List[Any]]]:
    if not WHISPER.is_file() or not WHISPER_MODEL.is_file():
        return None
    wav = tmp / "audio.wav"
    r = _run([str(FFMPEG), "-y", "-loglevel", "error", "-i", str(video), "-ar", "16000", "-ac", "1", str(wav)], timeout=120)
    if r.returncode != 0 or not wav.is_file():
        return None
    out = tmp / "whisper"
    cmd = [str(WHISPER), "-m", str(WHISPER_MODEL), "-l", lang or "zh", "-oj", "-of", str(out), "-f", str(wav)]
    r = _run(cmd, timeout=600)
    jp = tmp / "whisper.json"
    if not jp.is_file():
        return None
    try:
        data = json.loads(jp.read_text(encoding="utf-8"))
    except Exception:
        return None
    chars: List[List[Any]] = []
    for tok in data.get("transcription") or []:
        text = str(tok.get("text") or "")
        if not text.strip() or text.strip().startswith("["):
            continue
        a = float((tok.get("offsets") or {}).get("from") or 0) / 1000.0
        b = float((tok.get("offsets") or {}).get("to") or 0) / 1000.0
        clean = [c for c in text if c.strip()]
        if not clean:
            continue
        step = max(0.01, (b - a) / len(clean))
        for i, c in enumerate(clean):
            chars.append([c, round(a + i * step, 3)])
    return chars or None


def _align_timeline(project: Dict[str, Any], heard: List[List[Any]], duration: float) -> List[Dict[str, Any]]:
    """把 whisper 逐字时间对齐到文稿，再按分段切回去。"""
    segs_in = project.get("segments") or []
    texts = [_clean(str(s.get("text") or "")) for s in segs_in]
    A = "".join(texts)
    B = "".join(c for c, _ in heard)
    sm = difflib.SequenceMatcher(None, A, B, autojunk=False)
    times: List[Optional[float]] = [None] * len(A)
    for bl in sm.get_matching_blocks():
        for k in range(bl.size):
            times[bl.a + k] = heard[bl.b + k][1]
    known = [i for i, v in enumerate(times) if v is not None]
    for i in range(len(times)):
        if times[i] is not None:
            continue
        lo = max([j for j in known if j < i], default=None)
        hi = min([j for j in known if j > i], default=None)
        if lo is None and hi is None:
            times[i] = 0.0
        elif lo is None:
            times[i] = times[hi]
        elif hi is None:
            times[i] = times[lo] + 0.18 * (i - lo)
        else:
            times[i] = times[lo] + (times[hi] - times[lo]) * (i - lo) / (hi - lo)
    segs: List[Dict[str, Any]] = []
    pos = 0
    for s, clean in zip(segs_in, texts):
        n = len(clean)
        chars = [[c, round(float(times[pos + i]), 3)] for i, c in enumerate(clean)]
        start = chars[0][1] if chars else pos
        end = (chars[-1][1] + 0.35) if chars else start + 1.0
        segs.append({"start": round(float(start), 3), "end": round(min(duration, float(end)), 3),
                     "text": s.get("text") or "", "chars": chars})
        pos += n
    if segs:
        segs[0]["start"] = 0.0
        segs[-1]["end"] = max(segs[-1]["end"], min(duration, segs[-1]["start"] + 0.5))
    return segs


def build_timeline(project: Dict[str, Any], duration: float, tmp: Optional[Path] = None,
                   video: Optional[Path] = None, lang: str = "zh") -> Dict[str, Any]:
    heard = None
    if tmp is not None and video is not None:
        try:
            heard = _whisper_chars(video, tmp, lang)
        except Exception:
            heard = None
    segs = _align_timeline(project, heard, duration) if heard else _even_timeline(project, duration)
    # whisper 在纯音乐/静音上会给出离谱时间（字幕整段消失），覆盖不足就回退均分
    if heard and (not segs or segs[-1]["end"] < duration * 0.6 or segs[0]["start"] > duration * 0.2):
        segs = _even_timeline(project, duration)
        heard = None

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
    for idx, seg in enumerate(segs, 1):
        seg["chapter"] = {"no": "%02d" % idx, "title": (seg.get("text") or "")[:10]}
        nums = []
        for tok in re.findall(r"([¥$]?)(d[d,.]*)s*(%|元|块|万|亿)?", seg.get("text") or ""):
            try:
                val = float(tok[1].replace(",", ""))
            except Exception:
                continue
            if val <= 0:
                continue
            hit = next((c for c in seg["chars"] if c[0] in tok[1]), None)
            if not hit:
                continue
            nums.append({"prefix": tok[0], "value": val, "suffix": tok[2] or "",
                         "start": max(seg["start"], hit[1]), "end": min(seg["end"], hit[1] + 1.6)})
        seg["numbers"] = nums
        words = [n["prefix"] + str(int(n["value"])) + n["suffix"] for n in nums][:3]
        if not words:
            words = [w for w in re.findall(r"[\u4e00-\u9fa5A-Za-z]{2,6}", seg.get("text") or "")][:2]
        seg["stack"] = words
    cta_start = max(0.0, duration - min(3.0, max(1.6, duration * 0.28)))
    return {"segs": segs, "keywords": keywords, "cta": {"start": round(cta_start, 3)},
            "lowerThird": None, "duration": duration, "timing": "whisper" if heard else "even"}


class _QuietHandler(SimpleHTTPRequestHandler):
    font_dir: Optional[Path] = None

    def log_message(self, *args):
        pass

    def translate_path(self, path):
        if path.startswith("/fonts/") and self.font_dir is not None:
            return str(self.font_dir / Path(path).name)
        return super().translate_path(path)


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
    duration = info["duration"]
    ratio = _target_ratio(project, info["w"], info["h"])
    tw, th = _canvas(ratio)
    target_aspect = tw / th
    video_aspect = info["w"] / info["h"] if info["h"] else target_aspect
    if abs(video_aspect - target_aspect) / target_aspect > 0.12:
        # 成片比例与目标差太多时按原片尺寸渲染，避免大面积黑边
        W, H = info["w"], info["h"]
    else:
        W, H = tw, th
        # 原片比画布还小就不要放大，避免糊
        if info["w"] * info["h"] < W * H:
            W, H = info["w"], info["h"]
    W -= W % 2
    H -= H % 2
    total_frames = max(1, int(math.ceil(duration * FPS)))
    p = PRESETS.get(preset) or PRESETS["标准"]
    comps = set(components or ["caption", "keyword", "cta", "lowerthird", "punch", "chapter", "stack", "counter"])
    opts = project.get("options") or {}
    lang = LANG_MAP.get(opts.get("language") or "Mandarin Chinese", "zh")

    tmp = Path(tempfile.mkdtemp(prefix="koubo-render-", dir=str(DATA_DIR)))
    frames = tmp / "frames"
    frames.mkdir()
    srv = None
    try:
        r = _run([str(FFMPEG), "-y", "-loglevel", "error", "-i", str(src),
                  "-vf", "fps=%d" % FPS, "-q:v", "2", str(frames / "f%05d.jpg")], timeout=600)
        if r.returncode != 0:
            raise RuntimeError("抽帧失败：" + (r.stderr or "")[-400:])

        timeline = build_timeline(project, duration, tmp, src, lang)
        if "keyword" not in comps:
            timeline["keywords"] = []
        if "cta" not in comps:
            timeline["cta"] = None
        if "caption" not in comps:
            timeline["segs"] = []
        if "punch" not in comps:
            timeline["punch"] = False
        timeline["showChapter"] = "chapter" in comps
        timeline["showStack"] = "stack" in comps
        timeline["showCounter"] = "counter" in comps
        host = (opts.get("host") or project.get("host") or "tank")
        names = {"tank": "白背心数字人", "me": "用户本人", "cat": "橘猫店长", "dog": "柴犬主播"}
        if "lowerthird" in comps:
            timeline["lowerThird"] = {"start": 0.4, "end": min(3.2, duration * 0.4),
                                      "name": names.get(host, "数字人"), "sub": "AI 数字人演绎"}
        # 跳切：按段交替 1.0 / (1+strength)
        if "punch" in comps:
            strength = p["punch"]
            for i, seg in enumerate(timeline["segs"]):
                seg["punch"] = 1.0 if i % 2 == 0 else (1.0 + strength)

        _QuietHandler.font_dir = FONTS_DIR
        handler = functools.partial(_QuietHandler, directory=str(frames))
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        font_css = ""
        font_family = "\"Microsoft YaHei\",\"PingFang SC\",\"Noto Sans SC\",\"Source Han Sans SC\",sans-serif"
        if (FONTS_DIR / "NotoSansSC-400.woff2").is_file():
            base = "http://127.0.0.1:%d/fonts" % port
            font_css = ("@font-face{font-family:'NotoSC';src:url('%s/NotoSansSC-400.woff2') format('woff2');font-weight:400;font-display:swap}"
                        "@font-face{font-family:'NotoSC';src:url('%s/NotoSansSC-700.woff2') format('woff2');font-weight:700;font-display:swap}") % (base, base)
            font_family = "'NotoSC'," + font_family

        html = (_HTML.replace("__W__", str(W)).replace("__H__", str(H))
                .replace("__CAP__", str(round(H * p["cap"])))
                .replace("__STROKE__", str(max(2, round(H * 0.004))))
                .replace("__KW__", str(round(H * p["kw"])))
                .replace("__KWSTROKE__", str(max(2, round(H * 0.005))))
                .replace("__CTA__", str(round(H * p["cta"])))
                .replace("__LT__", str(round(H * 0.030)))
                .replace("__LTS__", str(round(H * 0.020)))
                .replace("__CH__", str(round(H * 0.024)))
                .replace("__STK__", str(round(H * 0.026)))
                .replace("__CNT__", str(round(H * 0.11)))
                .replace("__KEY__", p["keyColor"])
                .replace("__TL__", json.dumps(timeline, ensure_ascii=False)))
        html = html.replace(
            'font-family:\"Microsoft YaHei\",\"PingFang SC\",\"Noto Sans SC\",\"Source Han Sans SC\",sans-serif;',
            "font-family:" + font_family + ";")
        if font_css:
            html = html.replace("</style>", font_css + "</style>")

        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            page.set_content(html, wait_until="load")
            for i in range(total_frames):
                t = i / FPS
                frame_url = "http://127.0.0.1:%d/f%05d.jpg" % (port, i + 1)
                page.evaluate("([t, src]) => new Promise((resolve) => { window.renderAt(t, src); "
                              "const img = document.getElementById('bg'); "
                              "if (img.complete) resolve(true); else { img.onload = () => resolve(true); img.onerror = () => resolve(true); } })",
                              [t, frame_url])
                page.screenshot(path=str(tmp / ("o%05d.jpg" % i)), type="jpeg", quality=92)
                if progress and (i % 10 == 0 or i == total_frames - 1):
                    progress(i + 1, total_frames, "渲染帧 %d/%d" % (i + 1, total_frames))
            browser.close()

        out = OUTPUT_DIR / pid / "final_packaged.mp4"
        cmd = [str(FFMPEG), "-y", "-framerate", str(FPS), "-i", str(tmp / "o%05d.jpg"),
               "-i", str(src), "-map", "0:v", "-map", "1:a",
               "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
               "-c:a", "copy", "-shortest", str(out)]
        r = _run(cmd, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError("合成失败：" + (r.stderr or "")[-600:])
        if progress:
            progress(total_frames, total_frames, "合成完成（时间轴：%s）" % timeline.get("timing"))
        return out
    finally:
        if srv is not None:
            try:
                srv.shutdown()
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def qa(project: Dict[str, Any]) -> Dict[str, Any]:
    """逐段回听：whisper 转写后与文稿比对，返回每段准确率与是否建议重拍。"""
    pid = str(project.get("id") or "")
    src = OUTPUT_DIR / pid / "final.mp4"
    if not src.is_file():
        return {"ok": False, "error": "还没有成片"}
    if not WHISPER.is_file() or not WHISPER_MODEL.is_file():
        return {"ok": False, "error": "whisper 未安装（请先在资源里下载模型）"}
    info = probe(src)
    opts = project.get("options") or {}
    lang = LANG_MAP.get(opts.get("language") or "Mandarin Chinese", "zh")
    tmp = Path(tempfile.mkdtemp(prefix="koubo-qa-", dir=str(DATA_DIR)))
    try:
        heard = _whisper_chars(src, tmp, lang)
        if not heard:
            return {"ok": False, "error": "没有识别到语音"}
        segs = _align_timeline(project, heard, info["duration"])
        results = []
        for seg, src_seg in zip(segs, project.get("segments") or []):
            spoken = "".join(c for c, t in heard if seg["start"] <= t <= seg["end"])
            a = _clean(str(src_seg.get("text") or ""))
            b = _clean(spoken)
            acc = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
            results.append({
                "id": src_seg.get("id"),
                "text": src_seg.get("text") or "",
                "heard": spoken,
                "acc": round(acc, 3),
                "warn": acc < 0.85,
                "start": seg["start"],
                "end": seg["end"],
            })
        overall = round(sum(x["acc"] for x in results) / max(1, len(results)), 3)
        return {"ok": True, "duration": info["duration"], "overall": overall, "segments": results}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
