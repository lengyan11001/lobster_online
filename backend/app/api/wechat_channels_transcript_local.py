from __future__ import annotations

import base64
import asyncio
import csv
import io
import json
import logging
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .assets import _upload_bytes_to_auth_server
from .auth import _ServerUser, get_current_user_for_local
from ..core.config import settings
from ..db import get_db
from ..models import WechatChannelsTranscriptAccount, WechatChannelsTranscriptJob, WechatChannelsTranscriptVideo
from ..services.asset_storage_paths import get_asset_export_dir
from ..services.media_edit_exec import find_ffmpeg

logger = logging.getLogger(__name__)
router = APIRouter()

_MAX_VIDEO_BYTES = 380 * 1024 * 1024
_DECRYPT_PREFIX_BYTES = 131072


class LocalTranscriptBody(BaseModel):
    video: Dict[str, Any] = Field(default_factory=dict)
    keystream_b64: str = Field(default="", max_length=256 * 1024)


class VideoCacheSaveBody(BaseModel):
    account_key: str = Field(min_length=1, max_length=256)
    account: Dict[str, Any] = Field(default_factory=dict)
    videos: list[Dict[str, Any]] = Field(default_factory=list, max_length=300)
    selected_keys: list[str] = Field(default_factory=list, max_length=300)


class VideoCacheSelectionBody(BaseModel):
    account_key: str = Field(min_length=1, max_length=256)
    selected_keys: list[str] = Field(default_factory=list, max_length=300)


class TranscriptJobsSaveBody(BaseModel):
    jobs: list[Dict[str, Any]] = Field(default_factory=list, max_length=50)


class TranscriptExportBody(BaseModel):
    filename: str = Field(default="wechat-channels-transcripts", max_length=160)
    format: str = Field(default="txt", max_length=16)
    text: str = Field(default="", max_length=5_000_000)
    rows: list[Dict[str, Any]] = Field(default_factory=list, max_length=500)


def _safe_error(value: Any, limit: int = 1200) -> str:
    if isinstance(value, dict):
        for key in ("message", "detail", "error", "code"):
            if value.get(key):
                return _safe_error(value.get(key), limit)
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value or "").strip()
    return text[-limit:] if len(text) > limit else text


