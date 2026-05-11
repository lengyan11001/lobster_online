"""Viral video remix async job store: in-memory status for split/remix/merge workflow."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

JOBS_LOCK = threading.Lock()
_JOBS: Dict[str, Dict[str, Any]] = {}
_JOB_TTL_SEC = 86400 * 3


def _prune_stale_unlocked(now: float) -> None:
    dead: List[str] = []
    for jid, job in _JOBS.items():
        if now - float(job.get("created_at_ts") or 0) > _JOB_TTL_SEC:
            dead.append(jid)
    for jid in dead:
        _JOBS.pop(jid, None)


def create_job_record(*, user_id: int, payload: Dict[str, Any], job_dir: str, job_id: Optional[str] = None) -> str:
    jid = (job_id or "").strip().lower()
    if not jid or len(jid) != 32 or any(c not in "0123456789abcdef" for c in jid):
        jid = uuid.uuid4().hex
    now = time.time()
    with JOBS_LOCK:
        _prune_stale_unlocked(now)
        _JOBS[jid] = {
            "job_id": jid,
            "user_id": user_id,
            "status": "queued",
            "stage": "queued",
            "created_at_ts": now,
            "updated_at_ts": now,
            "payload": payload,
            "job_dir": job_dir,
            "error": None,
            "result": None,
            "segments": [],
            "total_segments": 0,
            "completed_segments": 0,
            "merged_video_url": "",
            "merged_video_path": "",
            "source_duration_seconds": 0.0,
            "segment_duration_seconds": 0,
        }
    return jid


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    jid = (job_id or "").strip().lower()
    if not jid or len(jid) != 32 or any(c not in "0123456789abcdef" for c in jid):
        return None
    now = time.time()
    with JOBS_LOCK:
        _prune_stale_unlocked(now)
        job = _JOBS.get(jid)
        return dict(job) if job is not None else None


def update_job(job_id: str, **fields: Any) -> bool:
    jid = (job_id or "").strip().lower()
    with JOBS_LOCK:
        job = _JOBS.get(jid)
        if not job:
            return False
        job.update(fields)
        job["updated_at_ts"] = time.time()
        return True

