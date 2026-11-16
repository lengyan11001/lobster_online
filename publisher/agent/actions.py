"""Allowlisted browser actions (Playwright)."""
from __future__ import annotations

import logging
from typing import Any, Dict

from .models import Action, StepResult

logger = logging.getLogger(__name__)


async def _first_visible(locator: Any) -> Any:
    count = await locator.count()
    for idx in range(min(count, 8)):
        item = locator.nth(idx)
        try:
            if await item.is_visible():
                return item
        except Exception:
            continue
    return locator.nth(0) if count else None


async def _resolve_target(page: Any, target: Dict[str, Any]) -> Any:
    by = str((target or {}).get("by") or "text").lower()
    value = str((target or {}).get("value") or target.get("name") or "").strip()
    if by == "text" and value:
        return await _first_visible(page.get_by_text(value, exact=False))
    if by == "role" and value:
        role = str(target.get("role") or "button")
        return await _first_visible(page.get_by_role(role, name=value, exact=False))
    if by == "selector" and value:
        return await _first_visible(page.locator(value))
    return None

async def run_browser_action(page: Any, action: Action, *, file_path: str = "") -> StepResult:
    name = action.action
    target = action.target or {}
    try:
        if name == "abort":
            return StepResult(False, "agent_abort")
        if name == "reload":
            await page.reload(wait_until="domcontentloaded", timeout=30000)
            return StepResult(True, "reloaded")
        if name == "screenshot":
            await page.screenshot(path=str(target.get("value") or "agent_shot.png"))
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
