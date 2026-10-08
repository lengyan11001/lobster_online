"""Compatibility wrapper for the Liepin recruiting browser skill.

技能实现放在 backend/liepin_origin/（浏览器驱动 + 能力分发 + HTTP 入口），
这里只负责把它挂到客户端 FastAPI 应用上。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()
_BASE_DIR = Path(__file__).resolve().parents[3]
_BACKEND_DIR = _BASE_DIR / "backend"


def _install_origin_import_path() -> None:
    backend_path = str(_BACKEND_DIR)
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)


try:
    _install_origin_import_path()
    from liepin_origin.liepin_api import router as liepin_router  # type: ignore

    router.include_router(liepin_router)
    LIEPIN_ORIGIN_BACKEND_READY = True
except Exception as exc:  # pragma: no cover - defensive startup guard
    LIEPIN_ORIGIN_BACKEND_READY = False
    LIEPIN_ORIGIN_BACKEND_ERROR = str(exc)
    logger.exception("Liepin skill backend failed to load: %s", exc)
else:
    LIEPIN_ORIGIN_BACKEND_ERROR = ""


@router.get("/api/liepin/origin-status", summary="猎聘技能加载状态")
async def liepin_origin_status() -> Dict[str, Any]:
    if LIEPIN_ORIGIN_BACKEND_READY:
        return {"code": 200, "ready": True}
    return {"code": 503, "ready": False,
            "message": "Liepin skill backend failed to load. Check backend/liepin_origin.",
            "error": LIEPIN_ORIGIN_BACKEND_ERROR}
