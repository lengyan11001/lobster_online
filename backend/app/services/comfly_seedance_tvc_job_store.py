"""Seedance TVC pipeline async job store: in-memory status plus manifest polling."""
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


def create_job_record(
    *,
    user_id: int,
    inp: Dict[str, Any],
    auto_save: bool,
    job_output_dir: str,
    job_id: Optional[str] = None,
    auth_header: str = "",
    installation_id: str = "",
    meta: Optional[Dict[str, Any]] = None,
) -> str:
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
            "auth_header": (auth_header or "").strip(),
            "installation_id": (installation_id or "").strip(),
            "meta": meta if isinstance(meta, dict) else {},
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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _step_status(meta: Dict[str, Any]) -> str:
    return str((meta or {}).get("status") or "").strip().lower()


def _payload_progress(meta: Dict[str, Any]) -> Optional[int]:
    if not isinstance(meta, dict):
        return None
    payload = meta.get("payload") if isinstance(meta.get("payload"), dict) else meta
    for key in ("progress", "progress_percent", "percent", "percentage"):
        value = payload.get(key)
        if isinstance(value, (int, float)):
            num = float(value)
            if 0 <= num <= 1:
                num *= 100
            if 0 <= num <= 100:
                return int(round(num))
        if isinstance(value, str):
            raw = value.strip().rstrip("%")
            try:
                num = float(raw)
            except ValueError:
                continue
            if 0 <= num <= 1:
                num *= 100
            if 0 <= num <= 100:
                return int(round(num))
    return None


def _progress_label(name: str) -> str:
    key = str(name or "").strip().lower()
    for prefix in ("segment_",):
        if key.startswith(prefix):
            parts = key.split("_", 2)
            if len(parts) == 3:
                idx = _safe_int(parts[1], 0)
                stage = parts[2]
                base = _progress_label(stage)
                return f"第 {idx} 段{base}" if idx else base
    labels = {
        "reference_upload": "上传参考图",
        "storyboard_plan": "规划分镜",
        "plan": "规划分镜",
        "direct_video_plan": "准备视频任务",
        "board_image": "生成分镜图",
        "segment_reference_image": "生成视频参考图",
        "submit_primary": "提交视频任务",
        "submit_fallback": "提交备用通道",
        "video_fallback": "切换备用通道",
        "poll": "等待视频生成",
        "video_poll": "等待视频生成",
        "download_video": "下载视频",
        "merge_clips": "合并视频",
        "90_merge_clips": "合并视频",
        "final": "整理成片",
    }
    if key.startswith("reference_upload_"):
        return "上传参考图"
    return labels.get(key, str(name or "处理中").replace("_", " "))


