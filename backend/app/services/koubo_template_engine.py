"""数字人 3.0 模板引擎 v1：TemplateSpec + 透明叠层 + ffmpeg 合成。"""
from __future__ import annotations

import functools
import json
import math
import re
import shutil
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import koubo_renderer as kr

ROOT = Path(__file__).resolve().parents[3]
SPEC_DIR = ROOT / "skills" / "digital_human_koubo" / "templates" / "specs"
OVERLAY_HTML = Path(__file__).with_name("koubo_template_overlay.html")
OUTPUT_DIR = kr.OUTPUT_DIR
DATA_DIR = kr.DATA_DIR
FONTS_DIR = kr.FONTS_DIR
FFMPEG = kr.FFMPEG
FPS = 30


class _FontHandler(SimpleHTTPRequestHandler):
    font_dir: Optional[Path] = None
    def log_message(self, *args):
        pass
    def translate_path(self, path):
        if path.startswith('/fonts/') and self.font_dir is not None:
            return str(self.font_dir / Path(path).name)
        return super().translate_path(path)

    def copyfile(self, source, outputfile):
        try:
            return super().copyfile(source, outputfile)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return None


def load_templates() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not SPEC_DIR.is_dir():
        return out
    for path in sorted(SPEC_DIR.glob('*.json')):
        try:
            spec = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            continue
        tid = str(spec.get('id') or path.stem).strip()
        if tid:
            out[tid] = spec
    return out


def get_template(template_id: str) -> Optional[Dict[str, Any]]:
    return load_templates().get(template_id)


def public_templates() -> List[Dict[str, Any]]:
    out = []
    for spec in sorted(load_templates().values(), key=lambda x: (not bool(x.get('default')), str(x.get('id') or ''))):
        style = spec.get('style') or {}
        out.append({
            'id': spec.get('id'),
            'name': spec.get('name'),
            'description': spec.get('description'),
            'tags': spec.get('tags') or [],
            'ratio': spec.get('ratio') or 'source',
            'variant': spec.get('variant') or 'kinetic',
            'colors': [style.get('accent'), style.get('accent2'), style.get('accent3')],
            'default': bool(spec.get('default')),
        })
    return out


def _even(value: int) -> int:
    return max(2, int(value) - (int(value) % 2))


def _canvas(spec: Dict[str, Any], info: Dict[str, Any]) -> Tuple[int, int]:
    ratio = str(spec.get('ratio') or 'source')
    if ratio == '9:16':
        return 720, 1280
    if ratio == '16:9':
        return 1280, 720
    return _even(info.get('w') or 720), _even(info.get('h') or 1280)


def _title_for(project: Dict[str, Any], spec: Dict[str, Any]) -> str:
    opts = project.get('options') or {}
    raw = str(opts.get('title') or '').strip()
    if not raw:
        for seg in project.get('segments') or []:
            raw = str(seg.get('text') or '').strip()
            if raw:
                break
    raw = raw or str(project.get('name') or '数字人 3.0')
    raw = re.sub(r'[\s，。、：；！？,.:;!?“”"\'（）()]', '', raw)
    limit = int((spec.get('rules') or {}).get('titleChars') or 14)
    return raw[:limit] or '数字人 3.0'


def _ensure_keywords(timeline: Dict[str, Any]) -> None:
    if timeline.get('keywords'):
        return
    out = []
    for idx, seg in enumerate((timeline.get('segs') or [])[:3]):
        text = re.sub(r'[\s，。、：；！？,.:;!?“”"\'（）()]', '', str(seg.get('text') or ''))
        if text:
            start_offset = 2.2 if idx == 0 else 0.45
            start = float(seg.get('start') or 0) + start_offset
            end = min(float(seg.get('end') or 0), start + 1.8)
            if end > start:
                out.append({'text': text[:4], 'start': start, 'end': end, 'pop': 0.2})
    timeline['keywords'] = out


