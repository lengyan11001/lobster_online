from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from ..core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


def _brands_path() -> Path:
    # backend/app/api/branding.py -> 仓库根（与 create_app 中 static_dir 一致）
    return Path(__file__).resolve().parent.parent.parent.parent / "static" / "branding" / "brands.json"


def _load_registry() -> Dict[str, Any]:
    p = _brands_path()
    if not p.exists():
        raise HTTPException(status_code=500, detail=f"Branding registry missing: {p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Invalid branding JSON: {e}") from e
    if not isinstance(raw, dict) or "marks" not in raw or not isinstance(raw["marks"], dict):
        raise HTTPException(status_code=500, detail="Branding registry invalid structure")
    return raw


def _load_runtime_profile(mark: str) -> Dict[str, Any] | None:
    raw_path = str(os.environ.get("LOBSTER_BRAND_PROFILE_PATH") or "").strip()
    if not raw_path:
        return None
    try:
        record = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("read runtime branding failed path=%s: %s", raw_path, exc)
        return None
    profile = record.get("profile") if isinstance(record, dict) else None
    if not isinstance(profile, dict) or str(profile.get("mark") or "").strip().lower() != mark:
        return None
    return profile


@router.get("/api/branding", summary="当前品牌标记下的文案与图标路径（供前端与安装脚本同源配置）")
def get_branding() -> Dict[str, Any]:
    registry = _load_registry()
    mark = (getattr(settings, "lobster_brand_mark", None) or "").strip().lower()
    if not mark:
        raise HTTPException(status_code=500, detail="LOBSTER_BRAND_MARK is empty")
    marks = registry["marks"]
    cfg = marks.get(mark)
    if not isinstance(cfg, dict):
        cfg = _load_runtime_profile(mark)
    if not isinstance(cfg, dict):
        raise HTTPException(status_code=400, detail=f"Unknown brand mark: {mark}")
    out: Dict[str, Any] = {"mark": mark, **cfg}
    parent = (getattr(settings, "lobster_parent_account", None) or "").strip()
    if parent:
        out["parent_account"] = parent
    is_overseas_user = bool(getattr(settings, "lobster_is_overseas_user", False))
    out["is_overseas_user"] = is_overseas_user
    if is_overseas_user:
        out["document_title"] = "必火AI海外员工"
        out["logo_primary"] = "必火"
        out["logo_accent"] = "AI海外员工"
    return out
