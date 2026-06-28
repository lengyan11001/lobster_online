from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def application_root_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def runtime_root() -> Path:
    root = application_root_dir() / "_lobster_runtime" / "ai_3d_model"
    root.mkdir(parents=True, exist_ok=True)
    return root


def uploads_root() -> Path:
    root = runtime_root() / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def jobs_root() -> Path:
    root = runtime_root() / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_job_id() -> str:
    return uuid.uuid4().hex


def job_dir(job_id: str) -> Path:
    safe = "".join(ch for ch in str(job_id or "") if ch.isalnum() or ch in {"-", "_"})[:80]
    if not safe:
        raise ValueError("empty job id")
    path = jobs_root() / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_manifest_path(job_id: str) -> Path:
    return job_dir(job_id) / "manifest.json"


def load_job(job_id: str) -> Optional[Dict[str, Any]]:
    path = job_manifest_path(job_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def list_jobs(*, limit: int = 20) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    root = jobs_root()
    for manifest in root.glob("*/manifest.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("job_id") and not is_internal_test_job(data):
            rows.append(data)
    rows.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return rows[: max(1, min(100, int(limit or 20)))]


def is_internal_test_job(job: Dict[str, Any]) -> bool:
    title = str(job.get("title") or "").strip().lower()
    description = str(job.get("description") or "").strip().lower()
    if not title:
        return False
    if set(title) == {"?"}:
        return True
    markers = (
        "recover-test",
        "preprocess api test",
        "component wording test",
        "恢复测试任务",
        "codex test",
        "test-character",
    )
    return any(marker in title or marker in description for marker in markers)


def save_job(job: Dict[str, Any]) -> Dict[str, Any]:
    job_id = str(job.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("job missing job_id")
    path = job_manifest_path(job_id)
    path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return job


def update_job(job_id: str, **patch: Any) -> Dict[str, Any]:
    job = load_job(job_id) or {"job_id": job_id, "created_at": now_iso()}
    job.update(patch)
    job["updated_at"] = now_iso()
    return save_job(job)


def public_job(job: Dict[str, Any]) -> Dict[str, Any]:
    keys = {
        "job_id",
        "status",
        "stage",
        "progress",
        "provider",
        "final_3d_provider",
        "image_stage_provider",
        "image_stage_models",
        "mode",
        "strategy",
        "quality",
        "title",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "error",
        "inputs",
        "target_formats",
        "outputs",
        "mesh_metrics",
        "provider_task_id",
        "consumed_credits",
        "quality_notes",
        "preprocessing",
        "view_generation_plan",
        "asset_template",
        "reference_strength",
        "image_model",
        "description",
        "steps",
        "subtasks",
    }
    return {k: v for k, v in job.items() if k in keys}
