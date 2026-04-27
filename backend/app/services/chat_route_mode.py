"""Local runtime setting for main chat routing.

The setting is stored in custom_configs.json so it survives OTA updates and can
be changed from the UI without restarting the backend.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

CHAT_ROUTE_MODE_DIRECT = "direct"
CHAT_ROUTE_MODE_OPENCLAW = "openclaw"
DEFAULT_CHAT_ROUTE_MODE = CHAT_ROUTE_MODE_DIRECT
VALID_CHAT_ROUTE_MODES = {CHAT_ROUTE_MODE_DIRECT, CHAT_ROUTE_MODE_OPENCLAW}

_BASE_DIR = Path(__file__).resolve().parents[3]
_CUSTOM_CONFIGS_FILE = _BASE_DIR / "custom_configs.json"
_LOCK = Lock()


def normalize_chat_route_mode(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    if raw in ("", CHAT_ROUTE_MODE_DIRECT, "llm", "local", "local_llm", "direct_llm"):
        return CHAT_ROUTE_MODE_DIRECT
    if raw in (CHAT_ROUTE_MODE_OPENCLAW, "open_claw", "oc", "gateway"):
        return CHAT_ROUTE_MODE_OPENCLAW
    return ""


def _load_custom_configs_unlocked() -> dict[str, Any]:
    if not _CUSTOM_CONFIGS_FILE.exists():
        return {"configs": {}, "custom_models": []}
    try:
        data = json.loads(_CUSTOM_CONFIGS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read custom_configs.json for chat route mode: %s", exc)
        return {"configs": {}, "custom_models": []}
    if not isinstance(data, dict):
        return {"configs": {}, "custom_models": []}
    if not isinstance(data.get("configs"), dict):
        data["configs"] = {}
    if not isinstance(data.get("custom_models"), list):
        data["custom_models"] = []
    return data


def get_chat_route_mode() -> str:
    with _LOCK:
        data = _load_custom_configs_unlocked()
    local_settings = data.get("local_settings")
    if not isinstance(local_settings, dict):
        return DEFAULT_CHAT_ROUTE_MODE
    mode = normalize_chat_route_mode(local_settings.get("chat_route_mode"))
    return mode or DEFAULT_CHAT_ROUTE_MODE


def set_chat_route_mode(mode: Any) -> str:
    normalized = normalize_chat_route_mode(mode)
    if normalized not in VALID_CHAT_ROUTE_MODES:
        raise ValueError(f"Invalid chat route mode: {mode!r}")

    with _LOCK:
        data = _load_custom_configs_unlocked()
        local_settings = data.get("local_settings")
        if not isinstance(local_settings, dict):
            local_settings = {}
            data["local_settings"] = local_settings
        local_settings["chat_route_mode"] = normalized
        _CUSTOM_CONFIGS_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return normalized
