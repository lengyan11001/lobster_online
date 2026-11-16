"""Desktop (Windows UIA) adapter for WeChat takeover."""
from __future__ import annotations

import base64
import hashlib
import io
import logging
from typing import Any, Dict, List, Optional

from .budget import Budget, agent_mode
from .models import Action, Observation, RecoverResult
from .planner import decide_sync
from .trace import TraceWriter

logger = logging.getLogger(__name__)


def find_window(keyword: str = "微信") -> Any:
    try:
        from pywinauto import Desktop
        for win in Desktop(backend="uia").windows():
            try:
                if keyword in str(win.window_text() or ""):
                    return win
            except Exception:
                continue
    except Exception as exc:
        logger.debug("[agent] find_window failed: %s", exc)
    return None


def _rect(window: Any) -> Optional[tuple]:
    try:
        r = window.rectangle()
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        return None


def observe_desktop_window(window: Any, *, stage: str) -> Observation:
    nodes: List[Dict[str, Any]] = []
    title = ""
    try:
        title = str(window.window_text() or "")
    except Exception:
        pass
    try:
        controls = window.descendants()[:60]
        for c in controls:
            try:
                info = c.element_info
                rect = c.rectangle()
                name = str(info.name or "")
                if not name:
                    continue
                nodes.append({
                    "role": str(info.control_type or ""),
                    "text": name[:80],
                    "x": int(rect.left), "y": int(rect.top),
                    "w": int(rect.right - rect.left), "h": int(rect.bottom - rect.top),
                    "enabled": bool(c.is_enabled()),
                })
            except Exception:
                continue
    except Exception as exc:
        logger.debug("[agent] desktop controls failed: %s", exc)
    shot_b64 = ""
    try:
        from PIL import ImageGrab
        rect = _rect(window)
        if rect:
            img = ImageGrab.grab(bbox=rect)
            longest = max(img.size)
            if longest > 1024:
                ratio = 1024.0 / longest
                img = img.resize((max(1, int(img.width * ratio)), max(1, int(img.height * ratio))))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=70)
            shot_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:
        logger.debug("[agent] desktop screenshot failed: %s", exc)
    sig_src = "desktop|" + stage + "|" + title + "|" + "|".join(
        (str(n.get("role")) + ":" + str(n.get("text")))[:40] for n in nodes[:40]
    )
    signature = "sig_" + hashlib.sha256(sig_src.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return Observation(env="desktop", stage=stage, url="", title=title, nodes=nodes,
                       visible_text="", screenshot_b64=shot_b64, signature=signature)

def _find_control(window: Any, name: str) -> Any:
    low = str(name or "").strip()
    if not low:
        return None
    try:
        for c in window.descendants():
            try:
                if low in str(c.element_info.name or ""):
                    return c
            except Exception:
                continue
    except Exception:
        pass
    return None


def verify_desktop(window: Any, expect: Optional[Dict[str, Any]]) -> bool:
    expect = dict(expect or {})
    kind = str(expect.get("kind") or "").lower()
    if not kind or kind == "any":
        return True
    if kind in {"text", "role"}:
        return _find_control(window, str(expect.get("value") or expect.get("name") or "")) is not None
    if kind == "title_contains":
        try:
            return str(expect.get("value") or "") in str(window.window_text() or "")
        except Exception:
            return False
    return True


def run_desktop_action(window: Any, action: Action) -> Any:
    from .models import StepResult
    name = action.action
    target = action.target or {}
    try:
        if name == "abort":
            return StepResult(False, "agent_abort")
        if name == "press":
            from pywinauto import keyboard
            keyboard.send_keys(str(target.get("value") or action.text or "{ESC}"))
            return StepResult(True, "pressed")
        if name == "close_overlay":
            from pywinauto import keyboard
            keyboard.send_keys("{ESC}")
            return StepResult(True, "overlay_closed")
        if name == "scroll":
            from pywinauto import mouse
            x, y = int(target.get("x") or 0), int(target.get("y") or 0)
            mouse.wheel(coords=(x, y), wheel_dist=int(target.get("value") or -3))
            return StepResult(True, "scrolled")
        if str(target.get("by") or "").lower() == "point":
            from pywinauto import mouse
            mouse.click(coords=(int(target.get("x") or 0), int(target.get("y") or 0)))
            return StepResult(True, "clicked_point")
        control = _find_control(window, str(target.get("value") or target.get("name") or ""))
        if control is None:
            return StepResult(False, "target_not_found")
        if name == "click":
            control.click_input()
            return StepResult(True, "clicked")
        if name == "type":
            control.click_input()
            control.type_keys(str(action.text or target.get("text") or ""), with_spaces=True, set_foreground=True)
            return StepResult(True, "typed")
        if name == "wait_for":
            return StepResult(True, "visible")
        return StepResult(False, "unsupported_action")
    except Exception as exc:
        return StepResult(False, f"{type(exc).__name__}: {exc}"[:200])

def recover_desktop(
    window: Any = None,
    *,
    stage: str,
    goal: str,
    max_steps: int = 8,
    timeout_seconds: float = 60.0,
    mode: Optional[str] = None,
) -> RecoverResult:
    from . import memory
    mode = (mode or agent_mode("desktop")).lower()
    result = RecoverResult(mode=mode)
    if mode == "off":
        return result
    win = window or find_window()
    if win is None:
        result.error = "window_not_found"
        return result
    writer = TraceWriter(platform="wechat", stage=stage, env="desktop")
    budget = Budget(max_steps=max_steps, timeout_seconds=timeout_seconds)
    history = []
    try:
        first = observe_desktop_window(win, stage=stage)
        writer.event("observe", signature=first.signature, title=first.title, nodes=len(first.nodes))
        shot = writer.save_screenshot(first.screenshot_b64, "observe")
        if shot:
            writer.event("screenshot", path=shot)
        if mode == "observe":
            writer.finish(False, 0, "observe_only")
            return result
        executed = []
        while budget.take_step():
            obs = observe_desktop_window(win, stage=stage)
            action = decide_sync(obs, goal, history, budget)
            if action is None:
                result.error = "no_action"
                break
            writer.event("decide", action=action.action, target=action.target,
                         confidence=action.confidence, thought=action.thought[:300])
            if action.action == "abort":
                result.error = "agent_abort"
                break
            step = run_desktop_action(win, action)
            verified = bool(step.ok) and verify_desktop(win, action.expect)
            history.append({"action": action.action, "target": action.target, "ok": step.ok,
                            "verified": verified, "error": step.error})
            writer.event("step", action=action.action, ok=step.ok, verified=verified, error=step.error)
            executed.append({"action": action.action, "target": action.target,
                             "text": action.text, "expect": action.expect})
            if verified:
                result.ok = True
                result.recovered = True
                memory.record_success(obs.signature, executed)
                break
        result.steps = budget.steps
        if not result.ok:
            memory.record_failure(first.signature)
            if not result.error:
                result.error = "recovery_failed"
        writer.finish(result.ok, result.steps, result.error)
        return result
    except Exception as exc:
        logger.warning("[agent] desktop recovery crashed: %s", exc)
        result.error = f"{type(exc).__name__}: {exc}"[:200]
        writer.finish(False, budget.steps, result.error)
        return result
