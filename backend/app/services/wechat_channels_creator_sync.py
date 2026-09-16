"""视频号（微信视频号助手）作品数据同步：只读取「内容管理 → 作品列表」的计数。

沿用 publisher.browser_pool 的持久化登录态（与视频号发布共用账号浏览器目录），
打开 https://channels.weixin.qq.com/platform/post/list 后监听页面自身发出的
作品列表 XHR JSON，归一成与抖音一致的 items 结构，供发布数据（播放量）采样使用。

只读：不点击任何发布/编辑/删除按钮；命中登录拦截直接返回 need_relogin，不做重试。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from .creator_metrics_collect import parse_channels_post_list_json

logger = logging.getLogger(__name__)

CHANNELS_HOME_URL = "https://channels.weixin.qq.com/platform"
CHANNELS_POST_LIST_URL = "https://channels.weixin.qq.com/platform/post/list"
CHANNELS_LOGIN_MARK = "channels.weixin.qq.com/login"

_POST_LIST_URL_MARKS = (
    "post_list",
    "post/list",
    "get_post_list",
    "object/list",
)
_LOGGED_IN_TEXT_MARKS = ("视频号助手", "内容管理", "数据中心", "发表动态", "作品管理")


def is_channels_post_list_response(response: Any) -> bool:
    """判断一条网络响应是否为视频号作品列表接口（宽松匹配，避免版本改名漏抓）。"""
    try:
        url = str(getattr(response, "url", "") or "").lower()
        status = int(getattr(response, "status", 0) or 0)
    except Exception:
        return False
    if status != 200:
        return False
    if "channels.weixin.qq.com" not in url:
        return False
    return any(mark in url for mark in _POST_LIST_URL_MARKS)


class ChannelsPostCollector:
    """挂在 page.on("response") 上，缓存作品列表接口返回的 JSON。"""

    def __init__(self) -> None:
        self.payloads: List[Dict[str, Any]] = []
        self.error: Optional[str] = None

    async def on_response(self, response: Any) -> None:
        if not is_channels_post_list_response(response):
            return
        try:
            data = await response.json()
        except Exception as exc:  # 响应体可能已被释放或非 JSON
            self.error = f"响应解析失败: {exc}"
            return
        if isinstance(data, dict):
            self.payloads.append(data)

    def merged_items(self) -> Tuple[Optional[str], List[Dict[str, Any]], int]:
        """合并多页 payload；按作品 id 去重，保留首次出现的顺序。"""
        merged: List[Dict[str, Any]] = []
        seen: set = set()
        last_error: Optional[str] = None
        pages = 0
        for payload in self.payloads:
            err, items, _has_more = parse_channels_post_list_json(payload)
            if err:
                last_error = err
                continue
            pages += 1
            for item in items:
                key = str(item.get("id") or "")
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(item)
        if not merged and last_error is None and self.error:
            last_error = self.error
        return last_error, merged, pages


async def channels_login_signals(page: Any) -> Dict[str, Any]:
    """登录态信号：URL 命中登录页 / 页面文案命中工作台关键词。"""
    url = ""
    try:
        url = str(getattr(page, "url", "") or "")
    except Exception:
        url = ""
    text = ""
    try:
        text = await page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 4000)")
    except Exception:
        text = ""
    text = str(text or "")
    logged_in = False
    if CHANNELS_LOGIN_MARK not in url.lower():
        logged_in = any(mark in text for mark in _LOGGED_IN_TEXT_MARKS)
    return {
        "url": url,
        "logged_in": logged_in,
        "login_wall": CHANNELS_LOGIN_MARK in url.lower(),
        "text_head": text[:200],
    }


async def _scroll_for_more(page: Any) -> None:
    """触发下一页加载；只做滚动，不点任何按钮。"""
    try:
        mouse = getattr(page, "mouse", None)
        if mouse is not None:
            await mouse.wheel(0, 4000)
            return
    except Exception:
        pass
    try:
        await page.evaluate("() => window.scrollBy(0, Math.max(800, window.innerHeight))")
    except Exception:
        pass


async def collect_channels_posts(
    page: Any,
    *,
    max_scrolls: int = 3,
    wait_ms: int = 1500,
) -> Dict[str, Any]:
    """在当前页面收集作品列表（假定已打开作品列表页）。"""
    collector = ChannelsPostCollector()
    try:
        page.on("response", collector.on_response)
    except Exception as exc:
        return {"ok": False, "items": [], "error": f"无法监听页面响应: {exc}", "meta": {}}

    async def _settle() -> None:
        try:
            await page.wait_for_timeout(wait_ms)
        except Exception:
            await asyncio.sleep(wait_ms / 1000.0)

    await _settle()
    for _ in range(max(0, int(max_scrolls))):
        if collector.payloads:
            break
        await _scroll_for_more(page)
        await _settle()

    err, items, pages = collector.merged_items()
    signals = await channels_login_signals(page)
    need_relogin = bool(signals.get("login_wall")) or (not items and not signals.get("logged_in"))
    meta: Dict[str, Any] = {
        "source": "wechat_channels_post_list",
        "pages_captured": pages,
        "payload_count": len(collector.payloads),
        "login_url": signals.get("url"),
        "logged_in": bool(signals.get("logged_in")),
        "need_relogin": need_relogin,
    }
    if items:
        meta["fetched_count"] = len(items)
        return {"ok": True, "items": items, "error": None, "meta": meta}

    if need_relogin:
        logger.warning("[WECHAT-CHANNELS-SYNC] 视频号登录态失效，需重新登录: %s", signals.get("url"))
        return {
            "ok": False,
            "items": [],
            "error": "视频号登录态已失效，请重新登录该账号浏览器后再同步",
            "meta": meta,
        }
    return {
        "ok": False,
        "items": [],
        "error": err or "未捕获到视频号作品列表数据",
        "meta": meta,
    }


async def sync_wechat_channels_creator_content(
    profile_dir: str,
    *,
    new_context_headless: bool = False,
    browser_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """用持久化上下文打开视频号作品列表并采集计数（只读）。"""
    from publisher.browser_pool import (
        _acquire_context,
        _default_browser_options,
        _get_page_with_reacquire,
        _setup_auto_close,
    )

    opts = browser_options if browser_options is not None else _default_browser_options()
    ctx, _ = await _acquire_context(
        profile_dir, new_headless=new_context_headless, browser_options=opts
    )
    page, ctx = await _get_page_with_reacquire(
        profile_dir,
        ctx,
        new_headless_on_recreate=new_context_headless,
        browser_options=opts,
    )
    try:
        await page.goto(CHANNELS_POST_LIST_URL, wait_until="domcontentloaded", timeout=45_000)
        result = await collect_channels_posts(page)
        _setup_auto_close(ctx, profile_dir, page, browser_options=opts)
        return result
    except Exception as exc:
        logger.exception("sync_wechat_channels_creator_content")
        try:
            _setup_auto_close(ctx, profile_dir, page, browser_options=opts)
        except Exception:
            pass
        return {"ok": False, "items": [], "error": str(exc), "meta": {}}
