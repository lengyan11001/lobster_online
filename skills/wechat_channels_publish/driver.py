"""WeChat Channels creator-platform publish driver."""
from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import Any, Dict, Optional

from publisher.pw_timeouts import ms as _pw_ms
from publisher.pw_timeouts import navigation_timeout_ms as _nav_ms

from skills._base import BaseDriver

logger = logging.getLogger(__name__)

HOME_URL = "https://channels.weixin.qq.com/platform"
LOGIN_URL = "https://channels.weixin.qq.com/login.html"
PUBLISH_URL = "https://channels.weixin.qq.com/platform/post/create"

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
_TITLE_MAX = 30
_DESC_MAX = 1000

_MEDIA_IMAGE = "image"
_MEDIA_VIDEO = "video"


async def _delay(lo: float = 0.4, hi: float = 1.2) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


def _env_int(name: str, default: int, min_value: int, max_value: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(min_value, min(value, max_value))


def _typing_delay_bounds_ms() -> tuple[int, int]:
    lo = _env_int("WECHAT_CHANNELS_TYPE_DELAY_MIN_MS", 65, 20, 500)
    hi = _env_int("WECHAT_CHANNELS_TYPE_DELAY_MAX_MS", 165, 40, 800)
    if hi < lo:
        hi = lo
    return lo, hi


async def _human_type_text(page: Any, text: str) -> None:
    if not text:
        return
    lo, hi = _typing_delay_bounds_ms()
    next_micro_pause = random.randint(7, 15)
    for idx, char in enumerate(text, start=1):
        await page.keyboard.type(char, delay=random.randint(lo, hi))
        if char in "，。！？；：、,.!?;:\n":
            await asyncio.sleep(random.uniform(0.18, 0.55))
        elif idx >= next_micro_pause:
            await asyncio.sleep(random.uniform(0.08, 0.28))
            next_micro_pause = idx + random.randint(7, 15)


async def _human_click(page: Any, loc: Any, *, timeout_ms: int = 12000) -> None:
    try:
        await loc.scroll_into_view_if_needed(timeout=_pw_ms(5000))
    except Exception:
        pass
    await _delay(0.35, 0.9)
    try:
        box = await loc.bounding_box()
    except Exception:
        box = None
    if box and float(box.get("width") or 0) > 2 and float(box.get("height") or 0) > 2:
        width = float(box.get("width") or 0)
        height = float(box.get("height") or 0)
        x = float(box.get("x") or 0) + random.uniform(width * 0.25, width * 0.75)
        y = float(box.get("y") or 0) + random.uniform(height * 0.30, height * 0.70)
        try:
            await page.mouse.move(x + random.uniform(-8, 8), y + random.uniform(-5, 5), steps=random.randint(8, 18))
            await _delay(0.12, 0.35)
            await page.mouse.move(x, y, steps=random.randint(3, 8))
            await _delay(0.10, 0.28)
            await page.mouse.click(x, y, delay=random.randint(70, 180))
            await _delay(0.35, 0.85)
            return
        except Exception as exc:
            logger.debug("[WECHAT-CHANNELS] human mouse click fallback: %s", exc)
    try:
        await loc.click(timeout=_pw_ms(timeout_ms))
    except Exception:
        await loc.click(force=True, timeout=_pw_ms(timeout_ms))
    await _delay(0.35, 0.85)


async def _human_click_box(page: Any, box: Dict[str, Any]) -> None:
    width = float(box.get("width") or box.get("w") or 0)
    height = float(box.get("height") or box.get("h") or 0)
    if width <= 2 or height <= 2:
        return
    x = float(box.get("x") or 0) + random.uniform(width * 0.25, width * 0.75)
    y = float(box.get("y") or 0) + random.uniform(height * 0.30, height * 0.70)
    await page.mouse.move(x + random.uniform(-8, 8), y + random.uniform(-5, 5), steps=random.randint(8, 18))
    await _delay(0.10, 0.28)
    await page.mouse.move(x, y, steps=random.randint(3, 8))
    await _delay(0.10, 0.25)
    await page.mouse.click(x, y, delay=random.randint(70, 180))
    await _delay(0.35, 0.85)


async def _human_clear_focused(page: Any) -> None:
    await page.keyboard.press("Control+KeyA")
    await _delay(0.08, 0.22)
    await page.keyboard.press("Delete")
    await _delay(0.15, 0.35)


def _merge_tags(description: str, tags: str) -> str:
    text = (description or "").strip()
    tag_parts = [item.strip().lstrip("#") for item in (tags or "").split(",") if item.strip()]
    if tag_parts:
        text = (text + " " + " ".join(f"#{item}" for item in tag_parts)).strip()
    return text[:_DESC_MAX]


def _option_value(options: Dict[str, Any], *keys: str) -> str:
    if not isinstance(options, dict):
        return ""
    for key in keys:
        if key in options and options.get(key) is not None:
            value = options.get(key)
            if isinstance(value, bool):
                return "true" if value else "false"
            text = str(value or "").strip()
            if text:
                return text
    return ""


def _option_bool(options: Dict[str, Any], *keys: str) -> Optional[bool]:
    if not isinstance(options, dict):
        return None
    for key in keys:
        if key not in options or options.get(key) is None:
            continue
        value = options.get(key)
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "on", "是", "开启", "启用"}:
            return True
        if text in {"0", "false", "no", "off", "否", "关闭", "不启用"}:
            return False
    return None


def _upload_wait_seconds(file_size: int) -> int:
    # Base 90s, plus roughly 1s per 3MB, capped at 20 minutes.
    return min(1200, 90 + int(max(0, file_size) / (3 * 1024 * 1024)))


async def _deep_text(page: Any, limit: int = 80000) -> str:
    try:
        return await page.evaluate(
            """
            (limit) => {
              const chunks = [];
              const seen = new Set();
              const walk = (root) => {
                if (!root || seen.has(root)) return;
                seen.add(root);
                const hostText = root.body ? root.body.innerText : (root.innerText || '');
                if (hostText) chunks.push(hostText);
                const nodes = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (const el of nodes) {
                  const text = el.innerText || '';
                  if (text && text.length < 20000) chunks.push(text);
                  if (el.shadowRoot) walk(el.shadowRoot);
                }
              };
              walk(document);
              return chunks.join('\\n').slice(0, limit);
            }
            """,
            limit,
        )
    except Exception:
        try:
            return await page.locator("body").inner_text(timeout=_pw_ms(3000))
        except Exception:
            return ""


