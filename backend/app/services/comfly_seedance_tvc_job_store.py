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
_JOB_STORE_FILE = Path(__file__).resolve().parents[3] / "_lobster_runtime" / "seedance_tvc_job_store.json"


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _load_store_from_disk() -> Dict[str, Dict[str, Any]]:
    try:
        if not _JOB_STORE_FILE.is_file():
            return {}
        raw = json.loads(_JOB_STORE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for jid, row in raw.items():
        if isinstance(row, dict):
            out[str(jid).strip().lower()] = row
    return out


def _save_store_to_disk_unlocked() -> None:
    _JOB_STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {jid: _json_safe(job) for jid, job in _JOBS.items()}
    tmp = _JOB_STORE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_JOB_STORE_FILE)


def _ensure_loaded_unlocked() -> None:
    if _JOBS:
        return
    disk_rows = _load_store_from_disk()
    if disk_rows:
        _JOBS.update(disk_rows)


def _prune_stale_unlocked(now: float) -> None:
    dead: List[str] = []
    for jid, job in _JOBS.items():
        if now - float(job.get("created_at_ts") or 0) > _JOB_TTL_SEC:
            dead.append(jid)
    for jid in dead:
        _JOBS.pop(jid, None)


def _persist_jobs_unlocked(now: Optional[float] = None) -> None:
    _ensure_loaded_unlocked()
    _prune_stale_unlocked(now if now is not None else time.time())
    _save_store_to_disk_unlocked()


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
        _ensure_loaded_unlocked()
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
        _save_store_to_disk_unlocked()
    return jid


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    jid = (job_id or "").strip().lower()
    if not jid or len(jid) != 32 or any(c not in "0123456789abcdef" for c in jid):
        return None
    now = time.time()
    with JOBS_LOCK:
        _ensure_loaded_unlocked()
        _prune_stale_unlocked(now)
        job = _JOBS.get(jid)
        return dict(job) if job is not None else None


def update_job(job_id: str, **fields: Any) -> bool:
    jid = (job_id or "").strip().lower()
    with JOBS_LOCK:
        _ensure_loaded_unlocked()
        job = _JOBS.get(jid)
        if not job:
            return False
        job.update(fields)
        job["updated_at_ts"] = time.time()
        _save_store_to_disk_unlocked()
        return True


def list_jobs_for_user(user_id: int, *, limit: int = 60) -> List[Dict[str, Any]]:
    now = time.time()
    with JOBS_LOCK:
        _ensure_loaded_unlocked()
        _prune_stale_unlocked(now)
        rows = [
            dict(job)
            for job in _JOBS.values()
            if int(job.get("user_id") or -1) == int(user_id)
        ]
        rows.sort(key=lambda item: float(item.get("updated_at_ts") or item.get("created_at_ts") or 0), reverse=True)
        rows = rows[: max(1, int(limit or 12))]
        _save_store_to_disk_unlocked()
        return rows


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


def _latest_manifest(job_output_dir: str) -> Optional[Path]:
    base = Path((job_output_dir or "").strip())
    if not base.is_dir():
        return None
    candidates = list(base.glob("run_*/manifest.json"))
    if not candidates:
        return None
    try:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    except Exception:
        return None


def _read_json_file(path: Path) -> Dict[str, Any]:
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_url(value: Any) -> str:
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith(("http://", "https://", "/api/")):
            return raw
        return ""
    if isinstance(value, dict):
        keys = (
            "url",
            "preview_url",
            "local_preview_url",
            "source_url",
            "image_url",
            "video_url",
            "mp4url",
            "output",
            "download_url",
        )
        for key in keys:
            found = _first_url(value.get(key))
            if found:
                return found
        for key in ("data", "result", "content", "payload", "raw", "video", "image", "final_video"):
            found = _first_url(value.get(key))
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _first_url(item)
            if found:
                return found
    return ""


def _looks_like_video_url(url: str) -> bool:
    path = str(url or "").split("?", 1)[0].lower()
    return path.endswith((".mp4", ".mov", ".m4v", ".webm", ".mkv"))


def _looks_like_image_url(url: str) -> bool:
    path = str(url or "").split("?", 1)[0].lower()
    return path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"))