def _server_base() -> str:
    base = (settings.auth_server_base or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("AUTH_SERVER_BASE is not configured")
    if base.lower().startswith("http://") and not re.search(
        r"^http://(127\.0\.0\.1|localhost|\[?::1\]?)(:\d+)?$",
        base,
        re.IGNORECASE,
    ):
        base = "https://" + base[7:]
    return base


def _auth_headers(request: Request) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    auth = (request.headers.get("Authorization") or "").strip()
    if auth:
        headers["Authorization"] = auth
    installation_id = (
        request.headers.get("X-Installation-Id")
        or request.headers.get("x-installation-id")
        or ""
    ).strip()
    if installation_id:
        headers["X-Installation-Id"] = installation_id
    return headers


def _clean_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text.startswith(("http://", "https://")):
        return ""
    return text


def _video_title_text(value: Any, fallback: str = "") -> str:
    if isinstance(value, list):
        for item in value:
            text = _video_title_text(item, "")
            if text:
                return text
        return fallback
    if isinstance(value, dict):
        return _video_title_text(
            value.get("shortTitle")
            or value.get("title")
            or value.get("desc")
            or value.get("description")
            or value.get("content")
            or value.get("text"),
            fallback,
        )
    text = str(value or "").strip()
    if not text:
        return fallback
    match = re.search(r"['\"]shortTitle['\"]\s*:\s*['\"]([^'\"]+)", text)
    if match:
        return match.group(1).strip() or fallback
    return text


def _safe_export_filename(name: str, suffix: str) -> str:
    stem = Path(str(name or "")).stem.strip() or "wechat-channels-transcripts"
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .")
    if not stem:
        stem = "wechat-channels-transcripts"
    return f"{stem}.{suffix.lstrip('.')}"


def _unique_export_path(directory: Path, filename: str) -> Path:
    target = directory / filename
    if not target.exists():
        return target
    stem = target.stem or "wechat-channels-transcripts"
    suffix = target.suffix
    for idx in range(1, 1000):
        candidate = directory / f"{stem} ({idx}){suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}-{int(datetime.utcnow().timestamp())}{suffix}"


def _first(obj: Any, paths: list[str]) -> Any:
    for path in paths:
        cur = obj
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            elif isinstance(cur, list):
                try:
                    cur = cur[int(part)]
                except Exception:
                    ok = False
                    break
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return cur
    return ""


def _append_url_token(url: str, token: Any) -> str:
    cleaned = _clean_url(url)
    token_text = str(token or "").strip()
    if not cleaned or not token_text or "token=" in cleaned:
        return cleaned
    if token_text.startswith("&"):
        suffix = token_text if "?" in cleaned else "?" + token_text.lstrip("&")
    elif token_text.startswith("?"):
        suffix = token_text if "?" not in cleaned else "&" + token_text.lstrip("?")
    elif token_text.startswith("token="):
        suffix = ("&" if "?" in cleaned else "?") + token_text
    else:
        return cleaned
    return _clean_url(cleaned + suffix)


def _video_url(video: Dict[str, Any]) -> str:
    url = _clean_url(video.get("video_url")) or _clean_url(video.get("public_url"))
    if url and ("finder.video.qq.com" not in url or "token=" in url):
        return url
    raw = video.get("raw") if isinstance(video.get("raw"), dict) else {}
    token_pairs = [
        ("object_desc.media.0.url", "object_desc.media.0.url_token"),
        ("objectDesc.media.0.url", "objectDesc.media.0.url_token"),
        ("objectDesc.mediaList.0.url", "objectDesc.mediaList.0.url_token"),
        ("media.0.url", "media.0.url_token"),
        ("mediaList.0.url", "mediaList.0.url_token"),
    ]
    for url_path, token_path in token_pairs:
        candidate = _append_url_token(str(_first(raw, [url_path]) or ""), _first(raw, [token_path]))
        if candidate:
            return candidate
    return url


def _decode_key(video: Dict[str, Any]) -> str:
    raw = video.get("raw") if isinstance(video.get("raw"), dict) else {}
    value = video.get("decode_key") or _first(
        raw,
        [
            "decode_key",
            "decodeKey",
            "object_desc.media.0.decode_key",
            "objectDesc.media.0.decode_key",
            "objectDesc.mediaList.0.decode_key",
            "media.0.decode_key",
            "mediaList.0.decode_key",
        ],
    )
    return str(value or "").strip()


def _download_video(url: str, target: Path) -> None:
    with httpx.Client(timeout=300.0, follow_redirects=True, trust_env=False) as client:
        with client.stream("GET", url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
            if resp.status_code >= 400:
                retmsg = resp.headers.get("x-retmsg") or resp.headers.get("X-retmsg") or ""
                errno = resp.headers.get("x-errno") or resp.headers.get("X-Errno") or resp.headers.get("x-videoerrno") or ""
                detail = f": {retmsg}" if retmsg else ""
                if errno:
                    detail = f"{detail} ({errno})"
                raise RuntimeError(f"video download failed HTTP {resp.status_code}{detail}")
            total = 0
            with target.open("wb") as fh:
                for chunk in resp.iter_bytes(1024 * 512):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > _MAX_VIDEO_BYTES:
                        raise RuntimeError("video file is too large")
                    fh.write(chunk)
    if not target.exists() or target.stat().st_size <= 0:
        raise RuntimeError("downloaded video is empty")


def _decrypt_video_prefix(path: Path, keystream: bytes) -> None:
    if len(keystream) < 4096:
        raise RuntimeError("wechat video decrypt keystream is empty")
    with path.open("r+b") as fh:
        chunk = fh.read(min(_DECRYPT_PREFIX_BYTES, len(keystream)))
        if not chunk:
            raise RuntimeError("downloaded video is empty")
        fh.seek(0)
        fh.write(bytes(a ^ b for a, b in zip(chunk, keystream)))
    with path.open("rb") as fh:
        header = fh.read(16)
    if len(header) < 12 or b"ftyp" not in header[:12]:
        raise RuntimeError("video decrypt failed: decode_key or keystream mismatch")


def _extract_audio(ffmpeg: str, source: Path, out_path: Path) -> None:
    proc = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-acodec",
            "pcm_s16le",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size <= 128:
        msg = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"ffmpeg extract audio failed: {msg[-1800:]}")


def _call_server_stt(audio_url: str, auth_headers: Dict[str, str]) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    headers.update(auth_headers or {})
    payload = {"audio_url": audio_url, "return_captions": False}
    with httpx.Client(timeout=1200.0, follow_redirects=True, trust_env=False) as client:
        resp = client.post(f"{_server_base()}/api/cutcli/stt/transcribe", json=payload, headers=headers)
    try:
        data = resp.json()
    except Exception:
        data = {"detail": resp.text}
    if resp.status_code >= 400 or (isinstance(data, dict) and data.get("ok") is False):
        raise RuntimeError(_safe_error(data.get("detail") if isinstance(data, dict) else data))
    return data if isinstance(data, dict) else {}


def _extract_stt_output(stt_data: Any) -> Dict[str, Any]:
    if not isinstance(stt_data, dict):
        return {}
    for key in ("output", "result"):
        value = stt_data.get(key)
        if isinstance(value, dict):
            return _extract_stt_output(value)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return _extract_stt_output(parsed)
                if isinstance(parsed, list):
                    return {"utterances": parsed}
            except Exception:
                pass
            return {"text": text}
    data = stt_data.get("data")
    if isinstance(data, dict):
        return _extract_stt_output(data)
    if isinstance(data, str) and data.strip():
        try:
            parsed = json.loads(data.strip())
            if isinstance(parsed, dict):
                return _extract_stt_output(parsed)
            if isinstance(parsed, list):
                return {"utterances": parsed}
        except Exception:
            pass
        return {"text": data.strip()}
    nested = stt_data.get("stt_data")
    if isinstance(nested, dict):
        return _extract_stt_output(nested)
    return stt_data


def _extract_text_from_jsonish(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            direct = parsed.get("text")
            if isinstance(direct, str) and direct.strip():
                return direct.strip()
        if isinstance(parsed, list):
            parts = [
                str(item.get("text") or "").strip()
                for item in parsed
                if isinstance(item, dict) and str(item.get("text") or "").strip()
            ]
            return "\n".join(parts).strip()
    except Exception:
        pass
    match = re.search(r'"text"\s*:\s*"(.*?)"\s*,\s*"(?:duration_ms|duration|audio_info|utterances)"', text, re.S)
    if match:
        try:
            return json.loads('"' + match.group(1) + '"').strip()
        except Exception:
            return match.group(1).strip()
    return text


def _transcript_text(stt_data: Any) -> str:
    output = _extract_stt_output(stt_data)
    for key in ("text", "transcript", "content", "result_text"):
        value = output.get(key) if isinstance(output, dict) else None
        if isinstance(value, str) and value.strip():
            return _extract_text_from_jsonish(value)
    utterances = output.get("utterances") if isinstance(output, dict) else None
    if isinstance(utterances, list):
        parts = []
        for item in utterances:
            if isinstance(item, dict):
                text = item.get("text") or item.get("words")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        if parts:
            return "\n".join(parts).strip()
    return ""


def _account_display_name(account: Dict[str, Any], account_key: str) -> str:
    return str(
        account.get("display_name")
        or account.get("nickname")
        or account.get("name")
        or account_key
        or ""
    )[:255]


def _video_item_key(video: Dict[str, Any]) -> str:
    return str(video.get("item_key") or video.get("public_url") or video.get("video_url") or "")[:256]


def _compact_video_for_db(video: Dict[str, Any]) -> Dict[str, Any]:
    raw = video.get("raw") if isinstance(video.get("raw"), dict) else {}
    return {
        "item_key": _video_item_key(video),
        "title": _video_title_text(video.get("title"))[:4000],
        "publish_time": str(video.get("publish_time") or "")[:128],
        "video_url": _clean_url(video.get("video_url")),
        "public_url": _clean_url(video.get("public_url")),
        "cover_url": _clean_url(video.get("cover_url")),
        "decode_key": _decode_key(video)[:128],
        "metrics": video.get("metrics") if isinstance(video.get("metrics"), dict) else {},
        "raw": raw,
    }


def _video_payload(row: WechatChannelsTranscriptVideo) -> Dict[str, Any]:
    return {
        "item_key": row.item_key,
        "title": _video_title_text(row.title or ""),
        "publish_time": row.publish_time or "",
        "video_url": row.video_url or "",
        "public_url": row.public_url or "",
        "cover_url": row.cover_url or "",
        "decode_key": row.decode_key or "",
        "metrics": row.metrics or {},
        "raw": row.raw or {},
    }


def _cache_payload(account: WechatChannelsTranscriptAccount, videos: list[WechatChannelsTranscriptVideo]) -> Dict[str, Any]:
    return {
        "ok": True,
        "account_key": account.account_key,
        "account": account.account_payload or {"username": account.account_key, "display_name": account.display_name or account.account_key},
        "videos": [_video_payload(row) for row in videos],
        "selected_keys": account.selected_keys or [],
        "updated_at": account.updated_at.isoformat() if account.updated_at else "",
    }


def _job_id(job: Dict[str, Any]) -> str:
    return str(job.get("id") or job.get("job_id") or "").strip()[:128]


def _job_status(job: Dict[str, Any]) -> str:
    status = str(job.get("status") or "").strip().lower()
    if status in {"queued", "running", "pending", "completed", "failed"}:
        return status
    items = job.get("items") if isinstance(job.get("items"), list) else []
    if any(isinstance(item, dict) and item.get("status") in {"running", "queued"} for item in items):
        return "running"
    if any(isinstance(item, dict) and item.get("status") == "failed" for item in items):
        return "failed"
    if items and all(isinstance(item, dict) and item.get("status") == "completed" for item in items):
        return "completed"
    return status or "pending"


@router.post("/api/wechat-channels-transcript/local-cache", summary="Persist queried WeChat Channels videos locally")
def save_wechat_channels_video_cache(
    body: VideoCacheSaveBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    account_key = body.account_key.strip()
    if not account_key:
        raise HTTPException(status_code=400, detail="account_key is required")
    account = (
        db.query(WechatChannelsTranscriptAccount)
        .filter(
            WechatChannelsTranscriptAccount.user_id == current_user.id,
            WechatChannelsTranscriptAccount.account_key == account_key,
        )
        .first()
    )
    now = datetime.utcnow()
    if not account:
        account = WechatChannelsTranscriptAccount(
            user_id=current_user.id,
            account_key=account_key,
            created_at=now,
        )
        db.add(account)
    account.display_name = _account_display_name(body.account or {}, account_key)
    account.account_payload = body.account or {}
    account.selected_keys = [str(x)[:256] for x in body.selected_keys if str(x or "").strip()]
    account.video_count = len(body.videos or [])
    account.updated_at = now

    incoming_keys: set[str] = set()
    for video in body.videos or []:
        if not isinstance(video, dict):
            continue
        payload = _compact_video_for_db(video)
        item_key = payload.get("item_key") or ""
        if not item_key:
            continue
        incoming_keys.add(item_key)
        row = (
            db.query(WechatChannelsTranscriptVideo)
            .filter(
                WechatChannelsTranscriptVideo.user_id == current_user.id,
                WechatChannelsTranscriptVideo.account_key == account_key,
                WechatChannelsTranscriptVideo.item_key == item_key,
            )
            .first()
        )
        if not row:
            row = WechatChannelsTranscriptVideo(
                user_id=current_user.id,
                account_key=account_key,
                item_key=item_key,
                created_at=now,
            )
            db.add(row)
        row.title = payload.get("title") or ""
        row.publish_time = payload.get("publish_time") or ""
        row.video_url = payload.get("video_url") or ""
        row.public_url = payload.get("public_url") or ""
        row.cover_url = payload.get("cover_url") or ""
        row.decode_key = payload.get("decode_key") or ""
        row.metrics = payload.get("metrics") or {}
        row.raw = payload.get("raw") or {}
        row.updated_at = now
    stale_query = db.query(WechatChannelsTranscriptVideo).filter(
        WechatChannelsTranscriptVideo.user_id == current_user.id,
        WechatChannelsTranscriptVideo.account_key == account_key,
    )
    if incoming_keys:
        stale_query.filter(~WechatChannelsTranscriptVideo.item_key.in_(incoming_keys)).delete(synchronize_session=False)
    else:
        stale_query.delete(synchronize_session=False)
    db.commit()
    videos = (
        db.query(WechatChannelsTranscriptVideo)
        .filter(
            WechatChannelsTranscriptVideo.user_id == current_user.id,
            WechatChannelsTranscriptVideo.account_key == account_key,
        )
        .order_by(WechatChannelsTranscriptVideo.id.asc())
        .all()
    )
    return _cache_payload(account, videos)


@router.post("/api/wechat-channels-transcript/local-cache/selection", summary="Persist selected WeChat Channels video keys locally")
def save_wechat_channels_video_selection(
    body: VideoCacheSelectionBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    account_key = body.account_key.strip()
    account = (
        db.query(WechatChannelsTranscriptAccount)
        .filter(
            WechatChannelsTranscriptAccount.user_id == current_user.id,
            WechatChannelsTranscriptAccount.account_key == account_key,
        )
        .first()
    )
    if not account:
        raise HTTPException(status_code=404, detail="account cache not found")
    account.selected_keys = [str(x)[:256] for x in body.selected_keys if str(x or "").strip()]
    account.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "account_key": account_key, "selected_keys": account.selected_keys or []}


@router.post("/api/wechat-channels-transcript/local-jobs", summary="Persist transcript jobs locally")
def save_wechat_channels_transcript_jobs(
    body: TranscriptJobsSaveBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    now = datetime.utcnow()
    incoming_ids: set[str] = set()
    for job in body.jobs or []:
        if not isinstance(job, dict):
            continue
        job_id = _job_id(job)
        if not job_id:
            continue
        incoming_ids.add(job_id)
        items = job.get("items") if isinstance(job.get("items"), list) else []
        row = (
            db.query(WechatChannelsTranscriptJob)
            .filter(
                WechatChannelsTranscriptJob.user_id == current_user.id,
                WechatChannelsTranscriptJob.job_id == job_id,
            )
            .first()
        )
        if not row:
            row = WechatChannelsTranscriptJob(user_id=current_user.id, job_id=job_id, created_at=now)
            db.add(row)
        row.title = str(job.get("title") or "")[:500]
        row.status = _job_status(job)
        row.item_count = len(items)
        row.payload = job
        row.updated_at = now
    if incoming_ids:
        db.query(WechatChannelsTranscriptJob).filter(
            WechatChannelsTranscriptJob.user_id == current_user.id,
            ~WechatChannelsTranscriptJob.job_id.in_(incoming_ids),
        ).delete(synchronize_session=False)
    else:
        db.query(WechatChannelsTranscriptJob).filter(
            WechatChannelsTranscriptJob.user_id == current_user.id,
        ).delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "count": len(incoming_ids)}


@router.get("/api/wechat-channels-transcript/local-jobs", summary="Load transcript jobs from local DB")
def get_wechat_channels_transcript_jobs(
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(WechatChannelsTranscriptJob)
        .filter(WechatChannelsTranscriptJob.user_id == current_user.id)
        .order_by(WechatChannelsTranscriptJob.created_at.desc(), WechatChannelsTranscriptJob.id.desc())
        .limit(50)
        .all()
    )
    return {"ok": True, "jobs": [row.payload or {} for row in rows if isinstance(row.payload, dict)]}


@router.post("/api/wechat-channels-transcript/export", summary="Export WeChat Channels transcripts to local file")
def export_wechat_channels_transcripts(
    body: TranscriptExportBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    fmt = (body.format or "txt").strip().lower()
    if fmt not in {"txt", "csv"}:
        raise HTTPException(status_code=400, detail="format must be txt or csv")
    export_dir = get_asset_export_dir() / "视频号文案"
    export_dir.mkdir(parents=True, exist_ok=True)
    filename = _safe_export_filename(body.filename, fmt)
    target = _unique_export_path(export_dir, filename)

    if fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["标题", "发布时间", "状态", "原链接", "视频链接", "转写文本"])
        for row in body.rows or []:
            if not isinstance(row, dict):
                continue
            writer.writerow(
                [
                    str(row.get("title") or ""),
                    str(row.get("publish_time") or ""),
                    str(row.get("status") or ""),
                    str(row.get("public_url") or ""),
                    str(row.get("video_url") or ""),
                    str(row.get("transcript") or row.get("error") or ""),
                ]
            )
        content = "\ufeff" + output.getvalue()
    else:
        content = body.text or ""
    if not content.strip():
        raise HTTPException(status_code=400, detail="暂无可导出内容")
    target.write_text(content, encoding="utf-8", newline="")
    return {
        "ok": True,
        "path": str(target),
        "directory": str(target.parent),
        "filename": target.name,
        "format": fmt,
    }


@router.get("/api/wechat-channels-transcript/local-cache", summary="Load queried WeChat Channels videos from local DB")
def get_wechat_channels_video_cache(
    account_key: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    key = account_key.strip()
    account = (
        db.query(WechatChannelsTranscriptAccount)
        .filter(
            WechatChannelsTranscriptAccount.user_id == current_user.id,
            WechatChannelsTranscriptAccount.account_key == key,
        )
        .first()
    )
    if not account:
        raise HTTPException(status_code=404, detail="account cache not found")
    videos = (
        db.query(WechatChannelsTranscriptVideo)
        .filter(
            WechatChannelsTranscriptVideo.user_id == current_user.id,
            WechatChannelsTranscriptVideo.account_key == key,
        )
        .order_by(WechatChannelsTranscriptVideo.id.asc())
        .all()
    )
    return _cache_payload(account, videos)


@router.get("/api/wechat-channels-transcript/local-cache/latest", summary="Load latest queried WeChat Channels videos from local DB")
def get_latest_wechat_channels_video_cache(
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    account = (
        db.query(WechatChannelsTranscriptAccount)
        .filter(WechatChannelsTranscriptAccount.user_id == current_user.id)
        .order_by(WechatChannelsTranscriptAccount.updated_at.desc(), WechatChannelsTranscriptAccount.id.desc())
        .first()
    )
    if not account:
        return {"ok": True, "account": None, "videos": [], "selected_keys": []}
    videos = (
        db.query(WechatChannelsTranscriptVideo)
        .filter(
            WechatChannelsTranscriptVideo.user_id == current_user.id,
            WechatChannelsTranscriptVideo.account_key == account.account_key,
        )
        .order_by(WechatChannelsTranscriptVideo.id.asc())
        .all()
    )
    return _cache_payload(account, videos)


@router.post("/api/wechat-channels-transcript/local-transcribe", summary="Download WeChat Channels video locally, extract audio, and transcribe on server")
async def local_transcribe_wechat_channels_video(
    body: LocalTranscriptBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    video = body.video if isinstance(body.video, dict) else {}
    url = _video_url(video)
    if not url:
        raise HTTPException(status_code=400, detail="video_url is required")
    if not _decode_key(video):
        raise HTTPException(status_code=400, detail="decode_key is required")
    try:
        keystream = base64.b64decode((body.keystream_b64 or "").strip(), validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid keystream") from exc
    auth_headers = _auth_headers(request)
    if not auth_headers.get("Authorization"):
        raise HTTPException(status_code=401, detail="Authorization is required")

    work = Path(tempfile.mkdtemp(prefix="wct_local_"))
    source = work / "source.mp4"
    audio_path = work / "audio.wav"
    try:
        await asyncio.to_thread(_download_video, url, source)
        await asyncio.to_thread(_decrypt_video_prefix, source, keystream)
        await asyncio.to_thread(_extract_audio, find_ffmpeg(), source, audio_path)
        audio_bytes = await asyncio.to_thread(audio_path.read_bytes)
        audio_url, diag = await _upload_bytes_to_auth_server(
            audio_bytes,
            "wechat_channels_audio.wav",
            "audio/wav",
            request,
            timeout=180.0,
        )
        if not audio_url:
            raise RuntimeError("audio upload failed: " + _safe_error(diag))
        stt_result = await asyncio.to_thread(_call_server_stt, audio_url, auth_headers)
        stt_data = stt_result.get("stt_data") if isinstance(stt_result.get("stt_data"), dict) else stt_result
        transcript = _transcript_text(stt_data)
        if not transcript:
            raise RuntimeError(
                "STT completed but returned empty transcript: "
                + _safe_error(stt_data, 1000)
            )
        return {
            "ok": True,
            "audio_url": audio_url,
            "source_size": source.stat().st_size if source.exists() else 0,
            "audio_size": len(audio_bytes),
            "task_id": stt_result.get("task_id"),
            "stt_model": stt_result.get("stt_model"),
            "token_source": stt_result.get("token_source"),
            "transcript": transcript,
            "stt_data": stt_data,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("[wechat-channels-transcript-local] failed user_id=%s err=%s", current_user.id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)[:2000]) from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