async def _login_signals(page: Any) -> Dict[str, Any]:
    text = await _deep_text(page, 60000)
    url = ""
    try:
        url = getattr(page, "url", "") or ""
    except Exception:
        url = ""
    counts: Dict[str, int] = {}
    for key, selector in {
        "file_inputs": 'input[type="file"]',
        "desc_editor": 'div.input-editor[data-placeholder="添加描述"]',
        "short_title": 'input[placeholder="填写短标题有机会获得更多流量"]',
        "publish_buttons": 'button:has-text("发表")',
        "wujie_apps": "wujie-app",
    }.items():
        try:
            counts[key] = await page.locator(selector).count()
        except Exception:
            counts[key] = 0
    contains = {
        "video_id": "视频号ID" in text,
        "account_menu": "数据中心" in text or "内容管理" in text or "互动管理" in text,
        "publish_form": "发表" in text or "手机预览" in text or "保存草稿" in text,
        "login_qr": "扫码登录" in text or "微信扫码" in text,
        "assistant_title": "视频号助手" in text or "视频号 · 助手" in text,
    }
    return {
        "url": url,
        "contains": contains,
        "counts": counts,
        "text_head": text[:240],
    }


def _signals_logged_in(signals: Dict[str, Any]) -> bool:
    url = str(signals.get("url") or "").lower()
    contains = signals.get("contains") if isinstance(signals.get("contains"), dict) else {}
    counts = signals.get("counts") if isinstance(signals.get("counts"), dict) else {}
    if "channels.weixin.qq.com/login" in url:
        return False
    if contains.get("account_menu") or contains.get("video_id") or contains.get("publish_form"):
        return True
    if int(counts.get("file_inputs") or 0) > 0 and contains.get("assistant_title"):
        return True
    return False


async def _wait_login_ready(page: Any, seconds: int = 20) -> bool:
    last: Dict[str, Any] = {}
    for _ in range(max(1, seconds)):
        last = await _login_signals(page)
        if _signals_logged_in(last):
            logger.info("[WECHAT-CHANNELS] login signals ok: %s", {k: v for k, v in last.items() if k != "text_head"})
            return True
        await asyncio.sleep(1)
    logger.warning("[WECHAT-CHANNELS] login signals missing: %s", last)
    return False


async def _first_visible_locator(page: Any, selector: str) -> Any:
    loc = page.locator(selector)
    try:
        count = await loc.count()
    except Exception:
        count = 0
    for idx in range(count):
        item = loc.nth(idx)
        try:
            if await item.is_visible(timeout=_pw_ms(1200)):
                return item
        except Exception:
            continue
    return None


def _css_text(text: str) -> str:
    return str(text or "").replace("\\", "\\\\").replace('"', '\\"')


def _normalize_button_text(text: str) -> str:
    return "".join(str(text or "").split())


def _accept_matches_kind(accept: str, media_kind: str, *, generic_ok: bool = False) -> bool:
    text = (accept or "").strip().lower()
    if not text:
        return generic_ok
    if media_kind == _MEDIA_IMAGE:
        if "image" in text or any(ext in text for ext in _IMAGE_EXTS):
            return True
        return False
    if media_kind == _MEDIA_VIDEO:
        if "video" in text or "mp4" in text or "quicktime" in text or "movie" in text:
            return True
        return False
    return generic_ok


async def _find_file_input(page: Any, media_kind: str = _MEDIA_VIDEO, wait_ms: int = 30000) -> Any:
    try:
        await page.wait_for_selector('input[type="file"]', state="attached", timeout=_pw_ms(wait_ms))
    except Exception:
        pass
    loc = page.locator('input[type="file"]')
    try:
        count = await loc.count()
    except Exception:
        count = 0
    generic_fallback = None
    any_fallback = None
    for idx in range(count):
        item = loc.nth(idx)
        try:
            accept = ((await item.get_attribute("accept")) or "").lower()
        except Exception:
            accept = ""
        if any_fallback is None:
            any_fallback = item
        if not accept and generic_fallback is None:
            generic_fallback = item
        if _accept_matches_kind(accept, media_kind):
            return item
    # Only use a blank-accept input as fallback. Do not upload images into a video-only input
    # after the SPA leaves us on the wrong publish page.
    return generic_fallback


async def _visible_file_input_for_kind(page: Any, media_kind: str) -> Any:
    return await _find_file_input(page, media_kind=media_kind, wait_ms=1200)


async def _click_first_visible_text(page: Any, labels: tuple[str, ...], *, exact: bool = True) -> str:
    for label in labels:
        label_s = str(label or "").strip()
        if not label_s:
            continue
        candidates = [
            f'button:has-text("{_css_text(label_s)}")',
            f'a:has-text("{_css_text(label_s)}")',
            f'[role="button"]:has-text("{_css_text(label_s)}")',
            f'span:has-text("{_css_text(label_s)}")',
            f'div:has-text("{_css_text(label_s)}")',
        ]
        for selector in candidates:
            loc = page.locator(selector)
            try:
                count = min(await loc.count(), 10)
            except Exception:
                count = 0
            for idx in range(count):
                item = loc.nth(idx)
                try:
                    if not await item.is_visible(timeout=_pw_ms(700)):
                        continue
                    raw = (await item.inner_text(timeout=_pw_ms(1000))).strip()
                    normalized = _normalize_button_text(raw)
                    target = _normalize_button_text(label_s)
                    if exact and normalized != target:
                        continue
                    box = await item.bounding_box()
                    if box:
                        width = float(box.get("width") or 0)
                        height = float(box.get("height") or 0)
                        if width < 8 or height < 8:
                            continue
                        # Avoid clicking a whole-page container matched by div:has-text().
                        if selector.startswith("div:") and (width > 520 or height > 180):
                            continue
                    await _human_click(page, item, timeout_ms=7000)
                    return label_s
                except Exception:
                    continue
    return ""


