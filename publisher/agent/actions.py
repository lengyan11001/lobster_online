"""Allowlisted browser actions (Playwright)."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict
from urllib.parse import urlparse

from .models import Action, StepResult

logger = logging.getLogger(__name__)

# 只允许在这些平台域名内跳转：agent 能自己纠错（走错页面→回到上传页），但不会被模型带去别处
_ALLOWED_HOST_SUFFIXES = (
    "douyin.com",
    "weixin.qq.com",
    "tiktok.com",
    "xiaohongshu.com",
    "kuaishou.com",
    "toutiao.com",
    "weibo.com",
    "bilibili.com",
)


def _host_allowed(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    return any(host == suf or host.endswith("." + suf) for suf in _ALLOWED_HOST_SUFFIXES)


async def _in_nav(el: Any) -> bool:
    """左侧导航/顶部栏里的同名元素不算目标：抖音左栏那个「作品发布」会被「发布」误命中，
    点下去直接离开编辑页、草稿丢失。"""
    js = "el => !!el.closest('nav,aside,header,[role=navigation],[class*=sidebar],[class*=Sidebar]')"
    try:
        return bool(await el.evaluate(js))
    except Exception:
        return False


async def _first_visible(locator: Any, *, skip_nav: bool = False) -> Any:
    count = await locator.count()
    fallback = None
    for idx in range(min(count, 8)):
        item = locator.nth(idx)
        try:
            if not await item.is_visible():
                continue
            if skip_nav and await _in_nav(item):
                if fallback is None:
                    fallback = item
                continue
            return item
        except Exception:
            continue
    return fallback if fallback is not None else (locator.nth(0) if count else None)


async def _resolve_target(page: Any, target: Dict[str, Any]) -> Any:
    by = str((target or {}).get("by") or "text").lower()
    value = str((target or {}).get("value") or target.get("name") or "").strip()
    if by == "text" and value:
        exact = await _first_visible(page.get_by_text(value, exact=True), skip_nav=True)
        return exact or await _first_visible(page.get_by_text(value, exact=False), skip_nav=True)
    if by == "role" and value:
        role = str(target.get("role") or "button")
        # 精确名优先：name="发布" 不该命中「作品发布」
        exact = await _first_visible(page.get_by_role(role, name=value, exact=True), skip_nav=True)
        return exact or await _first_visible(page.get_by_role(role, name=value, exact=False), skip_nav=True)
    if by == "selector" and value:
        return await _first_visible(page.locator(value), skip_nav=True)
    return None

async def run_browser_action(page: Any, action: Action, *, file_path: str = "") -> StepResult:
    name = action.action
    target = action.target or {}
    try:
        if name == "abort":
            return StepResult(False, "agent_abort")
        if name == "op":
            from publisher.robot.dispatch import run_op_async

            op_name = str(target.get("name") or target.get("op") or "").strip()
            params = dict(target.get("params") or {})
            if not op_name:
                return StepResult(False, "empty_op")
            res = await run_op_async(op_name, params, page=page)
            err = "" if res.ok else f"{res.layer}:{res.error}"
            return StepResult(bool(res.ok), err[:200], observed=json.dumps(res.evidence, ensure_ascii=False)[:400])
        if name == "goto":
            url = str(target.get("value") or target.get("url") or action.text or "").strip()
            if not url:
                return StepResult(False, "empty_url")
            if url.startswith("/"):
                base = str(getattr(page, "url", "") or "")
                parts = urlparse(base)
                if not parts.scheme or not parts.netloc:
                    return StepResult(False, "no_base_url")
                url = f"{parts.scheme}://{parts.netloc}{url}"
            if not _host_allowed(url):
                return StepResult(False, "host_not_allowed")
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            return StepResult(True, "navigated")
        if name == "reload":
            await page.reload(wait_until="domcontentloaded", timeout=30000)
            return StepResult(True, "reloaded")
        if name == "screenshot":
            # 模型常把 target 写成 {by:'text', value:'body'} —— 截图只认文件路径
            raw = str(target.get("path") or "").strip()
            if not raw or not raw.lower().endswith((".png", ".jpg", ".jpeg")):
                raw = "agent_shot.png"
            await page.screenshot(path=raw)
            return StepResult(True, "screenshot")
        if name == "press":
            await page.keyboard.press(str(target.get("value") or action.text or "Escape"))
            return StepResult(True, "pressed")
        if name == "scroll":
            await page.mouse.wheel(0, int(target.get("value") or 600))
            return StepResult(True, "scrolled")
        if name == "close_overlay":
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            for text in ("取消", "关闭", "稍后", "我知道了"):
                el = await _first_visible(page.get_by_text(text, exact=True))
                if el is not None:
                    try:
                        await el.click(timeout=3000)
                        break
                    except Exception:
                        continue
            return StepResult(True, "overlay_closed")
        if str(target.get("by") or "").lower() == "point":
            await page.mouse.click(int(target.get("x") or 0), int(target.get("y") or 0))
            return StepResult(True, "clicked_point")
        el = await _resolve_target(page, target)
        if el is None:
            return StepResult(False, "target_not_found")
        if name == "click":
            await el.click(timeout=8000)
            return StepResult(True, "clicked")
        if name == "type":
            text = str(action.text or target.get("text") or "")
            try:
                await el.fill(text, timeout=8000)
            except Exception:
                await el.click(timeout=5000)
                await page.keyboard.type(text, delay=20)
            return StepResult(True, "typed")
        if name == "set_files":
            path = file_path or str(target.get("value") or "")
            selector = str(target.get("selector") or 'input[type="file"]')
            await page.set_input_files(selector, path, timeout=60000)
            return StepResult(True, "files_set")
        if name == "wait_for":
            await el.wait_for(state="visible", timeout=15000)
            return StepResult(True, "visible")
        return StepResult(False, "unsupported_action")
    except Exception as exc:
        logger.info("[agent] action %s failed: %s", name, exc)
        return StepResult(False, f"{type(exc).__name__}: {exc}"[:200])
