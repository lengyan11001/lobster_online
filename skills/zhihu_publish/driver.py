"""知乎专栏发布驱动 — zhuanlan.zhihu.com/write 自动化。

平台知识来源：竞品 Easel 的 ``web_publisher.py``（``PLATFORMS['zhihu']``，真机校准），
按本项目 Driver 接口重写。要点：

- 写文章页 ``https://zhuanlan.zhihu.com/write``；标题是独立 textarea，正文是
  Draft.js 的 ``.public-DraftEditor-content``，必须走真实键盘事件（直接改 innerHTML 不生效）；
- 发布按钮要精确匹配文案「发布」，避免点到「发布设置」；
- 成功判定：跳到文章页 ``zhuanlan.zhihu.com/p/<id>``；草稿是 ``/p/<id>/edit`` 必须排除；
- 知乎没有「视频投稿」这个入口，视频文件这里直接返回不支持，不做假成功；
- 图片是尽力插入正文：找不到上传入口时按纯文本发布并附带告警，不静默丢素材。
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import Any, Dict, List, Optional

from publisher.pw_timeouts import navigation_timeout_ms as _nav_ms

from skills._base import BaseDriver

logger = logging.getLogger(__name__)

LOGIN_URL = "https://www.zhihu.com/signin"
HOME_URL = "https://www.zhihu.com/"
WRITE_URL = "https://zhuanlan.zhihu.com/write"
ME_API = "https://www.zhihu.com/api/v4/me"

LOGIN_SELECTORS = ".AppHeader-profile, .AppHeader-userInfo"
LOGGED_OUT_SELECTORS = ".SignContainer, .Login-content, button:has-text('登录')"
TITLE_SELECTORS = ".WriteIndex-titleInput textarea, textarea[placeholder*='标题'], input[placeholder*='标题']"
BODY_SELECTORS = ".public-DraftEditor-content, [contenteditable=true]"
PUBLISH_BUTTON_SELECTORS = "button:has-text('发布')"
IMAGE_BUTTON_SELECTORS = (
    "button[aria-label*=图片], [class*=toolbar] button:has-text('图片'), "
    "button:has-text('图片')"
)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_VIDEO_EXTS = {".mp4", ".mov", ".flv", ".mkv", ".avi", ".webm", ".m4v"}

TITLE_MAX = 100
BODY_MAX = 8000


def media_kind(file_path: str) -> str:
    ext = os.path.splitext(file_path or "")[1].lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _VIDEO_EXTS:
        return "video"
    return "other"


def split_tags(tags: str) -> List[str]:
    raw = (tags or "").replace("，", ",")
    return [p.strip().lstrip("#") for p in raw.replace(",", " ").split() if p.strip()]


def compose_body(title: str, description: str, tags: str) -> str:
    """知乎正文：描述为主，标题已在标题框，话题以 #标签 形式附在末尾。"""
    d = (description or "").strip()
    t = (title or "").strip()
    if not d:
        d = t
    tag_line = " ".join(f"#{x}" for x in split_tags(tags))
    body = (d + ("\n\n" + tag_line if tag_line else "")).strip()
    return body[:BODY_MAX]


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
        await asyncio.sleep(0.6)
    return None


async def _body_text_len(page: Any) -> int:
    try:
        return int(
            await page.evaluate(
                """() => {
                    const el = document.querySelector('.public-DraftEditor-content')
                        || document.querySelector('[contenteditable=true]');
                    return el ? (el.innerText || '').length : 0;
                }"""
            )
        )
    except Exception:
        return 0


