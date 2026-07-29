from __future__ import annotations

import re
from typing import Mapping, Optional

from ..core.config import settings


DEFAULT_BRAND_MARK = "bihuo"
_BRAND_MARK_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")


def configured_brand_mark(raw: Optional[str] = None) -> str:
    value = str(raw or settings.lobster_brand_mark or DEFAULT_BRAND_MARK).strip().lower()
    return value if _BRAND_MARK_RE.fullmatch(value) else DEFAULT_BRAND_MARK


def with_oem_brand_header(
    headers: Optional[Mapping[str, str]] = None,
    *,
    brand_mark: Optional[str] = None,
) -> dict[str, str]:
    """Build headers for requests from Online to Lobster cloud services."""
    result = {str(key): str(value) for key, value in (headers or {}).items()}
    result["X-Lobster-Brand"] = configured_brand_mark(brand_mark)
    return result
