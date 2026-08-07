from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from ..core.config import get_settings
from .auth import _ServerUser, get_current_user_for_local


logger = logging.getLogger(__name__)
router = APIRouter()

ROOT = Path(__file__).resolve().parents[3]
JOBS_ROOT = ROOT / "data" / "audio_transcription_uploads"
MAX_AUDIO_BYTES = 200 * 1024 * 1024
MAX_OPEN_JOBS_PER_USER = 10
JOB_RETENTION_SECONDS = 7 * 24 * 60 * 60
UPLOAD_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".amr", ".wma", ".opus", ".webm"}

_UPLOAD_GATE = asyncio.Semaphore(2)
_ACTIVE_TASKS: dict[str, asyncio.Task[Any]] = {}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _job_dir(user_id: int, job_id: str) -> Path:
    return JOBS_ROOT / str(int(user_id)) / job_id


def _job_file(user_id: int, job_id: str) -> Path:
    return _job_dir(user_id, job_id) / "job.json"


def _read_job(user_id: int, job_id: str) -> dict[str, Any]:
    path = _job_file(user_id, job_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="本机上传任务不存在")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="本机上传任务记录损坏") from exc
    if not isinstance(value, dict) or int(value.get("user_id") or 0) != int(user_id):
        raise HTTPException(status_code=404, detail="本机上传任务不存在")
    return value


