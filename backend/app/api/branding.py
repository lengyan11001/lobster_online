from __future__ import annotations

import json
import logging
import os
import re
from html import escape
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from ..core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()
_OEM_CODE_RE = re.compile(r"^[0-9]{4,12}$")
_EMPTY_IMAGE = "data:,"


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


def _load_runtime_profile(mark: str, oem_code: str = "") -> Dict[str, Any] | None:
    candidates: list[Path] = []
    raw_path = str(os.environ.get("LOBSTER_BRAND_PROFILE_PATH") or "").strip()
    if raw_path:
        candidates.append(Path(raw_path))
    code = str(oem_code or os.environ.get("LOBSTER_OEM_CODE") or "").strip()
    if _OEM_CODE_RE.fullmatch(code):
        cached = _brands_path().parent / "cache" / "profiles" / f"{code}.json"
        if cached not in candidates:
            candidates.append(cached)

    for path in candidates:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("read runtime branding failed path=%s: %s", path, exc)
            continue
        profile = record.get("profile") if isinstance(record, dict) else None
        profile_mark = str(profile.get("mark") or "").strip().lower() if isinstance(profile, dict) else ""
        if isinstance(profile, dict) and (not mark or profile_mark == mark):
            return profile
    return None


def _empty_branding() -> Dict[str, Any]:
    """Return an explicitly empty state; never substitute a default brand."""
    return {"available": False, "mark": "", "_branding_unavailable": True}


def _index_branding(requested_mark: str = "") -> Dict[str, Any]:
    """Resolve the brand synchronously for the first HTML response.

    The browser later refreshes branding through ``/api/branding``, but the
    initial document must already contain the same profile or the default
    brand is visible for one paint.
    """
    registry = _load_registry()
    marks = registry["marks"]
    requested = str(requested_mark or "").strip().lower()
    configured_mark = (getattr(settings, "lobster_brand_mark", None) or "").strip().lower()
    configured_code = str(getattr(settings, "lobster_oem_code", None) or "").strip()
    if str(os.environ.get("LOBSTER_BRANDING_UNAVAILABLE") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return _empty_branding()
    runtime_profile = _load_runtime_profile(requested or configured_mark, configured_code)
    runtime_mark = str(runtime_profile.get("mark") or "").strip().lower() if runtime_profile else ""

    # An OEM profile is authoritative. If it cannot be resolved, do not leak
    # the bundled bihuo profile into the first HTML response.
    if configured_code and not runtime_profile:
        return _empty_branding()

    if requested and requested in marks:
        mark = requested
        config = marks.get(mark)
    elif runtime_profile and (not requested or not runtime_mark or runtime_mark == requested):
        mark = runtime_mark or configured_mark or str(registry.get("default_mark") or "bihuo")
        config = runtime_profile
    elif requested:
        return _empty_branding()
    else:
        mark = configured_mark or str(registry.get("default_mark") or "bihuo")
        config = marks.get(mark)
        if not isinstance(config, dict):
            config = runtime_profile

    if not isinstance(config, dict):
        return _empty_branding()
    return {"available": True, "mark": mark, **config}


def render_index_html(template: str, requested_mark: str = "") -> str:
    """Inject branding into the index before returning it to the browser."""
    branding = _index_branding(requested_mark)
    icons = branding.get("icons") if isinstance(branding.get("icons"), dict) else {}
    available = bool(branding.get("available")) and not branding.get("_branding_unavailable")
    hero_subtitle = str(branding.get("hero_subtitle") or "") if available else ""
    subtitle_html = "".join(
        f'<span class="hero-subtitle-line">{escape(line)}</span>'
        for line in hero_subtitle.splitlines()
    )
    values = {
        "__LOBSTER_BRANDING_AVAILABLE__": "true" if available else "false",
        "__LOBSTER_BRAND_MARK__": str(branding.get("mark") or "").strip().lower() if available else "",
        "__LOBSTER_DOCUMENT_TITLE__": str(branding.get("document_title") or "") if available else "",
        "__LOBSTER_FAVICON_32__": str(icons.get("favicon_32") or _EMPTY_IMAGE) if available else _EMPTY_IMAGE,
        "__LOBSTER_APPLE_TOUCH__": str(icons.get("apple_touch") or _EMPTY_IMAGE) if available else _EMPTY_IMAGE,
        "__LOBSTER_LOGO_MARK__": str(icons.get("logo_mark") or _EMPTY_IMAGE) if available else _EMPTY_IMAGE,
        "__LOBSTER_LOGO_PRIMARY__": str(branding.get("logo_primary") or "") if available else "",
        "__LOBSTER_LOGO_ACCENT__": str(branding.get("logo_accent") or "") if available else "",
        "__LOBSTER_HERO_TITLE__": str(branding.get("hero_title") or "") if available else "",
        "__LOBSTER_HERO_SUBTITLE__": subtitle_html,
        "__LOBSTER_HOME_VISUAL__": str(icons.get("home_visual") or _EMPTY_IMAGE) if available else _EMPTY_IMAGE,
        "__LOBSTER_PARTNER_LOGO__": str(icons.get("header_partner_logo") or "") if available else "",
        "__LOBSTER_PARTNER_ALT__": str(icons.get("header_partner_logo_alt") or "") if available else "",
        "__LOBSTER_PARTNER_VISIBILITY__": "" if available and icons.get("header_partner_logo") else "hidden",
    }
    rendered = str(template or "")
    for token, raw_value in values.items():
        value = raw_value if token == "__LOBSTER_HERO_SUBTITLE__" else escape(raw_value, quote=True)
        rendered = rendered.replace(token, value)
    return rendered


@router.get("/api/branding", summary="当前品牌标记下的文案与图标路径（供前端与安装脚本同源配置）")
def get_branding() -> Dict[str, Any]:
    registry = _load_registry()
    configured_mark = (getattr(settings, "lobster_brand_mark", None) or "").strip().lower()
    if not configured_mark:
        return _empty_branding()
    legacy_oem_code = configured_mark if _OEM_CODE_RE.fullmatch(configured_mark) else ""
    configured_oem_code = str(getattr(settings, "lobster_oem_code", None) or "").strip()
    runtime_profile = _load_runtime_profile(
        "" if legacy_oem_code else configured_mark,
        legacy_oem_code or configured_oem_code,
    )
    if str(os.environ.get("LOBSTER_BRANDING_UNAVAILABLE") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return _empty_branding()
    if configured_oem_code and not runtime_profile:
        return _empty_branding()
    mark = str(runtime_profile.get("mark") or "").strip().lower() if runtime_profile else configured_mark
    marks = registry["marks"]
    cfg = marks.get(mark)
    if not isinstance(cfg, dict):
        cfg = runtime_profile or _load_runtime_profile(mark)
    if not isinstance(cfg, dict):
        return _empty_branding()
    out: Dict[str, Any] = {"available": True, "mark": mark, **cfg}
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