async def _upload_media_file(page: Any, file_input: Any, file_path: str) -> str:
    upload_area = await _first_visible_locator(page, 'span.ant-upload.ant-upload-btn[role="button"]')
    if not upload_area:
        upload_area = await _first_visible_locator(page, '[role="button"]:has-text("上传")')
    if upload_area:
        try:
            async with page.expect_file_chooser(timeout=_pw_ms(10000)) as fc_info:
                await _human_click(page, upload_area, timeout_ms=6000)
            chooser = await fc_info.value
            await _delay(0.35, 0.9)
            await chooser.set_files(file_path)
            await _delay(0.6, 1.4)
            return "file_chooser"
        except Exception as exc:
            logger.warning("[WECHAT-CHANNELS] upload via chooser failed, fallback input: %s", exc)
    await _delay(0.4, 1.0)
    await file_input.set_input_files(file_path)
    await _delay(0.6, 1.4)
    return "input"


async def _publish_button(page: Any) -> Any:
    locators = [
        page.locator('button:has-text("发表")'),
        page.locator('button:has-text("发布")'),
    ]
    best = None
    best_y = -1.0
    for loc in locators:
        try:
            count = await loc.count()
        except Exception:
            count = 0
        for idx in range(count):
            item = loc.nth(idx)
            try:
                if not await item.is_visible(timeout=_pw_ms(1200)):
                    continue
                text = _normalize_button_text(await item.inner_text(timeout=_pw_ms(1000)))
                if text not in {"发表", "发布"}:
                    continue
                box = await item.bounding_box()
                y = float((box or {}).get("y") or 0)
                if y >= best_y:
                    best_y = y
                    best = item
            except Exception:
                continue
    return best


async def _button_enabled(btn: Any) -> bool:
    if not btn:
        return False
    try:
        return await btn.is_enabled(timeout=_pw_ms(1200))
    except Exception:
        return False


async def _fill_input(page: Any, selector: str, value: str) -> bool:
    loc = await _first_visible_locator(page, selector)
    if not loc:
        return False
    try:
        await _human_click(page, loc, timeout_ms=5000)
        await _human_clear_focused(page)
        if value:
            await _human_type_text(page, value)
        try:
            current = await loc.input_value(timeout=_pw_ms(2000))
            return str(current or "") == str(value or "")
        except Exception:
            return True
    except Exception as exc:
        logger.warning("[WECHAT-CHANNELS] human input failed, fallback fill: %s", exc)
    try:
        await loc.fill(value, timeout=_pw_ms(8000))
        return True
    except Exception:
        return False


async def _fill_contenteditable(page: Any, selector: str, value: str) -> bool:
    loc = await _first_visible_locator(page, selector)
    if not loc:
        return False
    try:
        await _human_click(page, loc, timeout_ms=5000)
        await _human_clear_focused(page)
        if value:
            await _human_type_text(page, value)
        current = await loc.evaluate("(el) => (el.innerText || el.textContent || '').trim()")
        if str(value or "").strip() and not str(current or "").strip():
            raise RuntimeError("contenteditable remained empty after human typing")
        return True
    except Exception as exc:
        logger.warning("[WECHAT-CHANNELS] human contenteditable typing failed, fallback js input: %s", exc)
    try:
        await loc.evaluate(
            """
            (el, value) => {
              el.focus();
              el.textContent = value;
              el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: value}));
              el.dispatchEvent(new Event('change', {bubbles: true}));
            }
            """,
            value,
        )
        return True
    except Exception:
        return False