def _estimate_manifest_progress(data: Dict[str, Any], items: List[tuple[str, Dict[str, Any]]]) -> Dict[str, Any]:
    manifest_status = str(data.get("status") or "").strip().lower()
    if manifest_status in {"completed", "success", "succeeded", "done"}:
        return {"percent": 100, "label": "视频已完成", "detail": "任务已完成"}
    if manifest_status in {"failed", "error", "partial_failure"}:
        return {"percent": 100, "label": "视频生成失败", "detail": "任务已失败"}

    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    segment_count = max(1, _safe_int(config.get("segment_count"), 1))
    segments = data.get("segments") if isinstance(data.get("segments"), dict) else {}
    steps = data.get("steps") if isinstance(data.get("steps"), dict) else {}
    workflow_mode = str(config.get("workflow_mode") or "").strip().lower()

    if not items:
        return {"percent": 2, "label": "任务排队中", "detail": "等待开始处理"}

    latest_name, latest_meta = items[-1]
    latest_status = _step_status(latest_meta)
    label = _progress_label(latest_name)

    if workflow_mode in {"direct", "direct_video", "image_to_video", "i2v"}:
        stage_weights = {
            "direct_video_plan": 10,
            "submit_primary": 22,
            "submit_fallback": 24,
            "poll": 92,
            "90_merge_clips": 98,
        }
    else:
        stage_weights = {
            "reference_upload": 8,
            "storyboard_plan": 18,
            "plan": 18,
            "board_image": 34,
            "segment_reference_image": 48,
            "submit_primary": 58,
            "submit_fallback": 60,
            "poll": 92,
            "90_merge_clips": 98,
        }

    percent = 5
    for name, meta in items:
        status = _step_status(meta)
        key = name.split("_", 2)[2] if name.startswith("segment_") and len(name.split("_", 2)) == 3 else name
        key = key.lower()
        if key.startswith("reference_upload_"):
            key = "reference_upload"
        stage_target = stage_weights.get(key)
        if stage_target is None:
            continue
        if status in {"success", "completed", "complete", "done"}:
            percent = max(percent, stage_target)
        elif status in {"running", "pending", "queued", "ready"}:
            running_percent = max(1, stage_target - 8)
            payload_pct = _payload_progress(meta)
            if payload_pct is not None and key == "poll":
                segment_keys = [k for k in segments.keys()]
                completed_polls = 0
                for stages in segments.values():
                    if isinstance(stages, dict) and _step_status(stages.get("poll") or {}) in {"success", "completed", "complete", "done"}:
                        completed_polls += 1
                current_share = ((completed_polls + (payload_pct / 100.0)) / max(1, segment_count)) * 34
                running_percent = max(running_percent, int(round(58 + min(34, current_share))))
                if not segment_keys:
                    running_percent = max(running_percent, int(round(58 + payload_pct * 0.34)))
            percent = max(percent, min(stage_target - 1, running_percent))
        elif status in {"failed", "error"}:
            percent = max(percent, min(99, stage_target))

    if "90_merge_clips" in steps and isinstance(steps.get("90_merge_clips"), dict):
        merge_status = _step_status(steps["90_merge_clips"])
        if merge_status == "running":
            percent = max(percent, 96)
        elif merge_status in {"success", "completed", "complete", "done"}:
            percent = max(percent, 99)

    percent = max(1, min(99, int(percent)))
    detail = label
    if latest_status:
        detail = f"{label} · {latest_status}"
    return {"percent": percent, "label": label, "detail": detail}


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
    shots = data.get("shots") if isinstance(data.get("shots"), dict) else {}
    segments = data.get("segments") if isinstance(data.get("segments"), dict) else {}
    items: List[tuple[str, Dict[str, Any]]] = []
    for name, meta in steps.items():
        if isinstance(meta, dict):
            items.append((name, meta))
    for seg_idx, stages in segments.items():
        if not isinstance(stages, dict):
            continue
        for stage_name, meta in stages.items():
            if isinstance(meta, dict):
                items.append((f"segment_{seg_idx}_{stage_name}", meta))
    items = sorted(items, key=lambda kv: str((kv[1] or {}).get("updated_at") or ""))
    last_steps: List[Dict[str, Any]] = []
    for name, meta in items[-16:]:
        if isinstance(meta, dict):
            row = {
                "name": name,
                "status": meta.get("status"),
                "attempts": meta.get("attempts"),
                "error": meta.get("error"),
                "updated_at": meta.get("updated_at"),
            }
            for key in ("progress", "message", "task_id", "upstream_status"):
                if meta.get(key) is not None:
                    row[key] = meta.get(key)
            last_steps.append(row)
    estimate = _estimate_manifest_progress(data, items)
    return {
        "manifest_file": str(latest),
        "manifest_status": data.get("status"),
        "run_dir": data.get("run_dir"),
        "progress_percent": estimate["percent"],
        "progress_label": estimate["label"],
        "progress_detail": estimate["detail"],
        "step_count": len(steps),
        "shot_indexes": sorted(shots.keys(), key=lambda x: int(x) if str(x).isdigit() else 0),
        "segment_indexes": sorted(segments.keys(), key=lambda x: int(x) if str(x).isdigit() else 0),
        "last_steps": last_steps,
        "errors": (data.get("errors") or [])[-5:] if isinstance(data.get("errors"), list) else [],
    }

