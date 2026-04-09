"""高消耗 invoke_capability 前：可选的费用预估与用户确认。"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

CONFIRM_WAIT_SECONDS = 300

_PENDING: Dict[str, "PendingCapabilityConfirm"] = {}


@dataclass
class PendingCapabilityConfirm:
    user_id: int
    future: asyncio.Future
    created: float


def _purge_stale_locked(max_age: float = 600.0) -> None:
    now = time.time()
    for k, v in list(_PENDING.items()):
        if now - v.created <= max_age:
            continue
        if not v.future.done():
            v.future.set_result(False)
        _PENDING.pop(k, None)


def register_capability_confirm(user_id: int) -> Tuple[str, asyncio.Future]:
    _purge_stale_locked()
    token = secrets.token_urlsafe(24)
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    _PENDING[token] = PendingCapabilityConfirm(user_id=user_id, future=fut, created=time.time())
    return token, fut


def resolve_capability_confirm(token: str, user_id: int, accept: bool) -> bool:
    t = (token or "").strip()
    p = _PENDING.get(t)
    if p is None or p.user_id != user_id:
        return False
    if p.future.done():
        return False
    _PENDING.pop(t, None)
    p.future.set_result(bool(accept))
    return True


def abandon_capability_confirm(token: str) -> None:
    t = (token or "").strip()
    p = _PENDING.pop(t, None)
    if p and not p.future.done():
        p.future.set_result(False)


def invoke_should_prompt_cost_confirm(args: Dict[str, Any]) -> bool:
    """仅对明显会产生扣费的能力弹确认；轮询类调用由 progress_cb=None 跳过。"""
    cap = (args.get("capability_id") or "").strip()
    if cap == "task.get_result":
        return False
    if cap in ("image.generate", "video.generate", "comfly.veo.daihuo_pipeline", "media.edit"):
        return True
    if cap == "comfly.veo":
        pl = args.get("payload") if isinstance(args.get("payload"), dict) else {}
        act = (pl.get("action") or "").strip()
        if act in ("poll_video", "upload"):
            return False
        return act in ("submit_video", "generate_prompts")
    return False


def _fallback_unit_credits(db: Session, capability_id: str) -> Optional[int]:
    from ..models import CapabilityConfig

    row = db.query(CapabilityConfig).filter(CapabilityConfig.capability_id == capability_id).first()
    if not row or row.unit_credits is None:
        return None
    u = int(row.unit_credits)
    return u if u > 0 else None


async def estimate_capability_credits_for_invoke(
    db: Session, capability_id: str, args: Dict[str, Any]
) -> Dict[str, Any]:
    """按本次 payload（已与对话层补全的 model 一致）估算参考积分；说明从简。"""
    from .xskill_model_pricing import estimate_credits_from_pricing, fetch_model_pricing

    payload = args.get("payload") if isinstance(args.get("payload"), dict) else {}

    if capability_id in ("image.generate", "video.generate"):
        model = (payload.get("model") or payload.get("model_id") or "").strip()
        if not model:
            fb = _fallback_unit_credits(db, capability_id)
            return {"credits": fb, "note": ""}
        pricing = await fetch_model_pricing(model)
        if pricing:
            credits, extra = estimate_credits_from_pricing(pricing, payload)
            return {"credits": credits, "note": (extra or "").strip()}
        fb = _fallback_unit_credits(db, capability_id)
        return {"credits": fb if fb else None, "note": ""}

    if capability_id == "comfly.veo":
        pl = payload
        act = (pl.get("action") or "").strip()
        if act == "submit_video":
            model = (pl.get("video_model") or pl.get("model") or "").strip()
            if model:
                pricing = await fetch_model_pricing(model)
                if pricing:
                    credits, extra = estimate_credits_from_pricing(pricing, pl)
                    return {"credits": credits, "note": (extra or "").strip()}
        fb = _fallback_unit_credits(db, capability_id)
        return {"credits": fb, "note": ""}

    if capability_id == "comfly.veo.daihuo_pipeline":
        fb = _fallback_unit_credits(db, capability_id)
        return {"credits": fb, "note": ""}

    fb = _fallback_unit_credits(db, capability_id)
    return {"credits": fb, "note": ""}
