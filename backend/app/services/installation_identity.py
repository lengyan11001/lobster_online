"""Persist the installation slot + device seed in a file.

The slot used to live in WebView localStorage, which is tied to
``ROOT/browser_data/desktop_webview``.  OEM upgrades can switch ROOT, which
made the client generate a new slot.  The file below lives under the install
directory (``data/`` is protected by the OTA updater) and is the only source
of truth after the first migration.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

CLIENT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_FILE = CLIENT_ROOT / "data" / "installation_identity.json"
INSTALLATION_IDENTITY_FILE = Path(
    os.environ.get("LOBSTER_INSTALLATION_IDENTITY_FILE") or str(_DEFAULT_FILE)
)
_VALID_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def normalize_identity_value(value: Any) -> str:
    text = str(value or "").strip()
    return text if _VALID_ID.fullmatch(text) else ""


def raw_device_seed(value: Any) -> str:
    text = normalize_identity_value(value)
    if "--" in text:
        text = text.split("--", 1)[1]
    return normalize_identity_value(text)


def _empty(path: Optional[Path] = None) -> Dict[str, Any]:
    return {
        "present": False,
        "installation_id": "",
        "device_seed": "",
        "path": str(path or INSTALLATION_IDENTITY_FILE),
    }


def read_installation_identity() -> Dict[str, Any]:
    path = Path(INSTALLATION_IDENTITY_FILE)
    if not path.is_file():
        return _empty(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty(path)
    if not isinstance(data, dict):
        return _empty(path)
    installation_id = normalize_identity_value(data.get("installation_id"))
    device_seed = normalize_identity_value(data.get("device_seed"))
    if not installation_id and not device_seed:
        return _empty(path)
    if not installation_id:
        installation_id = device_seed
    if not device_seed:
        device_seed = raw_device_seed(installation_id)
    return {
        "present": True,
        "installation_id": installation_id,
        "device_seed": device_seed,
        "path": str(path),
    }


def write_installation_identity(installation_id: Any, device_seed: Any) -> Dict[str, Any]:
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
        return {**existing, "ignored": True}

    path = Path(INSTALLATION_IDENTITY_FILE)
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
    return {
        "present": True,
        "installation_id": iid,
        "device_seed": seed,
        "path": str(path),
    }