class ZhihuDriver(BaseDriver):
    """知乎专栏：文章（文本为主，图片尽力插入）。"""

    def login_url(self) -> str:
        return LOGIN_URL

    # ── 登录 ────────────────────────────────────────────────────────────
    async def _passive_login_check(self, page: Any) -> bool:
        try:
            url = (getattr(page, "url", "") or "").strip().lower()
        except Exception:
            url = ""
        if not url or url == "about:blank" or url.startswith("chrome://"):
            return False
        if "signin" in url or "login" in url:
            return False
        if "zhihu.com" not in url:
            return False
        for sel in _split_selectors(LOGGED_OUT_SELECTORS):
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    return False
            except Exception:
                continue
        return await _first_visible(page, LOGIN_SELECTORS) is not None

    async def _me_probe(self, page: Any) -> Optional[bool]:
        try:
            resp = await page.request.get(ME_API, timeout=_nav_ms(8000))
        except Exception as exc:
            logger.info("[ZHIHU] /me 探针不可用（按 DOM 判定）: %s", exc)
            return None
        try:
            status = int(getattr(resp, "status", 0) or 0)
        except Exception:
            status = 0
        if status in (401, 403):
            return False
        if status != 200:
            return None
        try:
            data = await resp.json()
        except Exception:
            return None
        if isinstance(data, dict) and (data.get("id") or data.get("name") or data.get("url_token")):
            return True
        # 未登录时知乎返回 {"error":{...}}
        if isinstance(data, dict) and data.get("error"):
            return False
        return None

    async def check_login(self, page: Any, navigate: bool = True) -> bool:
        if navigate:
            try:
                await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=_nav_ms(20000))
                await _human_delay(1.5, 2.5)
            except Exception as exc:
                logger.info("[ZHIHU] 打开知乎首页失败: %s", exc)
                return False
        ok = await self._passive_login_check(page)
        if not ok:
            return False
        probed = await self._me_probe(page)
        if probed is False:
            logger.warning("[ZHIHU] DOM 显示已登录但 /me 失效，按未登录处理")
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
        applied: Dict[str, Any] = {"steps": [], "platform": "zhihu"}

        def _step(note: str, ok: bool, **extra: Any) -> None:
            applied["steps"].append({"action": note, "ok": ok, **extra})
            logger.info("[ZHIHU-PUBLISH] %s => %s %s", note, "OK" if ok else "FAIL", extra or "")

        kind = media_kind(file_path or "")
        if file_path and not os.path.isfile(file_path):
            _step("检查文件存在", False, path=file_path)
            return {"ok": False, "error": f"文件不存在: {file_path}", "applied": applied}
        if kind == "video":
            _step("识别素材类型", False, type="video")
            return {
                "ok": False,
                "error": "知乎不支持视频投稿：请改用图片或纯文本（知乎只有专栏图文入口）",
                "applied": applied,
            }
        _step("识别素材类型", True, type="image" if kind == "image" else "text")

        title_use = (title or "").strip()[:TITLE_MAX]
        body_use = compose_body(title_use, description or "", tags or "")
        if not title_use:
            title_use = (body_use.strip().splitlines() or [""])[0][:TITLE_MAX]
        if not body_use.strip():
            _step("正文为空", False)
            return {
                "ok": False,
                "error": "知乎文章需要正文：请传入 description（或 tags）",
                "applied": applied,
            }

        try:
            await page.goto(WRITE_URL, wait_until="domcontentloaded", timeout=_nav_ms(35000))
            await _human_delay(1.5, 2.5)

            title_el = await _wait_visible(page, TITLE_SELECTORS, 30000)
            if not title_el:
                _step("定位标题输入框", False)
                return {
                    "ok": False,
                    "error": "未找到知乎标题输入框（页面结构可能变更或未登录）",
                    "applied": applied,
                }
            try:
                await title_el.click(timeout=8000)
                await title_el.fill(title_use, timeout=10000)
            except Exception:
                await page.keyboard.type(title_use, delay=20)
            _step("填写标题", True, chars=len(title_use))
            await _human_delay(0.4, 0.8)

            body_el = await _wait_visible(page, BODY_SELECTORS, 30000)
            if not body_el:
                _step("定位正文编辑器", False)
                return {"ok": False, "error": "未找到知乎正文编辑器", "applied": applied}
            try:
                await body_el.click(timeout=8000)
            except Exception:
                pass
            await asyncio.sleep(0.4)
            # Draft.js 只认真实键盘事件：逐字输入（1ms/字），保证换行也按 Enter 生效
            try:
                await page.keyboard.type(body_use, delay=1)
            except Exception as exc:
                _step("填写正文", False, error=str(exc))
                return {"ok": False, "error": f"正文字符输入失败: {exc}", "applied": applied}
            written = await _body_text_len(page)
            if written < max(5, int(len(body_use) * 0.5)):
                _step("填写正文", False, written=written, expect=len(body_use))
                return {
                    "ok": False,
                    "error": f"正文只写入了 {written}/{len(body_use)} 字（Draft.js 编辑器未接收输入）",
                    "applied": applied,
                }
            _step("填写正文", True, written=written)
            await _human_delay(0.5, 1.0)

            if kind == "image":
                inserted = await self._try_insert_image(page, file_path, _step)
                if not inserted:
                    applied.setdefault("warnings", []).append(
                        "图片未插入正文（未找到知乎图片上传入口），已按纯文本发布"
                    )
                    applied["asset_skipped"] = True

            if options.get("dry_run"):
                return await self._dry_run_stop(page, applied, _step, str(options.get("screenshot_path") or ""))

            if not await self._click_publish(page, _step):
                return {"ok": False, "error": "未找到知乎「发布」按钮", "applied": applied}

            return await self._finish(page, applied, _step)
        except Exception as exc:
            logger.exception("[ZHIHU-PUBLISH] 发布异常")
            _step("异常", False, error=str(exc))
            return {"ok": False, "error": str(exc), "applied": applied}

    async def _dry_run_stop(
        self, page: Any, applied: Dict[str, Any], _step, shot_path: str
    ) -> Dict[str, Any]:
        """dry_run：停在「发布」按钮前，不提交。"""
        if shot_path:
            try:
                await page.screenshot(path=shot_path)
            except Exception as exc:
                logger.warning("[ZHIHU] dry_run 截图失败: %s", exc)
        btn = await self._find_publish_button(page)
        _step("dry_run 停在发布前", btn is not None)
        return {
            "ok": btn is not None,
            "dry_run": True,
            "url": getattr(page, "url", "") or "",
            "applied": applied,
            "publish_button_found": btn is not None,
            **({} if btn is not None else {"error": "dry_run：未找到知乎「发布」按钮"}),
        }

    async def _find_publish_button(self, page: Any) -> Any:
        for sel in _split_selectors(PUBLISH_BUTTON_SELECTORS):
            try:
                els = await page.query_selector_all(sel)
            except Exception:
                els = []
            for el in els:
                try:
                    text = (await el.inner_text() or "").strip().replace(" ", "")
                    if text != "发布":
                        continue
                    if await el.is_visible():
                        return el
                except Exception:
                    continue
        return None

    async def _try_insert_image(self, page: Any, file_path: str, _step) -> bool:
        try:
            button = await _wait_visible(page, IMAGE_BUTTON_SELECTORS, 4000)
            if button:
                async with page.expect_file_chooser(timeout=10000) as fc_info:
                    await button.click(timeout=6000)
                chooser = await fc_info.value
                await chooser.set_files(file_path)
                await asyncio.sleep(4)
                _step("插入正文图片", True)
                return True
        except Exception as exc:
            logger.info("[ZHIHU] 图片按钮上传失败，尝试隐藏 input: %s", exc)
        try:
            fi = await page.query_selector("input[type=file]")
            if fi:
                await fi.set_input_files(file_path)
                await asyncio.sleep(4)
                _step("插入正文图片", True, by="input")
                return True
        except Exception as exc:
            logger.info("[ZHIHU] 隐藏 input 上传图片失败: %s", exc)
        _step("插入正文图片", False)
        return False

    async def _click_publish(self, page: Any, _step) -> bool:
        """精确点「发布」，排除「发布设置」「发布预览」等。"""
        for sel in _split_selectors(PUBLISH_BUTTON_SELECTORS):
            try:
                els = await page.query_selector_all(sel)
            except Exception:
                els = []
            for el in els:
                try:
                    text = (await el.inner_text() or "").strip().replace(" ", "")
                    if text != "发布":
                        continue
                    if not await el.is_visible():
                        continue
                    await el.scroll_into_view_if_needed()
                    await asyncio.sleep(0.3)
                    try:
                        await el.click(timeout=8000)
                    except Exception:
                        await el.click(force=True, timeout=8000)
                    _step("点击发布", True)
                    return True
                except Exception:
                    continue
        try:
            clicked = await page.evaluate(
                """() => {
                    for (const el of Array.from(document.querySelectorAll('button'))) {
                        const t = (el.textContent || '').trim().replace(/\\s+/g, '');
                        if (t !== '发布') continue;
                        const r = el.getBoundingClientRect();
                        if (r.width <= 0 || r.height <= 0) continue;
                        el.scrollIntoView({ block: 'center' });
                        el.click();
                        return true;
                    }
                    return false;
                }"""
            )
        except Exception:
            clicked = False
        if clicked:
            _step("点击发布", True, by="js")
            return True
        _step("点击发布", False)
        return False

    async def _finish(self, page: Any, applied: Dict[str, Any], _step) -> Dict[str, Any]:
        """成功判定：跳文章页 /p/<id>（排除 /edit 草稿态），或出现「发布成功」。"""
        deadline = asyncio.get_event_loop().time() + 30
        while asyncio.get_event_loop().time() < deadline:
            try:
                url = (getattr(page, "url", "") or "")
            except Exception:
                url = ""
            low = url.lower()
            if "/p/" in low and "/edit" not in low:
                _step("校验发布成功", True, by="url")
                return {"ok": True, "url": url, "applied": applied}
            try:
                text = (await page.evaluate(
                    "() => (document.body && document.body.innerText) || ''"
                ))[:4000]
            except Exception:
                text = ""
            if "发布成功" in text:
                _step("校验发布成功", True, by="toast")
                return {"ok": True, "url": url, "applied": applied}
            await asyncio.sleep(1.5)
        _step("校验发布成功", False)
        return {
            "ok": False,
            "error": "已点击发布但未跳转到文章页（可能进入审核/草稿），请到知乎确认",
            "url": getattr(page, "url", "") or "",
            "applied": applied,
        }