def render(project: Dict[str, Any], template_id: str = 'premium_kinetic_v1',
           progress: Optional[Callable[[int, int, str], None]] = None) -> Path:
    spec = get_template(template_id) or get_template('premium_kinetic_v1')
    if not spec:
        raise RuntimeError('模板不存在')
    pid = str(project.get('id') or '')
    src = OUTPUT_DIR / pid / 'final.mp4'
    if not src.is_file():
        raise RuntimeError('还没有生成成片，无法套模板')
    info = kr.probe(src)
    duration = float(info.get('duration') or 0)
    if duration <= 0:
        raise RuntimeError('底片时长无效')
    W, H = _canvas(spec, info)
    fps = int(spec.get('fps') or FPS)
    total_frames = max(1, int(math.ceil(duration * fps)))
    opts = project.get('options') or {}
    lang = kr.LANG_MAP.get(opts.get('language') or 'Mandarin Chinese', 'zh')
    tmp = Path(tempfile.mkdtemp(prefix='koubo-template-', dir=str(DATA_DIR)))
    srv = None
    try:
        timeline = kr.build_timeline(project, duration, tmp, src, lang)
        _ensure_keywords(timeline)
        style = spec.get('style') or {}
        copy = spec.get('copy') or {}
        title = _title_for(project, spec)
        _FontHandler.font_dir = FONTS_DIR
        srv = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(_FontHandler, directory=str(FONTS_DIR)))
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        html = (OVERLAY_HTML.read_text(encoding='utf-8').lstrip('\ufeff')
                .replace('__FONT_PORT__', str(port))
                .replace('__W__', str(W)).replace('__H__', str(H))
                .replace('__ACCENT__', str(style.get('accent') or '#FFD84D'))
                .replace('__ACCENT2__', str(style.get('accent2') or '#FF4D8D'))
                .replace('__ACCENT3__', str(style.get('accent3') or '#4DE1FF'))
                .replace('__INK__', str(style.get('ink') or '#080B14'))
                .replace('__PAPER__', str(style.get('paper') or '#FFFFFF'))
                .replace('__VARIANT__', str(spec.get('variant') or 'kinetic'))
                .replace('__BADGE__', str(copy.get('badge') or 'AI 数字人'))
                .replace('__LOWER_TITLE__', str(copy.get('lowerTitle') or '数字人 3.0'))
                .replace('__LOWER_SUB__', str(copy.get('lowerSub') or 'AI 口播'))
                .replace('__CTA__', str(copy.get('cta') or '关注我，继续聊'))
                .replace('__TITLE_SIZE__', str(round(H * float(style.get('titleSize') or .07))))
                .replace('__CAPTION_SIZE__', str(round(H * float(style.get('captionSize') or .05))))
                .replace('__KEYWORD_SIZE__', str(round(H * float(style.get('keywordSize') or .12))))
                .replace('__BADGE_SIZE__', str(max(10, round(H * .020))))
                .replace('__SMALL_SIZE__', str(max(10, round(H * .019))))
                .replace('__TINY_SIZE__', str(max(9, round(H * .015))))
                .replace('__CTA_SIZE__', str(max(12, round(H * .026))))
                .replace('__KW_STROKE__', str(max(3, round(H * .008))))
                .replace('__TL__', json.dumps(timeline, ensure_ascii=False))
                .replace('__TITLE__', json.dumps(title, ensure_ascii=False))
                .replace('__DURATION__', str(round(duration, 4)))
                .replace('__FPS__', str(fps)))
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': W, 'height': H}, device_scale_factor=1)
            page.set_content(html, wait_until='networkidle')
            try:
                page.evaluate('() => document.fonts.ready')
            except Exception:
                pass
            for i in range(total_frames):
                t = i / float(fps)
                page.evaluate('(t) => window.renderAt(t)', t)
                page.screenshot(path=str(tmp / ('o%05d.png' % (i + 1))), omit_background=True, type='png')
                if progress and (i % 10 == 0 or i == total_frames - 1):
                    progress(i + 1, total_frames, '模板渲染 %d/%d' % (i + 1, total_frames))
            browser.close()
        out = OUTPUT_DIR / pid / 'final_packaged.mp4'
        fg = ('[0:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,setsar=1[bg];[bg][1:v]overlay=0:0:format=auto[v]') % (W, H, W, H)
        cmd = [str(FFMPEG), '-y', '-loglevel', 'error', '-i', str(src),
               '-framerate', str(fps), '-start_number', '1', '-i', str(tmp / 'o%05d.png'),
               '-filter_complex', fg, '-map', '[v]', '-map', '0:a?',
               '-c:v', 'libx264', '-crf', '18', '-preset', 'medium', '-pix_fmt', 'yuv420p',
               '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', str(out)]
        r = kr._run(cmd, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError('模板合成失败：' + (r.stderr or '')[-700:])
        if progress:
            progress(total_frames, total_frames, '模板合成完成')
        return out
    finally:
        if srv is not None:
            try:
                srv.shutdown()
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
