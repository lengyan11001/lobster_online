"""Viral video remix async job store: in-memory status with disk snapshots."""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

JOBS_LOCK = threading.Lock()
_JOBS: Dict[str, Dict[str, Any]] = {}
_JOB_TTL_SEC = 86400 * 3


def _lobster_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _jobs_root() -> Path:
    root = _lobster_root() / "static" / "generated" / "viral_remix_jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _job_file_for(job: Dict[str, Any]) -> Optional[Path]:
    job_dir = str(job.get("job_dir") or "").strip()
    if not job_dir:
        return None
    try:
        path = Path(job_dir).resolve()
    except Exception:
        return None
    return path / "job.json"


def _job_file_for_id(job_id: str) -> Path:
    return _jobs_root() / job_id / "job.json"


def _snapshot_job_unlocked(job: Dict[str, Any]) -> None:
    job_file = _job_file_for(job)
    if job_file is None:
        return
    try:
        job_file.parent.mkdir(parents=True, exist_ok=True)
        job_file.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return


def _load_job_from_disk(job_id: str) -> Optional[Dict[str, Any]]:
    job_file = _job_file_for_id(job_id)
    if not job_file.is_file():
        return None
    try:
        data = json.loads(job_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if str(data.get("job_id") or "").strip().lower() != job_id:
        data["job_id"] = job_id
    return data


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
        _snapshot_job_unlocked(_JOBS[jid])
    return jid


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    jid = (job_id or "").strip().lower()
    if not jid or len(jid) != 32 or any(c not in "0123456789abcdef" for c in jid):
        return None
    now = time.time()
    with JOBS_LOCK:
        _prune_stale_unlocked(now)
        job = _JOBS.get(jid)
        if job is not None:
            return dict(job)
        disk_job = _load_job_from_disk(jid)
        if disk_job is not None:
            _JOBS[jid] = disk_job
            return dict(disk_job)
        return None


def update_job(job_id: str, **fields: Any) -> bool:
    jid = (job_id or "").strip().lower()
    with JOBS_LOCK:
        job = _JOBS.get(jid)
        if not job:
            return False
        job.update(fields)
        job["updated_at_ts"] = time.time()
        _snapshot_job_unlocked(job)
        return True