async def _fill_field_by_dom_probe(page: Any, value: str, field_kind: str) -> bool:
    """Fallback for WeChat Channels graphic fields inside the wujie shadow tree.

    The graphic editor labels fields as "图文标题/图文描述"; the actual editable
    node is not always a stable textarea locator, so probe by placeholder and
    nearby label geometry, then dispatch native input events.
    """
    if not str(value or "").strip():
        return True
    try:
        result = await page.evaluate(
            """
            ({value, fieldKind}) => {
              const isDesc = fieldKind === 'desc';
              const labelTokens = isDesc ? ['图文描述', '描述'] : ['图文标题', '短标题', '标题'];
              const hintTokens = isDesc
                ? ['添加描述', '图文描述', '描述', '正文']
                : ['填写标题', '图文标题', '短标题', '标题'];
              const roots = [];
              const seenRoots = new Set();
              const pushRoot = (root) => {
                if (!root || seenRoots.has(root)) return;
                seenRoots.add(root);
                roots.push(root);
                const nodes = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (const el of nodes) {
                  if (el.shadowRoot) pushRoot(el.shadowRoot);
                }
              };
              pushRoot(document);

              const visible = (el) => {
                if (!el || !el.getBoundingClientRect) return false;
                const r = el.getBoundingClientRect();
                if (!r || r.width < 8 || r.height < 8) return false;
                const s = getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity || 1) === 0) return false;
                return true;
              };
              const norm = (s) => String(s || '').replace(/\\s+/g, '');
              const attrText = (el) => norm([
                el.getAttribute('placeholder'),
                el.getAttribute('data-placeholder'),
                el.getAttribute('aria-label'),
                el.getAttribute('title'),
                el.getAttribute('name'),
                el.id,
                el.className && String(el.className),
              ].filter(Boolean).join(' '));
              const includesAny = (text, tokens) => tokens.some((t) => text.includes(norm(t)));

              const labelRects = [];
              for (const root of roots) {
                const all = root.querySelectorAll ? root.querySelectorAll('label, span, div, p') : [];
                for (const el of all) {
                  if (!visible(el)) continue;
                  const text = norm(el.textContent || '');
                  if (!text || text.length > 20) continue;
                  if (!includesAny(text, labelTokens)) continue;
                  const r = el.getBoundingClientRect();
                  labelRects.push({x: r.left, y: r.top, cx: r.left + r.width / 2, cy: r.top + r.height / 2});
                }
              }

              const fields = [];
              for (const root of roots) {
                const els = root.querySelectorAll
                  ? root.querySelectorAll('input, textarea, [contenteditable="true"], [role="textbox"]')
                  : [];
                for (const el of els) {
                  if (!visible(el)) continue;
                  const tag = (el.tagName || '').toLowerCase();
                  const type = String(el.getAttribute('type') || '').toLowerCase();
                  if (['hidden', 'file', 'button', 'submit', 'checkbox', 'radio'].includes(type)) continue;
                  if (el.disabled || el.readOnly) continue;
                  const r = el.getBoundingClientRect();
                  const text = attrText(el);
                  let score = 0;
                  if (includesAny(text, hintTokens)) score += 120;
                  if (isDesc && tag === 'textarea') score += 35;
                  if (!isDesc && tag === 'input') score += 35;
                  if (isDesc && r.height >= 70) score += 25;
                  if (!isDesc && r.height <= 70) score += 20;
                  for (const lr of labelRects) {
                    const dy = Math.abs((r.top + r.height / 2) - lr.cy);
                    const below = r.top - lr.y;
                    const right = r.left > lr.x;
                    if (right && dy < 90) score += Math.max(20, 95 - dy);
                    if (!right && below >= -10 && below < 170) score += Math.max(10, 60 - Math.abs(below));
                  }
                  if (score > 0) fields.push({el, score, tag, text, rect: {x: r.left, y: r.top, h: r.height}});
                }
              }
              fields.sort((a, b) => b.score - a.score || a.rect.y - b.rect.y);
              const picked = fields[0] && fields[0].el;
              if (!picked) return {ok: false, reason: 'no-field', fieldCount: fields.length};

              const fire = (el) => {
                el.dispatchEvent(new InputEvent('input', {bubbles: true, cancelable: true, inputType: 'insertText', data: value}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: 'Process'}));
              };
              picked.scrollIntoView({block: 'center', inline: 'nearest'});
              picked.focus();
              const tag = (picked.tagName || '').toLowerCase();
              if (tag === 'input' || tag === 'textarea') {
                const proto = tag === 'textarea' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                if (setter) setter.call(picked, value);
                else picked.value = value;
                fire(picked);
                return {ok: String(picked.value || '').trim().length > 0, tag, value: String(picked.value || '').slice(0, 40)};
              }
              picked.textContent = value;
              fire(picked);
              const current = String(picked.innerText || picked.textContent || '').trim();
              return {ok: current.length > 0, tag, value: current.slice(0, 40)};
            }
            """,
            {"value": value, "fieldKind": field_kind},
        )
        logger.info("[WECHAT-CHANNELS] dom probe fill %s result=%s", field_kind, result)
        return bool(isinstance(result, dict) and result.get("ok"))
    except Exception as exc:
        logger.warning("[WECHAT-CHANNELS] dom probe fill %s failed: %s", field_kind, exc)
        return False


_DESC_SELECTORS = (
    'div.input-editor[data-placeholder="添加描述"]',
    '[contenteditable="true"][data-placeholder*="添加描述"]',
    '[contenteditable="true"][data-placeholder*="描述"]',
    '[contenteditable="true"][data-placeholder*="正文"]',
    'textarea[placeholder*="添加描述"]',
    'textarea[placeholder*="图文描述"]',
    'textarea[placeholder*="描述"]',
    'textarea[placeholder*="正文"]',
    '[role="textbox"][aria-label*="描述"]',
)

_TITLE_SELECTORS = (
    'input[placeholder="填写短标题有机会获得更多流量"]',
    'input[placeholder*="填写标题"]',
    'input[placeholder*="图文标题"]',
    'input[placeholder*="短标题"]',
    'input[placeholder*="标题"]',
    'textarea[placeholder*="标题"]',
    '[role="textbox"][aria-label*="标题"]',
)


async def _fill_description(page: Any, value: str) -> bool:
    for selector in _DESC_SELECTORS:
        if selector.startswith("textarea"):
            if await _fill_input(page, selector, value):
                return True
        else:
            if await _fill_contenteditable(page, selector, value):
                return True
    return await _fill_field_by_dom_probe(page, value, "desc")


async def _fill_title(page: Any, value: str) -> bool:
    for selector in _TITLE_SELECTORS:
        if await _fill_input(page, selector, value):
            return True
    return await _fill_field_by_dom_probe(page, value, "title")


