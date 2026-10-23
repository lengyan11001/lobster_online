"""TikTok Studio 网页发布驱动：按抖音同样的持久化 Chrome + Playwright 逻辑。

不是官方 API，走 tiktok.com/tiktokstudio 网页上传；账号登录态存放在发布账号自己的
browser_data 目录。选择器做了多语言/多版本兜底，TikTok 改版时需要按实际页面校准。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Dict, Optional

from skills._base import BaseDriver

logger = logging.getLogger(__name__)

UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=upload"
HOME_URL = "https://www.tiktok.com/tiktokstudio"
LOGIN_URL = "https://www.tiktok.com/login?redirect_url=https%3A%2F%2Fwww.tiktok.com%2Ftiktokstudio%2Fupload"
MAX_CAPTION = 2200

_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

_FILE_SELECTORS = (
    'input[type="file"][accept*="video"]',
    'input[type="file"]',
)
_CAPTION_SELECTORS = (
    'div[contenteditable="true"]',
    'div[data-e2e="caption-input"]',
    'div.public-DraftEditor-content',
    'div[role="textbox"]',
    'textarea[placeholder*="description"]',
    'textarea[placeholder*="描述"]',
)
_POST_SELECTORS = (
    'button[data-e2e="post_video_button"]',
    'button[data-e2e="post-button"]',
    'button[data-e2e="publish-button"]',
)
_POST_TEXTS = re.compile(r"^(post|publish|发布|发布视频)$", re.I)
_SUCCESS_WORDS = (
    "your video is being posted",
    "your video has been posted",
    "video posted",
    "posted successfully",
    "发布成功",
    "已发布",
    "作品已发布",
    "提交成功",
)
_FAIL_WORDS = (
    "couldn't post",
    "could not post",
    "post failed",
    "upload failed",
    "something went wrong",
    "发布失败",
    "上传失败",
    "内容异常",
)


def _nav_ms(default: int) -> int:
    try:
        raw = int(os.environ.get("TIKTOK_NAV_TIMEOUT_MS") or 0)
    except Exception:
        raw = 0
    return raw or default


def _clean(value: Any, limit: int = MAX_CAPTION) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


def build_tiktok_caption(title: str, description: str, tags: str) -> str:
    """TikTok 只有一个 caption 输入框：标题/描述/话题合并，限制 2200 字符。"""
    title = (title or "").strip()
    description = (description or "").strip()
    parts: list[str] = []
    if title and description and title != description:
        parts = [title, description]
    elif description or title:
        parts = [description or title]
    caption = "\n".join(parts).strip()
    tag_list = [x.strip().lstrip("#") for x in re.split(r"[,，\s]+", tags or "") if x.strip()]
    if tag_list:
        suffix = " ".join("#" + x for x in tag_list)
        caption = (caption + "\n" + suffix).strip() if caption else suffix
    return caption[:MAX_CAPTION]


async def _visible_first(page: Any, selectors: tuple[str, ...], timeout_ms: int = 1200) -> Any:
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if await loc.is_visible(timeout=timeout_ms):
                return loc
        except Exception:
            continue
    return None


async def _wait_visible(page: Any, selectors: tuple[str, ...], timeout_ms: int = 30000) -> Any:
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000.0
    while asyncio.get_event_loop().time() < deadline:
        loc = await _visible_first(page, selectors, timeout_ms=800)
        if loc is not None:
            return loc
        await asyncio.sleep(0.5)
    return None


async def _dismiss_dialogs(page: Any) -> int:
    """关掉 TikTok 的引导/升级/权限弹窗。"""
    clicked = 0
    names = re.compile(r"^(got it|ok|continue|not now|maybe later|知道了|我知道了|稍后|以后再说|关闭|×)$", re.I)
    for _ in range(3):
        try:
            loc = page.get_by_role("button", name=names).first
            if await loc.is_visible(timeout=800):
                await loc.click(timeout=2000)
                clicked += 1
                await asyncio.sleep(0.4)
                continue
        except Exception:
            pass
        break
    return clicked


class TikTokDriver(BaseDriver):
    def login_url(self) -> str:
        return LOGIN_URL

    async def _passive_login_check(self, page: Any) -> bool:
        try:
            url = str(getattr(page, "url", "") or "").lower()
            if "/login" in url or "passport" in url:
                return False
            try:
                cookies = await page.context.cookies("https://www.tiktok.com")
            except Exception:
                cookies = []
            names = {str(c.get("name") or "") for c in cookies if c}
            if names & {"sessionid", "sessionid_ss", "sid_tt", "uid_tt"}:
                return True
            # Cookie 被清掉但页面仍在上传页时，file input 是可靠信号。
            return bool(await page.query_selector('input[type="file"]'))
        except Exception:
            return False

    async def check_login(self, page: Any, navigate: bool = True) -> bool:
        if not navigate:
            return await self._passive_login_check(page)
        try:
            await page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=_nav_ms(30000))
            await asyncio.sleep(2)
            return await self._passive_login_check(page)
        except Exception:
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
            entry = {"action": note, "ok": ok, **extra}
            applied["steps"].append(entry)
            logger.info("[TIKTOK-PUBLISH] %s => %s %s", note, "OK" if ok else "FAIL", extra or "")

        try:
            if not os.path.isfile(file_path):
                _step("检查文件存在", False, path=file_path)
                return {"ok": False, "error": f"文件不存在: {file_path}", "applied": applied}
            suffix = os.path.splitext(file_path)[1].lower()
            if suffix in _IMAGE_EXTS:
                _step("检查素材类型", False, ext=suffix)
                return {"ok": False, "error": "TikTok 当前只支持视频发布；图片/图文请先在 TikTok 后台手动发布", "applied": applied}
            if suffix not in _VIDEO_EXTS:
                _step("检查素材类型", False, ext=suffix)
                return {"ok": False, "error": f"TikTok 不支持的视频格式: {suffix}", "applied": applied}
            _step("检查文件存在", True, size=os.path.getsize(file_path), ext=suffix)

            logger.info("[TIKTOK-PUBLISH] navigate upload page: %s", UPLOAD_URL)
            await page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=_nav_ms(60000))
            await asyncio.sleep(2)
            n = await _dismiss_dialogs(page)
            if n:
                _step("关闭弹窗", True, count=n)

            if not await self._passive_login_check(page):
                return {"ok": False, "need_login": True, "error": "TikTok 未登录，请在打开的浏览器中登录后重试", "applied": applied}

            file_input = await _wait_visible(page, _FILE_SELECTORS, timeout_ms=30000)
            if file_input is None:
                # 隐藏 input 用 is_visible 可能为 False，直接取 DOM 节点。
                for selector in _FILE_SELECTORS:
                    try:
                        file_input = page.locator(selector).first
                        if await file_input.count():
                            break
                    except Exception:
                        file_input = None
                if file_input is None:
                    _step("查找上传控件", False)
                    return {"ok": False, "error": "TikTok 上传页没有找到文件选择控件，可能页面改版或未登录", "applied": applied}
            await file_input.set_input_files(file_path)
            _step("选择视频文件", True, path=os.path.basename(file_path))

            # 上传完成后才会出现完整的 caption 编辑区/发布按钮。
            caption_box = await _wait_visible(page, _CAPTION_SELECTORS, timeout_ms=300000)
            if caption_box is None:
                _step("等待上传完成", False)
                return {"ok": False, "error": "视频上传后未出现描述编辑框，请检查网络或 TikTok 页面是否改版", "applied": applied}
            _step("视频上传完成", True)

            caption = build_tiktok_caption(title, description, tags)
            if caption:
                try:
                    await caption_box.click(timeout=5000)
                    await page.keyboard.press("Control+A")
                    await page.keyboard.type(caption, delay=8)
                    _step("填写标题/描述/话题", True, length=len(caption))
                except Exception as exc:
                    # contenteditable 失败时用 JS 兜底。
                    try:
                        await caption_box.evaluate(
                            "(el, text) => { el.focus(); el.innerText = text; el.dispatchEvent(new Event('input', {bubbles:true})); }",
                            caption,
                        )
                        _step("填写标题/描述/话题", True, mode="js", length=len(caption))
                    except Exception:
                        _step("填写标题/描述/话题", False, error=str(exc))
                        return {"ok": False, "error": f"填写 TikTok 描述失败: {exc}", "applied": applied}
            else:
                _step("填写标题/描述/话题", True, skipped="empty")

            # 等发布按钮变可用（上传/版权检查未完成时按钮是 disabled）。
            post_btn = None
            deadline = asyncio.get_event_loop().time() + 300
            while asyncio.get_event_loop().time() < deadline:
                post_btn = await _visible_first(page, _POST_SELECTORS, timeout_ms=600)
                if post_btn is None:
                    try:
                        post_btn = page.get_by_role("button", name=_POST_TEXTS).first
                        if not await post_btn.is_visible(timeout=600):
                            post_btn = None
                    except Exception:
                        post_btn = None
                if post_btn is not None:
                    try:
                        if await post_btn.is_enabled():
                            break
                    except Exception:
                        break
                post_btn = None
                await asyncio.sleep(2)

            if post_btn is None:
                _step("查找发布按钮", False)
                return {"ok": False, "error": "TikTok 页面没有找到可用的 Post/发布 按钮", "applied": applied}

            await post_btn.scroll_into_view_if_needed()
            await post_btn.click(timeout=10000)
            _step("点击发布按钮", True)

            # 可能弹出版权/确认提示，继续发布。
            for _ in range(3):
                await asyncio.sleep(1)
                try:
                    confirm = page.get_by_role(
                        "button",
                        name=re.compile(r"^(post anyway|continue to post|继续发布|仍然发布|确认发布)$", re.I),
                    ).first
                    if await confirm.is_visible(timeout=800):
                        await confirm.click(timeout=3000)
                        _step("确认继续发布", True)
                except Exception:
                    pass

            success = False
            error_msg = ""
            for _ in range(90):
                await asyncio.sleep(2)
                cur = str(page.url or "")
                if "/tiktokstudio/content" in cur and "/upload" not in cur:
                    success = True
                    break
                try:
                    body = (await page.inner_text("body"))[:12000].lower()
                except Exception:
                    body = ""
                if any(w in body for w in _FAIL_WORDS):
                    error_msg = "TikTok 页面提示发布失败"
                    break
                if any(w in body for w in _SUCCESS_WORDS):
                    success = True
                    break

            if success:
                _step("确认发布成功", True, url=page.url)
                return {
                    "ok": True,
                    "url": page.url,
                    "message": "TikTok 视频已提交发布",
                    "applied": applied,
                }
            if error_msg:
                _step("确认发布成功", False, error=error_msg)
                return {"ok": False, "error": error_msg, "applied": applied}
            _step("确认发布结果", False, reason="timeout")
            return {
                "ok": False,
                "error": "已点击 TikTok 发布，但在 3 分钟内未确认成功；请到 TikTok 内容管理核对，避免重复发布",
                "applied": applied,
                "unconfirmed": True,
            }
        except Exception as exc:
            logger.exception("[TIKTOK-PUBLISH] publish failed")
            _step("异常", False, error=str(exc))
            return {"ok": False, "error": str(exc), "applied": applied}
