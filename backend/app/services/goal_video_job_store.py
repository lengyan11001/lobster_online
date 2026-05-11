"""In-memory job store for the goal-to-video pipeline."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

JOBS_LOCK = threading.Lock()
_JOBS: Dict[str, Dict[str, Any]] = {}
_JOB_TTL_SEC = 86400 * 3


def _valid_job_id(job_id: str) -> bool:
    jid = (job_id or "").strip().lower()
    return len(jid) == 32 and all(c in "0123456789abcdef" for c in jid)


def _prune_stale_unlocked(now: float) -> None:
    dead: List[str] = []
    for jid, job in _JOBS.items():
        if now - float(job.get("created_at_ts") or 0) > _JOB_TTL_SEC:
            dead.append(jid)
    for jid in dead:
        _JOBS.pop(jid, None)


def create_goal_video_job(*, user_id: int, payload: Dict[str, Any], job_id: Optional[str] = None) -> str:
    jid = (job_id or "").strip().lower()
    if not _valid_job_id(jid):
        jid = uuid.uuid4().hex
    now = time.time()
    with JOBS_LOCK:
        _prune_stale_unlocked(now)
        _JOBS[jid] = {
            "job_id": jid,
            "user_id": int(user_id),
            "status": "running",
            "stage": "queued",
            "created_at_ts": now,
            "updated_at_ts": now,
            "payload": dict(payload or {}),
            "error": None,
            "result": None,
            "progress": [],
        }
    return jid


def get_goal_video_job(job_id: str) -> Optional[Dict[str, Any]]:
    jid = (job_id or "").strip().lower()
    if not _valid_job_id(jid):
        return None
    now = time.time()
    with JOBS_LOCK:
        _prune_stale_unlocked(now)
        job = _JOBS.get(jid)
        return dict(job) if job is not None else None


def update_goal_video_job(job_id: str, **fields: Any) -> bool:
    jid = (job_id or "").strip().lower()
    if not _valid_job_id(jid):
        return False
    with JOBS_LOCK:
        job = _JOBS.get(jid)
        if not job:
            return False
        job.update(fields)
        job["updated_at_ts"] = time.time()
        return True


def append_goal_video_progress(job_id: str, *, stage: str, message: str, extra: Optional[Dict[str, Any]] = None) -> bool:
    jid = (job_id or "").strip().lower()
    if not _valid_job_id(jid):
        return False
    with JOBS_LOCK:
        job = _JOBS.get(jid)
        if not job:
            return False
        progress = list(job.get("progress") or [])
        item: Dict[str, Any] = {
            "ts": time.time(),
            "stage": (stage or "").strip()[:80],
            "message": (message or "").strip()[:500],
        }
        if extra:
            item["extra"] = extra
        progress.append(item)
        job["progress"] = progress[-80:]
        job["stage"] = (stage or "").strip()[:80] or job.get("stage") or "running"
        job["updated_at_ts"] = time.time()
        return True