async def _labeled_control_state(page: Any, labels: tuple[str, ...]) -> Dict[str, Any]:
    try:
        return await page.evaluate(
            """
            ({labels}) => {
              const labelTokens = labels.map((s) => String(s || '').replace(/\\s+/g, '')).filter(Boolean);
              const roots = [];
              const seen = new Set();
              const pushRoot = (root) => {
                if (!root || seen.has(root)) return;
                seen.add(root);
                roots.push(root);
                const nodes = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (const el of nodes) {
                  if (el.shadowRoot) pushRoot(el.shadowRoot);
                }
              };
              pushRoot(document);
              const visible = (el) => {
                if (!el || !el.getBoundingClientRect) return false;
                const r = el.getBoundingClientRect();
                if (!r || r.width < 8 || r.height < 8) return false;
                const s = getComputedStyle(el);
                return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
              };
              const norm = (s) => String(s || '').replace(/\\s+/g, '');
              const includesAny = (text, tokens) => tokens.some((t) => text.includes(t));
              const labelRects = [];
              for (const root of roots) {
                const els = root.querySelectorAll ? root.querySelectorAll('label, span, div, p') : [];
                for (const el of els) {
                  if (!visible(el)) continue;
                  const text = norm(el.textContent || '');
                  if (!text || text.length > 28) continue;
                  if (!includesAny(text, labelTokens)) continue;
                  const r = el.getBoundingClientRect();
                  labelRects.push({x: r.left, y: r.top, cx: r.left + r.width / 2, cy: r.top + r.height / 2});
                }
              }
              const selector = [
                'input:not([type="hidden"]):not([type="file"])',
                'textarea',
                '[contenteditable="true"]',
                '[role="textbox"]',
                '[role="button"]',
                '.ant-select-selector',
                '.ant-select',
                '.ant-input',
                '.ant-picker',
                '.weui-cell',
                'button',
                'div'
              ].join(',');
              const fields = [];
              for (const root of roots) {
                const els = root.querySelectorAll ? root.querySelectorAll(selector) : [];
                for (const el of els) {
                  if (!visible(el)) continue;
                  const tag = (el.tagName || '').toLowerCase();
                  const type = String(el.getAttribute('type') || '').toLowerCase();
                  if (['hidden', 'file', 'button', 'submit', 'checkbox', 'radio'].includes(type)) continue;
                  if (el.disabled || el.readOnly) continue;
                  const r = el.getBoundingClientRect();
                  if (r.width > 620 || r.height > 190) continue;
                  let text = '';
                  if (tag === 'input' || tag === 'textarea') text = el.value || el.getAttribute('placeholder') || '';
                  else text = el.innerText || el.textContent || el.getAttribute('aria-label') || '';
                  let score = 0;
                  for (const lr of labelRects) {
                    const cy = r.top + r.height / 2;
                    const dy = Math.abs(cy - lr.cy);
                    const right = r.left > lr.x + 8;
                    const below = r.top - lr.y;
                    if (right && dy < 90) score += Math.max(20, 110 - dy);
                    if (!right && below >= -10 && below < 180) score += Math.max(8, 55 - Math.abs(below));
                  }
                  if (score <= 0) continue;
                  const className = String(el.className || '');
                  if (/ant-select|select|dropdown|picker/i.test(className)) score += 20;
                  if (tag === 'input' || tag === 'textarea') score += 12;
                  fields.push({el, score, text: norm(text), rawText: String(text || '').trim(), box: {x: r.left, y: r.top, width: r.width, height: r.height}});
                }
              }
              fields.sort((a, b) => b.score - a.score || a.box.y - b.box.y);
              if (!fields.length) return {ok: false, reason: 'not-found'};
              const f = fields[0];
              return {ok: true, text: f.rawText, box: f.box, score: f.score};
            }
            """,
            {"labels": list(labels)},
        )
    except Exception as exc:
        logger.debug("[WECHAT-CHANNELS] labeled control probe failed labels=%s err=%s", labels, exc)
        return {"ok": False, "error": str(exc)}


async def _fill_visible_search_input(page: Any, value: str) -> bool:
    loc = page.locator(
        'input[placeholder*="搜索"], input[placeholder*="请输入"], input[placeholder*="输入"], '
        'input[placeholder*="地点"], input[placeholder*="位置"], input:not([type="file"]):not([type="hidden"])'
    )
    try:
        count = min(await loc.count(), 12)
    except Exception:
        count = 0
    for idx in range(count):
        item = loc.nth(idx)
        try:
            if not await item.is_visible(timeout=_pw_ms(800)):
                continue
            box = await item.bounding_box()
            if not box or float(box.get("width") or 0) < 20 or float(box.get("height") or 0) < 12:
                continue
            await _human_click(page, item, timeout_ms=5000)
            await _human_clear_focused(page)
            await _human_type_text(page, value)
            await _delay(0.5, 1.2)
            return True
        except Exception:
            continue
    return False


async def _select_visible_option(page: Any, value: str) -> bool:
    value_s = str(value or "").strip()
    if not value_s:
        return True
    hit = await _click_first_visible_text(page, (value_s,), exact=True)
    if hit:
        return True
    hit = await _click_first_visible_text(page, (value_s,), exact=False)
    return bool(hit)


async def _apply_select_option(page: Any, labels: tuple[str, ...], value: str, option_name: str, _step) -> bool:
    value_s = str(value or "").strip()
    if not value_s:
        return True
    state = await _labeled_control_state(page, labels)
    current = str(state.get("text") or "").strip()
    if state.get("ok") and current and value_s in current:
        _step(f"设置{option_name}", True, value=value_s, already=True)
        return True
    if not state.get("ok") or not isinstance(state.get("box"), dict):
        _step(f"设置{option_name}", False, value=value_s, reason="control_not_found")
        return False
    try:
        await _human_click_box(page, state["box"])
        await _delay(0.5, 1.3)
        if await _select_visible_option(page, value_s):
            await _delay(0.4, 1.0)
            verify = await _labeled_control_state(page, labels)
            verify_text = str(verify.get("text") or "")
            _step(f"设置{option_name}", True, value=value_s, visible_value=verify_text[:80])
            return True
        if await _fill_visible_search_input(page, value_s):
            if await _select_visible_option(page, value_s):
                verify = await _labeled_control_state(page, labels)
                _step(f"设置{option_name}", True, value=value_s, visible_value=str(verify.get("text") or "")[:80])
                return True
            await page.keyboard.press("Enter")
            await _delay(0.5, 1.0)
            verify = await _labeled_control_state(page, labels)
            verify_text = str(verify.get("text") or "")
            if value_s in verify_text:
                _step(f"设置{option_name}", True, value=value_s, visible_value=verify_text[:80])
                return True
    except Exception as exc:
        logger.warning("[WECHAT-CHANNELS] set optional %s failed: %s", option_name, exc)
    _step(f"设置{option_name}", False, value=value_s)
    return False


