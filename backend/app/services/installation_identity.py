"""Persist the installation slot + device seed in a file.

The slot used to live in WebView localStorage, which is tied to
``ROOT/browser_data/desktop_webview``.  OEM upgrades can switch ROOT, which
made the client generate a new slot.  The file below lives under the install
directory (``data/`` is protected by the OTA updater) and is the only source
of truth after the first migration.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CLIENT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_FILE = CLIENT_ROOT / "data" / "installation_identity.json"
INSTALLATION_IDENTITY_FILE = Path(
    os.environ.get("LOBSTER_INSTALLATION_IDENTITY_FILE") or str(_DEFAULT_FILE)
)
_VALID_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_LAST_READ_SIGNATURE: Optional[tuple] = None


def _prefix(value: Any, size: int = 18) -> str:
    text = str(value or "").strip()
    return text[:size]


def normalize_identity_value(value: Any) -> str:
    text = str(value or "").strip()
    return text if _VALID_ID.fullmatch(text) else ""


def raw_device_seed(value: Any) -> str:
    text = normalize_identity_value(value)
    if "--" in text:
        text = text.split("--", 1)[1]
    return normalize_identity_value(text)


def _empty(path: Optional[Path] = None, reason: str = "") -> Dict[str, Any]:
    return {
        "present": False,
        "installation_id": "",
        "device_seed": "",
        "path": str(path or INSTALLATION_IDENTITY_FILE),
        "reason": reason,
    }


def _log_read(result: Dict[str, Any], reason: str) -> None:
    global _LAST_READ_SIGNATURE
    signature = (
        bool(result.get("present")),
        str(result.get("installation_id") or ""),
        str(result.get("device_seed") or ""),
        str(reason or ""),
    )
    if signature == _LAST_READ_SIGNATURE:
        return
    _LAST_READ_SIGNATURE = signature
    if result.get("present"):
        logger.info(
            "[installation-identity] read present=True iid=%s seed_prefix=%s path=%s reason=%s",
            result.get("installation_id") or "",
            _prefix(result.get("device_seed"), 12),
            result.get("path") or "",
            reason or "ok",
        )
    else:
        logger.info(
            "[installation-identity] read present=False path=%s reason=%s",
            result.get("path") or "",
            reason or "missing",
        )


def read_installation_identity() -> Dict[str, Any]:
    path = Path(INSTALLATION_IDENTITY_FILE)
    if not path.is_file():
        result = _empty(path, "file_missing")
        _log_read(result, "file_missing")
        return result
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        result = _empty(path, "invalid_json")
        _log_read(result, f"invalid_json:{exc.__class__.__name__}")
        return result
    if not isinstance(data, dict):
        result = _empty(path, "invalid_payload")
        _log_read(result, "invalid_payload")
        return result
    installation_id = normalize_identity_value(data.get("installation_id"))
    device_seed = normalize_identity_value(data.get("device_seed"))
    if not installation_id and not device_seed:
        result = _empty(path, "empty_identity")
        _log_read(result, "empty_identity")
        return result
    if not installation_id:
        installation_id = device_seed
    if not device_seed:
        device_seed = raw_device_seed(installation_id)
    result = {
        "present": True,
        "installation_id": installation_id,
        "device_seed": device_seed,
        "path": str(path),
        "reason": "ok",
    }
    _log_read(result, "ok")
    return result


def write_installation_identity(
    installation_id: Any,
    device_seed: Any,
    *,
    source: str = "",
) -> Dict[str, Any]:
    iid = normalize_identity_value(installation_id)
    seed = normalize_identity_value(device_seed)
    if not iid and not seed:
        raise ValueError("installation_id or device_seed is required")
    if not iid:
        iid = seed
    if not seed:
        seed = raw_device_seed(iid)
    if not seed:
        raise ValueError("invalid installation_id/device_seed")

    # First-launch race guard: a second page/profile may load before the first
    # write completes and generate its own unsigned seed.  Once a file exists,
    # only a server-signed slot (u<digits>-...) may replace it.  Unsigned values
    # are ignored and the caller receives the file's authoritative identity.
    existing = read_installation_identity()
    incoming_signed = bool(re.match(r"^u\d+-", iid))
    if existing.get("present") and not incoming_signed:
        logger.info(
            "[installation-identity] write ignored source=%s incoming_iid=%s existing_iid=%s path=%s",
            source or "unknown",
            _prefix(iid),
            existing.get("installation_id") or "",
            existing.get("path") or "",
        )
        return {**existing, "ignored": True}

    path = Path(INSTALLATION_IDENTITY_FILE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "installation_id": iid,
            "device_seed": seed,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(str(tmp), str(path))
    except OSError as exc:
        logger.warning(
            "[installation-identity] write failed source=%s iid=%s path=%s error=%s",
            source or "unknown",
            _prefix(iid),
            path,
            exc,
        )
        raise

    logger.info(
        "[installation-identity] write ok source=%s iid=%s seed_prefix=%s signed=%s path=%s",
        source or "unknown",
        iid,
        _prefix(seed, 12),
        incoming_signed,
        path,
    )
    return {
        "present": True,
        "installation_id": iid,
        "device_seed": seed,
        "path": str(path),
    }
