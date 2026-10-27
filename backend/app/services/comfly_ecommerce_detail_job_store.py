"""电商详情图流水线异步任务：内存态 + manifest 轮询。"""
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


def _prune_stale_unlocked(now: float) -> None:
    dead: List[str] = []
    for jid, job in _JOBS.items():
        if now - float(job.get("created_at_ts") or 0) > _JOB_TTL_SEC:
            dead.append(jid)
    for jid in dead:
        _JOBS.pop(jid, None)


def create_job_record(*, user_id: int, inp: Dict[str, Any], auto_save: bool, job_output_dir: str, job_id: Optional[str] = None) -> str:
    jid = (job_id or "").strip().lower()
    if not jid or len(jid) != 32 or any(c not in "0123456789abcdef" for c in jid):
        jid = uuid.uuid4().hex
    now = time.time()
    with JOBS_LOCK:
        _prune_stale_unlocked(now)
        _JOBS[jid] = {
            "job_id": jid,
            "user_id": user_id,
            "status": "running",
            "created_at_ts": now,
            "updated_at_ts": now,
            "inp": inp,
            "auto_save": bool(auto_save),
            "job_output_dir": job_output_dir,
            "error": None,
            "result": None,
            "saved_assets": [],
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


def read_manifest_progress(job_output_dir: str) -> Optional[Dict[str, Any]]:
    base = Path((job_output_dir or "").strip())
    if not base.is_dir():
        return None
    candidates = list(base.glob("run_*/manifest.json"))
    if not candidates:
        return None
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
    except Exception:
        return None
    steps = data.get("steps") if isinstance(data.get("steps"), dict) else {}
    pages = data.get("pages") if isinstance(data.get("pages"), dict) else {}
    items = sorted(steps.items(), key=lambda kv: str((kv[1] or {}).get("updated_at") or ""))
    last_steps: List[Dict[str, Any]] = []
    for name, meta in items[-12:]:
        if isinstance(meta, dict):
            last_steps.append({"name": name, "status": meta.get("status"), "attempts": meta.get("attempts"), "error": meta.get("error"), "updated_at": meta.get("updated_at")})
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    summary = usage.get("summary") if isinstance(usage.get("summary"), dict) else {}
    return {
        "manifest_file": str(latest),
        "manifest_status": data.get("status"),
        "run_dir": data.get("run_dir"),
        "step_count": len(steps),
        "page_indexes": sorted(pages.keys(), key=lambda x: int(x) if str(x).isdigit() else 0),
        "last_steps": last_steps,
        "usage_summary": summary,
        "errors": (data.get("errors") or [])[-5:] if isinstance(data.get("errors"), list) else [],
    }


def read_manifest_partial_result(job_output_dir: str) -> Optional[Dict[str, Any]]:
    """Read page artifacts that are already available while a pipeline is running."""
    base = Path((job_output_dir or "").strip())
    if not base.is_dir():
        return None
    candidates = list(base.glob("run_*/manifest.json"))
    if not candidates:
        return None
    manifest_path = max(candidates, key=lambda p: p.stat().st_mtime)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    run_dir = Path(str(manifest.get("run_dir") or manifest_path.parent))
    pages = manifest.get("pages") if isinstance(manifest.get("pages"), dict) else {}
    page_results: List[Dict[str, Any]] = []
    failed_pages: List[Dict[str, Any]] = []

    for raw_index, stages in pages.items():
        if not isinstance(stages, dict):
            continue
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            continue
        successful = [
            (str(stage), meta)
            for stage, meta in stages.items()
            if isinstance(meta, dict) and str(meta.get("status") or "") == "success"
        ]
        successful.sort(key=lambda item: str(item[1].get("updated_at") or ""))
        payload = None
        for stage, _meta in reversed(successful):
            payload_path = run_dir / f"page_{index:02d}_{stage}.json"
            try:
                candidate = json.loads(payload_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(candidate, dict):
                payload = candidate
                break
        if payload is not None:
            page_results.append(payload)

        latest_stage = max(
            ((str(stage), meta) for stage, meta in stages.items() if isinstance(meta, dict)),
            key=lambda item: str(item[1].get("updated_at") or ""),
            default=None,
        )
        if latest_stage and str(latest_stage[1].get("status") or "") == "failed":
            failed_pages.append(
                {
                    "index": index,
                    "error": str(latest_stage[1].get("error") or ""),
                }
            )

    if not page_results and not failed_pages:
        return None
    page_results.sort(key=lambda item: int(item.get("index") or 0))
    failed_pages.sort(key=lambda item: int(item.get("index") or 0))
    return {
        "run_dir": str(run_dir),
        "page_results": page_results,
        "failed_pages": failed_pages,
        "config": manifest.get("config") if isinstance(manifest.get("config"), dict) else {},
        "partial": True,
    }
