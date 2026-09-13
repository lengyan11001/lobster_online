"""Record why the previous local client process went away.

The cloud reaper (``lobster_server`` ``backend/app/api/scheduled_tasks.py`` ->
``_fail_previous_client_runs``) decides whether an interrupted run is recorded
as ``client_restart_clean`` or ``client_crashed`` from
``run.progress.client_exit_reason``.  The local Online client never wrote that
field, so every restart collapsed into ``client_gone_unknown`` ("未收到正常退出
标记") -- a deliberate restart and a real crash were indistinguishable.

This module owns the local half of that chain:

``data/client_session.json``
    Written once per backend process start: which backend pid ran and under
    which ``client_process_id``.  Its existence is what proves "a previous
    session really existed", so a first install is never reported as a crash.

``data/last_exit.json``
    Written by ``desktop/launcher.py`` *immediately before* it intentionally
    stops the services (window closed, OTA update restart, watchdog restart,
    stale-backend cleanup).  A backend that dies on its own (exception, power
    loss) leaves no marker -- that is exactly the crash signal.

:func:`previous_exit_reason` runs on backend startup and returns:

* the marker's ``reason`` when a fresh marker exists (deliberate stop),
* ``"crash"`` when a previous session exists but no fresh marker does,
* ``""`` on a first run, where nothing can be claimed about a previous process.

The marker is consumed (deleted) while reading so one exit is reported once.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

MARKER_SCHEMA = 1
SESSION_FILENAME = "client_session.json"
MARKER_FILENAME = "last_exit.json"

# Declared once here; the launcher mirrors this literal because it runs as a
# standalone script and must not import the (heavy) backend package.
EXIT_REASON_HEADER = "X-Previous-Client-Exit-Reason"

# Reasons the launcher may write.  Kept in sync with
# ``backend/app/api/scheduled_tasks.py`` classification sets.
LAUNCHER_REASONS = frozenset(
    {
        "update_restart",
        "user_closed",
        "watchdog_kill",
        "startup_cleanup",
        "launcher_exit",
    }
)

_LOCK = threading.Lock()
_REASON: Optional[str] = None
_ACKED = False


def _repo_root() -> Path:
    # backend/app/services/client_exit_marker.py -> <root>
    return Path(__file__).resolve().parents[3]


def _data_dir(root: Optional[Path] = None) -> Path:
    return (Path(root) if root is not None else _repo_root()) / "data"


def _session_path(root: Optional[Path] = None) -> Path:
    return _data_dir(root) / SESSION_FILENAME


def _marker_path(root: Optional[Path] = None) -> Path:
    return _data_dir(root) / MARKER_FILENAME


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_time(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def write_exit_marker(
    reason: str,
    *,
    detail: str = "",
    backend_pid: Optional[int] = None,
    launcher_pid: Optional[int] = None,
    root: Optional[Path] = None,
) -> bool:
    """Record a deliberate stop.  Used by the launcher (and by tests)."""
    clean_reason = str(reason or "").strip().lower()
    if not clean_reason:
        return False
    payload: Dict[str, Any] = {
        "schema": MARKER_SCHEMA,
        "at": _now_iso(),
        "reason": clean_reason,
        "backend_pid": int(backend_pid) if backend_pid else None,
        "launcher_pid": int(launcher_pid) if launcher_pid else os.getpid(),
        "detail": str(detail or "")[:400],
    }
    try:
        data_dir = _data_dir(root)
        data_dir.mkdir(parents=True, exist_ok=True)
        _marker_path(root).write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        return True
    except OSError as exc:  # pragma: no cover - best effort only
        logger.warning("[CLIENT-EXIT] failed to write exit marker: %s", exc)
        return False


def _classify(session: Dict[str, Any], marker: Dict[str, Any]) -> str:
    if not str(session.get("started_at") or "").strip():
        # No previous session on disk: nothing to classify (fresh install).
        return ""
    marker_reason = str(marker.get("reason") or "").strip().lower()
    if not marker_reason:
        return "crash"
    session_started = _parse_time(session.get("started_at"))
    marker_at = _parse_time(marker.get("at"))
    if session_started is None or marker_at is None:
        # Cannot order the two events; refusing to claim "clean" is the safe
        # direction because a false "clean" hides real crashes.
        return "crash"
    if marker_at < session_started:
        # The marker predates the session that just ended, so it describes an
        # earlier stop: this process still died without notice.
        return "crash"
    return marker_reason


def peek_previous_exit_reason(root: Optional[Path] = None) -> str:
    """Classify without consuming the marker (diagnostics and tests)."""
    return _classify(_read_json(_session_path(root)), _read_json(_marker_path(root)))


def consume_previous_exit_reason(root: Optional[Path] = None) -> str:
    """Classify, consume the marker, and start a new session record."""
    session = _read_json(_session_path(root))
    marker = _read_json(_marker_path(root))
    reason = _classify(session, marker)
    try:
        _marker_path(root).unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover - best effort only
        logger.warning("[CLIENT-EXIT] failed to consume exit marker: %s", exc)
    payload: Dict[str, Any] = {
        "schema": MARKER_SCHEMA,
        "pid": os.getpid(),
        "started_at": _now_iso(),
        "reason": reason,
    }
    try:
        data_dir = _data_dir(root)
        data_dir.mkdir(parents=True, exist_ok=True)
        _session_path(root).write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    except OSError as exc:  # pragma: no cover - best effort only
        logger.warning("[CLIENT-EXIT] failed to write session record: %s", exc)
    if reason:
        logger.info("[CLIENT-EXIT] previous client process exit_reason=%s", reason)
    return reason


def previous_exit_reason() -> str:
    """Reason the previous client process exited; computed once per process."""
    global _REASON
    with _LOCK:
        if _REASON is None:
            try:
                _REASON = consume_previous_exit_reason()
            except Exception:  # pragma: no cover - never break the channel
                logger.warning("[CLIENT-EXIT] exit reason probe failed", exc_info=True)
                _REASON = ""
        return _REASON or ""


def pending_exit_reason_header() -> Dict[str, str]:
    """Header describing the previous process, until a claim has seen it."""
    global _ACKED
    if _ACKED:
        return {}
    reason = previous_exit_reason()
    if not reason:
        return {}
    return {EXIT_REASON_HEADER: reason}


def mark_exit_reason_reported() -> None:
    """Stop advertising the previous exit reason once a claim request landed.

    The cloud reaps interrupted runs while serving ``/api/scheduled-tasks/
    pending``, so after the first successful claim there is nothing left to
    classify and the header is dropped instead of being replayed forever.
    """
    global _ACKED
    _ACKED = True


def reset_cache() -> None:
    """Test helper: forget the per-process cache."""
    global _REASON, _ACKED
    _REASON = None
    _ACKED = False
