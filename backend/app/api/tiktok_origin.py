"""Compatibility wrapper for the TikTok lead-acquisition workbench backend."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()
_BASE_DIR = Path(__file__).resolve().parents[3]
_ORIGIN_DIR = _BASE_DIR / "backend" / "tiktok_origin"


def _install_origin_import_path() -> None:
    origin_path = str(_ORIGIN_DIR)
    if origin_path not in sys.path:
        sys.path.insert(0, origin_path)


try:
    from backend.tiktok_origin import tiktok_api  # type: ignore

    tiktok_router = tiktok_api.router

    router.include_router(tiktok_router)
    TIKTOK_ORIGIN_BACKEND_READY = True
except Exception as exc:  # pragma: no cover - defensive startup guard
    TIKTOK_ORIGIN_BACKEND_READY = False
    TIKTOK_ORIGIN_BACKEND_ERROR = str(exc)
    logger.exception("TikTok workbench backend failed to load: %s", exc)
else:
    TIKTOK_ORIGIN_BACKEND_ERROR = ""


@router.get("/api/tiktok/origin-status")
async def tiktok_origin_status() -> Dict[str, Any]:
    if TIKTOK_ORIGIN_BACKEND_READY:
        return {"code": 200, "ready": True}
    return {
        "code": 503,
        "ready": False,
        "message": "TikTok workbench backend failed to load. Please check backend/tiktok_origin.",
        "error": TIKTOK_ORIGIN_BACKEND_ERROR,
    }