async def _apply_checkbox_option(page: Any, labels: tuple[str, ...], enabled: Optional[bool], option_name: str, _step) -> bool:
    if enabled is None:
        return True
    try:
        result = await page.evaluate(
            """
            ({labels, enabled}) => {
              const tokens = labels.map((s) => String(s || '').replace(/\\s+/g, '')).filter(Boolean);
              const roots = [];
              const seen = new Set();
              const pushRoot = (root) => {
                if (!root || seen.has(root)) return;
                seen.add(root);
                roots.push(root);
                const nodes = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (const el of nodes) if (el.shadowRoot) pushRoot(el.shadowRoot);
              };
              pushRoot(document);
              const visible = (el) => {
                if (!el || !el.getBoundingClientRect) return false;
                const r = el.getBoundingClientRect();
                if (!r || r.width < 8 || r.height < 8) return false;
                const s = getComputedStyle(el);
                return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
              };
              const norm = (s) => String(s || '').replace(/\\s+/g, '');
              for (const root of roots) {
                const els = root.querySelectorAll ? root.querySelectorAll('label, span, div, p, input, [role="checkbox"]') : [];
                for (const el of els) {
                  if (!visible(el)) continue;
                  const text = norm(el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '');
                  if (!tokens.some((t) => text.includes(t))) continue;
                  const container = el.closest('label, .ant-checkbox-wrapper, .ant-form-item, div') || el;
                  const cb = container.querySelector ? container.querySelector('input[type="checkbox"], [role="checkbox"]') : null;
                  const target = cb || container;
                  let checked = false;
                  if (cb && 'checked' in cb) checked = !!cb.checked;
                  else checked = String(target.getAttribute('aria-checked') || '').toLowerCase() === 'true';
                  const box = target.getBoundingClientRect();
                  return {ok: true, checked, box: {x: box.left, y: box.top, width: box.width, height: box.height}};
                }
              }
              return {ok: false};
            }
            """,
            {"labels": list(labels), "enabled": bool(enabled)},
        )
        if not isinstance(result, dict) or not result.get("ok"):
            _step(f"设置{option_name}", False, value=enabled, reason="control_not_found")
            return False
        if bool(result.get("checked")) == bool(enabled):
            _step(f"设置{option_name}", True, value=enabled, already=True)
            return True
        await _human_click_box(page, result.get("box") or {})
        _step(f"设置{option_name}", True, value=enabled)
        return True
    except Exception as exc:
        logger.warning("[WECHAT-CHANNELS] set checkbox %s failed: %s", option_name, exc)
        _step(f"设置{option_name}", False, value=enabled, error=str(exc))
        return False


async def _upload_cover_image(page: Any, cover_path: Optional[str], _step) -> bool:
    if not cover_path:
        return True
    if not os.path.isfile(cover_path):
        _step("设置封面", False, path=cover_path, reason="cover_not_found")
        return False
    labels = ("上传封面", "更换封面", "选择封面", "设置封面", "编辑封面")
    entry = await _click_first_visible_text(page, labels, exact=True)
    if not entry:
        entry = await _click_first_visible_text(page, labels, exact=False)
    if entry:
        await _delay(0.8, 1.8)
    image_input = await _find_file_input(page, media_kind=_MEDIA_IMAGE, wait_ms=2500)
    if not image_input:
        _step("设置封面", False, path=cover_path, reason="cover_input_not_found")
        return False
    try:
        await _upload_media_file(page, image_input, cover_path)
        _step("设置封面", True, path=cover_path)
        return True
    except Exception as exc:
        _step("设置封面", False, path=cover_path, error=str(exc))
        return False


async def _apply_optional_publish_options(page: Any, media_kind: str, options: Dict[str, Any], cover_path: Optional[str], _step) -> bool:
    ok = True
    if media_kind == _MEDIA_VIDEO and cover_path:
        ok = await _upload_cover_image(page, cover_path, _step) and ok

    field_specs = (
        (
            "位置",
            ("位置", "所在地", "定位"),
            ("wechat_channels_location", "channels_location", "location", "poi", "city"),
        ),
        (
            "合集",
            ("添加到合集", "合集", "选择合集"),
            ("wechat_channels_collection", "channels_collection", "collection", "collection_name", "album"),
        ),
        (
            "链接",
            ("链接", "选择链接", "添加链接"),
            ("wechat_channels_link", "channels_link", "link", "link_title", "link_name"),
        ),
        (
            "音乐",
            ("音乐", "选择音乐", "添加音乐"),
            ("wechat_channels_music", "channels_music", "music", "music_name"),
        ),
        (
            "活动",
            ("活动", "参与活动", "选择活动"),
            ("wechat_channels_activity", "channels_activity", "activity", "activity_name"),
        ),
    )
    for option_name, labels, keys in field_specs:
        value = _option_value(options, *keys)
        if not value:
            continue
        ok = await _apply_select_option(page, labels, value, option_name, _step) and ok

    original = _option_bool(options, "wechat_channels_original", "channels_original", "original", "is_original")
    if original is not None:
        ok = await _apply_checkbox_option(page, ("原创", "原创声明", "声明原创"), original, "原创声明", _step) and ok
    return ok


async def _has_editor_fields(page: Any) -> bool:
    for selector in (*_DESC_SELECTORS, *_TITLE_SELECTORS):
        try:
            if await page.locator(selector).count():
                return True
        except Exception:
            continue
    return False


async def _wait_upload_entry_ready(page: Any, media_kind: str, timeout_seconds: int = 18) -> bool:
    for _ in range(max(1, timeout_seconds)):
        file_input = await _visible_file_input_for_kind(page, media_kind)
        if file_input:
            return True
        try:
            input_count = await page.locator('input[type="file"]').count()
        except Exception:
            input_count = 0
        if input_count == 0:
            upload_area = await _first_visible_locator(page, 'span.ant-upload.ant-upload-btn[role="button"]')
            if upload_area:
                return True
        await asyncio.sleep(1)
    return False


async def _wait_editor_ready(page: Any, timeout_seconds: int = 60) -> bool:
    for _ in range(max(1, timeout_seconds)):
        if await _has_editor_fields(page):
            return True
        await asyncio.sleep(1)
    return False


async def _click_publish_entry(page: Any, media_kind: str) -> str:
    if media_kind == _MEDIA_IMAGE:
        labels = ("发表图文", "发布图文", "发表图片", "发布图片", "新建图文", "创建图文")
    else:
        labels = ("发表视频", "发布视频", "新建视频", "上传视频")
    hit = await _click_first_visible_text(page, labels, exact=True)
    if hit:
        return hit
    # Some pages render the CTA as "发表 视频" or add a short suffix; allow partial only
    # for the explicit media-specific entry, never for the final "发表" submit button.
    return await _click_first_visible_text(page, labels, exact=False)


