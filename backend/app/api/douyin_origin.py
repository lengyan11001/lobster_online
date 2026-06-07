"""Compatibility wrapper for the original Douyin lead-acquisition backend."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()
_BASE_DIR = Path(__file__).resolve().parents[3]
_ORIGIN_DIR = _BASE_DIR / "backend" / "douyin_origin"


def _install_origin_import_path() -> None:
    origin_path = str(_ORIGIN_DIR)
    if origin_path not in sys.path:
        sys.path.insert(0, origin_path)


try:
    _install_origin_import_path()
    from douyin_api import router as douyin_router  # type: ignore

    router.include_router(douyin_router)
    DOUYIN_ORIGIN_BACKEND_READY = True
except Exception as exc:  # pragma: no cover - defensive startup guard
    DOUYIN_ORIGIN_BACKEND_READY = False
    DOUYIN_ORIGIN_BACKEND_ERROR = str(exc)
    logger.exception("Original Douyin backend failed to load: %s", exc)
else:
    DOUYIN_ORIGIN_BACKEND_ERROR = ""


@router.get("/api/douyin/origin-status")
async def douyin_origin_status() -> Dict[str, Any]:
    if DOUYIN_ORIGIN_BACKEND_READY:
        return {"code": 200, "ready": True}
    return {
        "code": 503,
        "ready": False,
        "message": "Douyin origin backend failed to load. Please check dependencies and backend/douyin_origin.",
        "error": DOUYIN_ORIGIN_BACKEND_ERROR,
    }
