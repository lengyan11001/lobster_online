"""Compatibility wrapper for the WeChat Channels lead-acquisition backend."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()
_BASE_DIR = Path(__file__).resolve().parents[3]
_ORIGIN_DIR = _BASE_DIR / "backend" / "channels_origin"


def _install_origin_import_path() -> None:
    origin_path = str(_ORIGIN_DIR)
    if origin_path not in sys.path:
        sys.path.insert(0, origin_path)


try:
    _install_origin_import_path()
    from channels_api import router as channels_router  # type: ignore

    router.include_router(channels_router)
    CHANNELS_ORIGIN_BACKEND_READY = True
    CHANNELS_ORIGIN_BACKEND_ERROR = ""
except Exception as exc:  # pragma: no cover
    CHANNELS_ORIGIN_BACKEND_READY = False
    CHANNELS_ORIGIN_BACKEND_ERROR = str(exc)
    logger.exception("WeChat Channels workbench backend failed to load: %s", exc)


@router.get("/api/channels/origin-status")
async def channels_origin_status() -> Dict[str, Any]:
    if CHANNELS_ORIGIN_BACKEND_READY:
        return {"code": 200, "ready": True, "platform": "wechat_channels"}
    return {
        "code": 503,
        "ready": False,
        "message": "视频号获客后端加载失败，请检查 backend/channels_origin。",
        "error": CHANNELS_ORIGIN_BACKEND_ERROR,
    }
