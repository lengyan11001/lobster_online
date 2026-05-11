"""今日头条头条号（mp.toutiao.com）— 登录检测 + 视频 / 图文发布自动化（对齐抖音流程思路）。

视频：…滚到封面区。无 cover：上传封面 → 封面截取 → 下一步 →（部分 UI 须点「完成」）→ 封面编辑「确定」→ 二次「确定」→ 可能再点「完成」收尾。有 cover：本地上传后同上 → 再发布。
图文：须标题 + 正文。
  · **有封面**：先选 Byte 单选「单图」再上传（否则常无 file input）；再滚动封面区、点击上传入口、shadow 兜底；另传 cover_asset_id 可作第二处配图。
  · **无封面**：会话未指定配图时，在 options 里设 `toutiao_graphic_no_cover: true`（或 `toutiao: { no_cover: true }`），则不传任何图片，仅标题+正文（纯文）。

options / 定时任务 payload 扩展（可与 options[\"toutiao\"] 合并）：
  toutiao_graphic_no_cover: true — 头条发文章不要封面图
  toutiao_ad_declaration: \"no_promo\"|\"is_promo\"|\"auto\" 或选项文案片段
  toutiao_ad_radio: 同上（别名）
  toutiao_radios: [{\"group_contains\":\"广告\",\"option_contains\":\"否\"}]
  toutiao_fills: [{\"placeholder_contains\":\"摘要\",\"text\":\"...\"}]
  toutiao_checkboxes: [{\"label_contains\":\"原创\",\"checked\":true}]
  toutiao_clicks: [{\"text_contains\":\"同意\"}] 或 [\"同意\"]
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from typing import Any, Callable, Dict, List, Optional

from publisher.pw_timeouts import ms as _pw_ms
from publisher.pw_timeouts import navigation_timeout_ms as _nav_ms

from skills._base import BaseDriver

logger = logging.getLogger(__name__)

HOME_URL = "https://mp.toutiao.com/"
LOGIN_ENTRY = "https://mp.toutiao.com/auth/page/login?redirect_url=JTJGcHJvZmlsZV92NCUyRg=="

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".flv", ".m4v"}

# 视频上传仅使用「上传视频」入口；/xigua/publish 多为作品管理/列表而非上传页，误作第二入口会导致无 file input、流程跑偏。
VIDEO_UPLOAD_ENTRY_URL = "https://mp.toutiao.com/profile_v4/xigua/upload-video"
VIDEO_ENTRY_URLS = (VIDEO_UPLOAD_ENTRY_URL,)
GRAPHIC_PUBLISH_URL = "https://mp.toutiao.com/profile_v4/graphic/publish"
# 与头条号后台常见限制对齐（图文/视频标题均截断到此长度）
_TOUTIAO_TITLE_MAX_LEN = 30
# 自动化返回 ok 仅表示流程点完；用户侧常以 toast「提交成功」为准，最终以作品管理列表为准。
_TOUTIAO_SUBMISSION_USER_HINT = (
    "头条确认发布后通常只有短暂 toast（如「提交成功」），一般无单独成功页；请以「管理 → 作品管理」是否出现新稿为准。"
)


def toutiao_publish_entry_url(file_path: str, options: Optional[Dict[str, Any]] = None) -> str:
    """
    空白标签启动发布时先进此 URL 再验登录，可避免 blank→首页→业务页 多一次中转。
    """
    options = options or {}
    ext = os.path.splitext(file_path or "")[1].lower()
    tt = _toutiao_extra_options(options)
    graphic_no_cover = _opt_truthy(tt.get("graphic_no_cover") or tt.get("no_cover"))
    is_image = ext in _IMAGE_EXTS
    is_video = ext in _VIDEO_EXTS
    if is_video and not graphic_no_cover:
        return VIDEO_UPLOAD_ENTRY_URL
    if is_image or graphic_no_cover:
        return GRAPHIC_PUBLISH_URL
    return HOME_URL


def _is_toutiao_graphic_publish_url(url: str) -> bool:
    if not url:
        return False
    u = url.split("?")[0].rstrip("/").lower()
    return u.endswith("/graphic/publish")


def _is_toutiao_video_entry_url(url: str) -> bool:
    if not url:
        return False
    u = url.split("?")[0].rstrip("/").lower()
    v0 = VIDEO_UPLOAD_ENTRY_URL.split("?")[0].rstrip("/").lower()
    if u == v0:
        return True
    # 历史链接：发布编辑流程中可能仍带 /xigua/publish，视为视频业务域内
    return "/xigua/" in u and ("upload-video" in u or "publish" in u)


async def _delay(lo: float = 0.4, hi: float = 1.2) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


def _merge_tags(description: str, tags: str) -> str:
    t = (description or "").strip()
    parts = [x.strip() for x in (tags or "").split(",") if x.strip()]
    if not parts:
        return t
    return (t + " " + " ".join(f"#{p}" for p in parts)).strip()


def _opt_truthy(v: Any) -> bool:
    """解析 options 里的布尔开关。"""
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "on")


def _iters_for_large_file(file_size: int, *, base: int, per_bytes: int, cap: int) -> int:
    """大文件多给几次轮询（每轮由调用方决定 sleep 秒数）。"""
    extra = int(max(0, file_size) / max(per_bytes, 1))
    return min(cap, base + extra)


async def _file_input_anywhere(page: Any) -> Any:
    for sel in (
        'input[type="file"]',
        'input[accept*="video"]',
        'input[accept*="image"]',
        'input[accept*="mp4"]',
    ):
        try:
            el = await page.query_selector(sel)
            if el:
                return el
        except Exception:
            pass
    try:
        for fr in page.frames:
            for sel in ('input[type="file"]', 'input[accept*="video"]', 'input[accept*="image"]'):
                try:
                    el = await fr.query_selector(sel)
                    if el:
                        return el
                except Exception:
                    continue
    except Exception:
        pass
    return None


async def _toutiao_video_try_reveal_file_input(page: Any) -> Any:
    """
    参考抖音 `_publish_video`：页面上传区可能先隐藏 file input，需点击「上传视频 / 点击上传」等再出现。
    """
    selectors = (
        'button:has-text("上传视频")',
        'button:has-text("选择视频")',
        '[class*="upload"] button',
        'text="点击上传"',
    )
    for ubs in selectors:
        try:
            ubtn = await page.query_selector(ubs)
            if not ubtn:
                continue
            try:
                if not await ubtn.is_visible():
                    continue
            except Exception:
                pass
            await ubtn.click(timeout=_pw_ms(4000))
            await asyncio.sleep(1.2)
            logger.info("[TOUTIAO-VIDEO] reveal file input clicked %s", ubs[:56])
        except Exception as e:
            logger.debug("[TOUTIAO-VIDEO] reveal skip %s: %s", ubs, e)
    try:
        el = await page.wait_for_selector(
            'input[type="file"]', state="attached", timeout=_pw_ms(10000)
        )
        if el:
            return el
    except Exception:
        pass
    return await _file_input_anywhere(page)


async def _toutiao_video_upload_via_file_chooser(page: Any, file_path: str) -> bool:
    """
    参考抖音图文：expect_file_chooser + 点击上传文案，由 Playwright 注入本地路径（避免依赖可见的 input）。
    """
    if not os.path.isfile(file_path):
        return False
    frames = _toutiao_graphic_frames_ordered(page)
    labels = (
        "上传视频",
        "选择视频",
        "点击上传",
        "本地上传",
        "添加视频",
    )
    for lab in labels:
        for fr in frames:
            try:
                loc = fr.get_by_text(lab, exact=False)
                if await loc.count() == 0:
                    continue
                target = loc.first
                await target.scroll_into_view_if_needed(timeout=_pw_ms(5000))
                async with page.expect_file_chooser(timeout=_pw_ms(12000)) as fc_info:
                    await target.click(timeout=_pw_ms(5000), force=True)
                chooser = await fc_info.value
                await chooser.set_files(file_path)
                logger.info("[TOUTIAO-VIDEO] file_chooser ok label=%s", lab)
                await asyncio.sleep(1.0)
                return True
            except Exception as e:
                logger.info("[TOUTIAO-VIDEO] file_chooser skip %r: %s", lab, e)
    return False


async def dismiss_xigua_open_modal(page: Any) -> bool:
    """西瓜 / 头条「开通 * 权益」类引导（含「小视频创作权益」）：点「暂不开通 / 暂不通」。

    弹窗不一定出现；且可能出现在填表后或首次点「发布」之后。须遍历主文档与各 frame；
    仅对疑似含按钮文案的 frame 做定位，避免无谓开销。
    """
    names = ("暂不开通", "暂不通")
    for fr in _toutiao_graphic_frames_ordered(page):
        try:
            head = await fr.evaluate(
                "() => ((document.body && document.body.innerText) || '').slice(0, 16000)"
            )
        except Exception:
            head = ""
        if not any(n in (head or "") for n in names):
            continue
        for name in names:
            try:
                loc = fr.get_by_role("button", name=name)
                if await loc.count() > 0:
                    first = loc.first
                    try:
                        if await first.is_visible():
                            await first.scroll_into_view_if_needed(timeout=3000)
                            await first.click(timeout=5000)
                            logger.info("[TOUTIAO] dismissed modal(frame role): %s", name)
                            await asyncio.sleep(0.85)
                            return True
                    except Exception:
                        pass
            except Exception:
                pass
        for name in names:
            for sel in (
                f'button:has-text("{name}")',
                f'button.byte-btn-default:has-text("{name}")',
                f'button.byte-btn-default:has(span:text-is("{name}"))',
                f'button.byte-btn:has(span:text-is("{name}"))',
            ):
                try:
                    loc2 = fr.locator(sel)
                    if await loc2.count() == 0:
                        continue
                    btn = loc2.first
                    if not await btn.is_visible():
                        continue
                    await btn.scroll_into_view_if_needed(timeout=3000)
                    await btn.click(timeout=4000)
                    logger.info("[TOUTIAO] dismissed modal(frame sel=%s)", sel[:56])
                    await asyncio.sleep(0.85)
                    return True
                except Exception:
                    pass
    return False


async def dismiss_toutiao_creative_assistant(page: Any) -> bool:
    """
    图文发布页右侧「头条创作助手」会挡操作；在助手外（主编辑区）点一下即可收起。
    """
    try:
        snippet = await page.evaluate(
            "() => ((document.body && document.body.innerText) || '').slice(0, 5000)"
        )
    except Exception:
        snippet = ""
    if "头条创作助手" not in snippet and "AI 创作" not in snippet:
        return False

    try:
        box = await page.evaluate(
            """() => ({
            w: Math.max(400, window.innerWidth || document.documentElement.clientWidth || 1280),
            h: Math.max(300, window.innerHeight || document.documentElement.clientHeight || 800),
          })"""
        )
        w = int(box.get("w", 1280))
        h = int(box.get("h", 800))
        # 助手多在右侧，点在左侧偏中的主工作区（避开顶栏）
        x = max(24, min(int(w * 0.14), w - 8))
        y = max(100, min(int(h * 0.45), h - 8))
        await page.mouse.click(x, y)
        await asyncio.sleep(0.55)
        logger.info("[TOUTIAO] creative assistant: clicked outside main area at %s,%s", x, y)
        return True
    except Exception as e:
        logger.debug("[TOUTIAO] creative assistant outside click failed: %s", e)
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.35)
        except Exception:
            pass
        return False


_JS_TOUTIAO_UPLOAD_STATE = """
() => {
  const b = (document.body && document.body.innerText) || '';
  if (b.includes('上传失败') || b.includes('转码失败') || b.includes('上传出错')) {
    return { state: 'fail', hint: 'error_keyword' };
  }
  const uploading =
    b.includes('上传中') || b.includes('正在上传') || b.includes('压缩中')
    || b.includes('导入中') || (b.includes('处理中') && b.includes('视频'))
    || b.includes('转码中') || b.includes('视频处理中');
  const hasReupload = b.includes('重新上传') || b.includes('更换视频');
  const hasTitle = !!document.querySelector(
    'input[placeholder*="标题"], textarea[placeholder*="标题"], input[placeholder*="概括"], input[placeholder*="作品"]'
  );
  let hasPublish = false;
  document.querySelectorAll('button').forEach((x) => {
    const t = (x.textContent || '').trim();
    if (t === '发布' || t === '发表' || t.includes('立即发布') || t === '预览并发布') {
      hasPublish = true;
    }
  });
  // 探测：上传成功后即出现标题区等表单；若仍报「上传中」但已「上传成功」且标题框已挂载，视为可进入填表阶段
  const uploadOk = b.includes('上传成功') || b.includes('上传已完成');
  if (uploadOk && hasTitle && !b.includes('转码中') && !b.includes('压缩中')) {
    return { state: 'ready', hint: 'upload_ok+title' };
  }
  if (!uploading && (hasReupload || hasTitle || hasPublish)) {
    return { state: 'ready' };
  }
  return { state: 'wait', uploading };
}
"""


async def _wait_toutiao_video_editor_ready(
    page: Any,
    file_size: int,
    _step: Callable[..., None],
    *,
    sleep_s: float = 2.0,
) -> bool:
    """视频 set_input_files 后等待页面进入可填标题/发布状态（大文件多等几轮）。"""
    iters = _iters_for_large_file(file_size, base=35, per_bytes=6 * 1024 * 1024, cap=150)
    _step("等待视频上传/处理", True, max_rounds=iters, sleep_s=sleep_s)
    for i in range(iters):
        try:
            st = await page.evaluate(_JS_TOUTIAO_UPLOAD_STATE)
        except Exception:
            st = {"state": "wait"}
        if st.get("state") == "fail":
            _step("等待视频上传/处理", False, round=i, detail=st)
            return False
        if st.get("state") == "ready":
            _step("等待视频上传/处理-就绪", True, rounds_used=i + 1)
            return True
        await asyncio.sleep(sleep_s)
    _step("等待视频上传/处理", False, reason="timeout", rounds=iters)
    return False


_JS_TOUTIAO_GRAPHIC_READY = """
() => {
  const b = (document.body && document.body.innerText) || '';
  if (b.includes('上传失败')) return { state: 'fail' };
  const ed = document.querySelector('[contenteditable="true"]');
  const title = document.querySelector(
    'input[placeholder*="标题"], textarea[placeholder*="标题"], input[placeholder*="概括"]'
  );
  const hasEditor = !!(ed || title);
  const looseUp = b.includes('上传中') || b.includes('正在上传');
  // 旧逻辑：全文任一「上传中」即一直 wait，侧栏/无关模块文案会误伤，导致封面已好仍空等几十秒不进填标题。
  // 新逻辑：若标题/正文编辑区已出现在 DOM，认为可与封面并行，不再被泛泛「上传中」卡住。
  if (hasEditor) return { state: 'ready' };
  if (looseUp) return { state: 'wait' };
  return { state: 'ready' };
}
"""


async def _wait_toutiao_graphic_ready(
    page: Any,
    file_size: int,
    _step: Callable[..., None],
    *,
    had_upload: bool,
    sleep_s: float = 2.0,
) -> bool:
    if not had_upload:
        await asyncio.sleep(1.5)
        return True
    iters = _iters_for_large_file(file_size, base=25, per_bytes=8 * 1024 * 1024, cap=90)
    if file_size < 512 * 1024:
        iters = min(iters, 18)
    for i in range(iters):
        try:
            st = await page.evaluate(_JS_TOUTIAO_GRAPHIC_READY)
        except Exception:
            st = {"state": "wait"}
        if st.get("state") == "fail":
            _step("等待图文上传", False, round=i)
            return False
        if st.get("state") == "ready":
            _step("等待图文上传-就绪", True, rounds_used=i + 1)
            return True
        await asyncio.sleep(sleep_s)
    _step("等待图文上传", False, reason="timeout")
    return False


# 发布按钮：排除「定时发布」「存草稿」等；优先即时发布类。
_JS_TOUTIAO_PUBLISH_BUTTON_CLICK = """
() => {
  const btns = Array.from(document.querySelectorAll('button')).filter((b) => b.offsetParent);
  const candidates = [];
  for (const b of btns) {
    const t = (b.textContent || '').trim();
    if (!t) continue;
    if (/定时|草稿|暂存/.test(t)) continue;
    let score = 0;
    if (t === '发表') score = 96;
    else if (t.includes('发表') && !t.includes('预览')) score = 90;
    else if (t === '发布') score = 94;
    else if (t.includes('立即发布') && !t.includes('预览')) score = 92;
    else if (t.includes('发布') && !t.includes('预览') && t !== '预览并发布') score = 55;
    else if (t === '预览并发布') score = 15;
    if (score > 0) candidates.push({ b, t, score });
  }
  candidates.sort((a, b) => b.score - a.score);
  if (!candidates.length) return '';
  const { b, t } = candidates[0];
  try { b.scrollIntoView({ block: 'center' }); } catch (e) {}
  b.click();
  return t;
}
"""

# 图文发布页主 CTA 多为底部「预览并发布」；通用脚本把「发布」打 94 分、「预览并发布」仅 15 分，会误点侧栏杂项「发布」。
_JS_TOUTIAO_GRAPHIC_PREFER_PREVIEW_PUBLISH = """
() => {
  function tryClick(sel) {
    const el = document.querySelector(sel);
    if (!el || !el.offsetParent) return '';
    try {
      if (el.disabled) return '';
    } catch (e) {}
    try {
      el.scrollIntoView({ block: 'center', inline: 'nearest' });
    } catch (e) {}
    try {
      el.click();
    } catch (e) {
      return '';
    }
    const t = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
    return t || '预览并发布';
  }
  let r = tryClick('button.publish-btn-last');
  if (r) return r;
  r = tryClick('button.byte-btn-primary.publish-btn');
  if (r) return r;
  r = tryClick('button.publish-btn');
  if (r) return r;
  const nodes = Array.from(
    document.querySelectorAll('button, [role="button"], div[class*="byte-btn"], span[class*="byte-btn"]')
  ).filter((b) => b.offsetParent);
  const scoreText = (raw) => {
    const t = (raw || '').replace(/\\s+/g, ' ').trim();
    if (!t || t.length > 28) return 0;
    if (/定时|草稿|暂存/.test(t)) return 0;
    if (t === '预览并发布') return 100;
    if (t.includes('预览') && t.includes('发布')) return 92;
    return 0;
  };
  let best = null;
  for (const b of nodes) {
    try {
      if (b.disabled) continue;
    } catch (e) {}
    const t = (b.innerText || b.textContent || '').replace(/\\s+/g, ' ').trim();
    const s = scoreText(t);
    if (s > 0 && (!best || s > best.s)) best = { b, t, s };
  }
  if (!best) return '';
  try {
    best.b.scrollIntoView({ block: 'center', inline: 'nearest' });
  } catch (e) {}
  try {
    best.b.click();
  } catch (e) {}
  return best.t;
}
"""


async def _toutiao_graphic_click_primary_publish(page: Any) -> str:
    """头条图文：优先底部「预览并发布」（button.publish-btn-last）；西瓜视频请用 _toutiao_video_click_primary_publish。"""
    _pub_sels = (
        "button.publish-btn-last",
        "button.byte-btn-primary.publish-btn",
        "button.publish-btn",
    )
    for fr in _toutiao_graphic_frames_ordered(page):
        for sel in _pub_sels:
            try:
                loc = fr.locator(sel)
                if await loc.count() == 0:
                    continue
                btn = loc.first
                try:
                    if not await btn.is_visible():
                        continue
                except Exception:
                    pass
                txt = (await btn.inner_text() or "").strip()
                if sel != "button.publish-btn-last" and txt and "预览" not in txt and "发布" not in txt:
                    continue
                if sel == "button.publish-btn-last":
                    for _ in range(10):
                        try:
                            if not await btn.is_disabled():
                                break
                        except Exception:
                            break
                        await asyncio.sleep(1.0)
                await btn.scroll_into_view_if_needed(timeout=5000)
                try:
                    await btn.click(timeout=5000, force=True)
                except Exception as first_e:
                    if sel == "button.publish-btn-last":
                        logger.info("[TOUTIAO-GRAPHIC] publish first click retry: %s", first_e)
                        await asyncio.sleep(0.55)
                        await btn.scroll_into_view_if_needed(timeout=4000)
                        await btn.click(timeout=5000, force=True)
                    else:
                        raise
                logger.info("[TOUTIAO-GRAPHIC] publish click via=pw %s text=%s", sel, txt[:24])
                return (txt or "").strip() or "预览并发布"
            except Exception as e:
                logger.info("[TOUTIAO-GRAPHIC] publish pw %s: %s", sel, e)
    label = ""
    try:
        label = await page.evaluate(_JS_TOUTIAO_GRAPHIC_PREFER_PREVIEW_PUBLISH)
    except Exception:
        label = ""
    if label:
        logger.info("[TOUTIAO-GRAPHIC] publish click prefer 预览并发布 via=js label=%s", label[:40])
        return label
    try:
        loc = page.get_by_text("预览并发布", exact=True)
        if await loc.count() > 0:
            await loc.first.scroll_into_view_if_needed(timeout=5000)
            await loc.first.click(timeout=5000, force=True)
            logger.info("[TOUTIAO-GRAPHIC] publish click via=pw exact 预览并发布")
            return "预览并发布"
    except Exception as e:
        logger.info("[TOUTIAO-GRAPHIC] publish pw exact: %s", e)
    try:
        loc = page.get_by_role("button", name=re.compile(r"预览\s*并\s*发布"))
        if await loc.count() > 0:
            await loc.first.scroll_into_view_if_needed(timeout=4000)
            await loc.first.click(timeout=5000, force=True)
            logger.info("[TOUTIAO-GRAPHIC] publish click via=pw role regex")
            return "预览并发布"
    except Exception as e:
        logger.info("[TOUTIAO-GRAPHIC] publish pw role: %s", e)
    for fr in _toutiao_graphic_frames_ordered(page):
        try:
            loc = fr.get_by_text("预览并发布", exact=True)
            if await loc.count() > 0:
                await loc.first.scroll_into_view_if_needed(timeout=4000)
                await loc.first.click(timeout=5000, force=True)
                logger.info("[TOUTIAO-GRAPHIC] publish click via=frame")
                return "预览并发布"
        except Exception:
            continue
    fallback = await page.evaluate(_JS_TOUTIAO_PUBLISH_BUTTON_CLICK)
    if fallback:
        logger.info("[TOUTIAO-GRAPHIC] publish click fallback generic js=%s", fallback[:40])
    return str(fallback or "")


# 西瓜视频上传页主 CTA 为「发布」；侧栏也有「发布」。按纵向位置 + publish-btn-last 类名优先底部主按钮。
_JS_TOUTIAO_VIDEO_CLICK_BOTTOM_PUBLISH = """() => {
  const vh = window.innerHeight || 800;
  const vw = window.innerWidth || 1280;
  let best = null;
  let bestScore = -9999;
  const btns = Array.from(document.querySelectorAll('button')).filter((b) => b.offsetParent);
  for (const b of btns) {
    let t = '';
    try {
      t = (b.innerText || b.textContent || '').replace(/\\s+/g, ' ').trim();
    } catch (e) {
      continue;
    }
    if (!t || t.length > 22) continue;
    if (/定时|草稿|暂存|返回编辑/.test(t)) continue;
    if (t.includes('预览并发布') || (t.includes('预览') && t.includes('发布'))) continue;
    const isPub =
      t === '发布' ||
      t === '立即发布' ||
      t === '发表' ||
      (t.length <= 10 && /^(发表|发布)/.test(t) && !/预览/.test(t));
    if (!isPub) continue;
    try {
      if (b.disabled) continue;
    } catch (e) {}
    const r = b.getBoundingClientRect();
    if (r.width < 10 || r.height < 8) continue;
    let score = ((r.top + r.height * 0.5) / Math.max(vh, 400)) * 100;
    if (r.left < Math.min(130, vw * 0.11)) score -= 55;
    const cn = (b.className && String(b.className)) || '';
    if (cn.indexOf('publish-btn-last') >= 0) score += 100;
    else if (cn.indexOf('publish-btn') >= 0) score += 35;
    if (cn.indexOf('byte-btn-primary') >= 0) score += 12;
    if (score > bestScore) {
      bestScore = score;
      best = b;
    }
  }
  if (!best) return '';
  try {
    best.scrollIntoView({ block: 'center', inline: 'nearest' });
    best.click();
    return ((best.innerText || '') + '').replace(/\\s+/g, ' ').trim().slice(0, 24) || '发布';
  } catch (e) {
    return '';
  }
}"""

# 与 _JS_TOUTIAO_VIDEO_CLICK_BOTTOM_PUBLISH 同分逻辑，只上报不点击（探测 / 日志用）
_JS_TOUTIAO_VIDEO_PUBLISH_CANDIDATES_REPORT = """() => {
  const vh = window.innerHeight || 800;
  const vw = window.innerWidth || 1280;
  const out = [];
  const btns = Array.from(document.querySelectorAll('button')).filter((b) => b.offsetParent);
  for (const b of btns) {
    let t = '';
    try {
      t = (b.innerText || b.textContent || '').replace(/\\s+/g, ' ').trim();
    } catch (e) {
      continue;
    }
    if (!t || t.length > 22) continue;
    if (/定时|草稿|暂存|返回编辑/.test(t)) continue;
    if (t.includes('预览并发布') || (t.includes('预览') && t.includes('发布'))) continue;
    const isPub =
      t === '发布' ||
      t === '立即发布' ||
      t === '发表' ||
      (t.length <= 10 && /^(发表|发布)/.test(t) && !/预览/.test(t));
    if (!isPub) continue;
    let dis = false;
    try {
      dis = !!b.disabled;
    } catch (e) {}
    const r = b.getBoundingClientRect();
    if (r.width < 10 || r.height < 8) continue;
    let score = ((r.top + r.height * 0.5) / Math.max(vh, 400)) * 100;
    if (r.left < Math.min(130, vw * 0.11)) score -= 55;
    const cn = (b.className && String(b.className)) || '';
    if (cn.indexOf('publish-btn-last') >= 0) score += 100;
    else if (cn.indexOf('publish-btn') >= 0) score += 35;
    if (cn.indexOf('byte-btn-primary') >= 0) score += 12;
    out.push({
      text: t.slice(0, 22),
      cls: cn.slice(0, 140),
      score: Math.round(score * 10) / 10,
      rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
      disabled: dis,
    });
  }
  out.sort((a, b) => b.score - a.score);
  const last = document.querySelector('button.publish-btn-last');
  let lastInfo = null;
  if (last && last.offsetParent) {
    const t = ((last.innerText || '') + '').replace(/\\s+/g, ' ').trim().slice(0, 22);
    let dis = false;
    try {
      dis = !!last.disabled;
    } catch (e) {}
    const r = last.getBoundingClientRect();
    lastInfo = { text: t, disabled: dis, rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)] };
  }
  return { candidateCount: out.length, topCandidates: out.slice(0, 10), publishBtnLast: lastInfo };
}"""


async def _toutiao_video_click_primary_publish(page: Any) -> str:
    """西瓜视频：主按钮为「发布」；先常见 footer 选择器，再几何加权 JS，避免侧栏同名按钮。"""
    _pub_sels = (
        "button.publish-btn-last",
        "button.byte-btn-primary.action-footer-btn",
        "button.action-footer-btn.submit",
        "button.byte-btn-primary.publish-btn",
        "button.publish-btn",
    )
    for fr in _toutiao_graphic_frames_ordered(page):
        for sel in _pub_sels:
            try:
                loc = fr.locator(sel)
                if await loc.count() == 0:
                    continue
                btn = loc.first
                try:
                    if not await btn.is_visible():
                        continue
                except Exception:
                    pass
                txt = (await btn.inner_text() or "").strip()
                if sel != "button.publish-btn-last":
                    if txt and not any(x in txt for x in ("发布", "发表", "预览")):
                        continue
                if sel == "button.publish-btn-last":
                    for _ in range(10):
                        try:
                            if not await btn.is_disabled():
                                break
                        except Exception:
                            break
                        await asyncio.sleep(1.0)
                await btn.scroll_into_view_if_needed(timeout=5000)
                try:
                    await btn.click(timeout=5000, force=True)
                except Exception as first_e:
                    if sel == "button.publish-btn-last":
                        logger.info("[TOUTIAO-VIDEO] publish first click retry: %s", first_e)
                        await asyncio.sleep(0.55)
                        await btn.scroll_into_view_if_needed(timeout=4000)
                        await btn.click(timeout=5000, force=True)
                    else:
                        raise
                logger.info("[TOUTIAO-VIDEO] publish click via=pw %s text=%s", sel, txt[:24])
                return (txt or "").strip() or "发布"
            except Exception as e:
                logger.info("[TOUTIAO-VIDEO] publish pw %s: %s", sel, e)
    try:
        hit = await page.evaluate(_JS_TOUTIAO_VIDEO_CLICK_BOTTOM_PUBLISH)
        if hit:
            logger.info("[TOUTIAO-VIDEO] publish bottom-weighted js label=%s", str(hit)[:40])
            return str(hit).strip()
    except Exception as e:
        logger.debug("[TOUTIAO-VIDEO] publish bottom js err: %s", e)
    fb = ""
    try:
        fb = await page.evaluate(_JS_TOUTIAO_PUBLISH_BUTTON_CLICK)
    except Exception:
        pass
    if fb:
        logger.info("[TOUTIAO-VIDEO] publish fallback _JS_TOUTIAO_PUBLISH_BUTTON_CLICK=%s", str(fb)[:40])
    return str(fb or "")


# 首点「发布」后若封面不合规，常弹窗问是否去改图；点「取消」关掉后再点「发布」才会继续提交。
_JS_TOUTIAO_VIDEO_DISMISS_COVER_COMPLIANCE_NAG = r"""() => {
  function visible(el) {
    if (!el) return false;
    const st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity || '1') < 0.05) return false;
    const r = el.getBoundingClientRect();
    return r.width > 20 && r.height > 20 && r.bottom > 0 && r.right > 0;
  }
  const selectors =
    '[role="dialog"], .semi-modal-wrapper, .semi-modal-content, .byte-modal-wrapper, .byte-modal, .bui-modal, .m-xigua-dialog, [class*="Modal"]';
  const roots = Array.from(document.querySelectorAll(selectors));
  for (const root of roots) {
    if (!visible(root)) continue;
    const t = ((root.innerText || '') + '').replace(/\s+/g, ' ').trim();
    if (t.length < 8 || t.length > 900) continue;
    if (!/封面/.test(t)) continue;
    if (/封面截取|截取封面|本地上传|选择帧|拖拽|裁剪编辑/.test(t) && !/不合规|不符合|不规范|建议.*修改|需要.*修改|是否/.test(t)) continue;
    const isNag =
      /不合规|不符合|不规范|未通过审核|无法发布|暂不发布|请先修改|前往修改|是否.*修改|是否前往|修改封面|封面.*(问题|异常|未通过)/.test(t) ||
      (/是否/.test(t) && /封面/.test(t) && /(修改|调整|更换|重选)/.test(t));
    if (!isNag) continue;
    const btnCandidates = root.querySelectorAll('button, [role="button"], .semi-button, span.semi-button-content');
    const prefer = [];
    const secondary = [];
    for (const b of btnCandidates) {
      if (!visible(b)) continue;
      let tx = ((b.innerText || '') + '').replace(/\s+/g, ' ').trim();
      if (!tx || tx.length > 36) continue;
      if (/去修改|立即修改|前往|现在就去|马上修改/.test(tx)) continue;
      if (tx === '取消' || /^取消/.test(tx)) {
        prefer.push(b);
        continue;
      }
      if (/^暂不/.test(tx) || tx === '关闭' || tx === '暂不修改' || tx === '我知道了') {
        secondary.push(b);
        continue;
      }
    }
    const hit = prefer[0] || secondary[0];
    if (hit) {
      try {
        hit.click();
      } catch (e) {}
      return ((hit.innerText || '') + '').replace(/\s+/g, ' ').trim().slice(0, 28);
    }
  }
  return '';
}"""


async def _toutiao_video_dismiss_cover_compliance_nag_if_present(page: Any) -> str:
    """若存在「封面不合规 / 建议修改」类弹窗，点「取消」或「暂不*」关闭。返回被点的按钮文案，无则返回空串。"""
    for fr in _toutiao_graphic_frames_ordered(page):
        try:
            hit = await fr.evaluate(_JS_TOUTIAO_VIDEO_DISMISS_COVER_COMPLIANCE_NAG)
            if hit:
                logger.info("[TOUTIAO-VIDEO] cover compliance nag dismissed btn=%r", str(hit)[:40])
                return str(hit).strip()
        except Exception as e:
            logger.debug("[TOUTIAO-VIDEO] cover compliance nag frame err: %s", e)
    return ""


# 点「预览并发布」后，常先出现「保存失败」类 toast，主按钮文案变为「确认发布」；该按钮多在页脚/抽屉而非 modal 内。
_JS_TOUTIAO_CONFIRM_AFTER_PREVIEW = """
(minScore) => {
  function scoreConfirmLabel(t) {
    if (!t || t.length > 36) return 0;
    if (t.includes('预览并发布')) return 0;
    if (/定时|草稿|暂存|返回编辑/.test(t)) return 0;
    if (t === '确认发布' || t === '确认并发布') return 100;
    if (t === '确定发布') return 99;
    if (t.includes('确认') && t.includes('发布') && !t.includes('预览')) return 95;
    if (t.includes('确定') && t.includes('发布')) return 94;
    return 0;
  }
  function tryPublishBtnLast() {
    const el = document.querySelector('button.publish-btn-last');
    if (!el || !el.offsetParent) return '';
    try {
      if (el.disabled) return '';
    } catch (e) {}
    const t = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
    const s = scoreConfirmLabel(t);
    if (s < minScore) return '';
    try {
      el.scrollIntoView({ block: 'center', inline: 'nearest' });
    } catch (e) {}
    try {
      el.click();
    } catch (e) {
      return '';
    }
    return t;
  }
  const fast = tryPublishBtnLast();
  if (fast) return fast;
  const vh = Math.max(400, window.innerHeight || 800);
  function inBottomArea(el) {
    try {
      const r = el.getBoundingClientRect();
      return r.top > vh * 0.48 && r.height > 8 && r.width > 8;
    } catch (e) {
      return false;
    }
  }
  const baseSel = 'button, [role="button"], a[role="button"]';
  const extraSel = [
    'div[class*="byte-btn"]',
    'span[class*="byte-btn"]',
    'div[class*="Button"]',
    'span[class*="Button"]',
    'div[class*="semi-button"]',
    'span[class*="semi-button"]',
    '.bui-btn',
    '[class*="publish"]',
  ].join(', ');
  let nodes = Array.from(document.querySelectorAll(baseSel + ', ' + extraSel)).filter(
    (b) => b.offsetParent !== null
  );
  nodes = nodes.filter((b, i, a) => a.indexOf(b) === i);
  const scoreOf = (raw, el) => {
    const t = (raw || '').replace(/\\s+/g, ' ').trim();
    if (!t || t.length > 36) return 0;
    if (t === '预览并发布' || t.includes('预览并发布')) return 0;
    if (/定时|草稿|暂存|返回编辑|取消|关闭/.test(t)) return 0;
    if (t === '确认发布' || t === '确认并发布') return 100;
    if (t === '确定发布') return 99;
    if (t.includes('确认') && t.includes('发布') && !t.includes('预览')) return 95;
    if (t.includes('提交') && t.includes('发布')) return 93;
    if (t.includes('完成') && t.includes('发布')) return 92;
    if (t.includes('立即发布') && !t.includes('预览')) return 90;
    if (t === '发表' || t === '发布' || t === '立即发表') return 78;
    if (/^发布$/.test(t) || /^发表$/.test(t)) return 76;
    if ((t === '确定' || t === '确认') && inBottomArea(el)) return 86;
    return 0;
  };
  let best = null;
  for (const b of nodes) {
    try {
      if (b.disabled) continue;
    } catch (e) {}
    const t = (b.innerText || b.textContent || '').replace(/\\s+/g, ' ').trim();
    const s = scoreOf(t, b);
    if (s > 0 && (!best || s > best.s)) best = { b, t, s };
  }
  if (!best || best.s < minScore) return '';
  try { best.b.scrollIntoView({ block: 'center' }); } catch (e) {}
  try { best.b.click(); } catch (e) {}
  return best.t;
}
"""


async def _toutiao_pw_click_confirm_after_preview(page: Any) -> str:
    """JS 未扫到 Byte 样式按钮时，用 Playwright 文案/正则兜底。"""
    for fr in _toutiao_graphic_frames_ordered(page):
        for sel in ("button.publish-btn-last", "button.byte-btn-primary.publish-btn"):
            try:
                loc = fr.locator(sel)
                if await loc.count() == 0:
                    continue
                btn = loc.first
                try:
                    if not await btn.is_visible():
                        continue
                except Exception:
                    pass
                txt = re.sub(r"\s+", " ", (await btn.inner_text() or "").strip())
                if "预览并发布" in txt:
                    continue
                if not txt or "发布" not in txt:
                    continue
                if not re.search(r"确认|确定", txt):
                    continue
                await btn.scroll_into_view_if_needed(timeout=5000)
                await btn.click(timeout=5000, force=True)
                logger.info("[TOUTIAO-GRAPHIC] after_preview confirm via=pw %s text=%s", sel, txt[:28])
                return txt[:40]
            except Exception as e:
                logger.info("[TOUTIAO-GRAPHIC] after_preview pw %s: %s", sel, e)
    patterns = (
        re.compile(r"确认\s*并?\s*发布"),
        re.compile(r"确定\s*发布"),
        re.compile(r"^提交\s*发布$"),
        re.compile(r"^完成\s*并?\s*发布$"),
        re.compile(r"^立即发布$"),
        re.compile(r"^发表$"),
        re.compile(r"^发布$"),
    )
    for fr in _toutiao_graphic_frames_ordered(page):
        for rx in patterns:
            try:
                loc = fr.get_by_role("button", name=rx)
                if await loc.count() == 0:
                    continue
                btn = loc.first
                try:
                    if await btn.is_disabled():
                        continue
                except Exception:
                    pass
                await btn.scroll_into_view_if_needed(timeout=4000)
                await btn.click(timeout=5000, force=True)
                return rx.pattern
            except Exception:
                continue
        try:
            loc = fr.locator("button, [role='button'], div[class*='byte-btn'], span[class*='byte-btn']")
            n = await loc.count()
            for i in range(min(n, 40)):
                el = loc.nth(i)
                try:
                    if not await el.is_visible():
                        continue
                    txt = (await el.inner_text() or "").strip()
                    txt1 = re.sub(r"\s+", " ", txt)[:40]
                    if not txt1 or len(txt1) > 34:
                        continue
                    if "预览" in txt1 and "发布" in txt1:
                        continue
                    if re.search(r"确认.*发布|确定.*发布|提交.*发布|完成.*发布", txt1):
                        await el.scroll_into_view_if_needed(timeout=3000)
                        await el.click(timeout=5000, force=True)
                        return txt1[:32]
                except Exception:
                    continue
        except Exception:
            pass
    return ""


_JS_TOUTIAO_DETECT_SUBMIT_TOAST = """() => {
  const keys = ['提交成功', '发布成功', '发表成功', '提交审核成功', '投稿成功', '已提交审核', '已提交'];
  function scan(txt) {
    if (!txt) return '';
    for (let i = 0; i < keys.length; i++) {
      if (txt.indexOf(keys[i]) >= 0) return keys[i];
    }
    return '';
  }
  var body = '';
  try {
    body = (document.body && document.body.innerText) ? document.body.innerText.slice(0, 14000) : '';
  } catch (e) {}
  var hit = scan(body);
  if (hit) return hit;
  try {
    var sels = '[class*="toast"],[class*="Toast"],[class*="message-content"],[class*="Notice"],[class*="semi-toast"],[class*="bui-toast"]';
    var nodes = document.querySelectorAll(sels);
    for (var j = 0; j < Math.min(nodes.length, 48); j++) {
      var n = nodes[j];
      if (!n.offsetParent) continue;
      hit = scan((n.innerText || '').slice(0, 240));
      if (hit) return hit;
    }
  } catch (e2) {}
  return '';
}"""


async def _toutiao_wait_submit_success_toast(page: Any, _step: Callable[..., None], timeout_s: float = 14.0) -> None:
    """确认发布后轮询「提交成功」等文案（toast 消失快，不据此改 ok，仅记录步骤并附用户提示）。"""
    deadline = asyncio.get_running_loop().time() + max(3.0, float(timeout_s))
    while asyncio.get_running_loop().time() < deadline:
        kw = ""
        for fr in _toutiao_graphic_frames_ordered(page):
            try:
                hit = await fr.evaluate(_JS_TOUTIAO_DETECT_SUBMIT_TOAST)
                if hit:
                    kw = str(hit)
                    break
            except Exception:
                continue
        if kw:
            _step(
                "检测到发布提交反馈",
                True,
                detail="页面提示含「%s」" % kw[:24],
                submission_user_hint=_TOUTIAO_SUBMISSION_USER_HINT,
            )
            logger.info("[TOUTIAO-PUBLISH] submit_feedback toast=%s", kw[:40])
            return
        await asyncio.sleep(0.42)
    _step(
        "检测到发布提交反馈",
        False,
        detail="限时内未扫到「提交成功」等提示（可能已消失或未展示）",
        submission_user_hint=_TOUTIAO_SUBMISSION_USER_HINT,
    )


async def _toutiao_post_success_reload_entry(
    page: Any,
    entry_url: str,
    _step: Callable[..., None],
    *,
    branch: str,
) -> None:
    """
    发布已成功闭环后，整页重新进入发布入口 URL。

    头条后台多为 SPA：成功后常仍停在同一 URL，编辑器里仍是上次标题/正文/上传状态；
    若不刷新，下次自动化「已在发布页则 skip goto」时会误判并叠加上一遍内容。
    """
    url = (entry_url or "").strip()
    if not url:
        return
    try:
        logger.info("[TOUTIAO-NAV] post_success_reload branch=%s entry=%s", branch, url[:200])
        await page.goto(url, wait_until="domcontentloaded", timeout=_nav_ms(45000))
        await _delay(1.0, 2.0)
        try:
            await page.wait_for_load_state("networkidle", timeout=_pw_ms(10000))
        except Exception:
            pass
        await _delay(0.35, 0.85)
        _step("发布后重置发布页(便于同会话连续发稿)", True, detail=url[:160])
    except Exception as e:
        logger.warning("[TOUTIAO-NAV] post_success_reload failed branch=%s: %s", branch, e)
        _step(
            "发布后重置发布页(便于同会话连续发稿)",
            False,
            error=str(e)[:200],
            hint="本次发布已成功，仅刷新失败；下次发稿前可手动刷新或关闭页签",
        )


async def _toutiao_after_preview_publish(page: Any, _step: Callable[..., None]) -> bool:
    """主流程点了「预览并发布」后，须等主按钮变为「确认发布」再点，才算真正发出（草稿保存失败 toast 可忽略）。"""
    await asyncio.sleep(3.6)
    if await dismiss_xigua_open_modal(page):
        _step("关闭开通或创作权益引导", True, detail="预览后等确认发布")
        await asyncio.sleep(0.55)
    for i in range(56):
        if i % 4 == 0:
            if await dismiss_xigua_open_modal(page):
                _step("关闭开通或创作权益引导", True, detail="预览后轮询找确认发布", round=i)
                await asyncio.sleep(0.45)
        # 前几轮页面尚在切预览态，阈值略放宽；后期再收紧避免误点侧栏
        if i < 8:
            min_score = 78
        elif i < 22:
            min_score = 86
        else:
            min_score = 72
        label = ""
        for fr in _toutiao_graphic_frames_ordered(page):
            try:
                label = await fr.evaluate(_JS_TOUTIAO_CONFIRM_AFTER_PREVIEW, min_score)
            except Exception:
                label = ""
            if label:
                break
        if not label:
            label = await _toutiao_pw_click_confirm_after_preview(page)
        if label:
            _step("预览后确认发布", True, button=str(label)[:48], round=i)
            await asyncio.sleep(1.0)
            await _toutiao_wait_submit_success_toast(page, _step, timeout_s=14.0)
            return True
        await asyncio.sleep(0.6)
    _step(
        "预览后确认发布",
        False,
        detail="超时未点到「确认发布/确认并发布」（可略增等待；与草稿保存提示无关）",
    )
    return False


_JS_TOUTIAO_VIDEO_PRE_PUBLISH_BUSY = """
() => {
  const b = (document.body && document.body.innerText) || '';
  if (b.includes('上传失败') || b.includes('转码失败') || b.includes('上传出错')) return 'fail';
  if (b.includes('上传中') || b.includes('正在上传') || b.includes('压缩中') || b.includes('转码中')
      || (b.includes('处理中') && b.includes('视频'))) return 'busy';
  return '';
}
"""

# 封面/发布卡住时打日志：看是否仍有弹层、页面 busy、发布类按钮 disabled
_JS_TOUTIAO_STALL_DIAGNOSTIC = """() => {
  const b = ((document.body && document.body.innerText) || '').replace(/\\s+/g, ' ').trim();
  const modals = [];
  try {
    document.querySelectorAll('.m-content, [role="dialog"], .semi-modal-content').forEach((el) => {
      if (!el.offsetParent) return;
      const t = (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 260);
      if (t.length < 3) return;
      if (/封面|上传|完成|确定|下一步|截取|本地上传|编辑|裁剪|海报|模版/.test(t)) modals.push(t);
    });
  } catch (e) {}
  let busyGuess = '';
  if (/上传失败|转码失败|上传出错/.test(b)) busyGuess = 'fail';
  else if (/上传中|正在上传|压缩中|转码中/.test(b)) busyGuess = 'uploading';
  else if (/处理中/.test(b) && /视频/.test(b)) busyGuess = 'processing';
  const pubBtns = [];
  document.querySelectorAll('button').forEach((btn) => {
    if (!btn.offsetParent) return;
    const t = (btn.innerText || '').replace(/\\s+/g, ' ').trim();
    if (!t || t.length > 26) return;
    if (!/发布|发表|预览/.test(t)) return;
    let dis = false;
    try { dis = !!btn.disabled; } catch (e) {}
    pubBtns.push(t + (dis ? '(灰)' : ''));
  });
  return {
    url: (location.href || '').slice(0, 200),
    busyGuess: busyGuess,
    publishButtons: pubBtns.slice(0, 16),
    modalHints: modals.slice(0, 6),
    bodyHead: b.slice(0, 720),
  };
}"""


async def _toutiao_log_stall_diagnostic(page: Any, tag: str) -> None:
    """发布卡住时写入一条结构化日志，便于在 backend.log 搜 TOUTIAO-STALL 定位阶段。"""
    try:
        d = await page.evaluate(_JS_TOUTIAO_STALL_DIAGNOSTIC)
    except Exception as e:
        logger.info("[TOUTIAO-STALL] tag=%s err=%s", tag, str(e)[:220])
        return
    if not isinstance(d, dict):
        logger.info("[TOUTIAO-STALL] tag=%s bad_payload", tag)
        return
    logger.info(
        "[TOUTIAO-STALL] tag=%s url=%s busyGuess=%s publishButtons=%s modalCount=%s bodyHead=%r",
        tag,
        d.get("url", ""),
        d.get("busyGuess", ""),
        d.get("publishButtons", []),
        len(d.get("modalHints") or []),
        ((d.get("bodyHead") or "")[:480]),
    )
    for i, m in enumerate((d.get("modalHints") or [])[:6]):
        logger.info("[TOUTIAO-STALL] tag=%s modal[%s]=%r", tag, i, (m or "")[:300])


async def _toutiao_wait_video_publish_clickable(
    page: Any, timeout_s: int, _step: Callable[..., None]
) -> bool:
    """西瓜/头条视频：填完表后再确认无上传中文案且「发布」类按钮可点。"""
    n = max(30, int(timeout_s))
    idle_rounds = 0
    for i in range(n):
        try:
            st = await page.evaluate(_JS_TOUTIAO_VIDEO_PRE_PUBLISH_BUSY)
        except Exception:
            st = ""
        if st == "fail":
            _step("视频发布前检测到上传/转码失败", False)
            return False
        if st == "busy":
            idle_rounds = 0
            await asyncio.sleep(1)
            continue
        idle_rounds += 1
        try:
            # 西瓜视频页多为「发布」；「预览并发布」放后面以免图文侧栏误匹配（视频路径不依赖此项也可由 idle 兜底）
            for btn_name in ("发布", "立即发布", "发表", "预览并发布"):
                loc = page.get_by_role("button", name=btn_name, exact=True)
                if await loc.count() > 0:
                    try:
                        if await loc.first.is_disabled():
                            await asyncio.sleep(1)
                            continue
                    except Exception:
                        pass
                    _step("视频发布按钮可点击", True, waited=i, button=btn_name)
                    return True
        except Exception:
            pass
        if idle_rounds >= 35:
            _step("视频页已空闲(未匹配标准发布按钮，交由后续 JS 扫描)", True, fallback=True)
            return True
        await asyncio.sleep(1)
    _step("视频发布按钮等待超时", False)
    await _toutiao_log_stall_diagnostic(page, "video_publish_button_timeout")
    return False


async def _toutiao_graphic_current_cover_mode_label(page: Any) -> str:
    """
    头条 Byte Radio：选中态画在 div.byte-radio-inner.checked 上，与原生 input:checked 未必同步。
    返回当前选中项文案（单图 / 三图 / 无封面），无法读取时为空串。
    """
    js = r"""() => {
      const group = document.querySelector(".article-cover-radio-group");
      if (!group) return "";
      const checked = group.querySelector(".byte-radio-inner.checked");
      if (checked) {
        const wrap = checked.parentElement;
        const sp = wrap && wrap.querySelector(".byte-radio-inner-text");
        if (sp) return (sp.textContent || "").replace(/\s+/g, " ").trim();
      }
      const inp = group.querySelector("input[type=\"radio\"]:checked");
      if (inp) {
        const lb = inp.closest("label");
        const sp = lb && lb.querySelector(".byte-radio-inner-text");
        if (sp) return (sp.textContent || "").replace(/\s+/g, " ").trim();
      }
      return "";
    }"""
    for fr in _toutiao_graphic_frames_ordered(page):
        try:
            t = await fr.evaluate(js)
            if (t or "").strip():
                return str(t).strip()
        except Exception:
            continue
    try:
        t = await page.evaluate(js)
        return (t or "").strip()
    except Exception:
        return ""


async def _toutiao_click_article_cover_radio_value(page: Any, value: str) -> bool:
    """
    图文封面 Byte 单选容器：div.article-cover-radio-group（pgc-radio）
    单图 value=2，三图 value=3，无封面 value=1。
    须优先点 label 内 div.byte-radio-inner（选中态由该类控制）；只点 label/input 常不切换 UI。
    """
    sel_label = (
        f'.article-cover-radio-group label.byte-radio:has(input[type="radio"][value="{value}"])'
    )
    for fr in _toutiao_graphic_frames_ordered(page):
        try:
            loc = fr.locator(sel_label)
            if await loc.count() == 0:
                continue
            await loc.first.scroll_into_view_if_needed(timeout=5000)
            inner = loc.first.locator("div.byte-radio-inner")
            if await inner.count() > 0:
                try:
                    await inner.first.click(timeout=4500)
                    logger.info("[TOUTIAO-GRAPHIC] article-cover-radio byte-inner value=%s", value)
                    return True
                except Exception as e:
                    logger.debug("[TOUTIAO-GRAPHIC] byte-inner fail value=%s: %s", value, e)
            try:
                await loc.first.click(timeout=4500)
                logger.info("[TOUTIAO-GRAPHIC] article-cover-radio-group label value=%s", value)
                return True
            except Exception as e:
                logger.debug("[TOUTIAO-GRAPHIC] label click value=%s: %s", value, e)
        except Exception as e:
            logger.debug("[TOUTIAO-GRAPHIC] cover-radio value=%s: %s", value, e)
    try:
        loc = page.locator(
            f'.article-cover-radio-group input[type="radio"][value="{value}"]'
        )
        if await loc.count() > 0:
            await loc.first.scroll_into_view_if_needed(timeout=4000)
            await loc.first.click(timeout=4000)
            logger.info("[TOUTIAO-GRAPHIC] article-cover-radio input value=%s", value)
            return True
    except Exception as e:
        logger.debug("[TOUTIAO-GRAPHIC] cover-radio input: %s", e)
    return False


async def _toutiao_pw_click_byte_radio_inner_text(page: Any, text: str) -> bool:
    """点选 Byte Design 单选项：文案在 .byte-radio-inner-text（与无封面/单图封面共用）。"""
    try:
        exact = re.compile("^" + re.escape(text) + "$")
        scoped = page.locator(".article-cover-radio-group").locator(".byte-radio-inner-text").filter(
            has_text=exact
        )
        sn = await scoped.count()
        if sn > 0:
            for i in range(min(sn, 4)):
                sp = scoped.nth(i)
                try:
                    lab = sp.locator("xpath=ancestor::label[1]")
                    if await lab.count() > 0:
                        await lab.first.scroll_into_view_if_needed()
                        await asyncio.sleep(0.12)
                        inner = lab.first.locator("div.byte-radio-inner")
                        if await inner.count() > 0:
                            await inner.first.click(timeout=4000)
                        else:
                            await lab.first.click(timeout=4000)
                        logger.info("[TOUTIAO] byte-radio scoped article-cover-radio-group %s", text)
                        return True
                except Exception:
                    continue
        inner = page.locator(".byte-radio-inner-text").filter(has_text=exact)
        n = await inner.count()
        if n == 0:
            inner = page.locator("span.byte-radio-inner-text").filter(has_text=exact)
            n = await inner.count()
        for i in range(min(n, 8)):
            sp = inner.nth(i)
            for parent_sel in (
                "xpath=ancestor::label[1]",
                "xpath=ancestor::*[contains(@class,'byte-radio')][1]",
                "xpath=ancestor::*[@role='radio'][1]",
            ):
                try:
                    par = sp.locator(parent_sel)
                    if await par.count() > 0:
                        await par.first.scroll_into_view_if_needed()
                        await asyncio.sleep(0.15)
                        if parent_sel == "xpath=ancestor::label[1]":
                            inner2 = par.first.locator("div.byte-radio-inner")
                            if await inner2.count() > 0:
                                await inner2.first.click(timeout=4000)
                            else:
                                await par.first.click(timeout=4000)
                        else:
                            await par.first.click(timeout=4000)
                        return True
                except Exception:
                    continue
            try:
                await sp.scroll_into_view_if_needed()
                await asyncio.sleep(0.15)
                await sp.click(timeout=4000)
                return True
            except Exception:
                continue
    except Exception as e:
        logger.debug("[TOUTIAO] byte-radio click %s: %s", text, e)
    return False


async def _toutiao_graphic_scroll_cover_block_into_view(page: Any) -> None:
    """把「展示封面 / 封面」区域滚进视口，便于点单图、无封面等选项。"""
    try:
        await page.evaluate(
            """() => {
          const markers = ['展示封面', '封面设置', '添加封面', '封面'];
          const nodes = document.querySelectorAll('div, span, section, label');
          for (const el of nodes) {
            const t = (el.innerText || '').slice(0, 100);
            if (!t || t.length > 90) continue;
            if (markers.some((m) => t.includes(m))) {
              try { el.scrollIntoView({ block: 'center', inline: 'nearest' }); return true; } catch (e) {}
            }
          }
          const sp = document.querySelector('.byte-radio-inner-text');
          if (sp) { try { sp.scrollIntoView({ block: 'center' }); } catch (e) {} return true; }
          return false;
        }"""
        )
        await asyncio.sleep(0.4)
    except Exception:
        pass


async def _toutiao_graphic_ensure_single_image_cover_mode(page: Any, _step: Callable[..., None]) -> None:
    """
    探测结论：默认状态下 light/shadow 均无 file input；须先选「单图」封面模式才会挂载上传控件。
    """
    await _toutiao_graphic_scroll_cover_block_into_view(page)
    clicked = ""
    if await _toutiao_click_article_cover_radio_value(page, "2"):
        clicked = "article-cover-radio:value=2:单图"
    if not clicked:
        for kw in ("单图",):
            if await _toutiao_pw_click_byte_radio_inner_text(page, kw):
                clicked = f"byte-radio:{kw}"
                break
    if not clicked:
        try:
            loc = page.get_by_text("单图", exact=True)
            if await loc.count() > 0:
                await loc.first.scroll_into_view_if_needed()
                await loc.first.click(timeout=4000, force=True)
                clicked = "pw-text:单图"
        except Exception as e:
            logger.debug("[TOUTIAO-GRAPHIC] get_by_text 单图: %s", e)
    if not clicked:
        clicked = await page.evaluate(
            r"""
            () => {
              const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
              const tryClick = (el) => {
                try {
                  el.scrollIntoView({ block: 'center', inline: 'nearest' });
                  el.click();
                  return true;
                } catch (e) {
                  return false;
                }
              };
              const isSingle = (t) => {
                t = norm(t);
                if (!t || t.length > 12) return false;
                if (t.includes('三图') || t.includes('无封面') || t.includes('无图')) return false;
                return t === '单图' || /^单图\b/.test(t);
              };
              const spans = Array.from(
                document.querySelectorAll('.byte-radio-inner-text, span.byte-radio-inner-text')
              );
              for (const sp of spans) {
                const t = norm(sp.textContent || '');
                if (!isSingle(t)) continue;
                let el =
                  sp.closest('label') ||
                  sp.closest('.byte-radio') ||
                  sp.closest('[role="radio"]') ||
                  sp.parentElement;
                while (el && el !== document.body) {
                  if (el.offsetParent || el.getClientRects().length) {
                    if (tryClick(el)) return 'js-byte:' + t;
                    break;
                  }
                  el = el.parentElement;
                }
              }
              return "";
            }
            """
        )
    if clicked:
        _step("封面模式选单图", True, ui=str(clicked))
        logger.info("[TOUTIAO-GRAPHIC] single_image_cover_mode %s", clicked)
    else:
        _step("封面模式选单图", False, detail="未点到单图，将仍尝试滚动与上传")
        logger.warning("[TOUTIAO-GRAPHIC] single_image_cover_mode not clicked")
    await asyncio.sleep(0.65)


async def _toutiao_try_switch_no_image_cover_mode(page: Any, _step: Callable[..., None]) -> None:
    """
    无封面发文：头条「封面展示」多为 Byte Design 单选，文案在 span.byte-radio-inner-text
    （单图/三图/无封面）；原生 radio 常隐藏，不能只点 input[type=radio]。
    """
    clicked = ""

    try:
        await page.evaluate(
            """
            () => {
              const markers = ['封面', '封面设置', '展示封面', '添加封面'];
              const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT, null);
              let n;
              while ((n = walk.nextNode())) {
                const t = (n.innerText || '').slice(0, 80);
                if (t && markers.some((m) => t.includes(m)) && t.length < 100) {
                  try { n.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (e) {}
                  return true;
                }
              }
              const sp = document.querySelector('.byte-radio-inner-text');
              if (sp) { try { sp.scrollIntoView({ block: 'center' }); } catch (e) {} return true; }
              return false;
            }
            """
        )
        await asyncio.sleep(0.35)
    except Exception:
        pass

    if await _toutiao_click_article_cover_radio_value(page, "1"):
        clicked = "article-cover-radio:value=1:无封面"
    if not clicked:
        for kw in ("无封面", "无图"):
            if await _toutiao_pw_click_byte_radio_inner_text(page, kw):
                clicked = f"pw-byte-radio:{kw}"
                break

    if not clicked:
        try:
            loc = page.get_by_text("无封面", exact=True)
            if await loc.count() > 0:
                await loc.first.scroll_into_view_if_needed()
                await loc.first.click(timeout=4000, force=True)
                clicked = "pw-text:无封面"
        except Exception as e:
            logger.debug("[TOUTIAO-NO-COVER] get_by_text 无封面: %s", e)

    if not clicked:
        clicked = await page.evaluate(
            r"""
            () => {
              const tryClick = (el) => {
                try {
                  el.scrollIntoView({ block: 'center', inline: 'nearest' });
                  el.click();
                  return true;
                } catch (e) {
                  return false;
                }
              };
              const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
              const isTarget = (t) => {
                t = norm(t);
                if (!t || t.length > 16) return false;
                if (/单图|^三图|^多图|封面图/.test(t)) return false;
                return (
                  t === '无封面' || t === '无图' || /^无封面|^无图/.test(t)
                  || (t.includes('无封面') && t.length <= 10)
                  || (t.includes('无图') && t.length <= 8)
                );
              };
              const spans = Array.from(
                document.querySelectorAll('.byte-radio-inner-text, span.byte-radio-inner-text')
              );
              for (const sp of spans) {
                const t = norm(sp.textContent || '');
                if (!isTarget(t)) continue;
                let el =
                  sp.closest('label') ||
                  sp.closest('.byte-radio') ||
                  sp.closest('[role="radio"]') ||
                  sp.parentElement;
                while (el && el !== document.body) {
                  if (el.offsetParent || el.getClientRects().length) {
                    if (tryClick(el)) return 'js-byte:' + t.slice(0, 10);
                    break;
                  }
                  el = el.parentElement;
                }
              }
              const inputs = Array.from(document.querySelectorAll('input[type="radio"]'));
              for (const inp of inputs) {
                const id = inp.getAttribute('id');
                if (!id) continue;
                let lb = null;
                try {
                  lb = document.querySelector('label[for="' + id.replace(/"/g, '') + '"]');
                } catch (e) {
                  lb = null;
                }
                if (!lb) continue;
                const tt = norm(lb.innerText || '');
                if (isTarget(tt) && lb.offsetParent && tryClick(lb)) return 'js-label:' + tt.slice(0, 14);
              }
              for (const r of inputs) {
                const lab = r.closest('label');
                const tt = norm((lab && lab.innerText) || r.getAttribute('aria-label') || '');
                if (isTarget(tt)) {
                  const clickEl = (lab && lab.offsetParent) ? lab : r;
                  if (tryClick(clickEl)) return 'js-radio-wrap:' + tt.slice(0, 14);
                }
              }
              return "";
            }
            """
        )

    if not clicked:
        for txt in ("不上传封面", "无需封面", "不要封面"):
            try:
                loc = page.get_by_text(txt, exact=True)
                if await loc.count() > 0:
                    await loc.first.scroll_into_view_if_needed()
                    await loc.first.click(timeout=3000, force=True)
                    clicked = f"pw:{txt}"
                    break
            except Exception:
                continue

    if not clicked:
        try:
            loc = page.get_by_role("radio", name="无封面", exact=True)
            if await loc.count() > 0:
                await loc.first.scroll_into_view_if_needed()
                await loc.first.click(timeout=3000, force=True)
                clicked = "role:radio:无封面"
        except Exception:
            pass

    await asyncio.sleep(0.45)
    mode_lbl = await _toutiao_graphic_current_cover_mode_label(page)
    # Byte Radio：点 label 有时不更新 .checked，再点一次「无封面」项内的圆点区域
    if mode_lbl not in ("无封面", "无图") and "无封面" not in mode_lbl:
        logger.warning(
            "[TOUTIAO-GRAPHIC] 无封面点击后 UI 仍为 %r，尝试仅点 div.byte-radio-inner（value=1）",
            mode_lbl,
        )
        if await _toutiao_click_article_cover_radio_value(page, "1"):
            clicked = (clicked or "") + "+retry-inner"
        await asyncio.sleep(0.35)
        mode_lbl = await _toutiao_graphic_current_cover_mode_label(page)

    if mode_lbl in ("无封面", "无图") or "无封面" in mode_lbl:
        _step(
            "无封面：切换封面/发文模式",
            True,
            ui=str(clicked or "verified"),
            current=mode_lbl,
        )
    elif clicked:
        _step(
            "无封面：切换封面/发文模式",
            False,
            detail=(
                f"已触发点击({clicked})但选中态仍为「{mode_lbl or '?'}」；"
                "头条封面请依赖 div.byte-radio-inner.checked，若仍失败请反馈最新 HTML"
            ),
        )
    else:
        _step(
            "无封面：切换封面/发文模式",
            False,
            detail="未点到无封面（头条封面区多为 .byte-radio-inner-text；请截图或导出 HTML）",
        )

    await asyncio.sleep(0.45)


async def _fill_toutiao_graphic_title(page: Any, title: str, _step: Callable[..., None]) -> bool:
    """只写入主标题区域，避免与正文编辑器混淆。"""
    t = (title or "").strip()[:_TOUTIAO_TITLE_MAX_LEN]
    if not t:
        return False
    via = await page.evaluate(
        """
        (title) => {
          function bump(n) {
            n.dispatchEvent(new Event("input", { bubbles: true }));
            n.dispatchEvent(new Event("change", { bubbles: true }));
          }
          const fields = Array.from(
            document.querySelectorAll('input:not([type="hidden"]):not([type="file"]), textarea')
          ).filter((n) => n.offsetParent);
          for (const n of fields) {
            const ph = (n.getAttribute("placeholder") || "").toString();
            if (!ph.includes("标题")) continue;
            if (ph.includes("副标题")) continue;
            if (ph.includes("正文")) continue;
            n.focus();
            n.value = title;
            bump(n);
            return "input";
          }
          const ces = Array.from(document.querySelectorAll('[contenteditable="true"]')).filter(
            (e) => e.offsetParent
          );
          for (const el of ces) {
            const ph = (
              el.getAttribute("data-placeholder") ||
              el.getAttribute("aria-placeholder") ||
              el.getAttribute("placeholder") ||
              ""
            ).toString();
            if (ph.includes("标题") && !ph.includes("正文")) {
              el.focus();
              el.textContent = title;
              el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText" }));
              return "contenteditable-title";
            }
          }
          return "";
        }
        """,
        t,
    )
    if via:
        _step("填写标题", True, via=via)
        return True
    _step("填写标题", False, error="未匹配到标题输入框（请确认页面为图文发布页）")
    return False


async def _fill_toutiao_graphic_body(page: Any, body: str, _step: Callable[..., None]) -> bool:
    """
    正文写入正文区：禁止用「第一个 contenteditable」——头条常把标题也做成可编辑节点，
    误填会导致长文进标题、保存失败。
    """
    text = (body or "").strip()
    if not text:
        return False
    via = await page.evaluate(
        """
        (body) => {
          function bumpTa(n) {
            n.dispatchEvent(new Event("input", { bubbles: true }));
            n.dispatchEvent(new Event("change", { bubbles: true }));
          }
          const tas = Array.from(document.querySelectorAll("textarea")).filter((n) => n.offsetParent);
          for (const n of tas) {
            const ph = (n.getAttribute("placeholder") || "").toString();
            if (ph.includes("标题") || ph.includes("副标题")) continue;
            if (
              ph.includes("正文") ||
              ph.includes("文章内容") ||
              ph.includes("请输入正文") ||
              (ph.includes("内容") && !ph.includes("标题"))
            ) {
              n.focus();
              n.value = body;
              bumpTa(n);
              return "textarea-body";
            }
          }
          const editables = Array.from(document.querySelectorAll('[contenteditable="true"]')).filter(
            (e) => e.offsetParent
          );
          function titleLike(el) {
            const ph = (
              el.getAttribute("data-placeholder") ||
              el.getAttribute("aria-placeholder") ||
              el.getAttribute("placeholder") ||
              ""
            ).toString();
            if (/标题|概括/.test(ph) && !/正文/.test(ph)) return true;
            const h = el.getBoundingClientRect().height;
            if (editables.length >= 2 && h > 0 && h <= 52) return true;
            return false;
          }
          const bodyCandidates = editables.filter((e) => !titleLike(e));
          const pool = bodyCandidates.length ? bodyCandidates : editables;
          const el = pool.sort(
            (a, b) => b.clientHeight * b.clientWidth - a.clientHeight * a.clientWidth
          )[0];
          if (!el) return "";
          el.focus();
          el.innerHTML = "";
          try {
            document.execCommand("insertText", false, body);
          } catch (e) {
            el.textContent = body;
          }
          el.dispatchEvent(
            new InputEvent("input", { bubbles: true, inputType: "insertText", data: body })
          );
          return "contenteditable-body";
        }
        """,
        text[:5000],
    )
    if via:
        _step("填写正文", True, via=via)
        return True
    _step("填写正文", False, error="未匹配到正文编辑器")
    return False


def _toutiao_extra_options(options: Dict[str, Any]) -> Dict[str, Any]:
    """
    合并头条扩展配置。支持：
    - options[\"toutiao\"] 为 dict 时并入；
    - 顶层 toutiao_* 键（定时任务 / API payload 常用）。

    字段示例：
      ad_declaration: \"no_promo\" | \"is_promo\" | \"auto\" 或单选项文案片段如 \"否\"
      ad_option_text: 与 ad_declaration 等价（旧名 toutiao_ad_radio）
      radios: [{\"group_contains\": \"广告\", \"option_contains\": \"否\"}]
      fills: [{\"placeholder_contains\": \"摘要\", \"text\": \"...\"}]
      checkboxes: [{\"label_contains\": \"原创\", \"checked\": true}]
      clicks: [{\"text_contains\": \"同意\"}]
      graphic_no_cover / no_cover: true（与顶层 toutiao_graphic_no_cover 等价）— 发文章不要封面图
    """
    out: Dict[str, Any] = {}
    inner = options.get("toutiao")
    if isinstance(inner, dict):
        out.update(inner)
    aliases = {
        "toutiao_ad_declaration": "ad_declaration",
        "toutiao_ad_radio": "ad_option_text",
        "toutiao_radios": "radios",
        "toutiao_fills": "fills",
        "toutiao_checkboxes": "checkboxes",
        "toutiao_clicks": "clicks",
        "toutiao_graphic_no_cover": "graphic_no_cover",
    }
    for src, dest in aliases.items():
        v = options.get(src)
        if v is not None:
            out[dest] = v
    return out


async def _image_file_inputs(page: Any) -> List[Any]:
    """可传图片的 file input（排除纯 video）。"""
    out: List[Any] = []
    try:
        for inp in await page.query_selector_all('input[type="file"]'):
            try:
                acc = (await inp.get_attribute("accept")) or ""
                al = acc.lower()
                if "video" in al and "image" not in al:
                    continue
                out.append(inp)
            except Exception:
                continue
    except Exception:
        pass
    return out


async def _image_file_inputs_all_frames(page: Any) -> List[Any]:
    """主文档 + 子 frame 内可传图的 file input（视频封面常在 iframe 或与视频 input 分页）。"""
    out: List[Any] = []
    for fr in page.frames:
        try:
            for inp in await fr.query_selector_all('input[type="file"]'):
                try:
                    acc = (await inp.get_attribute("accept")) or ""
                    al = acc.lower()
                    if "video" in al and "image" not in al:
                        continue
                    out.append(inp)
                except Exception:
                    continue
        except Exception:
            continue
    return out


async def _toutiao_scroll_video_cover_block_into_view(page: Any) -> None:
    """视频封面区常在下部，先滚到视口内再 set_input_files 或点智能封面。"""
    try:
        await page.evaluate(
            """() => {
          const keys = ['视频封面', '上传封面', '添加封面', '封面图', '设置封面', '编辑封面'];
          for (const el of document.querySelectorAll('div, span, label, section, h3, h4')) {
            const t = (el.innerText || '').slice(0, 100);
            if (!t || t.length > 95) continue;
            if (keys.some((k) => t.includes(k))) {
              try { el.scrollIntoView({ block: 'center', inline: 'nearest' }); return true; } catch (e) {}
            }
          }
          window.scrollBy(0, Math.min(400, (window.innerHeight || 700) * 0.5));
          return false;
        }"""
        )
        await asyncio.sleep(0.45)
    except Exception:
        pass


_JS_TOUTIAO_CLICK_VIDEO_FAKE_COVER_UPLOAD = """() => {
  const triggers = document.querySelectorAll('.fake-upload-trigger');
  for (const t of triggers) {
    if (!t.offsetParent) continue;
    const tip = t.querySelector('.trigger-tip');
    const tx = ((tip && tip.textContent) || t.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 48);
    if (tx.includes('上传封面') || tx.includes('添加封面') || (tx.includes('封面') && tx.length < 22)) {
      try {
        t.scrollIntoView({ block: 'center', inline: 'nearest' });
        t.click();
        return 'fake-upload-trigger:' + tx.slice(0, 24);
      } catch (e) {}
    }
  }
  for (const t of triggers) {
    if (!t.offsetParent) continue;
    try {
      t.scrollIntoView({ block: 'center', inline: 'nearest' });
      t.click();
      return 'fake-upload-trigger:any';
    } catch (e) {}
  }
  return '';
}"""

# 实测（probe_toutiao_video_cover.json）：点「上传封面」后出现「封面截取 / 本地上传 / 下一步」，容器多为 .m-content；须再点「本地上传」才有 file chooser 或图片 input。
_JS_TOUTIAO_CLICK_VIDEO_COVER_MODAL_LOCAL_UPLOAD = """() => {
  function inCoverModal(el) {
    let p = el;
    for (let i = 0; i < 14 && p; i++) {
      const tx = (p.innerText || '').slice(0, 120);
      const cn = (p.className && String(p.className)) || '';
      if (tx.includes('封面截取') || cn.includes('m-content')) return true;
      p = p.parentElement;
    }
    return false;
  }
  const candidates = Array.from(
    document.querySelectorAll('[role="tab"], button, .byte-tabs-tab, span, div, a')
  );
  for (const el of candidates) {
    if (!el.offsetParent) continue;
    const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
    if (t !== '本地上传' && !(t.includes('本地上传') && t.length <= 18)) continue;
    if (!inCoverModal(el)) continue;
    try {
      el.scrollIntoView({ block: 'center', inline: 'nearest' });
      el.click();
      return 'cover_modal:本地上传';
    } catch (e) {}
  }
  return '';
}"""

# 点了「本地上传」后需回到「封面截取」标签，否则页面上没有可点的「下一步」（或点在错误层）。
_JS_TOUTIAO_CLICK_VIDEO_COVER_TAB_CROP = """() => {
  function inModal(el) {
    let p = el;
    for (let i = 0; i < 16 && p; i++) {
      const tx = (p.innerText || '').slice(0, 160);
      if (tx.includes('封面截取') && tx.includes('本地上传')) return true;
      p = p.parentElement;
    }
    return false;
  }
  const candidates = Array.from(
    document.querySelectorAll('[role="tab"], .byte-tabs-tab, button, span, div, a')
  );
  for (const el of candidates) {
    if (!el.offsetParent) continue;
    const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
    if (t !== '封面截取' && !(t.includes('封面截取') && t.length <= 14)) continue;
    if (!inModal(el)) continue;
    try {
      el.scrollIntoView({ block: 'center', inline: 'nearest' });
      el.click();
      return 'cover_modal:tab:封面截取';
    } catch (e) {}
  }
  return '';
}"""

# 封面截取弹层：视频帧未就绪时常显示「解析中」，此时「下一步」多为 disabled，须等待后再点。
_JS_TOUTIAO_COVER_MODAL_PARSE_STATE = """() => {
  const roots = Array.from(
    document.querySelectorAll(
      '.m-content, [class*="m-content"], [role="dialog"], .semi-modal-content, .semi-modal-wrapper, .byte-modal, .byte-modal-wrapper, .bui-modal'
    )
  );
  for (const root of roots) {
    if (!root.offsetParent) continue;
    const t = ((root.innerText || '') + '').replace(/\\s+/g, ' ').trim();
    if (t.length < 8 || t.length > 8000) continue;
    const isCover =
      t.includes('封面截取') ||
      (t.includes('本地上传') && t.includes('下一步')) ||
      (t.includes('封面') && t.includes('截取') && t.includes('下一步'));
    if (!isCover) continue;
    if (/解析中|正在解析|加载中|处理中|请稍候|转码中|生成预览|准备中/.test(t)) return 'parsing';
    return 'ready';
  }
  return 'no_modal';
}"""

# 无指定封面：在封面弹层内点「下一步」。优先含「封面截取」的容器，避免命中其它模块的「下一步」；跳过 disabled。
_JS_TOUTIAO_CLICK_VIDEO_COVER_MODAL_NEXT = """() => {
  function isNextLabel(t) {
    t = (t || '').replace(/\\s+/g, ' ').trim();
    if (t === '下一步') return true;
    return /^下一步[\\s>›»]*$/.test(t);
  }
  function isDisabled(el) {
    let n = el;
    for (let i = 0; i < 6 && n; i++) {
      try {
        if (n.disabled) return true;
        if ((n.getAttribute && n.getAttribute('aria-disabled')) === 'true') return true;
        if ((n.getAttribute && n.getAttribute('data-disabled')) === 'true') return true;
      } catch (e) {}
      n = n.parentElement;
    }
    return false;
  }
  const roots = Array.from(
    document.querySelectorAll(
      '.m-content, [class*="m-content"], [role="dialog"], .semi-modal-content, .semi-modal-wrapper, .byte-modal, .byte-modal-wrapper, .bui-modal'
    )
  );
  const scored = [];
  for (const root of roots) {
    if (!root.offsetParent) continue;
    const tx = (root.innerText || '').replace(/\\s+/g, ' ').trim();
    if (!tx.includes('下一步')) continue;
    let score = 0;
    if (tx.includes('封面截取')) score += 24;
    if (tx.includes('本地上传')) score += 6;
    if (tx.length < 1400) score += 2;
    scored.push({ root, score });
  }
  scored.sort((a, b) => b.score - a.score);
  const ordered = scored.length ? scored.map((x) => x.root) : [document.body];
  for (const root of ordered) {
    const candidates = root.querySelectorAll(
      'button, [role="button"], .byte-btn-primary, [class*="btn-primary"], span, div, a'
    );
    for (const el of candidates) {
      if (!el.offsetParent) continue;
      if (isDisabled(el)) continue;
      const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
      if (!isNextLabel(t)) continue;
      try {
        el.scrollIntoView({ block: 'center', inline: 'nearest' });
        el.click();
        return 'cover_modal:下一步';
      } catch (e) {}
    }
  }
  return '';
}"""

# 「封面截取」点下一步后进入封面编辑页：仅在 .m-content 内点文案为「确定」的按钮（跳过仍含「封面截取+本地上传+下一步」的首屏）。
_JS_TOUTIAO_CLICK_VIDEO_COVER_EDITOR_CONFIRM = """() => {
  const roots = Array.from(document.querySelectorAll('.m-content, [class*="m-content"]'));
  for (const root of roots) {
    const rtx = ((root.innerText || '').replace(/\\s+/g, ' ').trim()).slice(0, 500);
    if (!rtx.includes('确定')) continue;
    if (
      rtx.includes('封面截取') &&
      rtx.includes('本地上传') &&
      rtx.includes('下一步') &&
      !rtx.includes('取消')
    ) {
      continue;
    }
    const candidates = root.querySelectorAll(
      'button, [role="button"], .byte-btn-primary, [class*="primary"], span, div'
    );
    for (const el of candidates) {
      if (!el.offsetParent) continue;
      const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
      if (t !== '确定') continue;
      const r = el.getBoundingClientRect();
      if (r.width < 16 || r.height < 8 || r.width > 400) continue;
      try {
        el.scrollIntoView({ block: 'center', inline: 'nearest' });
        el.click();
        return 'cover_editor:确定';
      } catch (e) {}
    }
  }
  return '';
}"""

# 封面编辑第一次「确定」后，常见「确定后无法再编辑」类二次确认，须再点「确定」。
_JS_TOUTIAO_CLICK_VIDEO_COVER_SECOND_CONFIRM = """() => {
  const roots = Array.from(
    document.querySelectorAll(
      '[role="dialog"], .semi-modal-wrapper, .semi-modal-content, .byte-modal, .m-xigua-dialog, .m-content, .bui-modal'
    )
  );
  for (const root of roots) {
    if (!root.offsetParent) continue;
    const tx = ((root.innerText || '').replace(/\\s+/g, ' ').trim()).slice(0, 520);
    if (tx.length < 8 || tx.length > 720) continue;
    if (!/无法编辑|无法继续编辑|不可编辑|不能再编辑|确定后|将无法|温馨提示|是否确定完成|完成后无法/.test(tx)) continue;
    if (/封面编辑/.test(tx) && /模版|贴纸|滤镜|保存为模板/.test(tx)) continue;
    const btns = root.querySelectorAll(
      'button, [role="button"], .btn-sure, .byte-btn-primary, button.m-button.red, .footer button.m-button'
    );
    for (const b of btns) {
      if (!b.offsetParent) continue;
      const bt = (b.innerText || '').replace(/\\s+/g, ' ').trim();
      if (bt !== '确定') continue;
      try {
        b.scrollIntoView({ block: 'center', inline: 'nearest' });
        b.click();
        return 'second_dialog:确定';
      } catch (e) {}
    }
  }
  return '';
}"""

# 选完截取帧/预览后，部分西瓜版本主按钮为「完成」；不点则弹层不收起，后续「确定」点不到。
_JS_TOUTIAO_CLICK_VIDEO_COVER_FLOW_COMPLETE = """() => {
  function isCompleteLabel(t) {
    t = (t || '').replace(/\\s+/g, ' ').trim();
    if (t === '完成') return true;
    return /^完成[\\s›»]*$/.test(t);
  }
  const roots = Array.from(
    document.querySelectorAll('.m-content, [class*="m-content"], [role="dialog"], .semi-modal-content')
  );
  const scored = [];
  for (const root of roots) {
    if (!root.offsetParent) continue;
    const tx = (root.innerText || '').replace(/\\s+/g, ' ').trim();
    if (!tx.includes('完成')) continue;
    if (!/封面|截取|上传|视频|编辑|海报/.test(tx)) continue;
    let score = 0;
    if (tx.includes('封面')) score += 14;
    if (tx.includes('截取')) score += 6;
    if (tx.length < 2000) score += 2;
    scored.push({ root, score });
  }
  scored.sort((a, b) => b.score - a.score);
  for (const { root } of scored) {
    const candidates = root.querySelectorAll(
      'button, [role="button"], .byte-btn-primary, [class*="btn-primary"], .btn-sure, span, div, a'
    );
    for (const el of candidates) {
      if (!el.offsetParent) continue;
      const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
      if (!isCompleteLabel(t)) continue;
      try {
        el.scrollIntoView({ block: 'center', inline: 'nearest' });
        el.click();
        return 'cover_flow:完成';
      } catch (e) {}
    }
  }
  return '';
}"""


async def _toutiao_video_wait_cover_modal_parse_idle(page: Any, timeout_s: float = 120.0) -> str:
    """等待封面截取弹层内「解析中」等文案消失，避免下一步按钮一直为 disabled。返回 ready|no_modal（超时仍 parsing 则返回 parsing）。"""
    deadline = asyncio.get_running_loop().time() + max(5.0, float(timeout_s))
    last = "no_modal"
    while asyncio.get_running_loop().time() < deadline:
        any_ready = False
        any_parsing = False
        for fr in _toutiao_graphic_frames_ordered(page):
            try:
                st = str(await fr.evaluate(_JS_TOUTIAO_COVER_MODAL_PARSE_STATE) or "no_modal")
                if st == "ready":
                    any_ready = True
                    break
                if st == "parsing":
                    any_parsing = True
                last = st
            except Exception:
                continue
        if any_ready:
            return "ready"
        if not any_parsing:
            return "no_modal"
        logger.info("[TOUTIAO-VIDEO-COVER] cover_modal waiting parse_idle (parsing)")
        await asyncio.sleep(0.5)
    logger.warning("[TOUTIAO-VIDEO-COVER] cover_modal parse_idle timeout_s=%s last=%s", timeout_s, last)
    return "parsing" if last == "parsing" else "no_modal"


async def _toutiao_video_click_cover_modal_next(page: Any) -> bool:
    """封面截取页：等待帧解析就绪，再切「封面截取」标签，在弹层内点可点的「下一步」（多轮重试）。"""
    await _toutiao_video_wait_cover_modal_parse_idle(page, timeout_s=120.0)
    for fr in _toutiao_graphic_frames_ordered(page):
        fu = ""
        try:
            fu = (getattr(fr, "url", None) or "")[:90]
        except Exception:
            pass
        for attempt in range(16):
            if attempt:
                await asyncio.sleep(0.55)
            try:
                st = await fr.evaluate(_JS_TOUTIAO_COVER_MODAL_PARSE_STATE)
                if st == "parsing":
                    await asyncio.sleep(0.65)
                    continue
            except Exception:
                pass
            try:
                tab_hit = await fr.evaluate(_JS_TOUTIAO_CLICK_VIDEO_COVER_TAB_CROP)
                if tab_hit:
                    logger.info("[TOUTIAO-VIDEO-COVER] %s frame=%s attempt=%s", tab_hit, fu, attempt)
                    await asyncio.sleep(0.4)
            except Exception as e:
                logger.debug("[TOUTIAO-VIDEO-COVER] tab_crop err frame=%s: %s", fu, e)
            shell_selectors = (
                ".m-content",
                ".semi-modal-content",
                "[class*='semi-modal-content']",
                "[role='dialog']",
            )
            for shell_sel in shell_selectors:
                try:
                    shells = fr.locator(shell_sel).filter(has_text="封面截取")
                    sn = await shells.count()
                    for si in range(min(sn, 5)):
                        shell = shells.nth(si)
                        if not await shell.is_visible():
                            continue
                        try:
                            role_next = shell.get_by_role("button", name="下一步", exact=True)
                            rn = await role_next.count()
                            for j in range(min(rn, 6)):
                                btn = role_next.nth(j)
                                if not await btn.is_visible():
                                    continue
                                try:
                                    if await btn.is_disabled():
                                        continue
                                except Exception:
                                    pass
                                try:
                                    await btn.click(timeout=5500)
                                    logger.info(
                                        "[TOUTIAO-VIDEO-COVER] cover_modal pw %s 下一步 role btn j=%s frame=%s a=%s",
                                        shell_sel,
                                        j,
                                        fu,
                                        attempt,
                                    )
                                    return True
                                except Exception:
                                    continue
                        except Exception:
                            pass
                        for exact in (True, False):
                            nx = shell.get_by_text("下一步", exact=exact)
                            n = await nx.count()
                            for i in range(min(n, 8)):
                                el = nx.nth(i)
                                if not await el.is_visible():
                                    continue
                                skip_dis = False
                                for xp in (
                                    "xpath=ancestor-or-self::button[1]",
                                    "xpath=ancestor-or-self::*[@role=\"button\"][1]",
                                ):
                                    try:
                                        anc = el.locator(xp)
                                        if await anc.count() > 0:
                                            try:
                                                if await anc.first.is_disabled():
                                                    skip_dis = True
                                            except Exception:
                                                pass
                                            break
                                    except Exception:
                                        pass
                                if skip_dis:
                                    continue
                                try:
                                    await el.click(timeout=5500)
                                except Exception:
                                    continue
                                logger.info(
                                    "[TOUTIAO-VIDEO-COVER] cover_modal pw 封面截取壳 下一步 i=%s exact=%s sel=%s frame=%s a=%s",
                                    i,
                                    exact,
                                    shell_sel,
                                    fu,
                                    attempt,
                                )
                                return True
                except Exception as e:
                    logger.debug(
                        "[TOUTIAO-VIDEO-COVER] cover_modal next pw shell err sel=%s frame=%s: %s",
                        shell_sel,
                        fu,
                        e,
                    )
            try:
                for shell_sel in (".m-content", ".semi-modal-content", "[role='dialog']"):
                    shell = fr.locator(shell_sel).first
                    if await shell.count() == 0:
                        continue
                    if not await shell.is_visible():
                        continue
                    rtx = ""
                    try:
                        rtx = ((await shell.inner_text()) or "").replace("\n", " ").strip()[:800]
                    except Exception:
                        pass
                    if "下一步" not in rtx or ("封面截取" not in rtx and "本地上传" not in rtx):
                        continue
                    for exact in (True, False):
                        role_next = shell.get_by_role("button", name="下一步", exact=True)
                        rn = await role_next.count()
                        for j in range(min(rn, 6)):
                            btn = role_next.nth(j)
                            if not await btn.is_visible():
                                continue
                            try:
                                if await btn.is_disabled():
                                    continue
                            except Exception:
                                pass
                            try:
                                await btn.click(timeout=5500)
                                logger.info(
                                    "[TOUTIAO-VIDEO-COVER] cover_modal pw %s.first role下一步 j=%s frame=%s a=%s",
                                    shell_sel,
                                    j,
                                    fu,
                                    attempt,
                                )
                                return True
                            except Exception:
                                continue
                        nx = shell.get_by_text("下一步", exact=exact)
                        n = await nx.count()
                        for i in range(min(n, 6)):
                            el = nx.nth(i)
                            if not await el.is_visible():
                                continue
                            skip_dis = False
                            for xp in (
                                "xpath=ancestor-or-self::button[1]",
                                "xpath=ancestor-or-self::*[@role=\"button\"][1]",
                            ):
                                try:
                                    anc = el.locator(xp)
                                    if await anc.count() > 0:
                                        try:
                                            if await anc.first.is_disabled():
                                                skip_dis = True
                                        except Exception:
                                            pass
                                        break
                                except Exception:
                                    pass
                            if skip_dis:
                                continue
                            try:
                                await el.click(timeout=5500)
                            except Exception:
                                continue
                            logger.info(
                                "[TOUTIAO-VIDEO-COVER] cover_modal pw %s.first 下一步 i=%s exact=%s frame=%s a=%s",
                                shell_sel,
                                i,
                                exact,
                                fu,
                                attempt,
                            )
                            return True
            except Exception as e:
                logger.debug("[TOUTIAO-VIDEO-COVER] cover_modal next pw err frame=%s: %s", fu, e)
            try:
                hit = await fr.evaluate(_JS_TOUTIAO_CLICK_VIDEO_COVER_MODAL_NEXT)
                if hit:
                    logger.info("[TOUTIAO-VIDEO-COVER] %s frame=%s a=%s", hit, fu, attempt)
                    return True
            except Exception as e:
                logger.debug("[TOUTIAO-VIDEO-COVER] cover_modal next js err frame=%s: %s", fu, e)
            try:
                loc = fr.get_by_role("button", name="下一步")
                if await loc.count() > 0:
                    for i in range(min(await loc.count(), 8)):
                        el = loc.nth(i)
                        if not await el.is_visible():
                            continue
                        try:
                            if await el.is_disabled():
                                continue
                        except Exception:
                            pass
                        try:
                            await el.click(timeout=5500)
                        except Exception:
                            continue
                        logger.info(
                            "[TOUTIAO-VIDEO-COVER] cover_modal pw role=下一步 i=%s frame=%s a=%s",
                            i,
                            fu,
                            attempt,
                        )
                        return True
            except Exception:
                pass
    return False


async def _toutiao_video_click_cover_editor_confirm(page: Any) -> bool:
    """封面编辑（裁剪）页底部「确定」，完成选封面。"""
    for fr in _toutiao_graphic_frames_ordered(page):
        fu = ""
        try:
            fu = (getattr(fr, "url", None) or "")[:90]
        except Exception:
            pass
        try:
            shell = fr.locator(".m-content").first
            if await shell.count() > 0 and await shell.is_visible():
                rtx = ""
                try:
                    rtx = ((await shell.inner_text()) or "").replace("\n", " ").strip()[:500]
                except Exception:
                    pass
                skip_confirm = (
                    "封面截取" in rtx
                    and "本地上传" in rtx
                    and "下一步" in rtx
                    and "取消" not in rtx
                )
                if not skip_confirm:
                    try:
                        s0 = shell.locator("button.btn-sure, .footer-btns .btn-sure")
                        if await s0.count() > 0:
                            await s0.first.click(timeout=5000)
                            logger.info(
                                "[TOUTIAO-VIDEO-COVER] cover_editor pw .btn-sure frame=%s",
                                fu,
                            )
                            return True
                    except Exception:
                        pass
                    for exact in (True, False):
                        ok = shell.get_by_text("确定", exact=exact)
                        n = await ok.count()
                        for i in range(min(n, 6)):
                            el = ok.nth(i)
                            if not await el.is_visible():
                                continue
                            await el.click(timeout=5000)
                            logger.info(
                                "[TOUTIAO-VIDEO-COVER] cover_editor pw .m-content 确定 i=%s exact=%s frame=%s",
                                i,
                                exact,
                                fu,
                            )
                            return True
                    btn = shell.get_by_role("button", name="确定", exact=True)
                    if await btn.count() > 0:
                        await btn.first.click(timeout=5000)
                        logger.info("[TOUTIAO-VIDEO-COVER] cover_editor pw role=确定 frame=%s", fu)
                        return True
        except Exception as e:
            logger.debug("[TOUTIAO-VIDEO-COVER] cover_editor pw err frame=%s: %s", fu, e)
        try:
            hit = await fr.evaluate(_JS_TOUTIAO_CLICK_VIDEO_COVER_EDITOR_CONFIRM)
            if hit:
                logger.info("[TOUTIAO-VIDEO-COVER] %s frame=%s", hit, fu)
                return True
        except Exception as e:
            logger.debug("[TOUTIAO-VIDEO-COVER] cover_editor confirm js err frame=%s: %s", fu, e)
    return False


async def _toutiao_video_click_cover_second_confirm_if_present(page: Any) -> bool:
    """第一次确定后的「无法再编辑」等二次弹窗：再点「确定」。无弹窗则返回 False。"""
    await asyncio.sleep(0.5)
    for fr in _toutiao_graphic_frames_ordered(page):
        fu = ""
        try:
            fu = (getattr(fr, "url", None) or "")[:90]
        except Exception:
            pass
        try:
            hit = await fr.evaluate(_JS_TOUTIAO_CLICK_VIDEO_COVER_SECOND_CONFIRM)
            if hit:
                logger.info("[TOUTIAO-VIDEO-COVER] %s frame=%s", hit, fu)
                return True
        except Exception as e:
            logger.debug("[TOUTIAO-VIDEO-COVER] second_confirm js err frame=%s: %s", fu, e)
        for hint in ("完成后无法", "无法继续编辑", "是否确定完成", "无法编辑", "确定后", "不能再编辑"):
            for sel in ('[role="dialog"]', ".semi-modal-wrapper", ".byte-modal-wrapper", ".m-xigua-dialog"):
                try:
                    box = fr.locator(sel).filter(has_text=hint)
                    if await box.count() <= 0:
                        continue
                    if not await box.first.is_visible():
                        continue
                    btn = box.first.get_by_role("button", name="确定", exact=True)
                    if await btn.count() > 0:
                        await btn.first.click(timeout=4500)
                        logger.info(
                            "[TOUTIAO-VIDEO-COVER] second_confirm pw hint=%s sel=%s frame=%s",
                            hint,
                            sel[:28],
                            fu,
                        )
                        return True
                except Exception:
                    continue
    return False


async def _toutiao_video_click_cover_flow_complete_if_present(page: Any) -> bool:
    """封面相关弹层内主 CTA 为「完成」时点击（选帧/预览后收起半层）。"""
    for fr in _toutiao_graphic_frames_ordered(page):
        fu = ""
        try:
            fu = (getattr(fr, "url", None) or "")[:90]
        except Exception:
            pass
        for has_kw in ("封面", "截取"):
            try:
                shells = fr.locator(".m-content").filter(has_text=has_kw)
                sn = await shells.count()
                for si in range(min(sn, 5)):
                    shell = shells.nth(si)
                    if not await shell.is_visible():
                        continue
                    for exact in (True, False):
                        loc = shell.get_by_text("完成", exact=exact)
                        n = await loc.count()
                        for i in range(min(n, 8)):
                            el = loc.nth(i)
                            if not await el.is_visible():
                                continue
                            try:
                                await el.click(timeout=5000)
                            except Exception:
                                continue
                            logger.info(
                                "[TOUTIAO-VIDEO-COVER] cover_flow pw 完成 shell_kw=%s i=%s exact=%s frame=%s",
                                has_kw,
                                i,
                                exact,
                                fu,
                            )
                            return True
            except Exception as e:
                logger.debug(
                    "[TOUTIAO-VIDEO-COVER] cover_flow 完成 pw err kw=%s frame=%s: %s",
                    has_kw,
                    fu,
                    e,
                )
        try:
            hit = await fr.evaluate(_JS_TOUTIAO_CLICK_VIDEO_COVER_FLOW_COMPLETE)
            if hit:
                logger.info("[TOUTIAO-VIDEO-COVER] %s frame=%s", hit, fu)
                return True
        except Exception as e:
            logger.debug("[TOUTIAO-VIDEO-COVER] cover_flow 完成 js err frame=%s: %s", fu, e)
    return False


async def _toutiao_video_click_cover_modal_local_upload(page: Any) -> bool:
    """视频封面弹层内点击「本地上传」（与 UI 探测 m-content / 封面截取 一致）。"""
    for fr in _toutiao_graphic_frames_ordered(page):
        fu = ""
        try:
            fu = (getattr(fr, "url", None) or "")[:90]
        except Exception:
            pass
        try:
            hit = await fr.evaluate(_JS_TOUTIAO_CLICK_VIDEO_COVER_MODAL_LOCAL_UPLOAD)
            if hit:
                logger.info("[TOUTIAO-VIDEO-COVER] cover_modal %s frame=%s", hit, fu)
                return True
        except Exception as e:
            logger.debug("[TOUTIAO-VIDEO-COVER] cover_modal js err frame=%s: %s", fu, e)
        try:
            tab = fr.get_by_role("tab", name="本地上传")
            if await tab.count() > 0 and await tab.first.is_visible():
                await tab.first.click(timeout=5000)
                logger.info("[TOUTIAO-VIDEO-COVER] cover_modal role=tab 本地上传 frame=%s", fu)
                return True
        except Exception:
            pass
        try:
            locs = fr.get_by_text("本地上传", exact=False)
            n = await locs.count()
            for i in range(min(n, 10)):
                el = locs.nth(i)
                if not await el.is_visible():
                    continue
                tx = ((await el.inner_text()) or "").strip().replace("\n", " ")
                if len(tx) > 20:
                    continue
                await el.scroll_into_view_if_needed(timeout=4000)
                await el.click(timeout=5000)
                logger.info("[TOUTIAO-VIDEO-COVER] cover_modal pw 本地上传 i=%s frame=%s", i, fu)
                return True
        except Exception:
            continue
    return False


async def _toutiao_video_try_activate_cover_upload(page: Any, _step: Callable[..., None]) -> bool:
    """视频封面区：先点 .fake-upload-trigger（内为「上传封面」）才会挂出/激活 file input（与图文类似）。"""
    frames = _toutiao_graphic_frames_ordered(page)
    for fr in frames:
        fu = ""
        try:
            fu = (getattr(fr, "url", None) or "")[:90]
        except Exception:
            pass
        try:
            hit = await fr.evaluate(_JS_TOUTIAO_CLICK_VIDEO_FAKE_COVER_UPLOAD)
            if hit:
                logger.info("[TOUTIAO-VIDEO-COVER] js_click %s frame=%s", hit, fu)
                _step("点击视频封面上传入口", True, via=str(hit)[:40])
                return True
        except Exception as e:
            logger.debug("[TOUTIAO-VIDEO-COVER] js_click err frame=%s: %s", fu, e)
        try:
            loc0 = fr.locator(".fake-upload-trigger")
            if await loc0.count() > 0:
                loc = loc0.first
                await loc.scroll_into_view_if_needed(timeout=5000)
                if await loc.is_visible():
                    await loc.click(timeout=5000)
                    logger.info("[TOUTIAO-VIDEO-COVER] pw .fake-upload-trigger frame=%s", fu)
                    _step("点击视频封面上传入口", True, via="playwright:.fake-upload-trigger")
                    return True
        except Exception as e:
            logger.debug("[TOUTIAO-VIDEO-COVER] pw fake-upload-trigger frame=%s: %s", fu, e)
        for text in ("上传封面", "添加封面"):
            try:
                locs = fr.get_by_text(text, exact=False)
                n = await locs.count()
                for i in range(min(n, 10)):
                    el = locs.nth(i)
                    if not await el.is_visible():
                        continue
                    tx = ((await el.inner_text()) or "").strip().replace("\n", " ")
                    if len(tx) > 42:
                        continue
                    await el.scroll_into_view_if_needed(timeout=5000)
                    await el.click(timeout=5000)
                    logger.info("[TOUTIAO-VIDEO-COVER] pw text=%s i=%s frame=%s", text, i, fu)
                    _step("点击视频封面上传入口", True, via=f"text:{text}")
                    return True
            except Exception:
                continue
    logger.info("[TOUTIAO-VIDEO-COVER] try_activate no_hit")
    return False


async def _fill_toutiao_video_synopsis(page: Any, text: str, _step: Callable[..., None]) -> bool:
    """上传成功后作品简介/描述：textarea 或 contenteditable，避免与标题框混淆。"""
    t = (text or "").strip()
    if not t:
        return False
    via = await page.evaluate(
        """
        (body) => {
          function bump(n) {
            n.dispatchEvent(new Event("input", { bubbles: true }));
            n.dispatchEvent(new Event("change", { bubbles: true }));
          }
          const tas = Array.from(document.querySelectorAll("textarea")).filter((n) => n.offsetParent);
          for (const n of tas) {
            const ph = (n.getAttribute("placeholder") || "").toString();
            if (ph.includes("标题") || ph.includes("概括")) continue;
            if (
              ph.includes("简介") ||
              ph.includes("描述") ||
              ph.includes("摘要") ||
              ph.includes("介绍") ||
              (ph.includes("说明") && !ph.includes("标题")) ||
              (ph.includes("作品") && ph.includes("内容"))
            ) {
              n.focus();
              n.value = body;
              bump(n);
              return "textarea-synopsis";
            }
          }
          const editables = Array.from(document.querySelectorAll('[contenteditable="true"]')).filter(
            (e) => e.offsetParent
          );
          for (const el of editables) {
            const ph = (
              el.getAttribute("data-placeholder") ||
              el.getAttribute("aria-placeholder") ||
              el.getAttribute("placeholder") ||
              ""
            ).toString();
            if (/标题|概括/.test(ph) && !/简介|描述|正文|内容/.test(ph)) continue;
            if (/简介|描述|摘要|正文|内容|介绍|说明/.test(ph)) {
              el.focus();
              el.innerHTML = "";
              try {
                document.execCommand("insertText", false, body);
              } catch (e) {
                el.textContent = body;
              }
              el.dispatchEvent(
                new InputEvent("input", { bubbles: true, inputType: "insertText", data: body })
              );
              return "ce-synopsis";
            }
          }
          const bySize = editables.sort(
            (a, b) => b.clientHeight * b.clientWidth - a.clientHeight * a.clientWidth
          );
          const big = bySize.find((e) => e.getBoundingClientRect().height > 100);
          if (big) {
            big.focus();
            try {
              document.execCommand("insertText", false, body);
            } catch (e) {
              big.textContent = body;
            }
            big.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText" }));
            return "ce-largest";
          }
          return "";
        }
        """,
        t[:5000],
    )
    if via:
        _step("填写视频简介", True, via=via)
        return True
    _step("填写视频简介", False, detail="未匹配简介框，可仅标题发布")
    return False


async def _toutiao_video_finalize_cover_editor_modal(
    page: Any,
    _step: Callable[..., None],
    *,
    pre_sleep_s: float = 0.65,
) -> bool:
    """
    封面弹层：必要时先点「完成」；再点封面编辑「确定」+ 二次「确定」；最后再尝试「完成」收尾。
    pre_sleep_s：进入编辑页后的等待（本地上传后若已有 sleep 可设小一些）。
    """
    if pre_sleep_s > 0:
        await asyncio.sleep(pre_sleep_s)
    did_complete = False
    for _ in range(3):
        if await _toutiao_video_click_cover_flow_complete_if_present(page):
            if not did_complete:
                _step("封面流程-完成", True)
                did_complete = True
            await asyncio.sleep(0.42)
    ok_editor = await _toutiao_video_click_cover_editor_confirm(page)
    if ok_editor:
        _step("封面编辑-确定", True)
        await asyncio.sleep(0.55)
        if await _toutiao_video_click_cover_second_confirm_if_present(page):
            _step("封面编辑-二次确认确定", True)
        for _ in range(2):
            if await _toutiao_video_click_cover_flow_complete_if_present(page):
                _step("封面流程-完成(收尾)", True)
                await asyncio.sleep(0.35)
        return True
    if did_complete:
        await asyncio.sleep(0.5)
        return True
    _step("封面编辑-确定", False, detail="未命中「确定」且未点到封面相关「完成」")
    return False


async def _ensure_toutiao_video_cover(
    page: Any,
    cover_path: Optional[str],
    _step: Callable[..., None],
) -> bool:
    """西瓜视频须封面：有 cover_path 时本地上传后走封面编辑「确定」+ 二次确认；无则截取→下一步→同上。"""
    await _delay(1, 2)
    await _toutiao_scroll_video_cover_block_into_view(page)
    has_user_cover = bool(cover_path and os.path.isfile(cover_path))

    async def _try_set_video_cover_light() -> bool:
        if not (cover_path and os.path.isfile(cover_path)):
            return False
        inps = await _image_file_inputs_all_frames(page)
        logger.info("[TOUTIAO-COVER] image file inputs (all frames) count=%s", len(inps))
        for inp in inps:
            try:
                await inp.set_input_files(cover_path)
                _step("上传视频封面文件", True, path=cover_path)
                await asyncio.sleep(2.5)
                await _toutiao_video_finalize_cover_editor_modal(page, _step, pre_sleep_s=0.45)
                return True
            except Exception as e:
                logger.debug("[TOUTIAO-COVER] set_input_files: %s", e)
                continue
        return False

    async def _try_set_video_cover_deep() -> bool:
        if not (cover_path and os.path.isfile(cover_path)):
            return False
        frames = _toutiao_graphic_frames_ordered(page)
        for fr in frames:
            fu = ""
            try:
                fu = (getattr(fr, "url", None) or "")[:80]
            except Exception:
                pass
            jsh = None
            el_handle = None
            try:
                jsh = await fr.evaluate_handle(_JS_TOUTIAO_FIND_BEST_FILE_INPUT_DEEP)
                if jsh:
                    el_handle = jsh.as_element()
            except Exception as e:
                logger.info("[TOUTIAO-COVER] deep evaluate_handle frame=%s err=%s", fu, e)
            finally:
                if jsh:
                    try:
                        await jsh.dispose()
                    except Exception:
                        pass
            if not el_handle:
                continue
            try:
                await el_handle.set_input_files(cover_path)
                _step("上传视频封面文件", True, path=cover_path, via="shadow/deep")
                await asyncio.sleep(2.5)
                logger.info("[TOUTIAO-COVER] deep upload ok frame=%s", fu)
                await _toutiao_video_finalize_cover_editor_modal(page, _step, pre_sleep_s=0.45)
                return True
            except Exception as e:
                logger.debug("[TOUTIAO-COVER] deep set_input_files fail frame=%s: %s", fu, e)
            finally:
                try:
                    await el_handle.dispose()
                except Exception:
                    pass
        return False

    async def _try_set_video_cover_file_chooser() -> bool:
        """弹层内点「本地上传」会触发原生文件选择器；expect_file_chooser 须与该点击同事务。"""
        if not (cover_path and os.path.isfile(cover_path)):
            return False
        try:
            async with page.expect_file_chooser(timeout=18000) as fc_info:
                ok_local = await _toutiao_video_click_cover_modal_local_upload(page)
                if ok_local:
                    logger.info("[TOUTIAO-COVER] file_chooser via=cover_modal_本地上传")
                if not ok_local:
                    hit_any = False
                    for fr in _toutiao_graphic_frames_ordered(page):
                        try:
                            hit = await fr.evaluate(_JS_TOUTIAO_CLICK_VIDEO_FAKE_COVER_UPLOAD)
                            if hit:
                                hit_any = True
                                logger.info("[TOUTIAO-COVER] file_chooser prep js_click %s", hit)
                                break
                        except Exception:
                            continue
                    if not hit_any:
                        await _toutiao_video_try_activate_cover_upload(page, _step)
                    await asyncio.sleep(0.5)
                    ok2 = await _toutiao_video_click_cover_modal_local_upload(page)
                    logger.info("[TOUTIAO-COVER] file_chooser after_open_modal local_upload=%s", ok2)
            chooser = await fc_info.value
            await chooser.set_files(cover_path)
            _step("上传视频封面文件", True, path=cover_path, via="file_chooser")
            await asyncio.sleep(2.5)
            logger.info("[TOUTIAO-COVER] file_chooser set_files ok")
            await _toutiao_video_finalize_cover_editor_modal(page, _step, pre_sleep_s=0.45)
            return True
        except Exception as e:
            logger.info("[TOUTIAO-COVER] file_chooser: %s", e)
            return False

    cover_file_upload_failed = False
    if has_user_cover:
        if await _try_set_video_cover_light():
            return True
        if await _try_set_video_cover_deep():
            return True
        if await _toutiao_video_try_activate_cover_upload(page, _step):
            await asyncio.sleep(0.55)
            if await _try_set_video_cover_light():
                return True
            if await _try_set_video_cover_deep():
                return True
            if await _toutiao_video_click_cover_modal_local_upload(page):
                _step("点击封面弹层-本地上传", True)
                await asyncio.sleep(0.5)
                if await _try_set_video_cover_light():
                    return True
                if await _try_set_video_cover_deep():
                    return True
        if await _try_set_video_cover_file_chooser():
            return True
        _step(
            "上传视频封面文件",
            False,
            error="未找到可用的图片 file input（已尝试上传封面、弹层本地上传、Shadow、file_chooser）",
        )
        cover_file_upload_failed = True

    if not has_user_cover:
        if await _toutiao_video_try_activate_cover_upload(page, _step):
            await asyncio.sleep(1.05)
            if await _toutiao_video_click_cover_modal_next(page):
                _step("封面截取-下一步(视频帧)", True)
                await _toutiao_video_finalize_cover_editor_modal(page, _step, pre_sleep_s=1.15)
                await asyncio.sleep(2.0)
                for _ in range(4):
                    if await _toutiao_video_click_cover_flow_complete_if_present(page):
                        _step("封面弹层-完成(补点)", True)
                    await asyncio.sleep(0.35)
            else:
                _step("封面截取-下一步", False, detail="弹层内未命中「下一步」")
        else:
            _step("打开视频封面弹层", False, detail="未点到上传封面，将尝试其它封面入口")
    elif cover_file_upload_failed:
        # 本地上传未触发 file chooser 时常留在「本地上传」页，无「下一步」；切回「封面截取」再走视频帧兜底
        logger.info("[TOUTIAO-COVER] 本地上传失败，尝试封面截取→下一步（视频帧）兜底")
        _step("本地上传未成功，尝试视频帧封面兜底", True)
        await asyncio.sleep(0.85)
        if await _toutiao_video_click_cover_modal_next(page):
            _step("封面截取-下一步(视频帧,兜底)", True)
            await _toutiao_video_finalize_cover_editor_modal(page, _step, pre_sleep_s=1.15)
            await asyncio.sleep(2.0)
            for _ in range(4):
                if await _toutiao_video_click_cover_flow_complete_if_present(page):
                    _step("封面弹层-完成(补点,兜底)", True)
                await asyncio.sleep(0.35)
        else:
            _step("封面截取-下一步(兜底)", False, detail="弹层内未命中「下一步」")

    clicked = await page.evaluate(
        """() => {
          const keys = ['智能推荐', '推荐封面', '智能封面', '使用视频画面', '从视频选择', '选择封面', '编辑封面', '设置封面'];
          const els = Array.from(document.querySelectorAll('button,span,a,div[role="button"],div'));
          for (const k of keys) {
            for (const e of els) {
              if (!e || !e.offsetParent) continue;
              const t = (e.textContent || '').trim();
              if (t.includes(k) && t.length < 80) {
                try { e.click(); return k; } catch (err) {}
              }
            }
          }
          return '';
        }"""
    )
    if clicked:
        _step("尝试自动选封面(点击)", True, ui=clicked)
        await asyncio.sleep(1.8)
        await page.evaluate(
            """() => {
              const imgs = Array.from(document.querySelectorAll(
                '[class*="cover"] img, [class*="Cover"] img, [class*="frame"] img, [class*="thumb"] img'
              )).filter(i => i.offsetParent && i.naturalWidth > 40);
              if (imgs[0]) { try { imgs[0].click(); return true; } catch(e) {} }
              const cells = document.querySelectorAll('[class*="thumbnail"], [class*="Thumb"], li[role="option"]');
              for (const c of cells) {
                if (c.offsetParent) { try { c.click(); return true; } catch(e) {} }
              }
              return false;
            }"""
        )
        await asyncio.sleep(1.2)

    ok_hint = await page.evaluate(
        """() => {
          const b = (document.body && document.body.innerText) || '';
          if (b.includes('请上传封面') || b.includes('请选择封面') || b.includes('封面未设置')) return {ok:false,why:'prompt'};
          if (b.includes('上传封面') && b.includes('必填')) return {ok:false,why:'required'};
          if (b.includes('封面') && (b.includes('已选') || b.includes('完成') || b.includes('替换封面'))) return {ok:true};
          // 竖版/新版：封面区常为「封面 … 编辑 … 替换」，无连续「替换封面」四字（backend.log video_cover_flow_exhausted 即此）
          if (
            /封面[\\s\\S]{0,40}编辑[\\s\\S]{0,40}替换/.test(b) &&
            !b.includes('请上传封面')
          ) {
            return {ok:true, why:'cover_edit_replace'};
          }
          const imgs = Array.from(document.querySelectorAll('img')).filter((i) => {
            if (!i.offsetParent) return false;
            const r = i.getBoundingClientRect();
            if (r.width < 56 || r.height < 40 || r.top < 0) return false;
            let p = i;
            for (let d = 0; d < 12 && p; d++) {
              const tx = ((p.innerText || '') + ' ' + (p.getAttribute('class') || '')).slice(0, 120);
              if (tx.includes('封面') || /cover|thumb|poster|frame|video-cover/i.test(p.className || '')) return true;
              p = p.parentElement;
            }
            return false;
          });
          if (imgs.length >= 1 && !b.includes('请上传封面')) return {ok:true, why:'cover_img'};
          return {ok:null};
        }"""
    )
    if ok_hint.get("ok") is True:
        _step("视频封面就绪", True)
        return True
    if ok_hint.get("ok") is False:
        _step("视频封面仍缺失", False, detail=ok_hint)
        await _toutiao_log_stall_diagnostic(page, "video_cover_ok_hint_false")
        return False

    # 指定了本地封面路径时，不得以「文件在磁盘上」冒充已上传；须依赖上文 return 或下方智能选图结果
    if clicked:
        _step("视频封面就绪(已尝试自动)", True)
        return True
    _step(
        "视频封面",
        False,
        error=(
            "无指定封面时未能完成「上传封面→封面截取→下一步→封面编辑确定（含二次确认）」或兜底智能选图；"
            "指定了封面图时未能完成本地上传或封面编辑确定。"
        ),
    )
    await _toutiao_log_stall_diagnostic(page, "video_cover_flow_exhausted")
    return False


_JS_TOUTIAO_COVER_SLOT_FILE_INPUT_INDEX = """() => {
          const inps = Array.from(document.querySelectorAll('input[type=file]'));
          const imgOk = (acc) => {
            const a = (acc || '').toLowerCase();
            if (a.includes('video') && !a.includes('image')) return false;
            return true;
          };
          let best = -1;
          let bestScore = -1;
          for (let i = 0; i < inps.length; i++) {
            const inp = inps[i];
            if (!imgOk(inp.accept)) continue;
            let s = 0;
            let p = inp;
            for (let j = 0; j < 16 && p; j++) {
              const tx = ((p.innerText || '') + ' ' + (p.getAttribute('placeholder') || '')).slice(0, 1600);
              const cls = (p.className && String(p.className)) || '';
              const ac = (p.getAttribute('aria-label') || '') + (p.getAttribute('title') || '');
              if (tx.includes('封面')) { s = Math.max(s, 100); }
              if (/cover|upload|thumb|image|pic|photo/i.test(cls + ac)) { s = Math.max(s, 55); }
              p = p.parentElement;
            }
            if (s < 20 && (!inp.accept || inp.accept.toLowerCase().includes('image'))) s = 20;
            if (s > bestScore) { bestScore = s; best = i; }
          }
          if (bestScore >= 20) return best;
          for (let i = 0; i < inps.length; i++) {
            if (imgOk(inps[i].accept)) return i;
          }
          return -1;
        }"""


# 头条新版常用 Web Components：file input 在 Shadow 内，document.querySelectorAll 永远为 0（与 task_49 日志一致）。
_JS_TOUTIAO_COUNT_FILE_INPUTS_DEEP = """() => {
  function countIn(root) {
    if (!root || !root.querySelectorAll) return 0;
    let n = root.querySelectorAll('input[type=file]').length;
    for (const el of root.querySelectorAll('*')) {
      try { if (el.shadowRoot) n += countIn(el.shadowRoot); } catch (e) {}
    }
    return n;
  }
  return countIn(document);
}"""

_JS_TOUTIAO_FIND_BEST_FILE_INPUT_DEEP = """() => {
  function imgOk(acc) {
    const a = (acc || '').toLowerCase();
    if (a.includes('video') && !a.includes('image')) return false;
    return true;
  }
  function ancestorsContext(el) {
    let chunks = [];
    let n = el;
    for (let i = 0; i < 24 && n; i++) {
      if (n.nodeType !== 1) break;
      const tx = (n.innerText || '').slice(0, 500);
      const cls = (n.className && String(n.className)) || '';
      const ph = (n.getAttribute && (n.getAttribute('placeholder') || n.getAttribute('aria-label') || '')) || '';
      chunks.push(tx + ' ' + cls + ' ' + ph);
      if (n.parentElement) n = n.parentElement;
      else {
        const r = n.getRootNode && n.getRootNode();
        if (r && r.host) n = r.host;
        else break;
      }
    }
    return chunks.join(' | ');
  }
  let best = null;
  let bestScore = -1;
  function consider(inp) {
    if (!imgOk(inp.accept)) return;
    const ctx = ancestorsContext(inp);
    let s = 5;
    if (ctx.includes('封面')) s += 100;
    if (/cover|upload|thumb|配图|图片|本地上传/i.test(ctx)) s += 35;
    if (inp.accept && inp.accept.toLowerCase().includes('image')) s += 12;
    if (s > bestScore) { bestScore = s; best = inp; }
  }
  function walk(root) {
    if (!root || !root.querySelectorAll) return;
    for (const inp of root.querySelectorAll('input[type=file]')) consider(inp);
    for (const el of root.querySelectorAll('*')) {
      try { if (el.shadowRoot) walk(el.shadowRoot); } catch (e) {}
    }
  }
  walk(document);
  return best;
}"""


async def _cover_slot_file_input_index_for_frame(frame: Any) -> int:
    """在指定 frame 的 document 内解析封面用 file input 下标。"""
    try:
        idx = await frame.evaluate(_JS_TOUTIAO_COVER_SLOT_FILE_INPUT_INDEX)
        return int(idx) if isinstance(idx, int) else -1
    except Exception:
        return -1


async def _cover_slot_file_input_index(page: Any) -> int:
    """
    定位主 frame 内封面用 file input 的下标（兼容旧调用）。
    """
    try:
        mf = page.main_frame
    except Exception:
        mf = None
    if mf is None and page.frames:
        mf = page.frames[0]
    if mf is None:
        return -1
    return await _cover_slot_file_input_index_for_frame(mf)


async def _toutiao_graphic_file_input_total_count(page: Any) -> int:
    """含各 frame 内 ShadowRoot 中的 file input（与 mp 后台实际 DOM 一致）。"""
    n = 0
    for fr in page.frames:
        try:
            c = await fr.evaluate(_JS_TOUTIAO_COUNT_FILE_INPUTS_DEEP)
            n += int(c or 0)
        except Exception:
            continue
    return n


def _toutiao_graphic_frames_ordered(page: Any) -> List[Any]:
    """主 frame 优先，再其余 frame（封面控件偶发在子 frame）。"""
    out: List[Any] = []
    seen: set[int] = set()
    try:
        mf = page.main_frame
        if mf is not None:
            out.append(mf)
            seen.add(id(mf))
    except Exception:
        pass
    for fr in getattr(page, "frames", None) or []:
        if id(fr) in seen:
            continue
        seen.add(id(fr))
        out.append(fr)
    return out


async def _toutiao_graphic_cover_modal_visible_any_frame(page: Any) -> bool:
    """弹窗内才有「本地上传 / 扫码上传」；主表单上常只有虚线框 +。"""
    for fr in _toutiao_graphic_frames_ordered(page):
        try:
            if await fr.get_by_text("本地上传", exact=False).count() > 0:
                return True
            if await fr.get_by_text("扫码上传", exact=False).count() > 0:
                return True
        except Exception:
            continue
    return False


_JS_TOUTIAO_CLICK_COVER_PLUS_OR_DASHED = """() => {
  const ac = document.querySelector('.article-cover-add');
  if (ac && ac.offsetParent) {
    try {
      ac.scrollIntoView({ block: 'center', inline: 'nearest' });
      const r = ac.getBoundingClientRect();
      const cx = r.left + r.width / 2;
      const cy = r.top + r.height / 2;
      const hit = document.elementFromPoint(cx, cy);
      if (hit) {
        try {
          hit.click();
          return 'article-cover-add:center';
        } catch (e1) {}
      }
      ac.click();
      return 'article-cover-add';
    } catch (e) {}
  }
  function nearCoverBlock(el) {
    let p = el;
    for (let i = 0; i < 16 && p; i++) {
      const tx = (p.innerText || '').slice(0, 600);
      if (
        tx.includes('展示封面') ||
        tx.includes('优质的封面有利于推荐') ||
        (tx.includes('单图') && tx.includes('三图'))
      ) {
        return true;
      }
      p = p.parentElement;
    }
    return false;
  }
  const leaves = document.querySelectorAll('button, [role="button"], div, span, a, label, i, svg');
  for (const el of leaves) {
    if (!el.offsetParent) continue;
    const t = (el.innerText || '').replace(/\\s+/g, '').trim();
    if (t !== '+' && t !== '＋' && t !== '+添加' && t !== '添加图片' && t !== '上传图片') continue;
    if (!nearCoverBlock(el)) continue;
    try {
      el.scrollIntoView({ block: 'center', inline: 'nearest' });
      el.click();
      return 'text_plus:' + t;
    } catch (e) {}
  }
  const divs = document.querySelectorAll('div');
  for (const el of divs) {
    if (!el.offsetParent) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 64 || r.height < 64 || r.width > 520 || r.height > 520) continue;
    if (!nearCoverBlock(el)) continue;
    const st = window.getComputedStyle(el);
    const dashed =
      (st.borderStyle && String(st.borderStyle).includes('dash')) ||
      (st.borderTopStyle && String(st.borderTopStyle).includes('dash')) ||
      /dashed/i.test(st.border || '');
    if (!dashed) continue;
    try {
      el.scrollIntoView({ block: 'center', inline: 'nearest' });
      const cx = r.left + r.width / 2;
      const cy = r.top + r.height / 2;
      const hit = document.elementFromPoint(cx, cy);
      if (hit) {
        try {
          hit.click();
          return 'dashed_center:' + Math.round(r.width) + 'x' + Math.round(r.height);
        } catch (e2) {}
      }
      el.click();
      return 'dashed_el:' + Math.round(r.width) + 'x' + Math.round(r.height);
    } catch (e) {}
  }
  return '';
}"""


async def _toutiao_graphic_open_cover_upload_modal(page: Any, _step: Callable[..., None]) -> bool:
    """
    头条图文：选「单图」后主界面多为虚线框 +「+」，需先点开才出现弹窗里的「本地上传」
    （与用户截图一致；此前只在主文档找「本地上传」会永远 count=0）。
    """
    if await _toutiao_graphic_cover_modal_visible_any_frame(page):
        logger.info("[TOUTIAO-GRAPHIC] cover_modal already visible (本地上传)")
        _step("打开封面上传弹窗", True, detail="弹窗已存在")
        return True
    clicked = ""
    frames = _toutiao_graphic_frames_ordered(page)
    for fr in frames:
        try:
            loc = fr.locator(".article-cover-add").first
            if await loc.count() == 0:
                continue
            await loc.scroll_into_view_if_needed(timeout=5000)
            await loc.click(timeout=5000, force=True)
            clicked = "pw:.article-cover-add"
            fu = (getattr(fr, "url", None) or "")[:90]
            logger.info("[TOUTIAO-GRAPHIC] cover_modal click %s frame=%s", clicked, fu)
            break
        except Exception as e:
            logger.info("[TOUTIAO-GRAPHIC] cover_modal pw .article-cover-add err: %s", e)
    if not clicked:
        for fr in frames:
            try:
                r = await fr.evaluate(_JS_TOUTIAO_CLICK_COVER_PLUS_OR_DASHED)
                if r:
                    clicked = str(r)
                    fu = (getattr(fr, "url", None) or "")[:90]
                    logger.info("[TOUTIAO-GRAPHIC] cover_modal click_zone %s frame=%s", clicked, fu)
                    break
            except Exception as e:
                logger.info("[TOUTIAO-GRAPHIC] cover_modal click_zone err: %s", e)
    if not clicked:
        _step("打开封面上传弹窗", False, detail="未点到+或虚线封面区")
        return await _toutiao_graphic_cover_modal_visible_any_frame(page)
    for _ in range(40):
        await asyncio.sleep(0.25)
        if await _toutiao_graphic_cover_modal_visible_any_frame(page):
            _step("打开封面上传弹窗", True, detail=clicked[:80])
            return True
    _step("打开封面上传弹窗", False, detail="已点击封面区但未见本地上传弹窗")
    return False


async def _toutiao_graphic_try_confirm_cover_modal(page: Any) -> None:
    """弹窗内点「确定/确认」把已选素材写回主表单草稿。多根节点：dialog / Semi Modal。"""
    for fr in _toutiao_graphic_frames_ordered(page):
        roots: List[Any] = []
        try:
            dl = fr.locator('[role="dialog"]')
            nd = await dl.count()
            for i in range(min(nd, 4)):
                roots.append(dl.nth(i))
        except Exception:
            pass
        for sel in (
            'div[class*="semi-modal-wrap"]',
            'div[class*="semi-modal"]',
            'div[class*="SemiModal"]',
        ):
            try:
                sm = fr.locator(sel)
                ns = await sm.count()
                for i in range(min(ns, 6)):
                    roots.append(sm.nth(i))
            except Exception:
                pass
        for dlg in roots:
            try:
                if await dlg.count() == 0:
                    continue
                try:
                    if not await dlg.is_visible():
                        continue
                except Exception:
                    pass
                blob = ""
                try:
                    blob = (await dlg.inner_text() or "")[:900]
                except Exception:
                    blob = ""
                looks_cover_modal = (
                    "上传图片" in blob
                    or "本地上传" in blob
                    or "扫码上传" in blob
                    or ("已上传" in blob and "张" in blob)
                    or "支持拖拽调整" in blob
                    or ("已上传" in blob and "张图片" in blob)
                )
                if not looks_cover_modal:
                    continue
            except Exception:
                continue
            for name_rx in (
                re.compile(r"^确定$"),
                re.compile(r"^确认$"),
            ):
                try:
                    loc = dlg.get_by_role("button", name=name_rx)
                    if await loc.count() > 0:
                        await loc.first.click(timeout=5000, force=True)
                        logger.info("[TOUTIAO-GRAPHIC] cover_modal confirm role=%s", name_rx.pattern)
                        await asyncio.sleep(0.7)
                        return
                except Exception:
                    pass
            for txt in ("确定", "确认"):
                try:
                    btn = dlg.get_by_text(txt, exact=True)
                    if await btn.count() == 0:
                        continue
                    await btn.first.click(timeout=5000, force=True)
                    logger.info("[TOUTIAO-GRAPHIC] cover_modal confirm text=%s", txt)
                    await asyncio.sleep(0.7)
                    return
                except Exception:
                    continue
            try:
                reds = dlg.locator("button").filter(has_text=re.compile(r"^(确定|确认)$"))
                if await reds.count() > 0:
                    await reds.first.click(timeout=5000, force=True)
                    logger.info("[TOUTIAO-GRAPHIC] cover_modal confirm button filter")
                    await asyncio.sleep(0.7)
                    return
            except Exception:
                pass
            try:
                prim = dlg.locator(
                    "[class*='semi-button-primary'], [class*='semi-button_warning'], "
                    "[class*='Button-primary'], div.byte-btn-primary, button.byte-btn-primary"
                ).filter(has_text=re.compile(r"^(确定|确认)$"))
                if await prim.count() > 0:
                    await prim.last.click(timeout=5000, force=True)
                    logger.info("[TOUTIAO-GRAPHIC] cover_modal confirm semi/byte primary")
                    await asyncio.sleep(0.7)
                    return
            except Exception:
                pass


# 封面素材弹窗：上传后头条常见整句「已上传 1 张图片，支持拖拽调整图片顺序」——用全文检测兜底，避免根节点选不中时白等。
_JS_TOUTIAO_COVER_MATERIAL_ROOT_LIB = r"""
function __ttCoverShadowTextAppend(root, depth) {
  if (!root || depth > 12) return '';
  var chunks = [];
  try {
    var nodes = root.querySelectorAll('*');
    for (var i = 0; i < Math.min(nodes.length, 2000); i++) {
      try {
        var sr = nodes[i].shadowRoot;
        if (sr) {
          try {
            chunks.push(sr.innerText || sr.textContent || '');
          } catch (e1) {}
          chunks.push(__ttCoverShadowTextAppend(sr, depth + 1));
        }
      } catch (e2) {}
    }
  } catch (e3) {}
  return chunks.join(' ');
}
function __ttCoverUploadSuccessCaption(doc) {
  doc = doc || document;
  var bodyT = '';
  try {
    var base = '';
    try {
      base = (doc.body && doc.body.innerText) ? doc.body.innerText : '';
    } catch (e0) {
      base = '';
    }
    var extra = '';
    try {
      extra = __ttCoverShadowTextAppend(doc.body || doc.documentElement, 0);
    } catch (e1) {
      extra = '';
    }
    bodyT = (base + ' ' + extra).replace(/\s+/g, ' ');
  } catch (e) {
    return false;
  }
  if (bodyT.indexOf('支持拖拽调整图片顺序') >= 0) return true;
  if (bodyT.indexOf('支持拖拽调整') >= 0 && bodyT.indexOf('已上传') >= 0) return true;
  if (bodyT.indexOf('已上传') >= 0 && bodyT.indexOf('张图片') >= 0) return true;
  if (bodyT.indexOf('已上传') >= 0 && bodyT.indexOf('张') >= 0 && bodyT.indexOf('图片') >= 0) return true;
  if (/已上传\s*[\d\uFF10-\uFF19]+\s*张/.test(bodyT) && bodyT.indexOf('图片') >= 0) return true;
  return false;
}
function __ttCoverModalBlocksPublish(doc) {
  doc = doc || document;
  if (__ttCoverMaterialModalRoot(doc)) return true;
  if (__ttCoverUploadSuccessCaption(doc)) return true;
  return false;
}
function __ttCoverMaterialModalRoot(doc) {
  doc = doc || document;
  var candidates = [];
  function vis(el) {
    if (!el) return false;
    try {
      var r = el.getBoundingClientRect();
      var st = window.getComputedStyle(el);
      if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity || '1') < 0.05) return false;
      return r.width >= 64 && r.height >= 48 && r.bottom > -20 && r.top < (window.innerHeight || 900) + 80;
    } catch (e) {
      return false;
    }
  }
  function push(el) {
    if (el && vis(el)) candidates.push(el);
  }
  function harvest(root) {
    if (!root || !root.querySelector) return;
    push(root.querySelector('[role="dialog"]'));
    var semiSel =
      'div[class*="semi-modal-wrap"],div[class*="semi-modal"],div[class*="SemiModal"],[class*="modal_content"],div[class*="bui-modal"],div[class*="BuiModal"],[class*="Dialog-content"],[class*="arco-modal"],[class*="Modal-wrapper"],[class*="modal-wrapper"]';
    var nodes = root.querySelectorAll(semiSel);
    for (var j = 0; j < nodes.length; j++) push(nodes[j]);
    try {
      var all = root.querySelectorAll('*');
      var maxN = Math.min(all.length, 400);
      for (var x = 0; x < maxN; x++) {
        var h = all[x];
        try {
          var sr = h.shadowRoot;
          if (sr) harvest(sr);
        } catch (e2) {}
      }
    } catch (e3) {}
  }
  harvest(doc);

  function scoreModal(el) {
    var t = (el.innerText || '').replace(/\s+/g, ' ');
    var r = el.getBoundingClientRect();
    if (r.width < 64 || r.height < 48) return 0;
    var vw = window.innerWidth || 1200;
    var vh = window.innerHeight || 800;
    if (r.width > vw * 0.97 && r.height > vh * 0.97 && t.length > 18000) return 0;
    var s = 0;
    if (t.indexOf('上传图片') >= 0) s += 42;
    if (t.indexOf('本地上传') >= 0 || t.indexOf('扫码上传') >= 0) s += 28;
    if (t.indexOf('已上传') >= 0 && t.indexOf('张') >= 0) s += 44;
    if (t.indexOf('支持拖拽调整图片顺序') >= 0) s += 50;
    if (t.indexOf('张图片') >= 0 && t.indexOf('已上传') >= 0) s += 40;
    if (t.indexOf('拖拽调整') >= 0 || (t.indexOf('拖拽') >= 0 && t.indexOf('封面') >= 0)) s += 22;
    if (t.indexOf('上传成功') >= 0) s += 28;
    if ((t.indexOf('确定') >= 0 || t.indexOf('确认') >= 0) && t.length < 4800) s += 22;
    if (t.indexOf('取消') >= 0 && (t.indexOf('上传') >= 0 || t.indexOf('图片') >= 0)) s += 10;
    if (/\d+\s*张/.test(t) && (t.indexOf('上传') >= 0 || t.indexOf('图') >= 0 || t.indexOf('封面') >= 0)) s += 36;
    return s;
  }
  var best = null;
  var bestS = 0;
  for (var i = 0; i < candidates.length; i++) {
    var sc = scoreModal(candidates[i]);
    if (sc >= 44 && sc > bestS) {
      best = candidates[i];
      bestS = sc;
    }
  }
  if (!best) {
    var loose = doc.querySelectorAll('[role="dialog"], [class*="modal"], [class*="Modal"], [class*="Drawer"], [class*="Popup"], [class*="Dialog"]');
    for (var a = 0; a < loose.length; a++) {
      var el = loose[a];
      if (!vis(el)) continue;
      var tx = (el.innerText || '').replace(/\s+/g, ' ');
      if (tx.length < 6 || tx.length > 9000) continue;
      if ((tx.indexOf('确定') < 0 && tx.indexOf('确认') < 0)) continue;
      if (tx.indexOf('上传图片') < 0 && tx.indexOf('已上传') < 0 && tx.indexOf('本地上传') < 0 && tx.indexOf('支持拖拽') < 0) continue;
      var sc2 = scoreModal(el);
      if (sc2 >= 38 && sc2 > bestS) {
        best = el;
        bestS = sc2;
      }
    }
  }
  return best;
}
"""

_JS_TOUTIAO_COVER_PICKER_IS_OPEN = (
    "() => { "
    + _JS_TOUTIAO_COVER_MATERIAL_ROOT_LIB
    + " if (__ttCoverMaterialModalRoot(document)) return true;"
    + " if (__ttCoverUploadSuccessCaption(document)) return true;"
    + " return false; }"
)

_JS_TOUTIAO_COVER_MODAL_THUMBNAIL_READY = (
    """() => { """
    + _JS_TOUTIAO_COVER_MATERIAL_ROOT_LIB
    + """
  if (__ttCoverUploadSuccessCaption(document)) return true;
  var d = __ttCoverMaterialModalRoot(document);
  if (!d) return false;
  var t = (d.innerText || '').replace(/\s+/g, ' ');
  if (t.indexOf('支持拖拽调整图片顺序') >= 0) return true;
  if (t.indexOf('已上传') >= 0 && t.indexOf('张') >= 0) return true;
  if (t.indexOf('上传成功') >= 0) return true;
  if (/\d+\s*张/.test(t) && (t.indexOf('上传') >= 0 || t.indexOf('拖拽') >= 0 || t.indexOf('图') >= 0)) return true;
  if (/[\d\uFF10-\uFF19]+\s*张/.test(t) && t.indexOf('已上传') >= 0) return true;
  var hasCaption =
    (t.indexOf('已上传') >= 0 && t.indexOf('张') >= 0) ||
    t.indexOf('上传成功') >= 0 ||
    t.indexOf('拖拽调整') >= 0 ||
    t.indexOf('拖拽') >= 0;
  var imgs = d.querySelectorAll('img');
  for (var k = 0; k < imgs.length; k++) {
    var im = imgs[k];
    if (!im.offsetParent) continue;
    var r = im.getBoundingClientRect();
    if (r.width < 16 || r.height < 16) continue;
    if (r.width > 520 || r.height > 520) continue;
    var nw = im.naturalWidth || 0;
    var nh = im.naturalHeight || 0;
    if (nw < 8 && nh < 8 && !im.complete) continue;
    if (hasCaption || nw > 0 || nh > 0 || im.complete) return true;
  }
  return false;
}"""
)

_JS_TOUTIAO_COVER_PICKER_CLICK_FIRST_THUMB = (
    """() => { """
    + _JS_TOUTIAO_COVER_MATERIAL_ROOT_LIB
    + """
  var d = __ttCoverMaterialModalRoot(document);
  if (!d) return '';
  var imgs = d.querySelectorAll('img');
  for (var k = 0; k < imgs.length; k++) {
    var im = imgs[k];
    if (!im.offsetParent) continue;
    var r = im.getBoundingClientRect();
    if (r.width < 16 || r.height < 16) continue;
    if (r.width > 520 || r.height > 520) continue;
    var wrap = im.closest(
      'li, [role="listitem"], [role="button"], [class*="thumb"], [class*="preview"], [class*="image"], [class*="item"]'
    );
    var tgt = wrap || im;
    try {
      tgt.scrollIntoView({ block: 'center', inline: 'nearest' });
      tgt.click();
      return 'thumb';
    } catch (e) {}
  }
  return '';
}"""
)

_JS_TOUTIAO_COVER_MODAL_BLOCKS = (
    "() => { " + _JS_TOUTIAO_COVER_MATERIAL_ROOT_LIB + " return __ttCoverModalBlocksPublish(document); }"
)

# Playwright 常点不到 Semi 弹层里由 div/span 拼的主按钮；用 JS 在含「上传图片/已上传」的祖先链内找纯文案「确定」并 click（含 open shadow）。
_JS_TOUTIAO_COVER_MODAL_JS_CLICK_CONFIRM = r"""() => {
  function normTxt(el) {
    return ((el.innerText || el.textContent || '') + '').replace(/\s+/g, '').trim();
  }
  function inCoverContext(el) {
    var anc = el;
    var blob = '';
    for (var d = 0; d < 20 && anc; d++) {
      try {
        blob += (anc.innerText || '').slice(0, 600);
      } catch (e0) {}
      anc = anc.parentElement;
    }
    return (
      blob.indexOf('上传图片') >= 0 ||
      blob.indexOf('已上传') >= 0 ||
      blob.indexOf('本地上传') >= 0 ||
      blob.indexOf('支持拖拽') >= 0
    );
  }
  function tryRoot(root) {
    if (!root || !root.querySelectorAll) return false;
    var sel = 'button, [role="button"], div, span, a';
    var nodes = root.querySelectorAll(sel);
    var candidates = [];
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (!el.offsetParent) continue;
      var nt = normTxt(el);
      if (nt !== '确定' && nt !== '确认') continue;
      var r = el.getBoundingClientRect();
      if (r.width < 6 || r.height < 6) continue;
      if (!inCoverContext(el)) continue;
      candidates.push(el);
    }
    candidates.sort(function (a, b) {
      var la = normTxt(a).length;
      var lb = normTxt(b).length;
      if (la !== lb) return la - lb;
      var ra = a.getBoundingClientRect();
      var rb = b.getBoundingClientRect();
      return rb.bottom + rb.right - (ra.bottom + ra.right);
    });
    for (var j = 0; j < candidates.length; j++) {
      var e2 = candidates[j];
      try {
        e2.scrollIntoView({ block: 'center', inline: 'nearest' });
        e2.click();
        return true;
      } catch (e1) {}
      try {
        var r2 = e2.getBoundingClientRect();
        e2.dispatchEvent(
          new MouseEvent('click', {
            bubbles: true,
            cancelable: true,
            view: window,
            clientX: r2.left + r2.width / 2,
            clientY: r2.top + r2.height / 2,
          })
        );
        return true;
      } catch (ex) {}
    }
    var all = root.querySelectorAll('*');
    for (var k = 0; k < Math.min(all.length, 3500); k++) {
      try {
        var sr = all[k].shadowRoot;
        if (sr && tryRoot(sr)) return true;
      } catch (e3) {}
    }
    return false;
  }
  return tryRoot(document);
}"""


async def _toutiao_graphic_click_cover_modal_confirm_js(page: Any) -> bool:
    for fr in _toutiao_graphic_frames_ordered(page):
        try:
            if await fr.evaluate(_JS_TOUTIAO_COVER_MODAL_JS_CLICK_CONFIRM):
                logger.info("[TOUTIAO-GRAPHIC] cover_modal confirm via=js_deep_click frame=%s", (getattr(fr, "url", "") or "")[:90])
                await asyncio.sleep(0.5)
                return True
        except Exception as e:
            logger.info("[TOUTIAO-GRAPHIC] cover_modal js confirm err: %s", e)
    return False


async def _toutiao_graphic_modal_still_blocks_publish(page: Any) -> bool:
    """上传图片弹窗仍挡在主编辑区上（未点「确定」或 DOM 在 shadow 内）。"""
    for fr in _toutiao_graphic_frames_ordered(page):
        try:
            if await fr.evaluate(_JS_TOUTIAO_COVER_MODAL_BLOCKS):
                return True
        except Exception:
            continue
    return False


async def _toutiao_graphic_nudge_cover_modal_confirm(page: Any) -> None:
    """弹窗底部主按钮常为 div/span 包装，补充一层可见「确定」点击（限在含上传文案的弹层内）。"""
    for fr in _toutiao_graphic_frames_ordered(page):
        for box_sel in ('[role="dialog"]', 'div[class*="semi-modal"]'):
            try:
                roots = fr.locator(box_sel)
                n = await roots.count()
                for bi in range(min(n, 4)):
                    root = roots.nth(bi)
                    try:
                        if not await root.is_visible():
                            continue
                    except Exception:
                        pass
                    blob = ""
                    try:
                        blob = (await root.inner_text() or "")[:800]
                    except Exception:
                        blob = ""
                    if (
                        "上传图片" not in blob
                        and "本地上传" not in blob
                        and "已上传" not in blob
                        and "支持拖拽" not in blob
                    ):
                        continue
                    for loc in (
                        root.get_by_role("button", name=re.compile(r"^确定$")),
                        root.get_by_role("button", name=re.compile(r"^确认$")),
                        root.get_by_text("确定", exact=True),
                        root.locator("div[class*='byte-btn-primary'], span[class*='byte-btn']").filter(
                            has_text=re.compile(r"^确定$")
                        ),
                    ):
                        try:
                            if await loc.count() == 0:
                                continue
                            btn = loc.last
                            try:
                                if not await btn.is_visible():
                                    continue
                            except Exception:
                                pass
                            tx = re.sub(r"\s+", " ", (await btn.inner_text() or "").strip())
                            if "预览并发布" in tx:
                                continue
                            if tx not in ("确定", "确认") and not re.match(r"^(确定|确认)$", tx):
                                continue
                            await btn.click(timeout=4000, force=True)
                            logger.info("[TOUTIAO-GRAPHIC] nudge cover_modal confirm text=%s", tx[:20])
                            await asyncio.sleep(0.5)
                            return
                        except Exception:
                            continue
            except Exception:
                continue


async def _toutiao_graphic_wait_cover_picker_closed(page: Any, timeout_s: float = 15.0) -> bool:
    """封面素材选择弹窗（上传图片）关闭后再点主发布按钮更稳。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        any_open = False
        for fr in _toutiao_graphic_frames_ordered(page):
            try:
                if await fr.evaluate(_JS_TOUTIAO_COVER_PICKER_IS_OPEN):
                    any_open = True
                    break
            except Exception:
                continue
        if not any_open:
            logger.info("[TOUTIAO-GRAPHIC] cover picker closed (no findPickerRoot)")
            return True
        await asyncio.sleep(0.35)
    logger.warning("[TOUTIAO-GRAPHIC] wait cover picker closed timeout=%ss", timeout_s)
    return False


async def _toutiao_graphic_finalize_cover_material_modal(page: Any, _step: Callable[..., None]) -> None:
    """
    本地上传后：等弹窗中间预览区出现缩略图（「已上传 N 张…」下方），再点选一下，最后点红色「确定」写回草稿。
    缩略图检测超时仍会尝试点「确定」并等待弹窗关闭，避免遮罩挡住「预览并发布」。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 22.0
    thumb_ready = False
    while loop.time() < deadline:
        for fr in _toutiao_graphic_frames_ordered(page):
            try:
                if await fr.evaluate(_JS_TOUTIAO_COVER_MODAL_THUMBNAIL_READY):
                    thumb_ready = True
                    break
            except Exception:
                pass
            if thumb_ready:
                break
            try:
                modal = fr.locator('[role="dialog"]').filter(has_text=re.compile(r"上传图片|本地上传"))
                if await modal.count() > 0:
                    first_img = modal.first.locator("img").first
                    await first_img.wait_for(state="visible", timeout=800)
                    box = await first_img.bounding_box()
                    if box and 24 <= box.get("width", 0) <= 520 and 24 <= box.get("height", 0) <= 520:
                        thumb_ready = True
                        break
            except Exception:
                pass
            if thumb_ready:
                break
            try:
                modal = fr.locator('div[class*="semi-modal"]').filter(has_text=re.compile(r"上传图片"))
                if await modal.count() > 0:
                    first_img = modal.first.locator("img").first
                    await first_img.wait_for(state="visible", timeout=800)
                    box = await first_img.bounding_box()
                    if box and 24 <= box.get("width", 0) <= 520 and 24 <= box.get("height", 0) <= 520:
                        thumb_ready = True
                        break
            except Exception:
                pass
            if thumb_ready:
                break
            try:
                modal2 = fr.locator('div[class*="semi-modal"]').filter(
                    has_text=re.compile(r"已上传|拖拽调整|上传成功|上传图片|张图片")
                )
                if await modal2.count() > 0:
                    first_img = modal2.first.locator("img").first
                    await first_img.wait_for(state="visible", timeout=800)
                    box = await first_img.bounding_box()
                    if box and 16 <= box.get("width", 0) <= 520 and 16 <= box.get("height", 0) <= 520:
                        thumb_ready = True
                        break
            except Exception:
                pass
        if thumb_ready:
            break
        await asyncio.sleep(0.25)

    if thumb_ready:
        _step("等待弹窗预览缩略图", True, detail="中间预览区已出现（已上传文案下方）")
        logger.info("[TOUTIAO-GRAPHIC] cover_modal thumbnail visible, proceed select+确定")
    else:
        _step(
            "等待弹窗预览缩略图",
            False,
            detail="超时仍尝试点确定并关弹窗（避免主发布按钮被遮罩）",
        )
        logger.warning("[TOUTIAO-GRAPHIC] cover_modal: 缩略图未确认，仍尝试确定+关弹窗")

    if thumb_ready:
        picked_any = ""
        for fr in _toutiao_graphic_frames_ordered(page):
            try:
                picked = await fr.evaluate(_JS_TOUTIAO_COVER_PICKER_CLICK_FIRST_THUMB)
                picked_any = str(picked or "")
                if picked_any:
                    logger.info("[TOUTIAO-GRAPHIC] cover_modal selected uploaded thumb (%s)", picked_any)
                    _step("选中弹窗内刚上传的封面", True)
                    await asyncio.sleep(0.45)
                    break
            except Exception as e:
                logger.info("[TOUTIAO-GRAPHIC] cover_modal thumb: %s", e)
        if not picked_any:
            logger.info("[TOUTIAO-GRAPHIC] cover_modal no thumb click (单张可能已选中)")

    await asyncio.sleep(0.35)
    for _close_round in range(6):
        await _toutiao_graphic_try_confirm_cover_modal(page)
        await _toutiao_graphic_nudge_cover_modal_confirm(page)
        await _toutiao_graphic_click_cover_modal_confirm_js(page)
        await asyncio.sleep(0.45)
        if not await _toutiao_graphic_modal_still_blocks_publish(page):
            logger.info("[TOUTIAO-GRAPHIC] cover_modal dismissed after round=%s", _close_round)
            break
        await asyncio.sleep(0.35)
    await _toutiao_graphic_wait_cover_picker_closed(page, timeout_s=22.0)


# 在封面弹窗已打开时，再点这些会弹出系统文件框且未包 expect_file_chooser → 流程卡死（单图常见）。
_TOUTIAO_COVER_MODAL_OS_PICKER_HINTS = frozenset(
    {
        "本地上传",
        "上传图片",
        "点击上传",
        "扫码上传",
        "本地图片",
        "添加图片",
        "从本地上传",
        "+ 上传",
    }
)


async def _toutiao_graphic_set_cover_via_modal_file_input(
    page: Any, image_path: str, _step: Callable[..., None]
) -> bool:
    """
    弹窗内常有隐藏/可见的 input[type=file]；直接 set_input_files，不走系统文件选择器（避免 Windows 卡住）。
    对齐抖音图文优先走 file input 的策略。
    """
    if not os.path.isfile(image_path):
        return False
    for fr in _toutiao_graphic_frames_ordered(page):
        roots: List[Any] = []
        try:
            dl = fr.locator('[role="dialog"]')
            if await dl.count() > 0:
                roots.append(dl.first)
        except Exception:
            pass
        try:
            sm = fr.locator('div[class*="semi-modal"]').filter(
                has_text=re.compile(r"上传图片|本地上传|扫码上传")
            )
            if await sm.count() > 0:
                roots.append(sm.first)
        except Exception:
            pass
        try:
            sw = fr.locator('div[class*="semi-modal-wrap"]').filter(
                has_text=re.compile(r"上传图片|本地上传")
            )
            if await sw.count() > 0:
                roots.append(sw.first)
        except Exception:
            pass
        for root in roots:
            try:
                try:
                    await root.wait_for(state="visible", timeout=4000)
                except Exception:
                    pass
                n = await root.locator('input[type="file"]').count()
                for i in range(n):
                    inp = root.locator('input[type="file"]').nth(i)
                    try:
                        acc = (await inp.get_attribute("accept")) or ""
                        al = acc.lower()
                        if "video" in al and "image" not in al:
                            continue
                        await inp.set_input_files(image_path)
                        _step("上传封面(弹窗内注入)", True, detail=(acc or "")[:72])
                        logger.info(
                            "[TOUTIAO-GRAPHIC] modal set_input_files ok i=%s accept=%s",
                            i,
                            (acc or "")[:100],
                        )
                        await asyncio.sleep(1.0)
                        await _toutiao_graphic_finalize_cover_material_modal(page, _step)
                        return True
                    except Exception as e:
                        logger.info("[TOUTIAO-GRAPHIC] modal set_input_files i=%s err=%s", i, e)
            except Exception as e:
                logger.info("[TOUTIAO-GRAPHIC] modal file input root: %s", e)
    return False


async def _toutiao_graphic_try_activate_cover_upload_ui(page: Any, _step: Callable[..., None]) -> bool:
    """
    日志定位：仅滚动时 light DOM 无 file input；先点「上传封面」等入口，常见组件才会挂出/激活 input。
    封面弹窗已打开时禁止点「本地上传」等，否则会单独打开系统文件框（无 Playwright 接管）导致卡死。
    """
    modal_open = await _toutiao_graphic_cover_modal_visible_any_frame(page)
    hints_pw = (
        "上传封面",
        "添加封面",
        "选择封面",
        "本地上传",
        "上传图片",
        "本地图片",
        "+ 封面",
        "更换封面",
        "添加图片",
        "点击上传",
    )
    if modal_open:
        hints_pw = tuple(h for h in hints_pw if h not in _TOUTIAO_COVER_MODAL_OS_PICKER_HINTS)
        if not hints_pw:
            logger.info("[TOUTIAO-GRAPHIC] cover_ui_click skip_modal_triggers (avoid OS file dialog)")
            return False
    frames = _toutiao_graphic_frames_ordered(page)
    for text in hints_pw:
        for fr in frames:
            try:
                loc = fr.get_by_text(text, exact=False)
                n = await loc.count()
                if n <= 0:
                    continue
                await loc.first.scroll_into_view_if_needed(timeout=5000)
                await loc.first.click(timeout=5000)
                fu = ""
                try:
                    fu = (getattr(fr, "url", None) or "")[:80]
                except Exception:
                    pass
                logger.info(
                    "[TOUTIAO-GRAPHIC] cover_ui_click playwright text=%s frame_url=%s",
                    text,
                    fu,
                )
                _step("点击封面入口", True, text=text[:28])
                return True
            except Exception as e:
                logger.info(
                    "[TOUTIAO-GRAPHIC] cover_ui_click playwright skip text=%s err=%s",
                    text,
                    e,
                )
                continue
    js_hints = [
        "上传封面",
        "添加封面",
        "选择封面",
        "本地上传",
        "上传图片",
        "本地图片",
        "更换封面",
        "添加图片",
        "点击上传",
    ]
    if modal_open:
        js_hints = [h for h in js_hints if h not in _TOUTIAO_COVER_MODAL_OS_PICKER_HINTS]
    for fr in frames:
        try:
            hit = await fr.evaluate(
                """(hints) => {
              const nodes = Array.from(
                document.querySelectorAll('button, [role="button"], div, span, a, label')
              );
              for (const el of nodes) {
                if (!el.offsetParent) continue;
                const t = (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 48);
                if (!t) continue;
                for (const h of hints) {
                  if (t.includes(h)) {
                    try {
                      el.scrollIntoView({ block: 'center', inline: 'nearest' });
                      el.click();
                      return h;
                    } catch (e) {}
                  }
                }
              }
              return '';
            }""",
                js_hints,
            )
            if hit:
                logger.info("[TOUTIAO-GRAPHIC] cover_ui_click evaluate hit=%s", hit)
                _step("点击封面入口", True, ui=str(hit)[:32])
                return True
        except Exception as e:
            logger.info("[TOUTIAO-GRAPHIC] cover_ui_click evaluate err frame=%s: %s", fr, e)
    logger.info(
        "[TOUTIAO-GRAPHIC] cover_ui_click no_hit n_frames=%s hints_pw=%s",
        len(frames),
        len(hints_pw),
    )
    return False


async def _toutiao_graphic_set_cover_via_file_chooser(
    page: Any,
    image_path: str,
    _step: Callable[..., None],
) -> bool:
    """
    task_53/54 日志：选「单图」后 n_light/n_deep 仍为 0，封面区可能只走原生文件选择器，DOM 无稳定 file input。
    优先弹窗内 set_input_files；仍失败再用 expect_file_chooser + set_files（与点击配对，避免裸点「本地上传」）。
    """
    if not os.path.isfile(image_path):
        return False
    if await _toutiao_graphic_set_cover_via_modal_file_input(page, image_path, _step):
        return True
    frames = _toutiao_graphic_frames_ordered(page)
    text_vals = (
        "本地上传",
        "上传图片",
        "点击上传",
        "选择图片",
        "添加图片",
        "上传封面",
        "+ 上传",
        "更换封面",
        "从本地上传",
    )
    for val in text_vals:
        total_cnt = 0
        for fr in frames:
            try:
                total_cnt += await fr.get_by_text(val, exact=False).count()
            except Exception:
                continue
        if total_cnt == 0:
            logger.info("[TOUTIAO-GRAPHIC] file_chooser text=%r count=0 all_frames", val)
        for fr in frames:
            try:
                loc = fr.get_by_text(val, exact=False)
                if await loc.count() == 0:
                    continue
                target = loc.first
                await target.scroll_into_view_if_needed(timeout=5000)
                fu = ""
                try:
                    fu = (getattr(fr, "url", None) or "")[:80]
                except Exception:
                    pass
                logger.info(
                    "[TOUTIAO-GRAPHIC] file_chooser try_click text=%r frame=%s",
                    val,
                    fu,
                )
                async with page.expect_file_chooser(timeout=8000) as fc_info:
                    await target.click(timeout=5000, force=True)
                chooser = await fc_info.value
                await chooser.set_files(image_path)
                _step("上传封面(文件选择器)", True, via=val[:24])
                logger.info("[TOUTIAO-GRAPHIC] cover file_chooser ok via=%s frame=%s", val, fu)
                await asyncio.sleep(1.0)
                await _toutiao_graphic_finalize_cover_material_modal(page, _step)
                return True
            except Exception as e:
                logger.info("[TOUTIAO-GRAPHIC] cover file_chooser skip %r frame: %s", val, e)
                continue
    rx_vals = (
        re.compile(r"本地上传"),
        re.compile(r"上传.{0,4}图"),
        re.compile(r"点击.{0,4}上传"),
        re.compile(r"添加.{0,4}图"),
    )
    for rx in rx_vals:
        total_cnt = 0
        for fr in frames:
            try:
                total_cnt += await fr.get_by_text(rx).count()
            except Exception:
                continue
        if total_cnt == 0:
            logger.info("[TOUTIAO-GRAPHIC] file_chooser regex=%s count=0 all_frames", rx.pattern)
        for fr in frames:
            try:
                loc = fr.get_by_text(rx)
                if await loc.count() == 0:
                    continue
                target = loc.first
                await target.scroll_into_view_if_needed(timeout=5000)
                async with page.expect_file_chooser(timeout=8000) as fc_info:
                    await target.click(timeout=5000, force=True)
                chooser = await fc_info.value
                await chooser.set_files(image_path)
                _step("上传封面(文件选择器)", True, via=rx.pattern[:24])
                logger.info("[TOUTIAO-GRAPHIC] cover file_chooser ok regex=%s", rx.pattern)
                await asyncio.sleep(1.0)
                await _toutiao_graphic_finalize_cover_material_modal(page, _step)
                return True
            except Exception as e:
                logger.info("[TOUTIAO-GRAPHIC] cover file_chooser rx skip %s: %s", rx.pattern, e)
                continue
    _marked_js = """() => {
          document.querySelectorAll('[data-tt-cover-uc]').forEach((e) => e.removeAttribute('data-tt-cover-uc'));
          const bad = /发文|发布|发表|草稿|定时|预览|定时发布/;
          function nearCover(el) {
            let n = el;
            for (let i = 0; i < 8 && n; i++) {
              const tx = (n.innerText || '').slice(0, 300);
              if (tx.includes('封面') || tx.includes('展示封面') || tx.includes('单图')) return true;
              n = n.parentElement;
            }
            return false;
          }
          const nodes = Array.from(document.querySelectorAll('div, span, label, button, a'));
          for (const el of nodes) {
            if (!el.offsetParent) continue;
            const t = (el.innerText || '').replace(/\\s+/g, '').slice(0, 28);
            if (!t || t.length > 24) continue;
            if (!/上传|添加图|本地|选择图/.test(t)) continue;
            if (bad.test(t)) continue;
            if (!nearCover(el)) continue;
            const rect = el.getBoundingClientRect();
            if (rect.width < 16 || rect.height < 10) continue;
            try {
              el.setAttribute('data-tt-cover-uc', '1');
              return true;
            } catch (e) {}
          }
          return false;
        }"""
    for fr in frames:
        try:
            marked = await fr.evaluate(_marked_js)
        except Exception as e:
            logger.info("[TOUTIAO-GRAPHIC] cover file_chooser marked js err frame: %s", e)
            continue
        if not marked:
            continue
        try:
            loc = fr.locator('[data-tt-cover-uc="1"]').first
            if await loc.count() <= 0:
                continue
            await loc.scroll_into_view_if_needed(timeout=5000)
            async with page.expect_file_chooser(timeout=8000) as fc_info:
                await loc.click(timeout=5000, force=True)
            chooser = await fc_info.value
            await chooser.set_files(image_path)
            _step("上传封面(文件选择器)", True, via="封面区上传节点")
            logger.info("[TOUTIAO-GRAPHIC] cover file_chooser ok marked-near-cover")
            await asyncio.sleep(1.0)
            await _toutiao_graphic_finalize_cover_material_modal(page, _step)
            return True
        except Exception as e:
            logger.info("[TOUTIAO-GRAPHIC] cover file_chooser marked: %s", e)
        finally:
            try:
                await fr.evaluate(
                    """() => {
                          document.querySelectorAll('[data-tt-cover-uc]').forEach((e) => e.removeAttribute('data-tt-cover-uc'));
                        }"""
                )
            except Exception:
                pass
    logger.warning(
        "[TOUTIAO-GRAPHIC] cover file_chooser exhausted n_frames=%s (text+regex+marked)",
        len(frames),
    )
    return False


async def _toutiao_graphic_scroll_step_reveal_below_fold(page: Any) -> float:
    """
    单次向下滚约一屏，便于露出封面区。
    不再遍历页内所有 overflow 容器并一律滚到底（会触发侧栏/列表疯狂滚动，表现为「一直在滚」）。
    """
    try:
        y_after = await page.evaluate(
            """() => {
          const vh = Math.max(320, window.innerHeight || 700);
          const el = document.documentElement;
          const sh = Math.max(el.scrollHeight, (document.body && document.body.scrollHeight) || 0);
          const maxY = Math.max(0, sh - vh);
          const y0 = window.scrollY || window.pageYOffset || 0;
          const y1 = Math.min(y0 + Math.floor(vh * 0.9), maxY);
          window.scrollTo(0, y1);
          return window.scrollY || window.pageYOffset || 0;
        }"""
        )
        return float(y_after or 0)
    except Exception:
        return -1.0
    finally:
        try:
            await page.mouse.wheel(0, 280)
        except Exception:
            pass


async def _toutiao_graphic_prepare_cover_zone(page: Any, _step: Callable[..., None]) -> None:
    """
    首屏无 file input 时向下分步滚动并轮询；已有 input 则完全不滚。
    限制轮次与「滚不动仍 0 input」的早停，避免长时间反复滚动。
    """
    total = await _toutiao_graphic_file_input_total_count(page)
    if total > 0:
        logger.info("[TOUTIAO-GRAPHIC] prepare_cover_zone skip_scroll already total=%s", total)
        _step("滚动露出封面区", True, detail=f"已有{total}个 file input，跳过滚动")
        return

    last_y = -1.0
    stagnant_scroll = 0
    last_total = -1
    stagnant_total = 0
    max_rounds = 10

    for i in range(max_rounds):
        y_before = -1.0
        try:
            y_before = float(await page.evaluate("() => window.scrollY || window.pageYOffset || 0"))
        except Exception:
            pass

        await _toutiao_graphic_scroll_step_reveal_below_fold(page)
        await asyncio.sleep(0.5)

        total = await _toutiao_graphic_file_input_total_count(page)
        logger.info(
            "[TOUTIAO-GRAPHIC] prepare_cover_zone round=%s/%s total_file_inputs=%s",
            i + 1,
            max_rounds,
            total,
        )
        if total > 0:
            _step("滚动露出封面区", True, detail=f"第{i + 1}轮后全页共{total}个 file input")
            await asyncio.sleep(0.35)
            return

        try:
            y_after = float(await page.evaluate("() => window.scrollY || window.pageYOffset || 0"))
        except Exception:
            y_after = y_before

        if y_after <= y_before + 3:
            stagnant_scroll += 1
        else:
            stagnant_scroll = 0
        last_y = y_after

        if total == last_total:
            stagnant_total += 1
        else:
            stagnant_total = 0
            last_total = total

        # 已经滚不动且 input 数量也不变，不必再空转十几轮
        if stagnant_scroll >= 2 and stagnant_total >= 2:
            logger.info(
                "[TOUTIAO-GRAPHIC] prepare_cover_zone early_stop stagnant_scroll=%s y=%s",
                stagnant_scroll,
                last_y,
            )
            break

    logger.warning("[TOUTIAO-GRAPHIC] prepare_cover_zone done_without_inputs last_scroll_y=%s", last_y)
    _step("滚动露出封面区", False, detail="已分步下滚并早停，仍未发现 file input，将仍尝试上传")


async def _log_toutiao_graphic_file_inputs(page: Any, phase: str) -> None:
    """排查封面：按 frame 打印 file input；n 为 light DOM，n_deep 含 Shadow（与日志 task_49 对照）。"""
    parts: List[Any] = []
    total_deep = 0
    for i, fr in enumerate(page.frames):
        try:
            info = await fr.evaluate(
                """() => {
              return Array.from(document.querySelectorAll('input[type=file]')).map((inp, j) => ({
                j, accept: (inp.accept || '').slice(0, 120), vis: !!(inp.offsetParent),
              }));
            }"""
            )
            nd = int(await fr.evaluate(_JS_TOUTIAO_COUNT_FILE_INPUTS_DEEP) or 0)
            url = (getattr(fr, "url", None) or "")[:200]
            n_light = len(info or [])
            total_deep += nd
            parts.append(
                {
                    "frame_i": i,
                    "url": url,
                    "n_light": n_light,
                    "n_deep": nd,
                    "detail": info,
                }
            )
        except Exception as e:
            parts.append({"frame_i": i, "err": str(e)[:160]})
    logger.info("[TOUTIAO-GRAPHIC] file_inputs_%s total_deep=%s by_frame=%s", phase, total_deep, parts)


async def _upload_toutiao_graphic_cover_image(
    page: Any,
    image_path: str,
    _step: Callable[..., None],
) -> tuple[bool, int]:
    """
    将发布主图上传到头条「封面」位（满足最低发布要求）。
    Returns: (success, main_frame 内用于跳过重复位的 input 下标；若封面在子 frame 上传成功则为 -1)
    """
    if not os.path.isfile(image_path):
        logger.warning("[TOUTIAO-GRAPHIC] cover upload skip: file missing %s", image_path)
        return False, -1
    await _log_toutiao_graphic_file_inputs(page, "before_cover_pick")
    try:
        main_fr = page.main_frame
    except Exception:
        main_fr = None
    if main_fr is None and page.frames:
        main_fr = page.frames[0]

    frames = list(page.frames)
    for fi, fr in enumerate(frames):
        label = "main" if main_fr is not None and fr == main_fr else f"frame_{fi}"
        idx = await _cover_slot_file_input_index_for_frame(fr)
        all_inp = await fr.query_selector_all('input[type="file"]')
        try:
            n_deep = int(await fr.evaluate(_JS_TOUTIAO_COUNT_FILE_INPUTS_DEEP) or 0)
        except Exception:
            n_deep = -1
        logger.info(
            "[TOUTIAO-GRAPHIC] cover_try label=%s picked_index=%s n_light=%s n_deep=%s path=%s",
            label,
            idx,
            len(all_inp),
            n_deep,
            image_path,
        )
        if idx < 0 or idx >= len(all_inp):
            continue
        try:
            await all_inp[idx].set_input_files(image_path)
            _step("上传封面(发布主图)", True, input_index=idx, frame=label)
            await asyncio.sleep(2.0)
            slot = idx if main_fr is not None and fr == main_fr else -1
            return True, slot
        except Exception as e:
            logger.warning("[TOUTIAO-GRAPHIC] cover set_input_files fail frame=%s idx=%s: %s", label, idx, e)

    for fi, fr in enumerate(frames):
        label = "main" if main_fr is not None and fr == main_fr else f"frame_{fi}"
        all_inp = await fr.query_selector_all('input[type="file"]')
        for i, inp in enumerate(all_inp):
            try:
                acc = (await inp.get_attribute("accept")) or ""
                al = acc.lower()
                if "video" in al and "image" not in al:
                    continue
                if "image" in al or not acc.strip():
                    await inp.set_input_files(image_path)
                    _step("上传封面(发布主图·按序回退)", True, input_index=i, frame=label)
                    await asyncio.sleep(2.0)
                    slot = i if main_fr is not None and fr == main_fr else -1
                    return True, slot
            except Exception as e:
                logger.info(
                    "[TOUTIAO-GRAPHIC] cover_fb frame=%s i=%s err=%s",
                    label,
                    i,
                    e,
                )
                continue

    for fi, fr in enumerate(frames):
        label = "main" if main_fr is not None and fr == main_fr else f"frame_{fi}"
        jsh = None
        el_handle = None
        try:
            jsh = await fr.evaluate_handle(_JS_TOUTIAO_FIND_BEST_FILE_INPUT_DEEP)
            if jsh:
                el_handle = jsh.as_element()
        except Exception as e:
            logger.info("[TOUTIAO-GRAPHIC] cover deep evaluate_handle frame=%s err=%s", label, e)
        finally:
            if jsh:
                try:
                    await jsh.dispose()
                except Exception:
                    pass
        if not el_handle:
            continue
        try:
            await el_handle.set_input_files(image_path)
            _step("上传封面(发布主图·shadow/deep)", True, frame=label)
            await asyncio.sleep(2.0)
            logger.info("[TOUTIAO-GRAPHIC] cover deep upload ok frame=%s", label)
            return True, -1
        except Exception as e:
            logger.warning("[TOUTIAO-GRAPHIC] cover deep set_input_files fail frame=%s: %s", label, e)
        finally:
            try:
                await el_handle.dispose()
            except Exception:
                pass

    logger.warning("[TOUTIAO-GRAPHIC] cover_slot 全 frame 未成功上传（含 shadow 遍历）")
    _step("上传封面(发布主图)", False, detail="light+shadow 均未 set_input_files 成功")
    return False, -1


async def _upload_optional_second_graphic_image(
    page: Any,
    image_path: str,
    skip_index: int,
    _step: Callable[..., None],
) -> bool:
    """主图已占封面位后，将另一张图传到其它图片 file input（如正文配图）。"""
    if not os.path.isfile(image_path):
        return False
    all_inp = await page.query_selector_all('input[type="file"]')
    for i, inp in enumerate(all_inp):
        if skip_index >= 0 and i == skip_index:
            continue
        try:
            acc = (await inp.get_attribute("accept")) or ""
            al = acc.lower()
            if "video" in al and "image" not in al:
                continue
            await inp.set_input_files(image_path)
            _step("上传补充配图", True, input_index=i)
            await asyncio.sleep(2.0)
            return True
        except Exception:
            continue
    return False


async def _apply_toutiao_ad_declaration(
    page: Any,
    tt: Dict[str, Any],
    _step: Callable[..., None],
) -> None:
    """投放广告 / 推广声明类单选：默认选「否」侧；可用 ad_declaration / ad_option_text 指定。"""
    raw = tt.get("ad_declaration") or tt.get("ad_option_text") or "auto"
    if isinstance(raw, str):
        rs = raw.strip().lower()
        if rs in ("auto", "", "default", "no_promo"):
            option_hints: List[str] = ["否", "不含", "不声明", "非广告", "无推广", "不是"]
            group_hints = ["", "广告", "推广", "声明"]
        elif rs in ("is_promo", "yes", "promo", "true", "1"):
            option_hints = ["是", "含", "声明", "推广"]
            group_hints = ["广告", "推广", "声明"]
        else:
            option_hints = [raw]
            group_hints = ["广告", "推广", "声明", ""]
    else:
        option_hints = ["否"]
        group_hints = ["", "广告", "推广"]

    for gh in group_hints:
        for oh in option_hints:
            try:
                done = await page.evaluate(
                    """([gh, oh]) => {
                      const radios = Array.from(document.querySelectorAll('input[type="radio"]'));
                      for (const r of radios) {
                        if (!r.offsetParent) continue;
                        let block = r.closest('label') || r.parentElement;
                        for (let i = 0; i < 6 && block; i++) {
                          const tx = (block.innerText || '').slice(0, 400);
                          if (gh && !(tx.includes(gh))) { block = block.parentElement; continue; }
                          if (!oh || tx.includes(oh)) {
                            const lab = r.closest('label');
                            const lt = (lab && lab.innerText) ? lab.innerText : tx;
                            if (oh && !lt.includes(oh) && !tx.includes(oh)) { break; }
                            r.click();
                            return true;
                          }
                          break;
                        }
                      }
                      for (const r of radios) {
                        if (!r.offsetParent) continue;
                        let t = '';
                        let el = r;
                        for (let i = 0; i < 8; i++) {
                          el = el.parentElement;
                          if (!el) break;
                          t = (el.innerText || '').slice(0, 500);
                          if (t.includes('广告') || t.includes('推广') || t.includes('投放')) {
                            const lab = r.closest('label');
                            const lt = (lab && lab.innerText) ? lab.innerText : '';
                            if (lt.includes(oh) || t.includes(oh)) { r.click(); return true; }
                            break;
                          }
                        }
                      }
                      return false;
                    }""",
                    [gh, oh],
                )
                if done:
                    _step("投放广告/推广声明单选", True, group_hint=gh or "*", option_hint=oh)
                    await asyncio.sleep(0.4)
                    return
            except Exception as e:
                logger.debug("[TOUTIAO-AD] %s", e)
    _step("投放广告/推广声明单选", False, detail="未匹配到控件，请用 toutiao.radios 指定")


async def _apply_toutiao_custom_controls(
    page: Any,
    tt: Dict[str, Any],
    _step: Callable[..., None],
) -> None:
    """会话/任务 options.toutiao_* 扩展：单选组、占位输入、勾选、点击。"""
    radios = tt.get("radios")
    if isinstance(radios, list):
        for item in radios:
            if not isinstance(item, dict):
                continue
            gh = str(item.get("group_contains") or item.get("group") or "")
            oh = str(item.get("option_contains") or item.get("option") or "")
            if not oh:
                continue
            try:
                ok = await page.evaluate(
                    """([gh, oh]) => {
                      const radios = Array.from(document.querySelectorAll('input[type="radio"]'));
                      for (const r of radios) {
                        if (!r.offsetParent) continue;
                        let p = r.parentElement;
                        for (let i = 0; i < 8 && p; i++) {
                          const tx = (p.innerText || '').slice(0, 600);
                          if (gh && !tx.includes(gh)) { p = p.parentElement; continue; }
                          const lab = r.closest('label');
                          const lt = (lab && lab.innerText) ? lab.innerText : tx;
                          if (lt.includes(oh) || tx.includes(oh)) { r.click(); return true; }
                          break;
                        }
                      }
                      return false;
                    }""",
                    [gh, oh],
                )
                _step(f"自定义单选 group={gh!r} option={oh!r}", bool(ok))
                await asyncio.sleep(0.35)
            except Exception as e:
                _step(f"自定义单选失败 {gh}/{oh}", False, error=str(e)[:120])

    fills = tt.get("fills")
    if isinstance(fills, list):
        for item in fills:
            if not isinstance(item, dict):
                continue
            ph = str(item.get("placeholder_contains") or item.get("placeholder") or "")
            text = str(item.get("text") or "")
            if not ph or not text:
                continue
            try:
                filled = await page.evaluate(
                    """([ph, text]) => {
                      const nodes = document.querySelectorAll('input, textarea');
                      for (const n of nodes) {
                        const p = (n.getAttribute('placeholder') || '');
                        if (p.includes(ph)) {
                          n.focus();
                          n.value = text;
                          n.dispatchEvent(new Event('input', { bubbles: true }));
                          return true;
                        }
                      }
                      return false;
                    }""",
                    [ph, text],
                )
                _step(f"自定义填写 placeholder~{ph[:20]}", bool(filled))
            except Exception as e:
                _step(f"自定义填写失败 {ph}", False, error=str(e)[:120])

    cbs = tt.get("checkboxes")
    if isinstance(cbs, list):
        for item in cbs:
            if not isinstance(item, dict):
                continue
            lh = str(item.get("label_contains") or item.get("label") or "")
            want = bool(item.get("checked", True))
            if not lh:
                continue
            try:
                ok = await page.evaluate(
                    """([lh, want]) => {
                      const boxes = Array.from(document.querySelectorAll('input[type="checkbox"]'));
                      for (const c of boxes) {
                        let p = c.parentElement;
                        for (let i = 0; i < 6 && p; i++) {
                          if ((p.innerText || '').includes(lh)) {
                            if (want !== c.checked) c.click();
                            return true;
                          }
                          p = p.parentElement;
                        }
                      }
                      return false;
                    }""",
                    [lh, want],
                )
                _step(f"自定义勾选 {lh[:24]}", bool(ok))
            except Exception as e:
                _step(f"自定义勾选失败 {lh}", False, error=str(e)[:120])

    clks = tt.get("clicks")
    if isinstance(clks, list):
        for item in clks:
            if isinstance(item, str):
                sub = item
            elif isinstance(item, dict):
                sub = str(item.get("text_contains") or item.get("text") or "")
            else:
                continue
            if not sub:
                continue
            try:
                loc = page.locator(f"button:has-text(\"{sub}\")").first
                if await loc.count() > 0:
                    await loc.click(timeout=4000)
                    _step(f"自定义点击按钮 {sub[:30]}", True)
                    await asyncio.sleep(0.5)
                    continue
            except Exception:
                pass
            try:
                clicked = await page.evaluate(
                    """(sub) => {
                      const els = Array.from(document.querySelectorAll('button,a,span,[role="button"]'));
                      for (const e of els) {
                        if (!e.offsetParent) continue;
                        if ((e.textContent || '').includes(sub)) {
                          e.click();
                          return true;
                        }
                      }
                      return false;
                    }""",
                    sub,
                )
                _step(f"自定义点击 {sub[:30]}", bool(clicked))
            except Exception as e:
                _step(f"自定义点击失败 {sub}", False, error=str(e)[:120])


class ToutiaoDriver(BaseDriver):
    def login_url(self) -> str:
        return LOGIN_ENTRY

    async def check_login(self, page: Any, navigate: bool = True) -> bool:
        try:
            u0 = (getattr(page, "url", None) or "").strip()
        except Exception:
            u0 = ""
        logger.info("[TOUTIAO] check_login start navigate=%s url=%s", navigate, u0[:400])
        if navigate:
            try:
                logger.info("[TOUTIAO-NAV] check_login -> goto HOME %s", HOME_URL)
                await page.goto(
                    HOME_URL, wait_until="domcontentloaded", timeout=_nav_ms(25000)
                )
                await asyncio.sleep(2)
                try:
                    u1 = (getattr(page, "url", None) or "").strip()
                except Exception:
                    u1 = ""
                logger.info("[TOUTIAO-NAV] check_login <- after HOME url=%s", u1[:400])
            except Exception as e:
                logger.warning("[TOUTIAO] check_login goto failed: %s", e)
                return False
        try:
            url = (getattr(page, "url", None) or "").lower()
        except Exception:
            return False

        if "sso.toutiao.com" in url and "login" in url:
            return False
        if "passport" in url and "login" in url:
            return False

        if "mp.toutiao.com" in url:
            if "/login" in url.split("?")[0]:
                return False
            try:
                txt = await page.evaluate(
                    "() => (document.body && document.body.innerText) ? document.body.innerText.slice(0, 6000) : ''"
                )
            except Exception:
                return False
            if not txt:
                return False
            if ("手机号" in txt or "验证码" in txt) and "登录" in txt and "扫码" in txt:
                return False
            if "验证码登录" in txt and "密码登录" in txt:
                return False
            # 发布编辑页多为全屏表单，常不出现后台侧栏里的「创作中心」「作品管理」；
            # 若此处误判未登录，会 navigate 拉回首页，再 publish 进编辑页，看起来像「在编辑页等一会又回首页再进来」。
            path = url.split("?")[0]
            if (
                "/graphic/publish" in path
                or "/xigua/upload-video" in path
                or "/xigua/publish" in path
            ):
                editor_hints = (
                    "标题",
                    "正文",
                    "封面",
                    "预览并发布",
                    "添加封面",
                    "单图",
                    "无封面",
                    "三图",
                    "上传视频",
                    "智能封面",
                    "封面图",
                )
                if any(h in txt for h in editor_hints):
                    logger.info("[TOUTIAO] check_login ok=True (发布编辑页特征词)")
                    return True
                if len(txt.strip()) < 120:
                    logger.info(
                        "[TOUTIAO] check_login 编辑页文案过短(%d)，等待 2s 再读（避免误判未登录）",
                        len(txt.strip()),
                    )
                    await asyncio.sleep(2.0)
                    try:
                        txt = await page.evaluate(
                            "() => (document.body && document.body.innerText) ? document.body.innerText.slice(0, 8000) : ''"
                        )
                    except Exception:
                        pass
                    if txt and any(h in txt for h in editor_hints):
                        logger.info("[TOUTIAO] check_login ok=True (编辑页 hints 延迟命中)")
                        return True
            if "退出" in txt or "数据概览" in txt or "创作中心" in txt or "作品管理" in txt:
                logger.info("[TOUTIAO] check_login ok=True (后台侧栏特征)")
                return True
            if "头条号" in txt and ("发布" in txt or "内容管理" in txt):
                logger.info("[TOUTIAO] check_login ok=True (头条号+发布/内容管理)")
                return True
            try:
                ufail = (getattr(page, "url", None) or "").strip()
            except Exception:
                ufail = ""
            logger.info(
                "[TOUTIAO] check_login ok=False url=%s (将触发 navigate=True 时拉回首页)",
                ufail[:400],
            )
            return False

        logger.info("[TOUTIAO] check_login ok=False (非 mp.toutiao.com 域)")
        return False

    async def publish(
        self,
        page: Any,
        file_path: str,
        title: str,
        description: str,
        tags: str,
        options: Optional[Dict[str, Any]] = None,
        cover_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        options = options or {}
        applied: Dict[str, Any] = {"steps": []}

        def _step(note: str, ok: bool, **extra: Any) -> None:
            applied["steps"].append({"action": note, "ok": ok, **extra})
            logger.info("[TOUTIAO-PUBLISH] %s => %s %s", note, "OK" if ok else "FAIL", extra or "")

        try:
            if not os.path.isfile(file_path):
                _step("检查文件", False, path=file_path)
                return {"ok": False, "error": f"文件不存在: {file_path}", "applied": applied}
            fsize = os.path.getsize(file_path)
            _step("检查文件", True, size=fsize)

            ext = os.path.splitext(file_path)[1].lower()
            is_image = ext in _IMAGE_EXTS
            is_video = ext in _VIDEO_EXTS
            tt0 = _toutiao_extra_options(options)
            graphic_no_cover = _opt_truthy(tt0.get("graphic_no_cover") or tt0.get("no_cover"))

            if is_video and not graphic_no_cover:
                return await self._publish_video(
                    page, file_path, fsize, title, description, tags, options, applied, _step, cover_path=cover_path
                )
            if is_image or graphic_no_cover:
                return await self._publish_graphic(
                    page, file_path, fsize, title, description, tags, options, applied, _step, cover_path=cover_path
                )

            return {
                "ok": False,
                "error": (
                    f"不支持的文件类型: {ext}（头条：视频 {sorted(_VIDEO_EXTS)}；"
                    f"图文用图片；纯文字文章请在 options 设 toutiao_graphic_no_cover: true 并任选一个本地占位文件作 asset）"
                ),
                "applied": applied,
            }
        except Exception as e:
            logger.exception("[TOUTIAO-PUBLISH]")
            _step("异常", False, error=str(e))
            return {"ok": False, "error": str(e), "applied": applied}

    async def _publish_video(
        self,
        page: Any,
        file_path: str,
        fsize: int,
        title: str,
        description: str,
        tags: str,
        options: Dict[str, Any],
        applied: Dict[str, Any],
        _step: Callable[..., None],
        *,
        cover_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        tt = _toutiao_extra_options(options)
        file_input = None
        opened_url = ""
        for url in VIDEO_ENTRY_URLS:
            try:
                try:
                    cur_v = (getattr(page, "url", None) or "").strip()
                except Exception:
                    cur_v = ""
                tn = url.split("?")[0].rstrip("/").lower()
                cn = cur_v.split("?")[0].rstrip("/").lower()
                if cn == tn:
                    logger.info("[TOUTIAO-NAV] video skip goto（已在 %s）", url)
                    await _delay(1.2, 2.2)
                else:
                    logger.info("[TOUTIAO-NAV] video -> goto %s", url)
                    await page.goto(
                        url, wait_until="domcontentloaded", timeout=_nav_ms(45000)
                    )
                    try:
                        logger.info(
                            "[TOUTIAO-NAV] video after goto url=%s",
                            ((getattr(page, "url", None) or "").strip()[:400]),
                        )
                    except Exception:
                        pass
                    await _delay(1.5, 3)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=_pw_ms(12000))
                    except Exception:
                        pass
                    await _delay(0.8, 1.5)
                if "upload-video" in url:
                    clicked = await dismiss_xigua_open_modal(page)
                    if clicked:
                        _step("关闭视频开通引导", True)
                    await _delay(1, 2)
                if await dismiss_toutiao_creative_assistant(page):
                    _step("收起头条创作助手", True, detail="视频上传页，便于定位控件")
                    await _delay(0.35, 0.55)
                file_input = await _file_input_anywhere(page)
                if file_input:
                    opened_url = url
                    _step("打开发布页", True, url=url)
                    break
                _step("打开发布页", False, url=url, hint="无 file input")
            except Exception as e:
                _step("打开发布页", False, url=url, error=str(e)[:200])

        if not file_input:
            logger.info("[TOUTIAO-VIDEO] 各入口 URL 无 file input，按抖音流程尝试点击上传区")
            file_input = await _toutiao_video_try_reveal_file_input(page)

        via_chooser = False
        if not file_input:
            via_chooser = await _toutiao_video_upload_via_file_chooser(page, file_path)

        if not file_input and not via_chooser:
            return {
                "ok": False,
                "error": "未找到视频上传控件。请确认已登录并已关闭「开通西瓜」类弹窗。",
                "applied": applied,
            }

        if file_input:
            await file_input.set_input_files(file_path)
            _step("已选择本地视频", True, via="set_input_files")
        else:
            _step("已选择本地视频", True, via="file_chooser")
        logger.info(
            "[TOUTIAO-VIDEO] video file set size_bytes=%s path=%s via=%s",
            fsize,
            file_path,
            "set_input_files" if file_input else "file_chooser",
        )

        if not await _wait_toutiao_video_editor_ready(page, fsize, _step):
            return {"ok": False, "error": "视频上传或处理超时/失败（大文件请稍后在后台重试）", "applied": applied}

        await _delay(1, 2)

        if await dismiss_toutiao_creative_assistant(page):
            _step("收起头条创作助手", True, detail="视频发布页")
            await _delay(0.45, 0.65)

        # 与探测一致：上传成功后先出现作品标题/简介，封面区常靠下；先填文再处理封面更稳
        title_use = (title or "").strip()[:_TOUTIAO_TITLE_MAX_LEN]
        if title_use:
            try:
                filled = False
                for sel in (
                    'input[placeholder*="标题"]',
                    'textarea[placeholder*="标题"]',
                    'input[placeholder*="概括"]',
                    'input[placeholder*="作品"]',
                ):
                    el = await page.query_selector(sel)
                    if el:
                        await el.fill(title_use)
                        filled = True
                        break
                if not filled:
                    await page.evaluate(
                        """(t) => {
                          const nodes = document.querySelectorAll('input, textarea');
                          for (const i of nodes) {
                            const ph = (i.getAttribute('placeholder') || '').toString();
                            if (ph.includes('标题') || ph.includes('概括') || ph.includes('作品')) {
                              i.focus();
                              i.value = t;
                              i.dispatchEvent(new Event('input', { bubbles: true }));
                              return true;
                            }
                          }
                          return false;
                        }""",
                        title_use,
                    )
                _step("填写标题", True)
            except Exception as e:
                _step("填写标题", False, error=str(e)[:200])

        desc_use = _merge_tags(description, tags)[:5000]
        if desc_use:
            await _fill_toutiao_video_synopsis(page, desc_use, _step)

        if not await _ensure_toutiao_video_cover(page, cover_path, _step):
            return {
                "ok": False,
                "error": "视频发布需要封面：未在无封面图时完成「封面截取→下一步」，且智能选封面也未成功；可选传 cover_asset_id 走本地上传（完善中）",
                "applied": applied,
            }

        await _apply_toutiao_ad_declaration(page, tt, _step)
        await _apply_toutiao_custom_controls(page, tt, _step)

        if options.get("dry_run"):
            _step("dry_run — 未点击发布", True)
            return {"ok": True, "url": getattr(page, "url", "") or "", "applied": applied, "dry_run": True}

        await _delay(1.0, 2.0)
        _step("发布前短暂停顿", True, detail="草稿保存类提示不影响发布，直接点主「发布」")

        if await dismiss_xigua_open_modal(page):
            _step("关闭开通或创作权益引导", True, detail="发布前（小视频创作权益等）")
            await _delay(0.45, 0.9)

        if not await _toutiao_wait_video_publish_clickable(page, 120, _step):
            return {
                "ok": False,
                "error": "视频可能仍在上传/转码，或发布按钮不可用，请稍后重试",
                "applied": applied,
            }

        label = await _toutiao_video_click_primary_publish(page)
        if label:
            logger.info("[TOUTIAO-VIDEO] publish primary CTA label=%s", (label or "")[:48])
        if not label:
            _step("点击发布", False)
            await _toutiao_log_stall_diagnostic(page, "video_publish_no_button_after_wait")
            return {
                "ok": False,
                "error": "未找到主「发布/立即发布/发表」按钮（可能仍在上传中或封面未通过校验）",
                "applied": applied,
            }
        _step("点击发布", True, button=label)
        await _delay(1.0, 2.0)
        for _nag_i in range(3):
            had_revenue_dismiss = False
            if await dismiss_xigua_open_modal(page):
                had_revenue_dismiss = True
                _step("关闭开通或创作权益引导", True, detail="点发布后弹层", round=_nag_i)
                await _delay(0.55, 1.0)
                label2 = await _toutiao_video_click_primary_publish(page)
                if label2:
                    _step("权益弹窗关闭后再次点击发布", True, button=label2)
                    await _delay(1.0, 2.0)
                else:
                    _step("权益弹窗关闭后再次点击发布", False)
            nag_btn = await _toutiao_video_dismiss_cover_compliance_nag_if_present(page)
            if nag_btn:
                _step("封面不合规提示-点取消", True, detail=nag_btn[:40])
                await _delay(0.55, 1.1)
                label2 = await _toutiao_video_click_primary_publish(page)
                if not label2:
                    _step("再次点击发布", False, hint="已关弹窗但未再点到主发布按钮")
                    break
                _step("再次点击发布", True, button=label2)
                await _delay(1.0, 2.0)
                continue
            if not had_revenue_dismiss:
                break
        await _delay(0.8, 2.5)
        if label and "预览" in label:
            if not await _toutiao_after_preview_publish(page, _step):
                await _toutiao_log_stall_diagnostic(page, "video_preview_confirm_publish_timeout")
                return {
                    "ok": False,
                    "error": "已点「预览并发布」但未在限时内完成「确认发布」步骤，内容可能仍停留在预览页",
                    "applied": applied,
                    "opened_entry": opened_url,
                }

        entry_reset = (opened_url or "").strip() or VIDEO_UPLOAD_ENTRY_URL
        await _toutiao_post_success_reload_entry(page, entry_reset, _step, branch="video")
        return {
            "ok": True,
            "url": getattr(page, "url", "") or "",
            "applied": applied,
            "opened_entry": opened_url,
            "toutiao_submission_hint": _TOUTIAO_SUBMISSION_USER_HINT,
        }

    async def _publish_graphic(
        self,
        page: Any,
        file_path: str,
        fsize: int,
        title: str,
        description: str,
        tags: str,
        options: Dict[str, Any],
        applied: Dict[str, Any],
        _step: Callable[..., None],
        *,
        cover_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """图文：graphic/publish；须标题+正文。无封面模式见 graphic_no_cover。"""
        tt = _toutiao_extra_options(options)
        graphic_no_cover = _opt_truthy(tt.get("graphic_no_cover") or tt.get("no_cover"))
        body_preview = _merge_tags(description, tags)
        if not (title or "").strip():
            _step("校验标题", False)
            return {"ok": False, "error": "头条图文须填写标题", "applied": applied}
        if not (body_preview or "").strip():
            _step("校验正文", False)
            return {"ok": False, "error": "头条图文须填写正文/描述", "applied": applied}

        try:
            try:
                u_g0 = (getattr(page, "url", None) or "").strip()
            except Exception:
                u_g0 = ""
            logger.info("[TOUTIAO-NAV] graphic before goto url=%s", u_g0[:400])
            if _is_toutiao_graphic_publish_url(u_g0):
                logger.info("[TOUTIAO-NAV] graphic skip goto（已在图文发布页，避免整页重载）")
                await _delay(0.8, 1.6)
            else:
                logger.info("[TOUTIAO-NAV] graphic -> goto %s", GRAPHIC_PUBLISH_URL)
                await page.goto(
                    GRAPHIC_PUBLISH_URL,
                    wait_until="domcontentloaded",
                    timeout=_nav_ms(45000),
                )
                try:
                    u_g1 = (getattr(page, "url", None) or "").strip()
                except Exception:
                    u_g1 = ""
                logger.info("[TOUTIAO-NAV] graphic after goto url=%s", u_g1[:400])
                await _delay(2, 4)
        except Exception as e:
            _step("打开图文发布页", False, error=str(e)[:200])
            return {"ok": False, "error": f"无法打开图文发布页: {e}", "applied": applied}

        if await dismiss_toutiao_creative_assistant(page):
            _step("收起头条创作助手", True, detail="主编辑区外侧点击")
            await _delay(0.55, 0.75)

        # 先填首屏标题/正文，再滚动第二屏做封面，避免先滚下去后标题、正文控件不好定位
        merged_body = _merge_tags(description, tags)[:5000]
        if not await _fill_toutiao_graphic_title(page, (title or "").strip(), _step):
            return {
                "ok": False,
                "error": "未能填写标题（请确认标题框与正文编辑器未被头条改版）",
                "applied": applied,
            }
        if not await _fill_toutiao_graphic_body(page, merged_body, _step):
            return {
                "ok": False,
                "error": "未能填写正文（请确认正文区与标题分离）",
                "applied": applied,
            }

        had_upload = False
        cover_slot_idx = -1
        if graphic_no_cover:
            _step("图文无封面(仅标题+正文)", True)
            await _toutiao_try_switch_no_image_cover_mode(page, _step)
        else:
            await _toutiao_graphic_ensure_single_image_cover_mode(page, _step)
            await asyncio.sleep(1.0)
            await _toutiao_graphic_open_cover_upload_modal(page, _step)
            await asyncio.sleep(0.45)
            if await _toutiao_graphic_set_cover_via_modal_file_input(page, file_path, _step):
                had_upload = True
                cover_slot_idx = -1
            elif not await _toutiao_graphic_cover_modal_visible_any_frame(page):
                if await _toutiao_graphic_try_activate_cover_upload_ui(page, _step):
                    await asyncio.sleep(0.7)
            await _toutiao_graphic_prepare_cover_zone(page, _step)
            if not had_upload and not await _toutiao_graphic_cover_modal_visible_any_frame(page):
                if await _toutiao_graphic_try_activate_cover_upload_ui(page, _step):
                    await asyncio.sleep(0.7)

            if not had_upload and await _toutiao_graphic_set_cover_via_file_chooser(page, file_path, _step):
                had_upload = True
                cover_slot_idx = -1
            elif not had_upload:
                ok_slot, cover_slot_idx = await _upload_toutiao_graphic_cover_image(page, file_path, _step)
                if ok_slot:
                    had_upload = True
            if not had_upload:
                try:
                    all_inp = await page.query_selector_all('input[type="file"]')
                    logger.info("[TOUTIAO-GRAPHIC] cover_fallback loop n_inputs=%s", len(all_inp))
                    for i, inp in enumerate(all_inp):
                        acc = ""
                        try:
                            acc = (await inp.get_attribute("accept")) or ""
                            al = acc.lower()
                            if "video" in al and "image" not in al:
                                continue
                            if "image" in al or not acc.strip():
                                await inp.set_input_files(file_path)
                                had_upload = True
                                cover_slot_idx = i
                                _step("上传封面(回退图片位)", True, input_index=i)
                                logger.info(
                                    "[TOUTIAO-GRAPHIC] cover_fallback ok i=%s accept=%s",
                                    i,
                                    (acc or "")[:100],
                                )
                                break
                        except Exception as ex:
                            logger.info(
                                "[TOUTIAO-GRAPHIC] cover_fallback try i=%s accept=%s err=%s",
                                i,
                                (acc or "")[:100],
                                ex,
                            )
                            continue
                    if not had_upload:
                        inp = await _file_input_anywhere(page)
                        if inp:
                            acc = (await inp.get_attribute("accept")) or ""
                            if "image" in acc.lower() or not acc:
                                try:
                                    await inp.set_input_files(file_path)
                                    had_upload = True
                                    _step("上传封面(回退任意图片位)", True)
                                    logger.info("[TOUTIAO-GRAPHIC] cover_fallback _file_input_anywhere ok")
                                except Exception as e:
                                    logger.warning("[TOUTIAO-GRAPHIC] cover_fallback _file_input_anywhere: %s", e)
                                    _step("上传封面", False, error=str(e)[:160])
                        if not had_upload:
                            logger.warning(
                                "[TOUTIAO-GRAPHIC] cover all paths failed had_upload=false n_inputs=%s",
                                len(all_inp),
                            )
                            await _log_toutiao_graphic_file_inputs(page, "after_all_cover_fail")
                except Exception as e:
                    logger.warning("[TOUTIAO-GRAPHIC] cover fallback outer: %s", e)

        if not await _wait_toutiao_graphic_ready(page, fsize, _step, had_upload=had_upload):
            return {"ok": False, "error": "图文页上传或加载超时", "applied": applied}

        await _delay(1, 2)

        if not had_upload and not graphic_no_cover:
            return {
                "ok": False,
                "error": "头条图文需要封面图时请提供图片素材；若只要纯文字请在 options 中设置 toutiao_graphic_no_cover: true",
                "applied": applied,
            }

        if (
            not graphic_no_cover
            and cover_path
            and os.path.isfile(cover_path)
            and os.path.abspath(cover_path) != os.path.abspath(file_path)
        ):
            if not await _upload_optional_second_graphic_image(page, cover_path, cover_slot_idx, _step):
                _step("补充配图(可选)", False, detail="与主封面不同的第二素材未写入，可仅发封面+正文")

        await _apply_toutiao_ad_declaration(page, tt, _step)
        await _apply_toutiao_custom_controls(page, tt, _step)

        if options.get("dry_run"):
            _step("dry_run — 未点击发布", True)
            return {"ok": True, "url": getattr(page, "url", "") or "", "applied": applied, "dry_run": True}

        await _delay(1.0, 2.0)
        _step("发布前短暂停顿", True, detail="草稿保存类提示不影响发布，直接点预览并发布")

        if had_upload and not graphic_no_cover:
            await _toutiao_graphic_nudge_cover_modal_confirm(page)
            await _toutiao_graphic_click_cover_modal_confirm_js(page)
            await asyncio.sleep(0.45)
            if await _toutiao_graphic_modal_still_blocks_publish(page):
                await _toutiao_graphic_try_confirm_cover_modal(page)
                await _toutiao_graphic_nudge_cover_modal_confirm(page)
                await _toutiao_graphic_click_cover_modal_confirm_js(page)
                await asyncio.sleep(0.65)
            if await _toutiao_graphic_modal_still_blocks_publish(page):
                for _ in range(3):
                    await _toutiao_graphic_click_cover_modal_confirm_js(page)
                    await asyncio.sleep(0.5)
                    if not await _toutiao_graphic_modal_still_blocks_publish(page):
                        break
            if await _toutiao_graphic_modal_still_blocks_publish(page):
                _step("封面弹窗未关闭", False)
                return {
                    "ok": False,
                    "error": "「上传图片」弹窗仍在（未成功点「确定」），无法点「预览并发布」。",
                    "applied": applied,
                    "opened_entry": GRAPHIC_PUBLISH_URL,
                }

        try:
            await page.evaluate(
                """() => {
              const sh = Math.max(
                document.documentElement.scrollHeight,
                (document.body && document.body.scrollHeight) || 0
              );
              window.scrollTo(0, Math.max(0, sh - Math.min(900, Math.floor(window.innerHeight * 1.2))));
            }"""
            )
        except Exception:
            pass
        await asyncio.sleep(0.45)

        if await dismiss_xigua_open_modal(page):
            _step("关闭开通或创作权益引导", True, detail="图文点预览并发布前")
            await _delay(0.45, 0.85)

        label = await _toutiao_graphic_click_primary_publish(page)
        if not label:
            _step("点击发布", False)
            return {"ok": False, "error": "未找到「预览并发布」或「发布/发表」按钮", "applied": applied}
        _step("点击发布", True, button=label)
        await _delay(2, 5)
        if await dismiss_xigua_open_modal(page):
            _step("关闭开通或创作权益引导", True, detail="图文首次点发布后")
            await _delay(0.55, 1.0)
            label2 = await _toutiao_graphic_click_primary_publish(page)
            if label2:
                _step("权益弹窗关闭后再次点击发布", True, button=label2)
                await _delay(1.0, 2.5)
        if "预览" not in (label or ""):
            logger.warning(
                "[TOUTIAO-GRAPHIC] 主按钮文案无「预览」仍强制执行预览后确认: label=%r",
                (label or "")[:80],
            )
        if not await _toutiao_after_preview_publish(page, _step):
            return {
                "ok": False,
                "error": "未完成「确认发布」步骤（可能未点到「预览并发布」或仍停留在编辑/预览页），不得视为已发布。",
                "applied": applied,
                "opened_entry": GRAPHIC_PUBLISH_URL,
            }

        await _toutiao_post_success_reload_entry(page, GRAPHIC_PUBLISH_URL, _step, branch="graphic")
        return {
            "ok": True,
            "url": getattr(page, "url", "") or "",
            "applied": applied,
            "opened_entry": GRAPHIC_PUBLISH_URL,
            "toutiao_submission_hint": _TOUTIAO_SUBMISSION_USER_HINT,
        }
