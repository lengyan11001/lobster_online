"""带货整包流水线异步任务：内存态 + run 目录下 manifest.json 供轮询进度。"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

JOBS_LOCK = threading.Lock()
_JOBS: Dict[str, Dict[str, Any]] = {}

# 仅防止内存无限涨：访问 status 时顺带清理过期任务
_JOB_TTL_SEC = 86400 * 3


def _prune_stale_unlocked(now: float) -> None:
    dead: List[str] = []
    for jid, j in _JOBS.items():
        ts = float(j.get("created_at_ts") or 0)
        if now - ts > _JOB_TTL_SEC:
            dead.append(jid)
    for jid in dead:
        _JOBS.pop(jid, None)


def create_job_record(
    *,
    user_id: int,
    inp: Dict[str, Any],
    auto_save: bool,
    job_output_dir: str,
    job_id: Optional[str] = None,
) -> str:
    jid = (job_id or "").strip().lower()
    if not jid or len(jid) != 32 or any(c not in "0123456789abcdef" for c in jid):
        jid = uuid.uuid4().hex
    now = time.time()
    rec: Dict[str, Any] = {
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
    with JOBS_LOCK:
        _prune_stale_unlocked(now)
        _JOBS[jid] = rec
    return jid


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    jid = (job_id or "").strip().lower()
    if not jid or len(jid) != 32 or any(c not in "0123456789abcdef" for c in jid):
        return None
    now = time.time()
    with JOBS_LOCK:
        _prune_stale_unlocked(now)
        j = _JOBS.get(jid)
        if j is None:
            return None
        return dict(j)


def update_job(job_id: str, **fields: Any) -> bool:
    jid = (job_id or "").strip().lower()
    with JOBS_LOCK:
        j = _JOBS.get(jid)
        if not j:
            return False
        j.update(fields)
        j["updated_at_ts"] = time.time()
        return True


def read_manifest_progress(job_output_dir: str) -> Optional[Dict[str, Any]]:
    """读取该任务专属 output 目录下最新 run_*/manifest.json 的摘要（流水线运行中持续更新）。"""
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
    shots = data.get("shots") if isinstance(data.get("shots"), dict) else {}
    step_items = sorted(
        steps.items(),
        key=lambda kv: str((kv[1] or {}).get("updated_at") or ""),
    )
    last_steps: List[Dict[str, Any]] = []
    for name, meta in step_items[-12:]:
        if isinstance(meta, dict):
            last_steps.append(
                {
                    "name": name,
                    "status": meta.get("status"),
                    "attempts": meta.get("attempts"),
                    "error": meta.get("error"),
                    "updated_at": meta.get("updated_at"),
                }
            )
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    summary = usage.get("summary") if isinstance(usage.get("summary"), dict) else {}
    return {
        "manifest_file": str(latest),
        "manifest_status": data.get("status"),
        "run_dir": data.get("run_dir"),
        "step_count": len(steps),
        "shot_indexes": sorted(shots.keys(), key=lambda x: int(x) if str(x).isdigit() else 0),
        "last_steps": last_steps,
        "usage_summary": summary,
        "errors": (data.get("errors") or [])[-5:] if isinstance(data.get("errors"), list) else [],
    }