def _merge_segment_row(row: Dict[str, Any], payload: Dict[str, Any], *, stage: str = "") -> None:
    if not isinstance(payload, dict):
        return
    status = _first_text(payload.get("status"), payload.get("state"), payload.get("upstream_status"))
    if status:
        normalized = status.lower()
        if normalized in {"success", "succeeded", "completed", "complete", "done"}:
            if stage == "image":
                row["image_status"] = "ready"
            elif stage == "video":
                row["video_status"] = "ready"
            else:
                row["status"] = "ready"
        elif normalized in {"failed", "error"}:
            row["status"] = "failed"
        elif row.get("status") not in {"failed", "video_ready"}:
            row["status"] = "running"
    err = _first_text(payload.get("error"), payload.get("message"), payload.get("detail"))
    if err and not row.get("error"):
        row["error"] = err[:800]
    prompt = _first_text(
        payload.get("prompt"),
        payload.get("image_prompt"),
        payload.get("video_prompt"),
        payload.get("submitted_video_prompt"),
        payload.get("submitted_video_prompt_en"),
        payload.get("first_frame_prompt"),
        payload.get("first_frame_prompt_en"),
        payload.get("segment_reference_prompt"),
        payload.get("segment_reference_prompt_en"),
        payload.get("task_text"),
        payload.get("text"),
    )
    if prompt:
        if stage == "image" and not row.get("image_prompt"):
            row["image_prompt"] = prompt
        elif stage == "video" and not row.get("video_prompt"):
            row["video_prompt"] = prompt
        elif not row.get("prompt"):
            row["prompt"] = prompt
    provider = _first_text(payload.get("provider"), payload.get("channel"), payload.get("vendor"))
    model = _first_text(payload.get("model"), payload.get("video_model"), payload.get("image_model"))
    task_id = _first_text(payload.get("task_id"), payload.get("id"), payload.get("request_id"), payload.get("upstream_task_id"))
    if provider and not row.get("provider"):
        row["provider"] = provider
    if model and not row.get("model"):
        row["model"] = model
    if task_id and not row.get("task_id"):
        row["task_id"] = task_id
    progress = payload.get("progress")
    if progress is None:
        progress = payload.get("progress_percent")
    if progress is not None:
        row["progress"] = progress
    explicit_image_url = _first_url(
        payload.get("first_frame_image_url")
        or payload.get("segment_reference_image_url")
        or payload.get("storyboard_board_image_url")
        or payload.get("image_url")
    )
    if explicit_image_url and not row.get("image_url"):
        row["image_url"] = explicit_image_url
    explicit_video_url = _first_url(payload.get("mp4url") or payload.get("video_url"))
    if explicit_video_url and not row.get("video_url"):
        row["video_url"] = explicit_video_url
    url = _first_url(payload)
    if not url:
        url = explicit_video_url or explicit_image_url
    if url:
        if stage == "image" or (_looks_like_image_url(url) and not _looks_like_video_url(url)):
            row["image_url"] = row.get("image_url") or url
        elif stage == "video" or _looks_like_video_url(url):
            row["video_url"] = row.get("video_url") or url


def _segment_index_from_name(name: str) -> Optional[int]:
    match = None
    try:
        import re

        match = re.search(r"segment[_-]?(\d+)", name or "", re.IGNORECASE)
    except Exception:
        match = None
    if not match:
        return None
    try:
        return max(1, int(match.group(1)))
    except Exception:
        return None


def _segment_stage_from_name(name: str) -> str:
    raw = str(name or "").lower()
    if "image" in raw or "reference" in raw or "board" in raw:
        return "image"
    if "video" in raw or "poll" in raw or "final" in raw:
        return "video"
    return ""