async def _open_content_management(page: Any, media_kind: str, _step) -> bool:
    if await _wait_upload_entry_ready(page, media_kind, 2):
        return True
    if media_kind == _MEDIA_IMAGE:
        recent = await _click_first_visible_text(page, ("最近图文",), exact=True)
        if recent:
            _step("切换内容类型", True, entry=recent)
            await _delay(0.8, 1.8)
            entry = await _click_publish_entry(page, media_kind)
            if entry:
                _step("点击发布入口", True, entry=entry)
                await _delay(1.2, 2.6)
                if await _wait_upload_entry_ready(page, media_kind, 18):
                    return True

    hit = await _click_first_visible_text(page, ("内容管理",), exact=True)
    if hit:
        _step("打开内容管理", True, entry=hit)
        await _delay(0.8, 1.6)

    if media_kind == _MEDIA_IMAGE:
        section_labels = ("图文管理", "图文", "最近图文")
    else:
        section_labels = ("视频管理", "视频", "最近视频")
    section = await _click_first_visible_text(page, section_labels, exact=True)
    if section:
        _step("切换内容类型", True, entry=section)
        await _delay(0.8, 1.8)
        if await _wait_upload_entry_ready(page, media_kind, 3):
            return True

    entry = await _click_publish_entry(page, media_kind)
    if entry:
        _step("点击发布入口", True, entry=entry)
        await _delay(1.2, 2.6)
        if await _wait_upload_entry_ready(page, media_kind, 18):
            return True
    return await _wait_upload_entry_ready(page, media_kind, 3)


async def _open_publish_editor(page: Any, media_kind: str, _step) -> bool:
    """Open the video/image-text editor from stable UI entries.

    WeChat Channels is a SPA. After one publish it often stays on Home/Content
    pages and direct post/create can expose only the shell for several seconds,
    so every task resets through the assistant/content-management entry first.
    """
    media_label = "图文" if media_kind == _MEDIA_IMAGE else "视频"
    try:
        await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=_nav_ms(45000))
        await _delay(1.5, 2.8)
    except Exception as exc:
        logger.warning("[WECHAT-CHANNELS] goto home failed: %s", exc)
    if not await _wait_login_ready(page, 25):
        _step("登录状态", False)
        return False
    _step("登录状态", True)

    if await _open_content_management(page, media_kind, _step):
        _step(f"进入{media_label}发布编辑器", True, route="content_management")
        return True

    # Fallback: direct route. Kept as backup because Tencent sometimes changes
    # the menu text before changing the post/create route.
    try:
        await page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=_nav_ms(45000))
        await _delay(2.0, 3.5)
        if await _wait_upload_entry_ready(page, media_kind, 20):
            _step(f"进入{media_label}发布编辑器", True, route="direct")
            return True
    except Exception as exc:
        logger.warning("[WECHAT-CHANNELS] goto publish failed: %s", exc)

    _step(f"进入{media_label}发布编辑器", False)
    return False


async def _wait_publish_ready(page: Any, timeout_seconds: int, _step) -> Any:
    fail_words = ("上传失败", "转码失败", "处理失败", "格式不支持", "文件过大", "审核失败")
    busy_words = ("上传中", "正在上传", "上传进度", "转码中", "处理中", "图片处理中", "校验中", "解析中")
    last_state = ""
    for _ in range(max(1, timeout_seconds)):
        text = await _deep_text(page)
        if any(word in text for word in fail_words):
            _step("检测上传状态", False, state="failed")
            return None
        btn = await _publish_button(page)
        if btn and await _button_enabled(btn):
            if not any(word in text for word in busy_words):
                _step("发表按钮可用", True)
                return btn
            last_state = "busy"
        else:
            last_state = "waiting_button"
        await asyncio.sleep(1)
    _step("等待上传完成", False, state=last_state or "timeout")
    return None


async def _scroll_publish_form_to_bottom(page: Any) -> None:
    try:
        await page.evaluate(
            """
            () => {
              const roots = [];
              const seen = new Set();
              const pushRoot = (root) => {
                if (!root || seen.has(root)) return;
                seen.add(root);
                roots.push(root);
                const nodes = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (const el of nodes) {
                  if (el.shadowRoot) pushRoot(el.shadowRoot);
                }
              };
              pushRoot(document);
              const scrollers = [document.scrollingElement, document.documentElement, document.body].filter(Boolean);
              for (const root of roots) {
                const nodes = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (const el of nodes) {
                  if (el.scrollHeight && el.clientHeight && el.scrollHeight > el.clientHeight + 40) {
                    scrollers.push(el);
                  }
                }
              }
              for (const el of scrollers) {
                try { el.scrollTop = el.scrollHeight; } catch (_) {}
              }
              try { window.scrollTo(0, document.body.scrollHeight); } catch (_) {}
            }
            """
        )
        await _delay(0.5, 1.0)
    except Exception as exc:
        logger.debug("[WECHAT-CHANNELS] scroll form bottom failed: %s", exc)


async def _click_followup_confirm(page: Any) -> bool:
    labels = ("确认发表", "确定", "确认", "继续发表")
    for label in labels:
        btn = await _first_visible_locator(page, f'button:has-text("{label}")')
        if not btn or not await _button_enabled(btn):
            continue
        try:
            await _human_click(page, btn, timeout_ms=8000)
            return True
        except Exception:
            try:
                await btn.click(force=True, timeout=_pw_ms(8000))
                await _delay(0.8, 1.6)
                return True
            except Exception:
                continue
    return False


async def _verify_publish_result(page: Any, _step) -> Dict[str, Any]:
    success_words = ("发表成功", "发布成功", "提交成功", "已发表", "已发布", "审核中")
    fail_words = ("发表失败", "发布失败", "提交失败", "请上传", "不能为空", "格式不支持", "审核失败")
    for _ in range(45):
        text = await _deep_text(page)
        if any(word in text for word in success_words):
            _step("检测发布结果", True)
            return {"ok": True, "url": getattr(page, "url", "") or ""}
        if any(word in text for word in fail_words):
            _step("检测发布结果", False)
            return {"ok": False, "error": "视频号页面提示发布失败或资料不完整", "url": getattr(page, "url", "") or ""}
        url = (getattr(page, "url", "") or "").split("?")[0].rstrip("/")
        if url and not url.endswith("/post/create") and "/login" not in url:
            _step("检测发布跳转", True, url=url)
            return {"ok": True, "url": getattr(page, "url", "") or ""}
        await asyncio.sleep(1)
    _step("发布结果未明确返回", True)
    return {
        "ok": True,
        "url": getattr(page, "url", "") or "",
        "warning": "已点击发表，但页面未在 45 秒内返回明确成功提示，请到视频号助手确认。",
    }


