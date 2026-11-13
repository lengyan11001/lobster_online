"""Compatibility wrapper for the local WeChat Channels lead-acquisition backend."""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()

try:
    from backend.channels_origin.channels_api import router as channels_router

    router.include_router(channels_router)
    CHANNELS_ORIGIN_BACKEND_READY = True
    CHANNELS_ORIGIN_BACKEND_ERROR = ""
except Exception as exc:  # pragma: no cover
    CHANNELS_ORIGIN_BACKEND_READY = False
    CHANNELS_ORIGIN_BACKEND_ERROR = str(exc)
    logger.exception("WeChat Channels UI backend failed to load: %s", exc)


@router.get("/api/channels/backend-status")
async def channels_origin_status() -> Dict[str, Any]:
    if CHANNELS_ORIGIN_BACKEND_READY:
        return {"code": 200, "ready": True, "mode": "local_wechat_ui"}
    return {
        "code": 503,
        "ready": False,
        "message": "视频号获客后端加载失败，请检查 backend/channels_origin。",
        "error": CHANNELS_ORIGIN_BACKEND_ERROR,
    }
