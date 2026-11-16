"""Learned routes memory: page signature -> verified action list."""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PATH = Path(__file__).resolve().parents[1] / "learned_routes.json"
_LOCK = threading.Lock()


def _load() -> Dict[str, Any]:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8")) if _PATH.is_file() else {}
    except Exception:
        return {}


def _save(data: Dict[str, Any]) -> None:
    try:
        tmp = _PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_PATH)
    except Exception as exc:
        logger.debug("[agent-memory] save failed: %s", exc)


def get_route(signature: str) -> Optional[List[Dict[str, Any]]]:
    if not signature:
        return None
    with _LOCK:
        entry = _load().get(signature)
    if not entry or int(entry.get("fail_count") or 0) >= 2:
        return None
    actions = entry.get("actions")
    return actions if isinstance(actions, list) and actions else None


def record_success(signature: str, actions: List[Dict[str, Any]]) -> None:
    if not signature or not actions:
        return
    with _LOCK:
        data = _load()
        entry = data.get(signature) or {"success_count": 0, "fail_count": 0}
        entry["actions"] = actions
        entry["success_count"] = int(entry.get("success_count") or 0) + 1
        entry["fail_count"] = 0
        entry["stable"] = entry["success_count"] >= 3
        entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
        data[signature] = entry
        _save(data)


def record_failure(signature: str) -> None:
    if not signature:
        return
    with _LOCK:
        data = _load()
        entry = data.get(signature) or {"success_count": 0, "fail_count": 0}
        entry["fail_count"] = int(entry.get("fail_count") or 0) + 1
        entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
        data[signature] = entry
        _save(data)
