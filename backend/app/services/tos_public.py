"""TOS public base per edition (domestic Guangzhou bucket vs overseas Jakarta bucket)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

DOMESTIC_TOS_BASE = "https://lobster-online-assets-2103871705.tos-cn-guangzhou.volces.com"
OVERSEAS_TOS_BASE = "https://lobster-assets-jakarta-2103871705.tos-ap-southeast-3.volces.com"

_TRUTHY = {"1", "true", "yes", "on"}


def _read_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if value:
        return value
    env_file = _ROOT / ".env"
    if not env_file.is_file():
        return ""
    try:
        for raw in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, item = line.split("=", 1)
            if key.strip() == name:
                return item.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


@lru_cache(maxsize=1)
def is_overseas() -> bool:
    return _read_env("LOBSTER_IS_OVERSEAS_USER").lower() in _TRUTHY


def tos_base() -> str:
    override = _read_env("LOBSTER_TOS_BASE").rstrip("/")
    if override:
        return override
    return OVERSEAS_TOS_BASE if is_overseas() else DOMESTIC_TOS_BASE


def tos_url(path: str) -> str:
    return tos_base().rstrip("/") + "/" + str(path or "").lstrip("/")


def rewrite_tos_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return raw
    for marker in (DOMESTIC_TOS_BASE, OVERSEAS_TOS_BASE):
        if raw.startswith(marker):
            return tos_base().rstrip("/") + raw[len(marker):]
    return raw
