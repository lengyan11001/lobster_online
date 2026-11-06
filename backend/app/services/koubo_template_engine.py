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
BROLL_DIR = DATA_DIR / 'broll'
BROLL_VIDEO_EXT = {'.mp4', '.mov', '.webm', '.m4v'}
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


_LAYOUT_LABELS = {
    'kinetic': '大字快节奏', 'clean': '干净字幕', 'commercial': '带货转化', 'news': '新闻播报',
    'tutorial': '步骤教程', 'review': '测评对比', 'story': '故事叙事', 'interview': '访谈纪实',
    'matrix': '矩阵批量', 'knowledge': '知识卡片', 'minimal': '极简留白', 'quote': '金句大字',
    'banner': '横幅强调', 'magazine': '杂志排版',
}

_CATEGORY_SCENES = {
    '口播观点': ['观点输出', '热点锐评', '知识分享'],
    '访谈纪实': ['人物访谈', '创业故事', '经历复盘'],
    '带货转化': ['直播切片', '商品种草', '促销转化'],
    '知识教程': ['教程讲解', '步骤拆解', '干货清单'],
    '新闻资讯': ['快讯播报', '行业解读', '政策说明'],
    '测评对比': ['开箱测评', '参数对比', '选购建议'],
    '情绪剧情': ['剧情反转', '情绪表达', '故事讲述'],
    '矩阵批量': ['账号矩阵', '日更口播', '批量起号'],
}

_RATIO_LABELS = {'source': '跟随底片', '9:16': '竖屏 9:16', '16:9': '横屏 16:9'}
_MOTION_LABELS = {'push': '镜头缓推', 'pull': '镜头缓拉', 'none': '固定机位'}


def _public(spec: Dict[str, Any]) -> Dict[str, Any]:
    style = spec.get('style') or {}
    return {
        'id': spec.get('id'),
        'name': spec.get('name'),
        'description': spec.get('description'),
        'category': spec.get('category') or '其他',
        'featured': bool(spec.get('featured')),
        'order': int(spec.get('order') or 999),
        'tags': spec.get('tags') or [],
        'ratio': spec.get('ratio') or 'source',
        'variant': spec.get('variant') or 'kinetic',
        'colors': [style.get('accent'), style.get('accent2'), style.get('accent3')],
        'cover': spec.get('cover') or '',
        'sample_video_url': spec.get('sample_video_url') or '',
        'default': bool(spec.get('default')),
    }


def public_templates() -> List[Dict[str, Any]]:
    ordered = sorted(load_templates().values(), key=lambda x: (int(x.get('order') or 999), str(x.get('id') or '')))
    return [_public(spec) for spec in ordered]


def template_detail(template_id: str) -> Optional[Dict[str, Any]]:
    spec = load_templates().get(str(template_id or '').strip())
    if not spec:
        return None
    data = _public(spec)
    category = str(data.get('category') or '')
    motion = spec.get('motion') or {}
    cut = str((spec.get('transitions') or {}).get('cut') or 'none')
    features = []
    motion_label = _MOTION_LABELS.get(str(motion.get('type') or 'none'), '固定机位')
    if motion.get('type'):
        features.append(motion_label)
    if cut == 'flash':
        features.append('闪白转场')
    if (spec.get('chapter') or {}).get('show'):
        features.append('章节进度')
    if float((spec.get('rules') or {}).get('safeArea') or 0) > 0:
        features.append('字幕安全区')
    data.update({
        'layout': spec.get('layout') or data['variant'],
        'layout_label': _LAYOUT_LABELS.get(str(spec.get('layout') or data['variant']), '标准排版'),
        'motion': motion,
        'motion_label': motion_label,
        'transition': cut,
        'transition_label': '闪白转场' if cut == 'flash' else '硬切',
        'ratio_label': _RATIO_LABELS.get(str(data.get('ratio')), str(data.get('ratio'))),
        'scenes': _CATEGORY_SCENES.get(category) or ['通用口播'],
        'features': features,
        'copy': spec.get('copy') or {},
        'rules': spec.get('rules') or {},
    })
    return data


def _even(value: int) -> int:
    return max(2, int(value) - (int(value) % 2))


def _canvas(spec: Dict[str, Any], info: Dict[str, Any]) -> Tuple[int, int]:
    ratio = str(spec.get('ratio') or 'source')
    if ratio == '9:16':
        return 720, 1280
    if ratio == '16:9':
        return 1280, 720
    return _even(info.get('w') or 720), _even(info.get('h') or 1280)