class WechatChannelsDriver(BaseDriver):
    def login_url(self) -> str:
        return HOME_URL

    async def _passive_login_check(self, page: Any) -> bool:
        return _signals_logged_in(await _login_signals(page))

    async def check_login(self, page: Any, navigate: bool = True) -> bool:
        if not navigate:
            return await self._passive_login_check(page)
        try:
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=_nav_ms(30000))
            if await _wait_login_ready(page, 20):
                return True
            await page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=_nav_ms(30000))
            return await _wait_login_ready(page, 25)
        except Exception as exc:
            logger.warning("[WECHAT-CHANNELS] check_login failed: %s", exc)
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

        def _step(note: str, ok: bool, **extra):
            entry = {"action": note, "ok": ok, **extra}
            applied["steps"].append(entry)
            logger.info("[WECHAT-CHANNELS] %s => %s %s", note, "OK" if ok else "FAIL", extra or "")

        try:
            if not os.path.isfile(file_path):
                _step("检查文件存在", False, path=file_path)
                return {"ok": False, "error": f"文件不存在: {file_path}", "applied": applied}
            ext = os.path.splitext(file_path)[1].lower()
            requested_type = str(
                options.get("wechat_channels_publish_type")
                or options.get("publish_type")
                or ""
            ).strip().lower()
            if requested_type in {"image", "images", "graphic", "graphics", "article", "图文"}:
                media_kind = _MEDIA_IMAGE
            elif requested_type in {"video", "视频"}:
                media_kind = _MEDIA_VIDEO
            elif ext in _IMAGE_EXTS:
                media_kind = _MEDIA_IMAGE
            elif ext in _VIDEO_EXTS:
                media_kind = _MEDIA_VIDEO
            else:
                media_kind = ""
            if not media_kind:
                _step("识别素材类型", False, ext=ext)
                return {"ok": False, "error": "视频号发布支持视频（mp4/mov/m4v）和图文图片（jpg/png/webp/gif/bmp）素材", "applied": applied}
            file_size = os.path.getsize(file_path)
            _step("检查文件存在", True, size=file_size)
            _step("识别素材类型", True, type="图文" if media_kind == _MEDIA_IMAGE else "视频", ext=ext)

            if not await _open_publish_editor(page, media_kind, _step):
                logged_in = False
                try:
                    logged_in = _signals_logged_in(await _login_signals(page))
                except Exception:
                    logged_in = False
                if not logged_in:
                    return {"ok": False, "need_login": True, "error": "未登录视频号助手，请先扫码登录", "applied": applied}
                return {"ok": False, "error": "未找到对应的视频号发布入口，请从内容管理确认该账号支持发布视频/图文", "applied": applied}

            file_input = await _find_file_input(page, media_kind=media_kind)
            if not file_input:
                _step("查找上传输入框", False)
                media_label = "图文图片" if media_kind == _MEDIA_IMAGE else "视频"
                return {"ok": False, "error": f"未找到视频号{media_label}上传入口", "applied": applied}
            upload_method = await _upload_media_file(page, file_input, file_path)
            _step("选择本地素材", True, method=upload_method, type="图文" if media_kind == _MEDIA_IMAGE else "视频")
            await _delay(1.0, 2.0)
            if not await _wait_editor_ready(page, 60):
                _step("等待编辑表单", False)
                return {"ok": False, "error": "视频号编辑表单未加载出来，请稍后重试", "applied": applied}
            _step("等待编辑表单", True)

            title_limit = 22 if media_kind == _MEDIA_IMAGE else _TITLE_MAX
            title_use = (title or "").strip()[:title_limit]
            desc_use = _merge_tags(description or title or "", tags or "")
            if not title_use and desc_use:
                title_use = desc_use[:title_limit]

            desc_ok = True
            if desc_use:
                desc_ok = await _fill_description(page, desc_use)
            _step("填写描述", bool(desc_ok), length=len(desc_use))
            if not desc_ok:
                return {"ok": False, "error": "未能写入视频号描述", "applied": applied}

            title_ok = True
            if title_use:
                title_ok = await _fill_title(page, title_use)
            _step("填写标题", bool(title_ok), length=len(title_use))
            if not title_ok and media_kind == _MEDIA_VIDEO:
                return {"ok": False, "error": "未能写入视频号短标题", "applied": applied}

            options_ok = await _apply_optional_publish_options(page, media_kind, options, cover_path, _step)
            if not options_ok:
                return {"ok": False, "error": "视频号可选发布参数未能全部生效，请查看步骤记录", "applied": applied}

            wait_seconds = _upload_wait_seconds(file_size)
            _step("上传等待上限(秒)", True, wait_seconds=wait_seconds)
            await _scroll_publish_form_to_bottom(page)
            publish_btn = await _wait_publish_ready(page, wait_seconds, _step)
            if not publish_btn:
                return {"ok": False, "error": "素材仍在上传/处理，或发表按钮不可用，请稍后重试", "applied": applied}

            if options.get("dry_run"):
                _step("dry_run — 未点击发表", True)
                return {"ok": True, "url": getattr(page, "url", "") or "", "applied": applied, "dry_run": True}

            await _delay(1.2, 3.2)
            try:
                await _human_click(page, publish_btn, timeout_ms=12000)
            except Exception:
                await publish_btn.click(force=True, timeout=_pw_ms(12000))
                await _delay(0.8, 1.6)
            _step("点击发表", True)
            if await _click_followup_confirm(page):
                _step("确认发表", True)

            result = await _verify_publish_result(page, _step)
            result["applied"] = applied
            return result
        except Exception as exc:
            logger.exception("[WECHAT-CHANNELS] publish failed")
            _step("异常", False, error=str(exc))
            return {"ok": False, "error": str(exc), "applied": applied}