def read_manifest_artifacts(job_output_dir: str) -> Optional[Dict[str, Any]]:
    latest = _latest_manifest(job_output_dir)
    if latest is None:
        return None
    manifest = _read_json_file(latest)
    if not manifest:
        return None
    run_dir = latest.parent
    config = manifest.get("config") if isinstance(manifest.get("config"), dict) else {}
    segment_count = max(1, _safe_int(config.get("segment_count"), 1))
    segment_seconds = max(1, _safe_int(config.get("segment_duration_seconds") or config.get("segment_seconds"), 10))

    rows: Dict[int, Dict[str, Any]] = {}
    for idx in range(1, segment_count + 1):
        rows[idx] = {
            "index": idx,
            "start": (idx - 1) * segment_seconds,
            "end": idx * segment_seconds,
            "status": "pending",
            "image_status": "pending",
            "video_status": "pending",
            "image_prompt": "",
            "video_prompt": "",
            "prompt": "",
            "image_url": "",
            "video_url": "",
            "provider": "",
            "model": "",
            "task_id": "",
            "error": "",
            "progress": None,
        }

    reference_urls: List[str] = []
    steps = manifest.get("steps") if isinstance(manifest.get("steps"), dict) else {}
    for name, meta in steps.items():
        if not str(name or "").startswith("01_reference_upload") or not isinstance(meta, dict):
            continue
        payload = meta.get("payload") if isinstance(meta.get("payload"), dict) else meta
        ref_url = _first_url(payload.get("reference_image_url") or payload.get("url"))
        if ref_url and ref_url not in reference_urls:
            reference_urls.append(ref_url)

    shots = manifest.get("shots") if isinstance(manifest.get("shots"), dict) else {}
    for raw_idx, shot in shots.items():
        idx = _safe_int(raw_idx, 0)
        if idx <= 0:
            continue
        row = rows.setdefault(idx, {
            "index": idx,
            "start": (idx - 1) * segment_seconds,
            "end": idx * segment_seconds,
            "status": "pending",
            "image_status": "pending",
            "video_status": "pending",
            "image_prompt": "",
            "video_prompt": "",
            "prompt": "",
            "image_url": "",
            "video_url": "",
            "provider": "",
            "model": "",
            "task_id": "",
            "error": "",
            "progress": None,
        })
        if isinstance(shot, dict):
            row["image_prompt"] = row.get("image_prompt") or _first_text(shot.get("image_prompt"), shot.get("prompt"), shot.get("visual_prompt"))
            row["video_prompt"] = row.get("video_prompt") or _first_text(shot.get("video_prompt"), shot.get("motion_prompt"), shot.get("prompt"))
            _merge_segment_row(row, shot)

    segments = manifest.get("segments") if isinstance(manifest.get("segments"), dict) else {}
    for raw_idx, stages in segments.items():
        idx = _safe_int(raw_idx, 0)
        if idx <= 0 or not isinstance(stages, dict):
            continue
        row = rows.setdefault(idx, {
            "index": idx,
            "start": (idx - 1) * segment_seconds,
            "end": idx * segment_seconds,
            "status": "pending",
            "image_status": "pending",
            "video_status": "pending",
            "image_prompt": "",
            "video_prompt": "",
            "prompt": "",
            "image_url": "",
            "video_url": "",
            "provider": "",
            "model": "",
            "task_id": "",
            "error": "",
            "progress": None,
        })
        for stage_name, meta in stages.items():
            if isinstance(meta, dict):
                _merge_segment_row(row, meta, stage=_segment_stage_from_name(str(stage_name)))

    for path in sorted(run_dir.glob("segment_*_*.json")):
        idx = _segment_index_from_name(path.name)
        if not idx:
            continue
        row = rows.setdefault(idx, {
            "index": idx,
            "start": (idx - 1) * segment_seconds,
            "end": idx * segment_seconds,
            "status": "pending",
            "image_status": "pending",
            "video_status": "pending",
            "image_prompt": "",
            "video_prompt": "",
            "prompt": "",
            "image_url": "",
            "video_url": "",
            "provider": "",
            "model": "",
            "task_id": "",
            "error": "",
            "progress": None,
        })
        _merge_segment_row(row, _read_json_file(path), stage=_segment_stage_from_name(path.name))

    final_payload = _read_json_file(run_dir / "99_result.json")
    completed = final_payload.get("completed_segments")
    if not isinstance(completed, list):
        completed = final_payload.get("completed_shots")
    if isinstance(completed, list):
        for pos, item in enumerate(completed, start=1):
            if not isinstance(item, dict):
                continue
            idx = _safe_int(item.get("index") or item.get("segment_index") or item.get("shot_index"), pos)
            row = rows.get(idx)
            if row:
                _merge_segment_row(row, item, stage="video")

    out_segments: List[Dict[str, Any]] = []
    for idx in sorted(rows):
        row = rows[idx]
        if reference_urls and not row.get("image_url"):
            row["image_url"] = reference_urls[(idx - 1) % len(reference_urls)]
        if row.get("video_url"):
            row["status"] = "video_ready"
            row["video_status"] = "ready"
        elif row.get("image_url"):
            row["status"] = "image_ready" if row.get("status") not in {"failed"} else row.get("status")
            row["image_status"] = "ready"
        elif row.get("error"):
            row["status"] = "failed"
        elif row.get("progress") is not None or row.get("provider") or row.get("task_id"):
            row["status"] = "running"
        out_segments.append(_json_safe(row))

    video_ready_count = sum(1 for item in out_segments if item.get("video_url"))
    image_ready_count = sum(1 for item in out_segments if item.get("image_url"))
    failed_count = sum(1 for item in out_segments if item.get("status") == "failed")
    return {
        "manifest_file": str(latest),
        "run_dir": str(run_dir),
        "segment_count": len(out_segments),
        "ready_count": video_ready_count,
        "video_ready_count": video_ready_count,
        "image_ready_count": image_ready_count,
        "failed_count": failed_count,
        "segment_seconds": segment_seconds,
        "segments": out_segments,
    }