def _title_for(project: Dict[str, Any], spec: Dict[str, Any], title_override: str = '') -> str:
    opts = project.get('options') or {}
    raw = str(title_override or opts.get('title') or '').strip()
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
           progress: Optional[Callable[[int, int, str], None]] = None,
           title_override: str = '', quality: str = 'final',
           broll: Optional[Dict[str, Any]] = None) -> Path:
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
    preview_mode = (quality or '').strip().lower() == 'preview'
    if preview_mode:
        target_h = 480
        H = target_h
        W = _even(int(round((info.get('w') or 720) * target_h / max(1, info.get('h') or 1280))))
    fps = 12 if preview_mode else int(spec.get('fps') or FPS)
    total_frames = max(1, int(math.ceil(duration * fps)))
    qa_quality = 'preview' if preview_mode else 'final'
    layout_checks: List[Dict[str, Any]] = []
    layout_warnings: List[str] = []
    qa_targets = [duration * frac for frac in (0.08, 0.35, 0.65, 0.90)]
    qa_done: set = set()
    safe_ratio = float((spec.get('rules') or {}).get('safeArea') or 0.06)
    margin_x = W * safe_ratio
    margin_y = H * safe_ratio
    opts = project.get('options') or {}
    lang = kr.LANG_MAP.get(opts.get('language') or 'Mandarin Chinese', 'zh')
    tmp = Path(tempfile.mkdtemp(prefix='koubo-template-', dir=str(DATA_DIR)))
    srv = None
    try:
        timeline = kr.build_timeline(project, duration, tmp, src, lang)
        _ensure_keywords(timeline)
        style = spec.get('style') or {}
        copy = spec.get('copy') or {}
        title = _title_for(project, spec, title_override)
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
                .replace('__LAYOUT__', str(spec.get('layout') or 'kinetic'))
                .replace('__CH_SHOW__', 'true' if (spec.get('chapter') or {}).get('show') else 'false')
                .replace('__CH_NO__', json.dumps(str((spec.get('chapter') or {}).get('no') or ''), ensure_ascii=False))
                .replace('__CH_TITLE__', json.dumps(str((spec.get('chapter') or {}).get('title') or ''), ensure_ascii=False))
                .replace('__FLASH__', 'true' if (spec.get('transitions') or {}).get('cut') == 'flash' else 'false')
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
            qa_js = "() => { const ids=['title','caption','keyword','cta','lowerThird']; const out={}; for (const id of ids) { const el=document.getElementById(id); if(!el) continue; const r=el.getBoundingClientRect(); const cs=getComputedStyle(el); out[id]={left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height,opacity:parseFloat(cs.opacity||'1'),scrollW:el.scrollWidth,clientW:el.clientWidth,scrollH:el.scrollHeight,clientH:el.clientHeight}; } return out; }"
            for i in range(total_frames):
                t = i / float(fps)
                page.evaluate('(t) => window.renderAt(t)', t)
                for qi, qt in enumerate(qa_targets):
                    if qi in qa_done or t < qt:
                        continue
                    qa_done.add(qi)
                    try:
                        rects = page.evaluate(qa_js)
                    except Exception:
                        rects = {}
                    labels = {'title': '标题', 'caption': '字幕', 'keyword': '关键词', 'cta': 'CTA', 'lowerThird': '姓名条'}
                    for key, label in labels.items():
                        rect = rects.get(key) if isinstance(rects, dict) else None
                        if not rect or float(rect.get('opacity') or 0) <= 0.05 or float(rect.get('width') or 0) <= 0:
                            continue
                        left = float(rect.get('left') or 0); top = float(rect.get('top') or 0)
                        right = float(rect.get('right') or 0); bottom = float(rect.get('bottom') or 0)
                        hit = left < margin_x - 1 or top < margin_y - 1 or right > W - margin_x + 1 or bottom > H - margin_y + 1
                        clipped = key == 'caption' and (int(rect.get('scrollW') or 0) > int(rect.get('clientW') or 0) + 8 or int(rect.get('scrollH') or 0) > int(rect.get('clientH') or 0) + 8)
                        if hit or clipped:
                            parts = []
                            if hit:
                                parts.append('超出安全区')
                            if clipped:
                                parts.append('文字可能被截断')
                            detail = '%s：%s' % (label, '，'.join(parts))
                            if detail not in layout_warnings:
                                layout_warnings.append(detail)
                                layout_checks.append({'name': '安全区', 'ok': False, 'detail': detail + '（%.2fs）' % t})
                page.screenshot(path=str(tmp / ('o%05d.png' % (i + 1))), omit_background=True, type='png')
                if progress and (i % 10 == 0 or i == total_frames - 1):
                    progress(i + 1, total_frames, ('预览渲染' if preview_mode else '模板渲染') + ' %d/%d' % (i + 1, total_frames))
            browser.close()
        out = OUTPUT_DIR / pid / ('final_preview.mp4' if preview_mode else 'final_packaged.mp4')
        broll_path = None
        broll_mode = 'pip'
        broll_start = 0.0
        broll_dur = 0.0
        if isinstance(broll, dict) and str(broll.get('asset_id') or '').strip():
            candidate = BROLL_DIR / Path(str(broll.get('asset_id'))).name
            if candidate.is_file():
                broll_path = candidate
                broll_mode = 'full' if str(broll.get('mode') or '').strip().lower() == 'full' else 'pip'
                try:
                    broll_start = max(0.0, float(broll.get('start') or 0.0))
                except (TypeError, ValueError):
                    broll_start = 0.0
                broll_start = min(max(0.0, duration - 0.6), broll_start)
                try:
                    broll_dur = max(0.8, float(broll.get('duration') or 2.5))
                except (TypeError, ValueError):
                    broll_dur = 2.5
                broll_dur = min(max(0.8, duration - broll_start), broll_dur)
        motion = spec.get('motion') or {}
        motion_type = str(motion.get('type') or 'none').strip().lower()
        motion_strength = float(motion.get('strength') or 0.0)
        if motion_type in ('push', 'pull') and motion_strength > 0:
            sw = _even(int(round(W * 1.12)))
            sh = _even(int(round(H * 1.12)))
            frames = max(1, total_frames)
            if motion_type == 'pull':
                zexpr = "max(1.0,1.0+%.4f-%.4f*on/%d)" % (motion_strength, motion_strength, frames)
            else:
                zexpr = "1.0+%.4f*on/%d" % (motion_strength, frames)
            base_filter = ("[0:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,setsar=1,"
                           "zoompan=z='%s':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=%dx%d:fps=%d[bg]") % (
                               sw, sh, sw, sh, zexpr, W, H, fps)
        else:
            base_filter = ("[0:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,setsar=1[bg]") % (
                W, H, W, H)
        if broll_path is not None:
            if broll_mode == 'full':
                bfilter = ('[2:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,setsar=1') % (W, H, W, H)
                ox, oy = 0, 0
            else:
                pw = _even(int(round(W * 0.38)))
                bfilter = ('[2:v]scale=%d:-2,setsar=1,drawbox=x=0:y=0:w=iw:h=ih:color=white@0.9:t=4') % pw
                ox, oy = max(0, W - pw - 18), int(H * 0.62)
            if broll_path.suffix.lower() in BROLL_VIDEO_EXT:
                bfilter += ',trim=duration=%.3f,setpts=PTS-STARTPTS+%.3f/TB' % (broll_dur, broll_start)
            else:
                bfilter += ',setpts=PTS-STARTPTS+%.3f/TB' % broll_start
            fg = (base_filter + ';' + bfilter + "[br];[bg][br]overlay=%d:%d:enable='between(t,%.3f,%.3f)':eof_action=pass[scene];[scene][1:v]overlay=0:0:format=auto[v]") % (
                ox, oy, broll_start, broll_start + broll_dur)
        else:
            fg = base_filter + ';[bg][1:v]overlay=0:0:format=auto[v]'
        cmd = [str(FFMPEG), '-y', '-loglevel', 'error', '-i', str(src),
               '-framerate', str(fps), '-start_number', '1', '-i', str(tmp / 'o%05d.png')]
        if broll_path is not None:
            if broll_path.suffix.lower() in BROLL_VIDEO_EXT:
                cmd += ['-i', str(broll_path)]
            else:
                cmd += ['-loop', '1', '-framerate', str(fps), '-t', '%.3f' % broll_dur, '-i', str(broll_path)]
        cmd += ['-filter_complex', fg, '-map', '[v]', '-map', '0:a?',
               '-r', str(fps), '-c:v', 'libx264', '-crf', '18', '-preset', 'medium', '-pix_fmt', 'yuv420p',
               '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', str(out)]
        r = kr._run(cmd, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError('模板合成失败：' + (r.stderr or '')[-700:])
        qa_path = OUTPUT_DIR / pid / ('qa_%s.json' % qa_quality)
        qa_path.write_text(json.dumps({
            'duration': duration, 'fps': fps, 'width': W, 'height': H,
            'template_id': spec.get('id'), 'title': title, 'quality': qa_quality,
            'layout_checked': True, 'layout_checks': layout_checks, 'layout_warnings': layout_warnings,
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        if progress:
            progress(total_frames, total_frames, '预览合成完成' if preview_mode else '模板合成完成')
        return out
    finally:
        if srv is not None:
            try:
                srv.shutdown()
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
