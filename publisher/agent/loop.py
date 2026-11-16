"""Recovery loop: observe -> decide -> act -> verify."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from . import memory
from .actions import run_browser_action
from .budget import Budget, agent_mode
from .models import Action, RecoverResult
from .observer import observe_browser
from .planner import decide_async
from .trace import TraceWriter
from .verifier import verify_expect, verify_stage

logger = logging.getLogger(__name__)


def _is_login_url(url: str) -> bool:
    low = str(url or "").lower()
    return any(k in low for k in ("login", "signin", "passport", "sso"))


async def recover(
    page: Any,
    *,
    platform: str,
    stage: str,
    goal: str,
    file_path: str = "",
    mode: Optional[str] = None,
    max_steps: int = 12,
    timeout_seconds: float = 240.0,
    trace: Optional[TraceWriter] = None,
) -> RecoverResult:
    mode = (mode or agent_mode("browser")).lower()
    result = RecoverResult(mode=mode)
    if mode == "off" or page is None:
        return result
    writer = trace or TraceWriter(platform=platform, stage=stage, env="browser")
    budget = Budget(max_steps=max_steps, timeout_seconds=timeout_seconds)
    history: List[Dict[str, Any]] = []
    try:
        first = await observe_browser(page, platform=platform, stage=stage)
        writer.event("observe", signature=first.signature, url=first.url, nodes=len(first.nodes))
        shot = writer.save_screenshot(first.screenshot_b64, "observe")
        if shot:
            writer.event("screenshot", path=shot)
        if _is_login_url(first.url):
            result.need_login = True
            result.error = "login_required"
            writer.finish(False, 0, "login_required")
            return result
        if mode == "observe":
            writer.finish(False, 0, "observe_only")
            return result
        route = memory.get_route(first.signature)
        if route:
            writer.event("learned_route", actions=len(route))
            ok_all = True
            for item in route:
                action = Action(action=str(item.get("action") or ""), target=dict(item.get("target") or {}),
                                text=str(item.get("text") or ""), expect=dict(item.get("expect") or {}), confidence=1.0)
                if not action.valid():
                    ok_all = False
                    break
                step = await run_browser_action(page, action, file_path=file_path)
                writer.event("learned_step", action=action.action, ok=step.ok, error=step.error)
                if not step.ok:
                    ok_all = False
                    break
            if ok_all and await verify_stage(page, platform, stage):
                result.ok = True
                result.recovered = True
                result.steps = len(route)
                writer.finish(True, result.steps, "learned_route")
                return result
            memory.record_failure(first.signature)
        executed: List[Dict[str, Any]] = []
        signature = first.signature
        while budget.take_step():
            observation = await observe_browser(page, platform=platform, stage=stage)
            action = await decide_async(observation, goal, history, budget, file_path=file_path)
            if action is None:
                result.error = "no_action"
                break
            writer.event("decide", action=action.action, target=action.target,
                         confidence=action.confidence, thought=action.thought[:300])
            if action.action == "abort":
                result.error = "agent_abort"
                break
            step = await run_browser_action(page, action, file_path=file_path)
            verified = bool(step.ok) and await verify_expect(page, action.expect, platform=platform, stage=stage)
            history.append({"action": action.action, "target": action.target, "ok": step.ok,
                            "verified": verified, "error": step.error})
            writer.event("step", action=action.action, ok=step.ok, verified=verified, error=step.error)
            if verified:
                # 记忆只收“真的生效并且验过”的步骤，免得把 wait_for 死等写进路线
                executed.append({"action": action.action, "target": action.target,
                                 "text": action.text, "expect": action.expect})
            if verified and await verify_stage(page, platform, stage):
                result.ok = True
                result.recovered = True
                memory.record_success(signature, executed)
                break
        result.steps = budget.steps
        if not result.ok:
            memory.record_failure(signature)
            if not result.error:
                result.error = "recovery_failed"
        writer.finish(result.ok, result.steps, result.error)
        return result
    except Exception as exc:
        logger.warning("[agent] recovery crashed: %s", exc)
        result.error = f"{type(exc).__name__}: {exc}"[:200]
        writer.finish(False, budget.steps, result.error)
        return result