def _write_job(job: dict[str, Any]) -> None:
    path = _job_file(int(job["user_id"]), str(job["job_id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    job["updated_at"] = _now()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": str(job.get("job_id") or ""),
        "file_name": str(job.get("file_name") or ""),
        "file_size": int(job.get("file_size") or 0),
        "status": str(job.get("status") or "queued"),
        "stage": str(job.get("stage") or "queued"),
        "attempt": int(job.get("attempt") or 0),
        "error": str(job.get("error") or ""),
        "record": job.get("record") if isinstance(job.get("record"), dict) else None,
        "created_at": str(job.get("created_at") or ""),
        "updated_at": str(job.get("updated_at") or ""),
    }


def _auth_context(request: Request) -> tuple[str, str, str]:
    authorization = str(request.headers.get("Authorization") or "").strip()
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="登录状态已失效，请重新登录")
    installation_id = str(request.headers.get("X-Installation-Id") or "").strip()
    brand = str(request.headers.get("X-Lobster-Brand") or "bihuo").strip().lower() or "bihuo"
    return authorization, installation_id, brand


def _cloud_upload(job: dict[str, Any], authorization: str, installation_id: str, brand: str) -> dict[str, Any]:
    source = Path(str(job.get("local_path") or ""))
    if not source.is_file():
        raise RuntimeError("本机音频文件已不存在，请重新选择文件")
    base = str(get_settings().auth_server_base or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("Online 未配置服务器地址 AUTH_SERVER_BASE")
    headers = {"Authorization": authorization, "X-Lobster-Brand": brand}
    if installation_id:
        headers["X-Installation-Id"] = installation_id
    timeout = httpx.Timeout(connect=20.0, read=180.0, write=1800.0, pool=20.0)
    content_type = str(job.get("content_type") or "").strip() or mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    with source.open("rb") as stream, httpx.Client(timeout=timeout, trust_env=False, follow_redirects=True) as client:
        response = client.post(
            f"{base}/api/h5/recorder/files",
            headers=headers,
            data={
                "source_type": "local",
                "source_name": "Online 本地音频",
                "installation_id": installation_id,
            },
            files={"file": (str(job.get("file_name") or source.name), stream, content_type)},
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400:
        detail = payload.get("detail") if isinstance(payload, dict) else ""
        raise RuntimeError(str(detail or f"服务器返回 HTTP {response.status_code}"))
    if not isinstance(payload, dict) or not isinstance(payload.get("record"), dict):
        raise RuntimeError("服务器未返回转写记录")
    return payload


async def _run_upload(job_id: str, user_id: int, authorization: str, installation_id: str, brand: str) -> None:
    try:
        async with _UPLOAD_GATE:
            job = _read_job(user_id, job_id)
            job.update({"status": "uploading", "stage": "cloud_upload", "error": "", "attempt": int(job.get("attempt") or 0) + 1})
            await asyncio.to_thread(_write_job, job)
            last_error = ""
            for attempt in range(1, 4):
                try:
                    payload = await asyncio.to_thread(_cloud_upload, job, authorization, installation_id, brand)
                    job = _read_job(user_id, job_id)
                    job.update({"status": "completed", "stage": "transcribing", "error": "", "record": payload["record"]})
                    await asyncio.to_thread(_write_job, job)
                    source = Path(str(job.get("local_path") or ""))
                    await asyncio.to_thread(source.unlink, missing_ok=True)
                    return
                except Exception as exc:
                    last_error = str(exc)[:1000]
                    logger.warning("audio transcription cloud upload failed job=%s attempt=%s error=%s", job_id, attempt, last_error)
                    if attempt < 3:
                        await asyncio.sleep(2 if attempt == 1 else 5)
            job = _read_job(user_id, job_id)
            job.update({"status": "failed", "stage": "cloud_upload", "error": last_error or "后台上传失败"})
            await asyncio.to_thread(_write_job, job)
    except Exception as exc:
        logger.exception("audio transcription local job failed job=%s", job_id)
        try:
            job = _read_job(user_id, job_id)
            job.update({"status": "failed", "error": str(exc)[:1000]})
            await asyncio.to_thread(_write_job, job)
        except Exception:
            logger.exception("failed to persist audio transcription job failure job=%s", job_id)
    finally:
        _ACTIVE_TASKS.pop(job_id, None)


def _start_upload(job_id: str, user_id: int, authorization: str, installation_id: str, brand: str) -> None:
    existing = _ACTIVE_TASKS.get(job_id)
    if existing and not existing.done():
        return
    task = asyncio.create_task(_run_upload(job_id, user_id, authorization, installation_id, brand))
    task.set_name(f"audio-upload-{job_id[:8]}")
    _ACTIVE_TASKS[job_id] = task


def _cleanup_jobs(user_id: int) -> None:
    root = JOBS_ROOT / str(int(user_id))
    if not root.is_dir():
        return
    cutoff = time.time() - JOB_RETENTION_SECONDS
    for path in root.iterdir():
        try:
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


@router.post("/api/audio-transcription/local-uploads", status_code=202)
async def create_local_audio_upload(
    request: Request,
    file: UploadFile = File(...),
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    authorization, installation_id, brand = _auth_context(request)
    name = Path(file.filename or "audio.wav").name[:255]
    suffix = Path(name).suffix.lower()
    content_type = str(file.content_type or "").lower()
    if suffix not in UPLOAD_SUFFIXES and not content_type.startswith("audio/"):
        raise HTTPException(status_code=400, detail="请选择 MP3、WAV、M4A、AAC、OGG、FLAC、AMR、WMA、OPUS 或 WebM 音频")
    await asyncio.to_thread(_cleanup_jobs, current_user.id)
    user_root = JOBS_ROOT / str(int(current_user.id))
    open_jobs = 0
    if user_root.is_dir():
        for path in user_root.glob("*/job.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if str(job.get("status") or "") in {"receiving", "queued", "uploading"}:
                    open_jobs += 1
            except (OSError, ValueError):
                continue
    if open_jobs >= MAX_OPEN_JOBS_PER_USER:
        raise HTTPException(status_code=429, detail="本机待上传音频较多，请等待已有任务完成后再提交")

    job_id = uuid.uuid4().hex
    target_dir = _job_dir(current_user.id, job_id)
    target = target_dir / name
    await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)
    job: dict[str, Any] = {
        "job_id": job_id,
        "user_id": int(current_user.id),
        "file_name": name,
        "file_size": 0,
        "content_type": content_type,
        "local_path": str(target),
        "status": "receiving",
        "stage": "local_save",
        "attempt": 0,
        "error": "",
        "record": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    await asyncio.to_thread(_write_job, job)
    size = 0
    stream = await asyncio.to_thread(target.open, "wb")
    try:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_AUDIO_BYTES:
                raise HTTPException(status_code=413, detail="音频文件不能超过 200MB")
            await asyncio.to_thread(stream.write, chunk)
    except BaseException:
        await asyncio.to_thread(stream.close)
        await asyncio.to_thread(shutil.rmtree, target_dir, ignore_errors=True)
        raise
    else:
        await asyncio.to_thread(stream.close)
    if size <= 0:
        await asyncio.to_thread(shutil.rmtree, target_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="音频文件为空")
    job.update({"file_size": size, "status": "queued", "stage": "queued"})
    await asyncio.to_thread(_write_job, job)
    _start_upload(job_id, current_user.id, authorization, installation_id, brand)
    return {"ok": True, "job": _public_job(job)}


@router.get("/api/audio-transcription/local-uploads")
async def list_local_audio_uploads(current_user: _ServerUser = Depends(get_current_user_for_local)):
    await asyncio.to_thread(_cleanup_jobs, current_user.id)
    root = JOBS_ROOT / str(int(current_user.id))
    jobs: list[dict[str, Any]] = []
    if root.is_dir():
        for path in root.glob("*/job.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                job_id = str(job.get("job_id") or "")
                status = str(job.get("status") or "")
                if status in {"receiving", "queued", "uploading"} and job_id not in _ACTIVE_TASKS:
                    job.update({"status": "failed", "error": "Online 重启中断了后台上传，请点击重试"})
                    _write_job(job)
                jobs.append(_public_job(job))
            except (OSError, ValueError, KeyError, TypeError):
                continue
    jobs.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {"items": jobs[:50]}


@router.get("/api/audio-transcription/local-uploads/{job_id}")
def local_audio_upload_detail(job_id: str, current_user: _ServerUser = Depends(get_current_user_for_local)):
    return {"job": _public_job(_read_job(current_user.id, job_id))}


@router.post("/api/audio-transcription/local-uploads/{job_id}/retry", status_code=202)
async def retry_local_audio_upload(
    job_id: str,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    authorization, installation_id, brand = _auth_context(request)
    job = _read_job(current_user.id, job_id)
    if not Path(str(job.get("local_path") or "")).is_file():
        raise HTTPException(status_code=410, detail="本机音频文件已不存在，请重新选择文件")
    job.update({"status": "queued", "stage": "queued", "error": ""})
    _write_job(job)
    _start_upload(job_id, current_user.id, authorization, installation_id, brand)
    return {"ok": True, "job": _public_job(job)}


@router.delete("/api/audio-transcription/local-uploads/{job_id}")
async def delete_local_audio_upload(job_id: str, current_user: _ServerUser = Depends(get_current_user_for_local)):
    job = _read_job(current_user.id, job_id)
    task = _ACTIVE_TASKS.get(job_id)
    if task and not task.done():
        raise HTTPException(status_code=409, detail="音频正在后台上传，完成后会自动进入转写；上传失败后可重试或删除")
    await asyncio.to_thread(shutil.rmtree, _job_dir(current_user.id, str(job["job_id"])), ignore_errors=True)
    return {"ok": True, "job_id": job_id}
