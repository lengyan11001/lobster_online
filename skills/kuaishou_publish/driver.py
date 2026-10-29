"""快手创作者中心发布驱动 — cp.kuaishou.com 自动化。

平台知识来源：竞品 Easel 的 ``web_publisher.py``（``PLATFORMS['kuaishou']``，真机校准），
按本项目 Driver 接口重写。要点（都踩过坑）：

- 视频：先 /article/manage/video 再回 /article/publish/video 清草稿态；``input[type=file]``
  直接 ``set_input_files``，上传后要等转码出编辑表单（最长 3 分钟）；
- 图文：必须切「上传图文」tab，且图片只能走 filechooser（``set_input_files`` 不触发上传）；
- 真发布按钮是表单底部 ``div[class*=button-primary]``『发布』（品牌红，非 ``<button>``），
  用 JS dispatch 点击最可靠；右上角「发布作品」是下拉菜单，点了没用；
- 话题标签上限 4 个，超过平台直接拒绝提交；
- 创作者中心「外壳」登录态（cp 域 DOM）比发布上传用的 onvideo token 活得久：DOM 看着已登录、
  真发布时 401。所以 DOM 判定通过后再探一次 ``current/user``（result==1 才算发布鉴权正常）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from typing import Any, Dict, List, Optional, Tuple

from publisher.pw_timeouts import file_input_wait_ms, navigation_timeout_ms as _nav_ms

from skills._base import BaseDriver

logger = logging.getLogger(__name__)

LOGIN_URL = "https://cp.kuaishou.com"
MANAGE_VIDEO_URL = "https://cp.kuaishou.com/article/manage/video"
PUBLISH_VIDEO_URL = "https://cp.kuaishou.com/article/publish/video"
# 发布子系统的鉴权探针：比外壳 token 短命，用来识别「看着已登录但发不出去」
AUTH_PROBE_URL = "https://cp.kuaishou.com/rest/cp/works/v2/common/pc/current/user"

# 已登录标志（2026 改版后旧 .cp-header .user-name 失效）
LOGIN_SELECTORS = (
    ".user-info-avatar, .user-line, .vertical-menu, .setting-container"
)
LOGGED_OUT_SELECTORS = ".login-container, [class*=qrcode] img[src^='data:image']"

EDITOR_SELECTORS = (
    "textarea, [contenteditable=true], input[placeholder*=描述], "
    "[placeholder*=描述], [class*=_description]"
)
IMAGE_TAB_SELECTORS = ".ant-tabs-tab-btn:has-text('上传图文'), text=上传图文"
IMAGE_UPLOAD_BUTTON_SELECTORS = (
    "text=上传图片, [class*=upload] button:has-text('上传图片'), button:has-text('上传图片')"
)
VIDEO_PUBLISH_BUTTON_CSS = (
    "[class*=_edit-section-btns] [class*=button-primary], "
    "[class*=button-primary]:not([class*=publish-button] *)"
)
IMAGE_PUBLISH_BUTTON_SELECTORS = (
    "[class*=button-primary]:has-text('发布'), "
    "div[class*=_button]:has-text('发布'):not(:has-text('作品'))"
)
CONFIRM_DIALOG_SELECTORS = (
    "[class*=dialog] [class*=button-primary], [class*=dialog] button:has-text('确认'), "
    "[class*=dialog] button:has-text('确定'), text=确认发布, text=确定发布"
)
DISCARD_DRAFT_SELECTORS = "text=放弃, button:has-text('放弃'), [class*=abandon]"
COVER_READY_SELECTORS = "[class*=preview-cover], img[src*='ssrcdn']"
PUBLISH_BTN_CONTAINER_SELECTORS = "[class*=_edit-section-btns]"

MAX_HASHTAGS = 4
CAPTION_MAX = 500

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

JS_CLEAR_JOYRIDE = """
() => {
  for (const sel of ['#react-joyride-portal', '.react-joyride__overlay', '.react-joyride__spotlight']) {
    document.querySelectorAll(sel).forEach(el => el.remove());
  }
  document.querySelectorAll('[style*=pointer-events]').forEach(el => {
    const cls = (el.className || '').toString();
    if (cls.includes('joyride')) el.remove();
  });
  return true;
}
"""


def is_image_media(file_path: str) -> bool:
    return os.path.splitext(file_path or "")[1].lower() in _IMAGE_EXTS


def split_tags(tags: str) -> List[str]:
    raw = (tags or "").replace("，", ",")
    parts = [p.strip().lstrip("#") for p in re.split(r"[,\s]+", raw) if p.strip()]
    return [p for p in parts if p]


def limit_hashtags(tags: str, max_n: int = MAX_HASHTAGS) -> Tuple[str, List[str]]:
    """快手话题标签上限 4 个，超出会被平台拒绝提交，这里先截断并给告警。"""
    parts = split_tags(tags)
    warnings: List[str] = []
    if len(parts) > max_n:
        warnings.append(
            f"快手话题标签上限 {max_n} 个，已丢弃多余的 {len(parts) - max_n} 个（原 {len(parts)} 个）"
        )
        parts = parts[:max_n]
    return ",".join(parts), warnings


def compose_caption(title: str, description: str, tags: str) -> str:
    """快手只有一个作品描述框：标题 + 描述 + 话题合并，超长截断。"""
    parts: List[str] = []
    t = (title or "").strip()
    d = (description or "").strip()
    if t:
        parts.append(t)
    if d and d != t:
        parts.append(d)
    body = "\n".join(parts)
    tag_list = split_tags(tags)[:MAX_HASHTAGS]
    tag_line = " ".join(f"#{x}" for x in tag_list)
    caption = (body + ("\n" + tag_line if tag_line else "")).strip()
    return caption[:CAPTION_MAX]


def _split_selectors(selector: str) -> List[str]:
    return [s.strip() for s in (selector or "").split(", ") if s.strip()]


async def _human_delay(lo: float = 0.4, hi: float = 1.2) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


async def _first_visible(page: Any, selector: str) -> Any:
    for sel in _split_selectors(selector):
        try:
            el = await page.query_selector(sel)
        except Exception:
            el = None
        if not el:
            continue
        try:
            if await el.is_visible():
                return el
        except Exception:
            continue
    return None


async def _wait_visible(page: Any, selector: str, timeout_ms: int) -> Any:
    deadline = asyncio.get_event_loop().time() + max(1.0, timeout_ms / 1000.0)
    while asyncio.get_event_loop().time() < deadline:
        el = await _first_visible(page, selector)
        if el:
            return el
        await asyncio.sleep(0.8)
    return None


async def _click_first(page: Any, selector: str, *, timeout_ms: int = 5000) -> bool:
    el = await _wait_visible(page, selector, timeout_ms)
    if not el:
        return False
    try:
        await el.scroll_into_view_if_needed()
    except Exception:
        pass
    try:
        await el.click(timeout=8000)
        return True
    except Exception:
        try:
            await el.click(force=True, timeout=8000)
            return True
        except Exception:
            return False


async def _click_optional(page: Any, selector: str, *, timeout_ms: int = 2500) -> bool:
    """清理类按钮（放弃草稿/二次确认）：没有就静默跳过。"""
    try:
        return await _click_first(page, selector, timeout_ms=timeout_ms)
    except Exception:
        return False


async def _fill_first(page: Any, selector: str, value: str) -> bool:
    el = await _wait_visible(page, selector, 15000)
    if not el:
        return False
    try:
        await el.click(timeout=8000)
    except Exception:
        pass
    try:
        await el.fill(value, timeout=15000)
        return True
    except Exception:
        pass
    try:
        await page.keyboard.type(value, delay=2)
        return True
    except Exception as exc:
        logger.warning("[KUAISHOU] 填写描述失败: %s", exc)
        return False


async def _js_click_publish(page: Any, css_selector: str) -> bool:
    """发布按钮点击：scrollIntoView + dispatch 鼠标事件 + click()，比 locator.click 可靠。"""
    try:
        return bool(
            await page.evaluate(
                r"""(sels) => {
                    for (const s of sels) {
                        let els = [];
                        try { els = Array.from(document.querySelectorAll(s)); } catch (e) { continue; }
                        for (const el of els) {
                            const cls = (el.className || '').toString();
                            if (cls.includes('publish-button')) continue;
                            const r = el.getBoundingClientRect();
                            if (r.width <= 0 || r.height <= 0) continue;
                            el.scrollIntoView({ block: 'center' });
                            el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                            el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }""",
                _split_selectors(css_selector),
            )
        )
    except Exception as exc:
        logger.warning("[KUAISHOU] js_click 失败: %s", exc)
        return False


async def _js_click_by_text(
    page: Any, texts: List[str], excludes: Optional[List[str]] = None, prefer_class: str = "button-primary"
) -> bool:
    try:
        return bool(
            await page.evaluate(
                r"""([texts, excludes, preferClass]) => {
                    const cands = [];
                    for (const el of Array.from(document.querySelectorAll('div,button,a,span'))) {
                        const t = (el.textContent || '').trim().replace(/\s+/g, ' ');
                        if (!texts.includes(t)) continue;
                        if (excludes.some(x => t.includes(x))) continue;
                        if (el.closest('[class*=publish-button]')) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width <= 0 || r.height <= 0) continue;
                        const cls = (el.className || '').toString();
                        let score = r.top;
                        if (preferClass && cls.includes(preferClass)) score += 100000;
                        cands.push([score, el]);
                    }
                    if (!cands.length) return false;
                    cands.sort((a, b) => b[0] - a[0]);
                    const el = cands[0][1];
                    el.scrollIntoView({ block: 'center' });
                    el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                    el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                    el.click();
                    return true;
                }""",
                [list(texts), list(excludes or []), prefer_class],
            )
        )
    except Exception as exc:
        logger.warning("[KUAISHOU] js_click(text) 失败: %s", exc)
        return False


async def _wait_file_input(page: Any, timeout_ms: int) -> Any:
    deadline = asyncio.get_event_loop().time() + max(1.0, timeout_ms / 1000.0)
    while asyncio.get_event_loop().time() < deadline:
        try:
            el = await page.query_selector("input[type=file]")
        except Exception:
            el = None
        if el:
            return el
        await asyncio.sleep(0.6)
    return None


async def _page_text(page: Any, limit: int = 4000) -> str:
    try:
        return (await page.evaluate("() => (document.body && document.body.innerText) || ''"))[:limit]
    except Exception:
        return ""


async def _dry_run_stop(
    page: Any, selector: str, applied: Dict[str, Any], _step, shot_path: str
) -> Dict[str, Any]:
    """dry_run：停在「发布」按钮前，不提交，只回传按钮是否就绪 + 截图。"""
    btn = await _first_visible(page, selector)
    cls = ""
    if btn is not None:
        try:
            cls = (await btn.get_attribute("class")) or ""
        except Exception:
            cls = ""
    if shot_path:
        try:
            await page.screenshot(path=shot_path)
        except Exception as exc:
            logger.warning("[KUAISHOU] dry_run 截图失败: %s", exc)
    enabled = bool(btn) and "disabled" not in cls.lower()
    _step("dry_run 停在发布前", enabled, button_found=btn is not None, button_class=cls[:80])
    return {
        "ok": enabled,
        "dry_run": True,
        "url": getattr(page, "url", "") or "",
        "applied": applied,
        "publish_button_found": btn is not None,
        "publish_button_enabled": enabled,
        "publish_button_class": cls[:80],
        **({} if enabled else {"error": "dry_run：发布按钮未找到或被禁用"}),
    }


class KuaishouDriver(BaseDriver):
    """快手创作者中心：视频 / 图文发布。"""

    def login_url(self) -> str:
        return LOGIN_URL

    # ── 登录 ────────────────────────────────────────────────────────────
    async def _passive_login_check(self, page: Any) -> bool:
        try:
            url = (getattr(page, "url", "") or "").strip()
        except Exception:
            url = ""
        low = url.lower()
        if not low or low == "about:blank" or low.startswith("chrome://"):
            return False
        if "passport.kuaishou.com" in low or "/login" in low:
            return False
        if "cp.kuaishou.com" not in low:
            return False
        for sel in _split_selectors(LOGGED_OUT_SELECTORS):
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    return False
            except Exception:
                continue
        return await _first_visible(page, LOGIN_SELECTORS) is not None

    async def _publish_auth_state(self, page: Any) -> Optional[bool]:
        """返回 True=发布鉴权正常 / False=明确失效 / None=不确定（保持 DOM 判定）。"""
        try:
            resp = await page.request.post(AUTH_PROBE_URL, timeout=_nav_ms(8000))
        except Exception as exc:
            logger.info("[KUAISHOU] 鉴权探针不可用（按 DOM 判定）: %s", exc)
            return None
        if resp is None:
            return None
        try:
            status = int(getattr(resp, "status", 0) or 0)
        except Exception:
            status = 0
        if status in (401, 403):
            return False
        if status >= 400:
            return None
        try:
            data = await resp.json()
        except Exception:
            return None
        result = data.get("result") if isinstance(data, dict) else None
        if result == 1:
            return True
        if result in (109, 100110000, 401, 403):
            return False
        return None

    async def check_login(self, page: Any, navigate: bool = True) -> bool:
        if navigate:
            try:
                await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=_nav_ms(20000))
                await _human_delay(2.0, 3.0)
            except Exception as exc:
                logger.info("[KUAISHOU] 打开创作者中心失败: %s", exc)
                return False
        ok = await self._passive_login_check(page)
        if not ok:
            return False
        probed = await self._publish_auth_state(page)
        if probed is False:
            logger.warning("[KUAISHOU] 外壳已登录但发布鉴权失效（onvideo token），按未登录处理")
            return False
        return True

    # ── 发布 ────────────────────────────────────────────────────────────
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
        applied: Dict[str, Any] = {"steps": [], "platform": "kuaishou"}

        def _step(note: str, ok: bool, **extra: Any) -> None:
            applied["steps"].append({"action": note, "ok": ok, **extra})
            logger.info(
                "[KUAISHOU-PUBLISH] %s => %s %s", note, "OK" if ok else "FAIL", extra or ""
            )

        if not file_path or not os.path.isfile(file_path):
            _step("检查文件存在", False, path=file_path)
            return {"ok": False, "error": f"文件不存在: {file_path}", "applied": applied}
        _step("检查文件存在", True, size=os.path.getsize(file_path))

        dry_run = bool(options.get("dry_run"))
        shot_path = str(options.get("screenshot_path") or "")
        image = is_image_media(file_path)
        tags_use, tag_warnings = limit_hashtags(tags or "")
        caption = compose_caption(title or "", description or "", tags_use)
        _step("识别素材类型", True, type="图文" if image else "视频")
        if tag_warnings:
            applied.setdefault("warnings", []).extend(tag_warnings)

        try:
            if image:
                return await self._publish_image(
                    page, file_path, caption, applied, _step, dry_run=dry_run, shot_path=shot_path
                )
            return await self._publish_video(
                page, file_path, caption, applied, _step, dry_run=dry_run, shot_path=shot_path
            )
        except Exception as exc:
            logger.exception("[KUAISHOU-PUBLISH] 发布异常")
            _step("异常", False, error=str(exc))
            return {"ok": False, "error": str(exc), "applied": applied}

    async def _publish_video(
        self,
        page: Any,
        file_path: str,
        caption: str,
        applied: Dict[str, Any],
        _step,
        *,
        dry_run: bool = False,
        shot_path: str = "",
    ) -> Dict[str, Any]:
        # 先进作品管理页再回来，清掉上一条草稿的页面状态
        try:
            await page.goto(MANAGE_VIDEO_URL, wait_until="domcontentloaded", timeout=_nav_ms(25000))
        except Exception:
            pass
        await asyncio.sleep(2)
        await page.goto(PUBLISH_VIDEO_URL, wait_until="domcontentloaded", timeout=_nav_ms(30000))
        await asyncio.sleep(4)

        if await _click_optional(page, DISCARD_DRAFT_SELECTORS, timeout_ms=2000):
            _step("清理未发布草稿", True)
            await asyncio.sleep(2)
        else:
            _step("清理未发布草稿", True, skipped=True)

        file_input = await _wait_file_input(page, file_input_wait_ms(30000))
        if not file_input:
            _step("定位上传输入框", False)
            return {
                "ok": False,
                "error": "未找到快手视频上传输入框（页面结构可能变更）",
                "applied": applied,
            }
        try:
            await file_input.set_input_files(file_path)
            _step("上传视频文件", True, size=os.path.getsize(file_path))
        except Exception as exc:
            _step("上传视频文件", False, error=str(exc))
            return {"ok": False, "error": f"上传视频失败: {exc}", "applied": applied}

        # 上传后要转码才出编辑表单，网络慢时到 1-3 分钟
        editor = await _wait_visible(page, EDITOR_SELECTORS, 180000)
        if not editor:
            _step("等待转码出编辑表单", False)
            return {
                "ok": False,
                "error": "视频上传后 180s 内未出现编辑表单（可能仍在转码，请稍后到快手后台确认）",
                "applied": applied,
            }
        _step("等待转码出编辑表单", True)

        # 新手引导浮层会挡住描述框/发布按钮，只移除引导层，不动业务表单
        try:
            await page.evaluate(JS_CLEAR_JOYRIDE)
            _step("清理新手引导浮层", True)
        except Exception:
            _step("清理新手引导浮层", True, skipped=True)

        if not await _fill_first(page, EDITOR_SELECTORS, caption):
            _step("填写作品描述", False)
            return {"ok": False, "error": "未能填入快手作品描述", "applied": applied}
        _step("填写作品描述", True, chars=len(caption))
        await asyncio.sleep(3)

        if not await _wait_visible(page, PUBLISH_BTN_CONTAINER_SELECTORS, 60000):
            _step("等待发布按钮出现", False)
            return {"ok": False, "error": "发布按钮未在 60s 内出现", "applied": applied}
        _step("等待发布按钮出现", True)

        # 封面缩略图没加载完时点发布无效，等一会儿
        await asyncio.sleep(25)
        try:
            await page.evaluate(JS_CLEAR_JOYRIDE)
        except Exception:
            pass

        if dry_run:
            return await _dry_run_stop(page, VIDEO_PUBLISH_BUTTON_CSS, applied, _step, shot_path)

        if not await _js_click_publish(page, VIDEO_PUBLISH_BUTTON_CSS):
            _step("点击发布", False)
            return {"ok": False, "error": "未找到快手发布按钮", "applied": applied}
        _step("点击发布", True)
        await asyncio.sleep(8)

        if await _click_optional(page, CONFIRM_DIALOG_SELECTORS, timeout_ms=3000):
            _step("二次确认发布", True)
        await asyncio.sleep(4)

        return await self._finish(page, applied, _step, image=False)

    async def _publish_image(
        self,
        page: Any,
        file_path: str,
        caption: str,
        applied: Dict[str, Any],
        _step,
        *,
        dry_run: bool = False,
        shot_path: str = "",
    ) -> Dict[str, Any]:
        await page.goto(PUBLISH_VIDEO_URL, wait_until="domcontentloaded", timeout=_nav_ms(30000))
        await asyncio.sleep(4)
        await _click_optional(page, DISCARD_DRAFT_SELECTORS, timeout_ms=2000)
        await asyncio.sleep(1.5)

        if not await _click_first(page, IMAGE_TAB_SELECTORS, timeout_ms=10000):
            _step("切换到上传图文", False)
            return {"ok": False, "error": "未找到「上传图文」tab", "applied": applied}
        _step("切换到上传图文", True)
        await asyncio.sleep(3)

        # 图文的图片 input 用 set_input_files 不触发上传，必须点按钮走 filechooser
        uploaded = False
        try:
            button = await _wait_visible(page, IMAGE_UPLOAD_BUTTON_SELECTORS, 15000)
            if button:
                async with page.expect_file_chooser(timeout=15000) as fc_info:
                    await button.click(timeout=8000)
                chooser = await fc_info.value
                await chooser.set_files(file_path)
                uploaded = True
        except Exception as exc:
            logger.warning("[KUAISHOU] filechooser 上传图片失败，回退 set_input_files: %s", exc)
        if not uploaded:
            fi = await _wait_file_input(page, 15000)
            if fi:
                try:
                    await fi.set_input_files(file_path)
                    uploaded = True
                except Exception as exc:
                    _step("上传图片", False, error=str(exc))
        _step("上传图片", uploaded, size=os.path.getsize(file_path))
        if not uploaded:
            return {"ok": False, "error": "快手图文上传未触发（未找到上传入口）", "applied": applied}

        # 图片 CDN 预览就绪前点发布会提示「请在图片上传完成后再点击发布」
        if not await _wait_visible(page, COVER_READY_SELECTORS, 90000):
            _step("等待图片上传完成", False)
            return {"ok": False, "error": "图片 90s 内未上传完成", "applied": applied}
        _step("等待图片上传完成", True)
        await asyncio.sleep(2.5)

        if not await _wait_visible(page, EDITOR_SELECTORS, 60000):
            _step("等待图文编辑表单", False)
            return {"ok": False, "error": "未出现图文编辑表单", "applied": applied}
        if not await _fill_first(page, EDITOR_SELECTORS, caption):
            _step("填写图文描述", False)
            return {"ok": False, "error": "未能填入快手图文描述", "applied": applied}
        _step("填写图文描述", True, chars=len(caption))
        await asyncio.sleep(1.5)

        if dry_run:
            return await _dry_run_stop(page, IMAGE_PUBLISH_BUTTON_SELECTORS, applied, _step, shot_path)

        clicked = await _first_visible(page, IMAGE_PUBLISH_BUTTON_SELECTORS)
        if clicked:
            try:
                await clicked.scroll_into_view_if_needed()
                await clicked.click(timeout=8000)
            except Exception:
                clicked = None
        if not clicked:
            if not await _js_click_by_text(page, ["发布"], excludes=["作品", "设置"]):
                _step("点击发布", False)
                return {"ok": False, "error": "未找到快手图文发布按钮", "applied": applied}
        _step("点击发布", True)
        await asyncio.sleep(2.5)
        if await _click_optional(page, CONFIRM_DIALOG_SELECTORS, timeout_ms=3000):
            _step("二次确认发布", True)
        await asyncio.sleep(4)

        return await self._finish(page, applied, _step, image=True)

    async def _finish(
        self, page: Any, applied: Dict[str, Any], _step, *, image: bool
    ) -> Dict[str, Any]:
        """成功校验：离开发布页，或出现「发布成功」toast，避免「点了=成功」的假阳性。"""
        deadline = asyncio.get_event_loop().time() + 25
        while asyncio.get_event_loop().time() < deadline:
            try:
                url = (getattr(page, "url", "") or "").lower()
            except Exception:
                url = ""
            if "publish/video" not in url and "publish/image" not in url:
                _step("校验发布成功", True, by="url")
                return {"ok": True, "url": getattr(page, "url", "") or "", "applied": applied}
            text = await _page_text(page)
            if "发布成功" in text:
                _step("校验发布成功", True, by="toast")
                return {"ok": True, "url": getattr(page, "url", "") or "", "applied": applied}
            await asyncio.sleep(1.5)
        _step("校验发布成功", False)
        return {
            "ok": False,
            "error": "已点击发布但未检测到成功信号（可能仍在转码/审核），请到快手创作者中心确认",
            "url": getattr(page, "url", "") or "",
            "applied": applied,
        }