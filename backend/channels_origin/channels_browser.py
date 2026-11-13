"""视频号助手 Web 自动化（登录态、评论区、私信）。

视频号没有独立的公开网页版，创作者侧统一走 https://channels.weixin.qq.com/platform。
这里复用 publisher 的持久化浏览器目录，用户扫码一次后即可复用。
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from runtime import CHANNELS_PROFILE_DIR

logger = logging.getLogger(__name__)

CHANNELS_HOME_URL = "https://channels.weixin.qq.com/platform"
CHANNELS_LOGIN_MARK = "channels.weixin.qq.com/login"
CHANNELS_COMMENT_URL = "https://channels.weixin.qq.com/platform/comment"
CHANNELS_PRIVATE_MSG_URL = "https://channels.weixin.qq.com/platform/private_msg"

_LOGGED_IN_TEXT_MARKS = ("视频号助手", "内容管理", "数据中心", "发表动态", "作品管理", "评论管理")

# 扫码登录页需要保活，否则 Playwright 连接被回收会把浏览器一起关掉。
_login_page = None
_login_ctx = None


def _browser_api():
    from publisher.browser_pool import (  # type: ignore
        _acquire_context,
        _default_browser_options,
        _get_page_with_reacquire,
        _setup_auto_close,
    )

    return _acquire_context, _default_browser_options, _get_page_with_reacquire, _setup_auto_close


async def _open_page(*, headless: bool = False):
    acquire, default_options, get_page, _ = _browser_api()
    opts = default_options()
    ctx, _ = await acquire(str(CHANNELS_PROFILE_DIR), new_headless=headless, browser_options=opts)
    page, ctx = await get_page(
        str(CHANNELS_PROFILE_DIR),
        ctx,
        new_headless_on_recreate=headless,
        browser_options=opts,
    )
    return page, ctx, opts


def _text_of(value: Any) -> str:
    return str(value or "").strip()


def _walk_dicts(value: Any, depth: int = 0):
    if depth > 8:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child, depth + 1)
    elif isinstance(value, list):
        for child in value[:50]:
            yield from _walk_dicts(child, depth + 1)


def _extract_rows(payload: Any, kind: str) -> List[Dict[str, Any]]:
    """从视频号助手 XHR JSON 里宽松提取会话/评论行。"""
    rows: List[Dict[str, Any]] = []
    for node in _walk_dicts(payload):
        if kind == "conversation":
            nickname = node.get("nickname") or node.get("nickName") or node.get("title") or node.get("name")
            if not nickname:
                continue
            last = (
                node.get("lastMsg")
                or node.get("last_message")
                or node.get("content")
                or node.get("digest")
                or node.get("summary")
                or ""
            )
            if isinstance(last, dict):
                last = last.get("content") or last.get("text") or ""
            rows.append(
                {
                    "nickname": _text_of(nickname),
                    "username": _text_of(node.get("username") or node.get("finderUsername")),
                    "avatar": _text_of(node.get("avatar") or node.get("headUrl") or node.get("head_url")),
                    "last_message": _text_of(last),
                    "unread": int(node.get("unread") or node.get("unreadCount") or 0),
                    "updated_at": int(node.get("updateTime") or node.get("timestamp") or 0),
                }
            )
        elif kind == "comment":
            content = node.get("content") or node.get("commentContent") or node.get("text")
            if not content or node.get("commentId") is None and node.get("comment_id") is None:
                continue
            rows.append(
                {
                    "comment_id": _text_of(node.get("commentId") or node.get("comment_id")),
                    "nickname": _text_of(node.get("nickname") or node.get("nickName")),
                    "username": _text_of(node.get("username")),
                    "content": _text_of(content),
                    "like_count": int(node.get("likeCount") or node.get("like_count") or 0),
                    "create_time": int(node.get("createTime") or node.get("create_time") or 0),
                }
            )
    # 去重
    seen = set()
    unique: List[Dict[str, Any]] = []
    for row in rows:
        key = row.get("username") or row.get("comment_id") or row.get("nickname")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique[:200]


async def login_status() -> Dict[str, Any]:
    global _login_page, _login_ctx
    if _login_page is not None:
        try:
            url = str(getattr(_login_page, "url", "") or "")
            text = await _login_page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 3000)")
            text = _text_of(text)
            logged_in = CHANNELS_LOGIN_MARK not in url.lower() and any(m in text for m in _LOGGED_IN_TEXT_MARKS)
            return {
                "ok": True,
                "logged_in": logged_in,
                "url": url,
                "login_wall": CHANNELS_LOGIN_MARK in url.lower(),
                "text_head": text[:160],
                "profile_dir": str(CHANNELS_PROFILE_DIR),
            }
        except Exception:
            _login_page = None
            _login_ctx = None
    page = None
    ctx = None
    try:
        page, ctx, opts = await _open_page(headless=True)
        await page.goto(CHANNELS_HOME_URL, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(1500)
        url = str(getattr(page, "url", "") or "")
        text = ""
        try:
            text = await page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 3000)")
        except Exception:
            text = ""
        text = _text_of(text)
        logged_in = CHANNELS_LOGIN_MARK not in url.lower() and any(m in text for m in _LOGGED_IN_TEXT_MARKS)
        return {
            "ok": True,
            "logged_in": logged_in,
            "url": url,
            "login_wall": CHANNELS_LOGIN_MARK in url.lower(),
            "text_head": text[:160],
            "profile_dir": str(CHANNELS_PROFILE_DIR),
        }
    except Exception as exc:
        return {"ok": False, "logged_in": False, "error": str(exc)[:300], "profile_dir": str(CHANNELS_PROFILE_DIR)}
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass


async def open_login() -> Dict[str, Any]:
    """打开有头浏览器供用户扫码；保留全局引用，避免扫码过程中被回收。"""
    global _login_page, _login_ctx
    try:
        if _login_page is not None:
            try:
                await _login_page.bring_to_front()
                return {
                    "ok": True,
                    "message": "登录页已打开，请在浏览器里用微信扫码。",
                    "url": str(getattr(_login_page, "url", "") or ""),
                    "profile_dir": str(CHANNELS_PROFILE_DIR),
                }
            except Exception:
                _login_page = None
                _login_ctx = None
        page, ctx, opts = await _open_page(headless=False)
        _login_page, _login_ctx = page, ctx
        await page.goto(CHANNELS_HOME_URL, wait_until="domcontentloaded", timeout=45_000)
        return {
            "ok": True,
            "message": "已打开视频号助手登录页，请在浏览器里用微信扫码；登录后回到本页点“检查登录”。",
            "url": str(getattr(page, "url", "") or ""),
            "profile_dir": str(CHANNELS_PROFILE_DIR),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300], "profile_dir": str(CHANNELS_PROFILE_DIR)}


async def _capture(kind: str, url: str, wait_ms: int = 2500, scrolls: int = 2) -> Dict[str, Any]:
    page = None
    captured: List[Any] = []
    try:
        page, ctx, opts = await _open_page(headless=True)
        marker = "comment" if kind == "comment" else "msg"
        async def on_response(response):
            try:
                u = str(getattr(response, "url", "") or "").lower()
                if str(getattr(response, "status", 0) or 0) != 200:
                    return
                if "channels.weixin.qq.com" not in u:
                    return
                if marker not in u and "session" not in u and "conversation" not in u:
                    return
                captured.append(await response.json())
            except Exception:
                return

        page.on("response", on_response)
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(wait_ms)
        for _ in range(max(0, scrolls)):
            try:
                await page.mouse.wheel(0, 3000)
            except Exception:
                pass
            await page.wait_for_timeout(1200)
        rows: List[Dict[str, Any]] = []
        for payload in captured:
            rows.extend(_extract_rows(payload, kind))
        seen = set()
        unique = []
        for row in rows:
            key = row.get("username") or row.get("comment_id") or row.get("nickname")
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
        return {
            "ok": True,
            "rows": unique,
            "payloads": len(captured),
            "url": str(getattr(page, "url", "") or ""),
            "login_wall": CHANNELS_LOGIN_MARK in str(getattr(page, "url", "") or "").lower(),
        }
    except Exception as exc:
        return {"ok": False, "rows": [], "error": str(exc)[:300], "payloads": len(captured)}
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass


async def collect_private_messages() -> Dict[str, Any]:
    return await _capture("conversation", CHANNELS_PRIVATE_MSG_URL)


async def collect_own_comments() -> Dict[str, Any]:
    return await _capture("comment", CHANNELS_COMMENT_URL, scrolls=3)


async def send_private_message(nickname: str, text: str) -> Dict[str, Any]:
    nickname = _text_of(nickname)
    text = _text_of(text)
    if not nickname or not text:
        return {"ok": False, "error": "昵称和消息内容不能为空"}
    page = None
    try:
        page, ctx, opts = await _open_page(headless=False)
        await page.goto(CHANNELS_PRIVATE_MSG_URL, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(2000)
        if CHANNELS_LOGIN_MARK in str(getattr(page, "url", "") or "").lower():
            return {"ok": False, "need_login": True, "error": "视频号助手未登录，请先扫码登录"}
        # 会话列表里点昵称
        target = page.get_by_text(nickname, exact=False).first
        await target.click(timeout=8000)
        await page.wait_for_timeout(1200)
        box = None
        for selector in ("[contenteditable='true']", "textarea", "div[role='textbox']"):
            locator = page.locator(selector).last
            try:
                if await locator.count():
                    box = locator
                    break
            except Exception:
                continue
        if box is None:
            return {"ok": False, "error": "未找到私信输入框，视频号助手页面结构可能已更新"}
        await box.click(timeout=5000)
        try:
            await box.fill(text, timeout=5000)
        except Exception:
            await page.keyboard.type(text)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(1200)
        return {"ok": True, "nickname": nickname, "text": text}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass
