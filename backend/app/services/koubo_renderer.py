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
CUTOUT_MODEL = ROOT / "deps" / "cutout" / "selfie_segmenter.tflite"
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
#behind{position:absolute;left:0;right:0;top:20%;text-align:center;font-size:__BEHIND__px;font-weight:900;color:#fff;
  -webkit-text-stroke:6px #000;paint-order:stroke fill;opacity:0;}
#cut{position:absolute;left:0;top:0;width:100%;height:100%;object-fit:contain;transform-origin:50% 50%;opacity:0;}
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

.fx{position:absolute;pointer-events:none;}
.fx-teaser{left:0;right:0;top:22%;text-align:center;font-size:__FX1__px;font-weight:900;color:#fff;-webkit-text-stroke:4px #000;paint-order:stroke fill;}
.fx-cold{border:6px solid #fff;inset:4%;box-shadow:0 0 0 9999px rgba(0,0,0,.12) inset;}
.fx-search{left:8%;right:8%;top:9%;background:#fff;border-radius:999px;padding:2.4% 4%;display:flex;align-items:center;gap:2%;font-size:__FX2__px;color:#0f172a;box-shadow:0 8px 24px rgba(0,0,0,.25);}
.fx-search b{color:#2563eb;font-weight:700;}
.fx-slam{left:0;right:0;top:26%;text-align:center;font-size:__FX1__px;font-weight:900;color:#fff;-webkit-text-stroke:5px #000;paint-order:stroke fill;}
.fx-stack{left:6%;top:30%;display:flex;flex-direction:column;gap:8px;font-size:__FX2__px;font-weight:800;color:#fff;}
.fx-stack span{background:rgba(15,23,42,.72);padding:3px 10px;border-left:4px solid __KEY__;}
.fx-torn{left:-4%;right:-4%;top:40%;background:#FFE14D;color:#0f172a;font-weight:900;font-size:__FX2__px;padding:1.6% 0;text-align:center;transform:rotate(-4deg);box-shadow:0 10px 24px rgba(0,0,0,.25);}
.fx-card{left:8%;right:8%;top:16%;background:#fff;border-radius:14px;padding:3%;color:#0f172a;box-shadow:0 16px 40px rgba(0,0,0,.35);}
.fx-card h4{margin:0 0 6px;font-size:__FX2__px;}
.fx-card p{margin:0;font-size:__FX3__px;color:#475569;line-height:1.5;}
.fx-doc{left:10%;right:10%;top:14%;bottom:34%;background:#fff;border-radius:8px;border:2px solid #cbd5e1;padding:4%;}
.fx-doc i{display:block;height:8px;background:#e2e8f0;border-radius:4px;margin:6px 0;}
.fx-source{right:5%;bottom:20%;background:rgba(15,23,42,.78);color:#cbd5e1;font-size:__FX3__px;padding:4px 10px;border-radius:6px;}
.fx-zoom{left:12%;right:12%;top:18%;height:30%;border:3px solid #fff;border-radius:10px;box-shadow:0 0 0 9999px rgba(0,0,0,.18);}
.fx-glass{left:8%;right:8%;top:14%;background:rgba(255,255,255,.16);backdrop-filter:blur(10px);border:1px solid rgba(255,255,255,.35);border-radius:16px;padding:3%;color:#fff;font-size:__FX2__px;font-weight:600;}
.fx-spec{left:8%;right:8%;top:16%;background:rgba(15,23,42,.78);border-radius:12px;padding:3%;color:#e2e8f0;font-size:__FX3__px;}
.fx-spec div{display:flex;justify-content:space-between;border-bottom:1px solid rgba(255,255,255,.12);padding:3px 0;}
.fx-line{left:8%;right:8%;top:20%;height:20%;}
.fx-bars{left:10%;right:10%;top:18%;background:rgba(15,23,42,.72);border-radius:12px;padding:3%;}
.fx-bars .row{display:flex;align-items:center;gap:8px;margin:4px 0;color:#e2e8f0;font-size:__FX3__px;}
.fx-bars .bar{flex:1;height:10px;background:rgba(255,255,255,.18);border-radius:999px;overflow:hidden;}
.fx-bars .bar i{display:block;height:100%;background:__KEY__;}
.fx-review{left:8%;right:8%;top:16%;background:#fff;border-radius:14px;padding:3%;color:#0f172a;box-shadow:0 16px 40px rgba(0,0,0,.3);}
.fx-review .stars{color:#f59e0b;font-size:__FX2__px;}
.fx-archive{left:10%;right:10%;top:16%;height:24%;}
.fx-archive i{position:absolute;inset:0;background:#fff;border-radius:12px;box-shadow:0 10px 24px rgba(0,0,0,.2);}
.fx-map{left:10%;right:10%;top:14%;bottom:34%;}
.fx-compare{left:0;right:0;top:14%;height:26%;background:rgba(15,23,42,.35);border-top:3px solid #fff;border-bottom:3px solid #fff;}
.fx-collage i{position:absolute;background:#fff;border-radius:6px;box-shadow:0 8px 20px rgba(0,0,0,.25);}
.fx-hand{left:8%;right:8%;top:30%;height:12%;}
.fx-callout{left:6%;top:20%;background:#fff;color:#0f172a;font-size:__FX3__px;font-weight:700;padding:4px 10px;border-radius:999px;box-shadow:0 6px 16px rgba(0,0,0,.25);}
.fx-leader{left:6%;top:24%;color:#fff;font-size:__FX3__px;font-weight:700;border-left:3px solid #fff;padding-left:6px;}
.fx-typed{left:6%;top:18%;background:rgba(15,23,42,.72);color:#fff;font-size:__FX3__px;padding:3px 8px;border-radius:6px;}
.fx-freeze{inset:0;border:8px solid #fff;box-shadow:0 0 0 9999px rgba(0,0,0,.08) inset;}
.fx-tilt{left:-10%;right:-10%;top:26%;height:14%;background:__KEY__;transform:rotate(-7deg);opacity:.92;}
.fx-aside{right:5%;top:30%;max-width:34%;background:#fff;color:#0f172a;font-size:__FX3__px;padding:6px 10px;border-radius:10px;box-shadow:0 8px 20px rgba(0,0,0,.25);}
.fx-danmaku{left:0;right:0;top:10%;height:40%;overflow:hidden;color:#fff;font-size:__FX3__px;font-weight:700;}
.fx-danmaku span{position:absolute;white-space:nowrap;background:rgba(0,0,0,.35);padding:1px 8px;border-radius:999px;}
.fx-emoji{left:50%;top:40%;transform:translate(-50%,-50%);font-size:__FXEMOJI__px;}
.fx-meme{left:14%;right:14%;top:18%;background:#000;border:6px solid #fff;border-radius:10px;padding:6% 0;text-align:center;font-size:__FXEMOJI__px;}
.fx-poll{left:10%;right:10%;top:18%;background:#fff;border-radius:12px;padding:3%;color:#0f172a;font-size:__FX3__px;}
.fx-poll .opt{display:flex;align-items:center;gap:6px;margin:4px 0;}
.fx-poll .opt i{height:8px;background:#3b82f6;border-radius:999px;display:block;}
.fx-glitch{inset:0;mix-blend-mode:screen;background:linear-gradient(90deg,rgba(255,0,80,.25),rgba(0,255,255,.2));}
.fx-float{inset:0;}
.fx-float span{position:absolute;font-size:__FX3__px;opacity:.9;}
.fx-chapters{left:0;right:0;top:0;height:4px;background:rgba(255,255,255,.15);}
.fx-chapters i{position:absolute;top:0;height:100%;background:__KEY__;}
.fx-tracker{left:6%;right:6%;bottom:4%;height:6px;background:rgba(255,255,255,.18);border-radius:999px;}
.fx-tracker i{display:block;height:100%;background:__KEY__;border-radius:999px;}
.fx-recap{left:8%;right:8%;top:16%;background:rgba(15,23,42,.82);border-radius:12px;padding:3%;color:#e2e8f0;font-size:__FX3__px;}
.fx-recap div{margin:3px 0;}
.fx-count{left:0;right:0;top:34%;text-align:center;font-size:__FXCOUNT__px;font-weight:900;color:#fff;-webkit-text-stroke:6px #000;paint-order:stroke fill;}
.fx-ghost{left:0;right:0;top:30%;text-align:center;font-size:__FXGHOST__px;font-weight:900;color:rgba(255,255,255,.18);}

</style></head><body>
<div id="stage"><img id="bg" alt=""></div>
<div id="behind"></div>
<img id="cut" alt="">
<div id="cap"></div><div id="kw"></div>
<div id="lt"><span id="ltName"></span><small id="ltSub"></small></div>
<div id="cta">点个关注，下期见</div>
<div id="prog"><i></i></div>
<div id="chapter"><b id="chNo">01</b><span id="chTitle"></span></div>
<div id="stack"></div>
<div id="counter">0</div>
<div id="fx"></div>
<script>
window.TL = __TL__;

var FX = {
  TeaserColdOpen: function (s, t, p) { return p < 2.2 ? '<div class="fx fx-teaser">3 秒后告诉你</div>' : ''; },
  ColdOpenFreeze: function (s, t, p) { return p < 1.6 ? '<div class="fx fx-cold"></div>' : ''; },
  SearchTyping: function (s, t, p) { var q = s.text.slice(0, Math.max(1, Math.floor(p * 8))); return p < 3 ? '<div class="fx fx-search">搜索 <b>' + q + '</b></div>' : ''; },
  SlamText: function (s, t, p) { var sc = p < 0.35 ? (1.6 - p * 1.2) : 1; return p < 1.4 ? '<div class="fx fx-slam" style="transform:scale(' + sc + ')">' + s.text.slice(0, 8) + '</div>' : ''; },
  WordStack: function (s, t, p) { var w = (s.stack || []).slice(0, 3); return p < 2.6 ? '<div class="fx fx-stack">' + w.map(function (x, i) { return '<span style="opacity:' + (p > i * 0.25 ? 1 : 0) + '">' + x + '</span>'; }).join('') + '</div>' : ''; },
  TornStrip: function (s, t, p) { return p < 2.4 ? '<div class="fx fx-torn">' + s.text.slice(0, 10) + '</div>' : ''; },
  TextBehind: function (s, t, p) { return ''; },
  EvidenceCard: function (s, t, p) { return p < 3.2 ? '<div class="fx fx-card"><h4>' + (s.chapter ? s.chapter.title : '') + '</h4><p>' + s.text.slice(0, 30) + '</p></div>' : ''; },
  DocFocus: function (s, t, p) { return p < 3 ? '<div class="fx fx-doc"><i></i><i></i><i></i><i></i><i></i></div>' : ''; },
  SourceDoc: function (s, t, p) { return p < 3 ? '<div class="fx fx-source">来源：公开资料</div>' : ''; },
  ZoomFocus: function (s, t, p) { return p < 2.6 ? '<div class="fx fx-zoom"></div>' : ''; },
  Odometer: function (s, t, p) { var n = (s.numbers || [])[0]; if (!n) return ''; var v = Math.round(n.value * Math.min(1, p / 0.8)); return p < 2.4 ? '<div class="fx fx-card"><h4>' + n.prefix + v.toLocaleString() + n.suffix + '</h4><p>数字滚动</p></div>' : ''; },
  GlassPanel: function (s, t, p) { return p < 3 ? '<div class="fx fx-glass">' + s.text.slice(0, 16) + '</div>' : ''; },
  SpecCard: function (s, t, p) { var n = (s.numbers || [])[0]; var rows = '<div><span>核心参数</span><b>' + (n ? n.prefix + n.value + n.suffix : '—') + '</b></div><div><span>适用场景</span><b>口播</b></div>'; return p < 3 ? '<div class="fx fx-spec">' + rows + '</div>' : ''; },
  LineDraw: function (s, t, p) { var d = Math.min(1, p / 0.8); return p < 2.8 ? '<svg class="fx fx-line" viewBox="0 0 100 20"><path d="M0,16 Q25,2 50,12 T100,6" fill="none" stroke="' + TL.keyColor + '" stroke-width="2" stroke-dasharray="120" stroke-dashoffset="' + (120 - 120 * d) + '"/></svg>' : ''; },
  BarCompare: function (s, t, p) { var ns = s.numbers || []; var a = ns[0] ? ns[0].value : 70, b = ns[1] ? ns[1].value : 100; var m = Math.max(a, b, 1); return p < 3 ? '<div class="fx fx-bars"><div class="row">A<div class="bar"><i style="width:' + (a / m * 100) + '%"></i></div></div><div class="row">B<div class="bar"><i style="width:' + (b / m * 100) + '%"></i></div></div></div>' : ''; },
  ReviewCards: function (s, t, p) { return p < 3.2 ? '<div class="fx fx-review"><div class="stars">★★★★★</div><p style="margin:4px 0 0;font-size:' + 'inherit' + '">' + s.text.slice(0, 24) + '</p></div>' : ''; },
  ArchiveCards: function (s, t, p) { return p < 3 ? '<div class="fx fx-archive"><i style="transform:rotate(-4deg)"></i><i style="transform:rotate(2deg) translateY(6px)"></i><i></i></div>' : ''; },
  MapRoute: function (s, t, p) { var d = Math.min(1, p / 1.2); return p < 3.2 ? '<svg class="fx fx-map" viewBox="0 0 100 60"><path d="M10,50 Q30,10 50,30 T90,12" fill="none" stroke="' + TL.keyColor + '" stroke-width="2" stroke-dasharray="120" stroke-dashoffset="' + (120 - 120 * d) + '"/><circle cx="10" cy="50" r="3" fill="#fff"/><circle cx="90" cy="12" r="3" fill="#fff"/></svg>' : ''; },
  CompareWipe: function (s, t, p) { var w = Math.min(100, p / 0.8 * 100); return p < 2.6 ? '<div class="fx fx-compare" style="clip-path:inset(0 ' + (100 - w) + '% 0 0)"></div>' : ''; },
  PaperCollage: function (s, t, p) { return p < 3 ? '<div class="fx fx-collage" style="inset:12% 8% 40% 8%"><i style="left:0;top:0;width:45%;height:55%;transform:rotate(-6deg)"></i><i style="right:0;top:10%;width:48%;height:50%;transform:rotate(4deg)"></i><i style="left:20%;bottom:0;width:55%;height:35%;transform:rotate(-2deg)"></i></div>' : ''; },
  HandDraw: function (s, t, p) { var d = Math.min(1, p / 0.7); return p < 2.4 ? '<svg class="fx fx-hand" viewBox="0 0 100 20"><path d="M4,14 C20,4 40,18 60,10 S90,6 96,12" fill="none" stroke="' + TL.keyColor + '" stroke-width="3" stroke-linecap="round" stroke-dasharray="100" stroke-dashoffset="' + (100 - 100 * d) + '"/></svg>' : ''; },
  CalloutPill: function (s, t, p) { return p < 3 ? '<div class="fx fx-callout">' + s.text.slice(0, 6) + '</div>' : ''; },
  LeaderLabel: function (s, t, p) { return p < 3 ? '<div class="fx fx-leader">' + s.text.slice(0, 8) + '</div>' : ''; },
  TypedNameTag: function (s, t, p) { var n = Math.floor(p * 10); return p < 3 ? '<div class="fx fx-typed">' + s.text.slice(0, n) + '_</div>' : ''; },
  NamePlate: function (s, t, p) { return p < 3 ? '<div class="fx fx-typed" style="top:auto;bottom:22%;">主播 · 数字人</div>' : ''; },
  SourceTag: function (s, t, p) { return p < 3 ? '<div class="fx fx-source">来源：口播稿</div>' : ''; },
  FreezeFrame: function (s, t, p) { return p < 1.2 ? '<div class="fx fx-freeze"></div>' : ''; },
  TiltLine: function (s, t, p) { return p < 2.6 ? '<div class="fx fx-tilt"></div>' : ''; },
  Aside: function (s, t, p) { return p < 3 ? '<div class="fx fx-aside">' + s.text.slice(0, 14) + '</div>' : ''; },
  Danmaku: function (s, t, p) { var items = ['真的假的？', '学到了', '这也行？']; return p < 4 ? '<div class="fx fx-danmaku">' + items.map(function (x, i) { return '<span style="top:' + (i * 22) + '%;left:' + (100 - ((p * 40 + i * 25) % 140)) + '%">' + x + '</span>'; }).join('') + '</div>' : ''; },
  ReactionPop: function (s, t, p) { return p < 1.8 ? '<div class="fx fx-emoji">🔥</div>' : ''; },
  MemeWindow: function (s, t, p) { return p < 2.2 ? '<div class="fx fx-meme">😂</div>' : ''; },
  PollPop: function (s, t, p) { return p < 3.2 ? '<div class="fx fx-poll"><b>你觉得呢？</b><div class="opt">A<i style="width:62%"></i></div><div class="opt">B<i style="width:38%"></i></div></div>' : ''; },
  GlitchInterstitial: function (s, t, p) { return p < 0.4 ? '<div class="fx fx-glitch"></div>' : ''; },
  HostFloaters: function (s, t, p) { var e = ['✨', '💡', '📈']; return p < 3 ? '<div class="fx fx-float">' + e.map(function (x, i) { return '<span style="left:' + (8 + i * 28) + '%;top:' + (18 + ((p * 20 + i * 15) % 40)) + '%">' + x + '</span>'; }).join('') + '</div>' : ''; },
  ProgressChapters: function (s, t, p) { var n = TL.segs.length; var w = 100 / Math.max(1, n); return '<div class="fx fx-chapters">' + TL.segs.map(function (x, i) { return '<i style="left:' + (i * w) + '%;width:' + Math.max(1, w - 1) + '%;opacity:' + (s.index === i ? 1 : 0.35) + '"></i>'; }).join('') + '</div>'; },
  ProgressTracker: function (s, t, p) { return '<div class="fx fx-tracker"><i style="width:' + Math.min(100, t / Math.max(0.01, TL.duration) * 100) + '%"></i></div>'; },
  RecapBoard: function (s, t, p) { var last = TL.segs[TL.segs.length - 1]; if (!last || s.index !== TL.segs.length - 1) return ''; return p < 3.5 ? '<div class="fx fx-recap">✓ ' + TL.segs.slice(0, 3).map(function (x) { return x.chapter.title.slice(0, 6); }).join('<br>✓ ') + '</div>' : ''; },
  Countdown: function (s, t, p) { if (TL.segs[0] && s.index !== 0) return ''; var n = 3 - Math.floor(p); return (n >= 1 && n <= 3) ? '<div class="fx fx-count">' + n + '</div>' : ''; },
  GhostWord: function (s, t, p) { return p < 2.6 ? '<div class="fx fx-ghost">' + (s.chapter ? s.chapter.title.slice(0, 4) : '') + '</div>' : ''; }
};

window.renderAt = function (t, frameSrc, cutSrc) {
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
  var cut = document.getElementById('cut');
  if (TL.textBehind && seg && cutSrc) {
    var behind = document.getElementById('behind');
    behind.textContent = (seg.chapter ? seg.chapter.title : '');
    behind.style.opacity = (t - seg.start < 2.6) ? '1' : '0';
    if (cut.getAttribute('src') !== cutSrc) cut.setAttribute('src', cutSrc);
    cut.style.opacity = '1';
    cut.style.transform = 'scale(' + (zoom || 1) + ')';
  } else if (cut) {
    cut.style.opacity = '0';
    var bh = document.getElementById('behind'); if (bh) bh.style.opacity = '0';
  }
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
  var fx = document.getElementById('fx');
  if (fx) {
    var parts = [];
    if (seg && TL.comps) for (var q = 0; q < TL.comps.length; q++) {
      var fn = FX[TL.comps[q]];
      if (fn) { try { var out = fn(seg, t, t - seg.start); if (out) parts.push(out); } catch (e) {} }
    }
    fx.innerHTML = parts.join('');
  }
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
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


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
        seg["index"] = idx - 1
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

    cut_dir: Optional[Path] = None

    def translate_path(self, path):
        if path.startswith("/fonts/") and self.font_dir is not None:
            return str(self.font_dir / Path(path).name)
        if path.startswith("/cut/") and self.cut_dir is not None:
            return str(self.cut_dir / Path(path).name)
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

        cut_dir = None
        if "TextBehind" in comps and CUTOUT_MODEL.is_file():
            try:
                import mediapipe as mp
                from mediapipe.tasks import python as mpp
                from mediapipe.tasks.python import vision
                import numpy as np
                from PIL import Image

                job_msg = "抠像（TextBehind）"
                cut_dir = tmp / "cut"
                cut_dir.mkdir()
                base_opt = mpp.BaseOptions(model_asset_path=str(CUTOUT_MODEL))
                mp_opts = vision.ImageSegmenterOptions(base_options=base_opt,
                                                       output_confidence_masks=True,
                                                       running_mode=vision.RunningMode.IMAGE)
                seg = vision.ImageSegmenter.create_from_options(mp_opts)
                src_frames = sorted(frames.glob("f*.jpg"))
                for ci, f in enumerate(src_frames):
                    im = Image.open(f).convert("RGB")
                    res = seg.segment(mp.Image.create_from_file(str(f)))
                    mask = res.confidence_masks[0].numpy_view()
                    mask = np.clip(mask.reshape(im.height, im.width), 0, 1)
                    rgba = np.dstack([np.array(im), (mask * 255).astype(np.uint8)])
                    Image.fromarray(rgba).save(cut_dir / (f.stem + ".png"))
                    if progress and (ci % 20 == 0 or ci == len(src_frames) - 1):
                        progress(ci + 1, len(src_frames), "抠像 %d/%d" % (ci + 1, len(src_frames)))
                seg.close()
            except Exception:
                cut_dir = None

        timeline = build_timeline(project, duration, tmp, src, lang)
        timeline["textBehind"] = bool(cut_dir) and ("TextBehind" in comps)
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
        base_ids = {"caption", "cta", "lowerthird", "punch", "chapter", "stack", "counter", "keyword"}
        timeline["comps"] = [c for c in comps if c not in base_ids]
        timeline["keyColor"] = p["keyColor"]
        cut_ok = ("TextBehind" in comps) and CUTOUT_MODEL.is_file()
        timeline["textBehind"] = bool(cut_ok)
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
        _QuietHandler.cut_dir = cut_dir
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
                .replace("__FX1__", str(round(H * 0.10)))
                .replace("__FX2__", str(round(H * 0.036)))
                .replace("__FX3__", str(round(H * 0.024)))
                .replace("__FXEMOJI__", str(round(H * 0.16)))
                .replace("__FXCOUNT__", str(round(H * 0.22)))
                .replace("__FXGHOST__", str(round(H * 0.18)))
                .replace("__BEHIND__", str(round(H * 0.14)))
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
                cut_url = ("http://127.0.0.1:%d/cut/f%05d.png" % (port, i + 1)) if cut_dir else ""
                page.evaluate("([t, src, cut]) => new Promise((resolve) => { window.renderAt(t, src, cut); "
                              "const img = document.getElementById('bg'); "
                              "if (img.complete) resolve(true); else { img.onload = () => resolve(true); img.onerror = () => resolve(true); } })",
                              [t, frame_url, cut_url])
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
