from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Asset, ShanjianDigitalHumanProfile, ShanjianDigitalHumanVideoTask
from .assets import ASSETS_DIR, _save_bytes_or_tos, get_asset_public_url
from .auth import _ServerUser, get_current_user_for_local
from .shanjian_smart_clip import _data, _get, _post

logger = logging.getLogger(__name__)
router = APIRouter()
_ROOT_DIR = Path(__file__).resolve().parents[3]


class _TokenBody(BaseModel):
    token: Optional[str] = None


class ProfileTrainBody(_TokenBody):
    title: str = "未命名数字人"
    mode: str = "image"
    image_url: Optional[str] = None
    image_asset_id: Optional[str] = None
    video_url: Optional[str] = None
    video_asset_id: Optional[str] = None
    auth_video_url: Optional[str] = None
    auth_video_asset_id: Optional[str] = None
    auth_text: str = Field(..., min_length=2, max_length=500)
    callback_url: str = ""
    make_default: bool = True


class ProfileTaskBody(_TokenBody):
    task_id: Optional[str] = None
    profile_id: Optional[int] = None


class SetDefaultBody(BaseModel):
    profile_id: int = Field(..., gt=0)


class CreateVideoBody(_TokenBody):
    profile_id: Optional[int] = None
    virtualman_id: Optional[str] = None
    title: str = "数字人口播"
    text: Optional[str] = None
    speaker_id: Optional[str] = None
    audio_url: Optional[str] = None
    audio_asset_id: Optional[str] = None
    language: str = "zh-CN"
    speed_ratio: float = 1.0
    callback_url: str = ""


class VideoTaskBody(_TokenBody):
    task_id: Optional[str] = None
    record_id: Optional[int] = None


def _clean_text(value: Optional[str]) -> str:
    return str(value or "").strip()


def _url_hint(value: str) -> str:
    raw = _clean_text(value)
    if raw.startswith("data:"):
        return "data-url"
    try:
        parsed = urlparse(raw)
        path = (parsed.path or "")[:80]
        return f"{parsed.scheme}://{parsed.netloc}{path}"
    except Exception:
        return raw[:120]


def _normalize_mode(value: str) -> str:
    raw = _clean_text(value).lower()
    aliases = {
        "image": "image",
        "image_train": "image",
        "photo": "image",
        "video": "video",
        "pro": "video",
        "professional": "video",
        "fast_video": "fast_video",
        "fast": "fast_video",
    }
    mode = aliases.get(raw)
    if not mode:
        raise HTTPException(status_code=400, detail="mode 仅支持 image / video / fast_video")
    return mode


def _pick_result_value(result: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in result and result.get(key) not in (None, ""):
            return result.get(key)
    return None


def _task_status_text(status: str) -> str:
    mapping = {
        "processing": "处理中",
        "succeed": "已完成",
        "failed": "失败",
    }
    return mapping.get(_clean_text(status), _clean_text(status) or "处理中")


def _resolve_asset_or_url(
    *,
    request: Request,
    db: Session,
    current_user: _ServerUser,
    url: Optional[str],
    asset_id: Optional[str],
    label: str,
) -> str:
    raw_url = _clean_text(url)
    if raw_url:
        if raw_url.startswith("http://") or raw_url.startswith("https://"):
            return raw_url
        raise HTTPException(status_code=400, detail=f"{label} URL 必须是 http(s) 地址")
    aid = _clean_text(asset_id)
    if not aid:
        raise HTTPException(status_code=400, detail=f"请提供{label} URL 或 asset_id")
    public_url = get_asset_public_url(aid, int(current_user.id), request, db)
    if not public_url:
        raise HTTPException(status_code=400, detail=f"{label}素材还没有可用公网地址，请先确认素材已上传成功")
    return public_url


def _clear_default_profiles(db: Session, user_id: int) -> None:
    db.query(ShanjianDigitalHumanProfile).filter(
        ShanjianDigitalHumanProfile.user_id == int(user_id),
        ShanjianDigitalHumanProfile.is_default.is_(True),
    ).update({"is_default": False}, synchronize_session=False)


def _audio_ext_from_content(content_type: str, url: str = "") -> str:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    suffix = Path((url or "").split("?", 1)[0].split("#", 1)[0]).suffix.lower()
    if suffix in {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}:
        return suffix
    if "wav" in ct:
        return ".wav"
    if "mp4" in ct or "m4a" in ct:
        return ".m4a"
    if "aac" in ct:
        return ".aac"
    if "ogg" in ct:
        return ".ogg"
    if "flac" in ct:
        return ".flac"
    return ".mp3"


async def _download_audio_bytes(audio_url: str) -> tuple[bytes, str]:
    raw = _clean_text(audio_url)
    if raw.startswith("data:"):
        header, _, payload = raw.partition(",")
        if not payload:
            raise HTTPException(status_code=400, detail="audio_url data URL 格式无效")
        content_type = header[5:].split(";", 1)[0].strip() or "audio/mpeg"
        try:
            data = base64.b64decode(payload)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="audio_url data URL 解码失败") from exc
        return data, content_type
    if not raw.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="audio_url 必须是 http(s) 或 data: 音频地址")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "audio/*,*/*;q=0.8",
    }
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True, trust_env=False) as client:
            resp = await client.get(raw, headers=headers)
            resp.raise_for_status()
            return resp.content, resp.headers.get("content-type", "") or "audio/mpeg"
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=400, detail=f"下载音频失败: HTTP {exc.response.status_code}") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"下载音频失败: {type(exc).__name__}: {exc}") from exc


async def _persist_audio_for_shanjian(
    *,
    audio_url: str,
    db: Session,
    current_user: _ServerUser,
    title: str,
) -> tuple[str, str]:
    raw = _clean_text(audio_url)
    data, content_type = await _download_audio_bytes(raw)
    if not data:
        raise HTTPException(status_code=400, detail="音频内容为空，无法提交数字人任务")
    media_type = (content_type or "audio/mpeg").split(";", 1)[0].strip() or "audio/mpeg"
    ext = _audio_ext_from_content(media_type, raw)
    asset_id, filename_or_key, file_size, public_url = _save_bytes_or_tos(data, ext, media_type)
    if not public_url:
        raise HTTPException(status_code=503, detail="数字人口播音频转存 TOS 失败，无法提交闪剪")
    asset = Asset(
        asset_id=asset_id,
        user_id=int(current_user.id),
        filename=filename_or_key,
        media_type="audio",
        file_size=file_size,
        source_url=public_url,
        prompt=_clean_text(title)[:200],
        model="shanjian-digital-human-tts-audio",
        tags="shanjian,digital-human,audio",
        meta={
            "source": "shanjian_digital_human_audio_transfer",
            "original_url_hint": _url_hint(raw),
            "content_type": media_type,
        },
    )
    db.add(asset)
    db.flush()
    logger.info(
        "[shanjian-dh] audio rehosted user_id=%s asset_id=%s size=%s from=%s to=%s",
        getattr(current_user, "id", ""),
        asset_id,
        file_size,
        _url_hint(raw),
        _url_hint(public_url),
    )
    return public_url, asset_id


def _ffprobe_path() -> str:
    candidates = []
    if os.name == "nt":
        candidates.extend(
            [
                _ROOT_DIR / "deps" / "ffmpeg" / "ffprobe.exe",
                _ROOT_DIR / "skills" / "comfly_veo3_daihuo_video" / "tools" / "ffmpeg" / "windows" / "ffprobe.exe",
            ]
        )
    else:
        candidates.append(_ROOT_DIR / "deps" / "ffmpeg" / "ffprobe")
    for item in candidates:
        if item.exists():
            return str(item.resolve())
    return shutil.which("ffprobe.exe" if os.name == "nt" else "ffprobe") or ""


def _ffmpeg_path() -> str:
    candidates = []
    if os.name == "nt":
        candidates.extend(
            [
                _ROOT_DIR / "deps" / "ffmpeg" / "ffmpeg.exe",
                _ROOT_DIR / "skills" / "comfly_veo3_daihuo_video" / "tools" / "ffmpeg" / "windows" / "ffmpeg.exe",
            ]
        )
    else:
        candidates.append(_ROOT_DIR / "deps" / "ffmpeg" / "ffmpeg")
    for item in candidates:
        if item.exists():
            return str(item.resolve())
    return shutil.which("ffmpeg.exe" if os.name == "nt" else "ffmpeg") or ""


def _asset_local_path(asset: Optional[Asset]) -> Optional[Path]:
    if not asset or not getattr(asset, "filename", None):
        return None
    filename = str(asset.filename or "")
    if not filename or "/" in filename or "\\" in filename:
        return None
    path = ASSETS_DIR / filename
    return path if path.exists() else None


def _parse_fps(raw: Any) -> float:
    text = str(raw or "").strip()
    if not text:
        return 0.0
    if "/" in text:
        left, right = text.split("/", 1)
        try:
            numerator = float(left or 0)
            denominator = float(right or 1)
            if denominator == 0:
                return 0.0
            return numerator / denominator
        except Exception:
            return 0.0
    try:
        return float(text)
    except Exception:
        return 0.0


def _probe_video_media(source: str) -> Dict[str, Any]:
    ffprobe = _ffprobe_path()
    if not ffprobe:
        raise HTTPException(status_code=500, detail="本机缺少 ffprobe，暂时无法校验视频参数")
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,codec_name,codec_tag_string,avg_frame_rate,r_frame_rate,duration:format=duration",
            "-of",
            "json",
            source,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise HTTPException(status_code=400, detail=f"无法读取视频信息：{detail[:300] or 'ffprobe failed'}")
    try:
        payload = json.loads(proc.stdout or "{}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="无法解析视频元数据") from exc
    stream = ((payload.get("streams") or [{}])[0]) or {}
    fmt = payload.get("format") or {}
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "duration": float(stream.get("duration") or fmt.get("duration") or 0.0),
        "codec": str(stream.get("codec_name") or stream.get("codec_tag_string") or "").strip().lower(),
        "fps": _parse_fps(stream.get("avg_frame_rate") or stream.get("r_frame_rate")),
    }


def _normalize_avatar_video_kind(raw: str) -> str:
    kind = _clean_text(raw).lower()
    if kind in {"train", "training", "avatar_train", "video_train"}:
        return "training"
    if kind in {"auth", "authorize", "authorization", "auth_video"}:
        return "auth"
    raise HTTPException(status_code=400, detail="kind 仅支持 training 或 auth")


def _avatar_video_limits(kind: str) -> Dict[str, Optional[float]]:
    if kind == "training":
        return {
            "max_size_mb": 500,
            "min_duration_sec": 5.0,
            "max_duration_sec": 60.0,
            "max_side_px": 2000,
            "min_fps": 10.0,
            "max_fps": 60.0,
        }
    return {
        "max_size_mb": 100,
        "min_duration_sec": None,
        "max_duration_sec": 120.0,
        "max_side_px": 2000,
        "min_fps": None,
        "max_fps": None,
    }


def _guess_video_content_type(name: str) -> str:
    suffix = str(Path(name or "").suffix or "").lower()
    if suffix == ".mov":
        return "video/quicktime"
    return "video/mp4"


def _normalize_video_filename(name: str) -> str:
    stem = Path(name or "avatar_video").stem or "avatar_video"
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in stem).strip("._") or "avatar_video"
    return f"{safe}_normalized.mp4"


def _build_avatar_video_filters(width: int, height: int, max_side_px: Optional[int]) -> str:
    parts: list[str] = []
    if max_side_px and max(width, height) > int(max_side_px):
        if width >= height:
            parts.append(f"scale='min({int(max_side_px)},iw)':-2")
        else:
            parts.append(f"scale=-2:'min({int(max_side_px)},ih)'")
    parts.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    return ",".join(parts)


def _needs_avatar_video_normalization(
    *,
    kind: str,
    meta: Dict[str, Any],
    filename: str,
    file_size: int,
) -> tuple[bool, list[str]]:
    limits = _avatar_video_limits(kind)
    reasons: list[str] = []
    suffix = str(Path(filename or "").suffix or "").lower()
    codec = str(meta.get("codec") or "").lower()
    width = int(meta.get("width") or 0)
    height = int(meta.get("height") or 0)
    fps = float(meta.get("fps") or 0.0)
    max_side_px = int(limits.get("max_side_px") or 0)
    max_size_bytes = int(float(limits["max_size_mb"]) * 1024 * 1024)
    if suffix not in {".mp4", ".mov"}:
        reasons.append("format")
    if codec not in {"h264", "avc1", "hevc", "h265", "hev1"}:
        reasons.append("codec")
    if max_side_px and max(width, height) > max_side_px:
        reasons.append("resolution")
    if file_size > max_size_bytes:
        reasons.append("size")
    min_fps = limits.get("min_fps")
    max_fps = limits.get("max_fps")
    if fps > 0 and ((min_fps is not None and fps < float(min_fps)) or (max_fps is not None and fps > float(max_fps))):
        reasons.append("fps")
    return bool(reasons), reasons


def _transcode_avatar_video(
    *,
    src_path: Path,
    dst_path: Path,
    meta: Dict[str, Any],
    kind: str,
) -> None:
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        raise HTTPException(status_code=500, detail="本机缺少 ffmpeg，暂时无法自动修正视频规格")
    limits = _avatar_video_limits(kind)
    vf = _build_avatar_video_filters(
        int(meta.get("width") or 0),
        int(meta.get("height") or 0),
        int(limits.get("max_side_px") or 0),
    )
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(src_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
    ]
    fps = float(meta.get("fps") or 0.0)
    min_fps = limits.get("min_fps")
    max_fps = limits.get("max_fps")
    if fps > 0 and ((min_fps is not None and fps < float(min_fps)) or (max_fps is not None and fps > float(max_fps))):
        cmd.extend(["-r", "30"])
    cmd.append(str(dst_path))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
        check=False,
    )
    if proc.returncode != 0 or not dst_path.exists():
        detail = (proc.stderr or proc.stdout or "").strip()
        raise HTTPException(status_code=500, detail=f"自动修正视频失败：{detail[:300] or 'ffmpeg failed'}")


def _validate_video_constraints(
    *,
    label: str,
    url: str,
    asset: Optional[Asset],
    max_size_mb: int,
    min_duration_sec: Optional[float],
    max_duration_sec: float,
    max_side_px: Optional[int],
    min_fps: Optional[float],
    max_fps: Optional[float],
) -> None:
    filename = str(getattr(asset, "filename", "") or url or "").lower()
    if not (filename.endswith(".mp4") or filename.endswith(".mov")):
        raise HTTPException(status_code=400, detail=f"{label}仅支持 mp4、mov 格式")
    file_size = int(getattr(asset, "file_size", 0) or 0)
    if file_size > 0 and file_size > max_size_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"{label}文件大小需小于等于 {max_size_mb}MB")
    source = str(_asset_local_path(asset) or url)
    meta = _probe_video_media(source)
    duration = float(meta.get("duration") or 0.0)
    width = int(meta.get("width") or 0)
    height = int(meta.get("height") or 0)
    codec = str(meta.get("codec") or "").lower()
    fps = float(meta.get("fps") or 0.0)
    if min_duration_sec is not None and duration < min_duration_sec:
        raise HTTPException(status_code=400, detail=f"{label}时长需大于等于 {int(min_duration_sec)} 秒")
    if duration > max_duration_sec:
        raise HTTPException(status_code=400, detail=f"{label}时长需小于等于 {int(max_duration_sec if max_duration_sec < 120 else max_duration_sec / 60)}{'秒' if max_duration_sec < 120 else '分钟'}")
    if max_side_px and max(width, height) > max_side_px:
        raise HTTPException(status_code=400, detail=f"{label}分辨率单边不能超过 {max_side_px}px")
    if codec not in {"h264", "avc1", "hevc", "h265", "hev1"}:
        raise HTTPException(status_code=400, detail=f"{label}视频编码仅支持 h264、HEVC(h265)")
    if min_fps is not None and fps > 0 and fps < min_fps:
        raise HTTPException(status_code=400, detail=f"{label}帧率需在 {int(min_fps)}-{int(max_fps or min_fps)}fps 范围内")
    if max_fps is not None and fps > max_fps:
        raise HTTPException(status_code=400, detail=f"{label}帧率需在 {int(min_fps or 0)}-{int(max_fps)}fps 范围内")


def _profile_to_dict(row: ShanjianDigitalHumanProfile) -> Dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "title": row.title,
        "train_mode": row.train_mode,
        "status": row.status,
        "status_text": _task_status_text(row.status),
        "is_default": bool(row.is_default),
        "task_id": row.task_id or "",
        "request_id": row.request_id or "",
        "virtualman_id": row.virtualman_id or "",
        "source_asset_id": row.source_asset_id or "",
        "source_url": row.source_url or "",
        "auth_video_asset_id": row.auth_video_asset_id or "",
        "auth_video_url": row.auth_video_url or "",
        "auth_text": row.auth_text or "",
        "cover_url": row.cover_url or "",
        "error_message": row.error_message or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def _video_task_to_dict(row: ShanjianDigitalHumanVideoTask) -> Dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "profile_id": row.profile_id,
        "title": row.title,
        "status": row.status,
        "status_text": _task_status_text(row.status),
        "task_id": row.task_id,
        "request_id": row.request_id or "",
        "virtualman_id": row.virtualman_id or "",
        "audio_asset_id": row.audio_asset_id or "",
        "audio_url": row.audio_url or "",
        "speaker_id": row.speaker_id or "",
        "text": row.text or "",
        "video_url": row.video_url or "",
        "cover_url": row.cover_url or "",
        "duration": row.duration,
        "error_message": row.error_message or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def _resolve_profile_for_video(
    db: Session,
    current_user: _ServerUser,
    profile_id: Optional[int],
    virtualman_id: Optional[str],
) -> tuple[Optional[ShanjianDigitalHumanProfile], str]:
    vmid = _clean_text(virtualman_id)
    if profile_id:
        row = db.query(ShanjianDigitalHumanProfile).filter(
            ShanjianDigitalHumanProfile.id == int(profile_id),
            ShanjianDigitalHumanProfile.user_id == int(current_user.id),
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="未找到对应的闪剪数字人档案")
        if row.status != "succeed" or not _clean_text(row.virtualman_id):
            raise HTTPException(status_code=400, detail="该闪剪数字人还未训练完成，暂时不能用于出片")
        return row, _clean_text(row.virtualman_id)
    if vmid:
        row = db.query(ShanjianDigitalHumanProfile).filter(
            ShanjianDigitalHumanProfile.user_id == int(current_user.id),
            ShanjianDigitalHumanProfile.virtualman_id == vmid,
        ).first()
        return row, vmid
    row = db.query(ShanjianDigitalHumanProfile).filter(
        ShanjianDigitalHumanProfile.user_id == int(current_user.id),
        ShanjianDigitalHumanProfile.is_default.is_(True),
        ShanjianDigitalHumanProfile.status == "succeed",
    ).order_by(ShanjianDigitalHumanProfile.updated_at.desc()).first()
    if row and _clean_text(row.virtualman_id):
        return row, _clean_text(row.virtualman_id)
    raise HTTPException(status_code=400, detail="请先创建并训练一个自己的闪剪数字人，或显式传入 virtualman_id")


def _profile_endpoint_and_payload(
    body: ProfileTrainBody,
    *,
    request: Request,
    db: Session,
    current_user: _ServerUser,
) -> tuple[str, Dict[str, Any], str, Optional[str], Optional[str]]:
    mode = _normalize_mode(body.mode)
    source_asset_id = _clean_text(body.image_asset_id if mode == "image" else body.video_asset_id) or None
    auth_asset_id = _clean_text(body.auth_video_asset_id) or None
    auth_video_url = _resolve_asset_or_url(
        request=request,
        db=db,
        current_user=current_user,
        url=body.auth_video_url,
        asset_id=body.auth_video_asset_id,
        label="授权视频",
    )
    payload: Dict[str, Any] = {
        "title": _clean_text(body.title)[:80] or "未命名数字人",
        "authVideoUrl": auth_video_url,
        "authText": _clean_text(body.auth_text),
    }
    if _clean_text(body.callback_url):
        payload["callbackUrl"] = _clean_text(body.callback_url)
    if mode == "image":
        source_url = _resolve_asset_or_url(
            request=request,
            db=db,
            current_user=current_user,
            url=body.image_url,
            asset_id=body.image_asset_id,
            label="训练图片",
        )
        payload["imageUrl"] = source_url
        return "/v1/virtualman/image/train", payload, source_url, source_asset_id, auth_asset_id
    source_url = _resolve_asset_or_url(
        request=request,
        db=db,
        current_user=current_user,
        url=body.video_url,
        asset_id=body.video_asset_id,
        label="训练视频",
    )
    payload["videoUrl"] = source_url
    endpoint = "/v1/virtualman/fast/train" if mode == "fast_video" else "/v1/virtualman/train"
    return endpoint, payload, source_url, source_asset_id, auth_asset_id


@router.get("/api/shanjian-digital-human/profiles")
async def list_profiles(
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    rows = db.query(ShanjianDigitalHumanProfile).filter(
        ShanjianDigitalHumanProfile.user_id == int(current_user.id)
    ).order_by(
        ShanjianDigitalHumanProfile.is_default.desc(),
        ShanjianDigitalHumanProfile.updated_at.desc(),
        ShanjianDigitalHumanProfile.id.desc(),
    ).all()
    return {"ok": True, "items": [_profile_to_dict(row) for row in rows]}


@router.get("/api/shanjian-digital-human/videos")
async def list_video_tasks(
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    rows = db.query(ShanjianDigitalHumanVideoTask).filter(
        ShanjianDigitalHumanVideoTask.user_id == int(current_user.id)
    ).order_by(
        ShanjianDigitalHumanVideoTask.updated_at.desc(),
        ShanjianDigitalHumanVideoTask.id.desc(),
    ).limit(100).all()
    return {"ok": True, "items": [_video_task_to_dict(row) for row in rows]}


@router.post("/api/local/avatar-video/normalize")
async def normalize_avatar_video_upload(
    request: Request,
    file: UploadFile = File(...),
    kind: str = Form("training"),
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    del request, current_user
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="上传文件为空")

    normalized_kind = _normalize_avatar_video_kind(kind)
    limits = _avatar_video_limits(normalized_kind)
    filename = file.filename or "avatar_video.mp4"
    temp_root = Path(tempfile.mkdtemp(prefix="avatar_video_fix_"))
    input_suffix = Path(filename).suffix or ".mp4"
    input_path = temp_root / f"input{input_suffix}"
    output_path = temp_root / _normalize_video_filename(filename)

    try:
        input_path.write_bytes(raw)
        meta = _probe_video_media(str(input_path))
        duration = float(meta.get("duration") or 0.0)
        min_duration = limits.get("min_duration_sec")
        max_duration = float(limits.get("max_duration_sec") or 0.0)
        if min_duration is not None and duration < float(min_duration):
            raise HTTPException(status_code=400, detail=f"{'训练视频' if normalized_kind == 'training' else '授权视频'}时长不能小于 {int(float(min_duration))} 秒")
        if max_duration and duration > max_duration:
            if normalized_kind == "training":
                raise HTTPException(status_code=400, detail="训练视频时长需要在 5 到 60 秒之间")
            raise HTTPException(status_code=400, detail="授权视频时长不能超过 2 分钟")

        needs_fix, reasons = _needs_avatar_video_normalization(
            kind=normalized_kind,
            meta=meta,
            filename=filename,
            file_size=len(raw),
        )
        response_bytes = raw
        response_name = filename
        normalized_flag = "0"
        if needs_fix:
            _transcode_avatar_video(
                src_path=input_path,
                dst_path=output_path,
                meta=meta,
                kind=normalized_kind,
            )
            response_bytes = output_path.read_bytes()
            response_name = output_path.name
            normalized_flag = "1"
            max_size_bytes = int(float(limits["max_size_mb"]) * 1024 * 1024)
            if len(response_bytes) > max_size_bytes:
                raise HTTPException(
                    status_code=400,
                    detail=f"自动修正后文件仍超过 {int(limits['max_size_mb'])}MB，请裁剪更短的视频后重试",
                )
            fixed_meta = _probe_video_media(str(output_path))
            _validate_video_constraints(
                label="训练视频" if normalized_kind == "training" else "授权视频",
                url=str(output_path),
                asset=None,
                max_size_mb=int(limits["max_size_mb"]),
                min_duration_sec=limits.get("min_duration_sec"),
                max_duration_sec=float(limits.get("max_duration_sec") or 0.0),
                max_side_px=int(limits.get("max_side_px") or 0) or None,
                min_fps=limits.get("min_fps"),
                max_fps=limits.get("max_fps"),
            )
            del fixed_meta

        return Response(
            content=response_bytes,
            media_type=_guess_video_content_type(response_name),
            headers={
                "X-Lobster-Filename": response_name,
                "X-Lobster-Video-Normalized": normalized_flag,
                "X-Lobster-Video-Reason": ",".join(reasons[:8]),
            },
        )
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


@router.post("/api/shanjian-digital-human/profile/train")
async def create_profile(
    body: ProfileTrainBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    endpoint, payload, source_url, source_asset_id, auth_asset_id = _profile_endpoint_and_payload(
        body,
        request=request,
        db=db,
        current_user=current_user,
    )
    source_asset = None
    auth_asset = None
    if source_asset_id:
        source_asset = db.query(Asset).filter(
            Asset.asset_id == source_asset_id,
            Asset.user_id == int(current_user.id),
        ).first()
    if auth_asset_id:
        auth_asset = db.query(Asset).filter(
            Asset.asset_id == auth_asset_id,
            Asset.user_id == int(current_user.id),
        ).first()
    _validate_video_constraints(
        label="授权视频",
        url=_clean_text(payload.get("authVideoUrl")),
        asset=auth_asset,
        max_size_mb=100,
        min_duration_sec=None,
        max_duration_sec=120,
        max_side_px=None,
        min_fps=None,
        max_fps=None,
    )
    if _normalize_mode(body.mode) == "video":
        _validate_video_constraints(
            label="训练视频",
            url=source_url,
            asset=source_asset,
            max_size_mb=500,
            min_duration_sec=5,
            max_duration_sec=60,
            max_side_px=2000,
            min_fps=10,
            max_fps=60,
        )
    upstream = await _post(endpoint, body.token, payload)
    data = _data(upstream)
    task_id = _clean_text(data.get("taskId"))
    if not task_id:
        raise HTTPException(status_code=502, detail="闪剪未返回 taskId")
    if body.make_default:
        _clear_default_profiles(db, int(current_user.id))
    row = ShanjianDigitalHumanProfile(
        user_id=int(current_user.id),
        title=_clean_text(body.title)[:80] or "未命名数字人",
        train_mode=_normalize_mode(body.mode),
        status="processing",
        is_default=bool(body.make_default),
        task_id=task_id,
        request_id=_clean_text(upstream.get("requestId")),
        source_asset_id=source_asset_id,
        source_url=source_url,
        auth_video_asset_id=auth_asset_id,
        auth_video_url=_clean_text(payload.get("authVideoUrl")),
        auth_text=_clean_text(body.auth_text),
        train_payload=payload,
        train_result=upstream,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "ok": True,
        "profile": _profile_to_dict(row),
        "task_id": task_id,
        "request_id": row.request_id or "",
        "raw": upstream,
    }


@router.post("/api/shanjian-digital-human/profile/task")
async def query_profile_task(
    body: ProfileTaskBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    row = None
    if body.profile_id:
        row = db.query(ShanjianDigitalHumanProfile).filter(
            ShanjianDigitalHumanProfile.id == int(body.profile_id),
            ShanjianDigitalHumanProfile.user_id == int(current_user.id),
        ).first()
    elif _clean_text(body.task_id):
        row = db.query(ShanjianDigitalHumanProfile).filter(
            ShanjianDigitalHumanProfile.task_id == _clean_text(body.task_id),
            ShanjianDigitalHumanProfile.user_id == int(current_user.id),
        ).first()
    if not row:
        raise HTTPException(status_code=404, detail="未找到对应的闪剪数字人任务")

    payload = await _get("/v1/task/info", body.token, {"taskId": row.task_id})
    data = _data(payload)
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    status = _clean_text(data.get("status")) or "processing"
    virtualman_id = _clean_text(_pick_result_value(result, "virtualmanId", "virtualManId", "id"))
    cover_url = _clean_text(_pick_result_value(result, "coverUrl", "imageUrl", "posterUrl"))
    error_message = _clean_text(data.get("errorMessage") or payload.get("message"))

    row.status = status
    row.request_id = _clean_text(payload.get("requestId")) or row.request_id
    row.virtualman_id = virtualman_id or row.virtualman_id
    row.cover_url = cover_url or row.cover_url
    row.train_result = payload
    row.error_message = error_message or None
    row.updated_at = datetime.utcnow()

    if status == "succeed" and row.is_default:
        _clear_default_profiles(db, int(current_user.id))
        row.is_default = True

    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "ok": status != "failed",
        "status": status,
        "status_text": _task_status_text(status),
        "virtualman_id": row.virtualman_id or "",
        "profile": _profile_to_dict(row),
        "message": error_message,
        "raw": payload,
    }


@router.post("/api/shanjian-digital-human/profile/default")
async def set_default_profile(
    body: SetDefaultBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    row = db.query(ShanjianDigitalHumanProfile).filter(
        ShanjianDigitalHumanProfile.id == int(body.profile_id),
        ShanjianDigitalHumanProfile.user_id == int(current_user.id),
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="未找到对应的闪剪数字人档案")
    _clear_default_profiles(db, int(current_user.id))
    row.is_default = True
    row.updated_at = datetime.utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "profile": _profile_to_dict(row)}


@router.post("/api/shanjian-digital-human/video/create")
async def create_video(
    body: CreateVideoBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    profile, virtualman_id = _resolve_profile_for_video(db, current_user, body.profile_id, body.virtualman_id)
    text = _clean_text(body.text)
    speaker_id = _clean_text(body.speaker_id)
    audio_url = _clean_text(body.audio_url)
    audio_asset_id = _clean_text(body.audio_asset_id) or None
    if not audio_url and audio_asset_id:
        audio_url = _resolve_asset_or_url(
            request=request,
            db=db,
            current_user=current_user,
            url=None,
            asset_id=audio_asset_id,
            label="驱动音频",
        )
    if not audio_url and (not text or not speaker_id):
        raise HTTPException(status_code=400, detail="请提供 audio_url / audio_asset_id，或同时提供 text + speaker_id")

    payload: Dict[str, Any] = {
        "title": _clean_text(body.title)[:80] or "数字人口播",
        "virtualmanId": virtualman_id,
    }
    if _clean_text(body.callback_url):
        payload["callbackUrl"] = _clean_text(body.callback_url)
    if audio_url:
        payload["audioUrl"] = audio_url
    else:
        payload["text"] = text
        payload["speakerId"] = speaker_id
        payload["speakerExtra"] = {
            "speedRatio": max(0.5, min(float(body.speed_ratio or 1.0), 2.0)),
            "language": _clean_text(body.language) or "zh-CN",
        }

    upstream = await _post("/v1/virtualman/video", body.token, payload)
    data = _data(upstream)
    task_id = _clean_text(data.get("taskId"))
    if not task_id:
        raise HTTPException(status_code=502, detail="闪剪未返回 taskId")
    row = ShanjianDigitalHumanVideoTask(
        user_id=int(current_user.id),
        profile_id=getattr(profile, "id", None),
        title=_clean_text(body.title)[:80] or "数字人口播",
        status="processing",
        task_id=task_id,
        request_id=_clean_text(upstream.get("requestId")),
        virtualman_id=virtualman_id,
        audio_asset_id=audio_asset_id,
        audio_url=audio_url or None,
        speaker_id=speaker_id or None,
        text=text or None,
        submit_payload=payload,
        result_payload=upstream,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "ok": True,
        "task_id": task_id,
        "record": _video_task_to_dict(row),
        "raw": upstream,
    }


@router.post("/api/shanjian-digital-human/video/task")
async def query_video_task(
    body: VideoTaskBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    row = None
    if body.record_id:
        row = db.query(ShanjianDigitalHumanVideoTask).filter(
            ShanjianDigitalHumanVideoTask.id == int(body.record_id),
            ShanjianDigitalHumanVideoTask.user_id == int(current_user.id),
        ).first()
    elif _clean_text(body.task_id):
        row = db.query(ShanjianDigitalHumanVideoTask).filter(
            ShanjianDigitalHumanVideoTask.task_id == _clean_text(body.task_id),
            ShanjianDigitalHumanVideoTask.user_id == int(current_user.id),
        ).first()
    if not row:
        raise HTTPException(status_code=404, detail="未找到对应的闪剪视频任务")

    payload = await _get("/v1/task/info", body.token, {"taskId": row.task_id})
    data = _data(payload)
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    status = _clean_text(data.get("status")) or "processing"
    error_message = _clean_text(data.get("errorMessage") or payload.get("message"))

    row.status = status
    row.request_id = _clean_text(payload.get("requestId")) or row.request_id
    row.video_url = _clean_text(_pick_result_value(result, "videoUrl")) or row.video_url
    row.cover_url = _clean_text(_pick_result_value(result, "coverUrl")) or row.cover_url
    try:
        duration_value = _pick_result_value(result, "duration")
        row.duration = int(duration_value) if duration_value not in (None, "") else row.duration
    except Exception:
        pass
    row.result_payload = payload
    row.error_message = error_message or None
    row.updated_at = datetime.utcnow()

    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "ok": status != "failed",
        "status": status,
        "status_text": _task_status_text(status),
        "video_url": row.video_url or "",
        "cover_url": row.cover_url or "",
        "duration": row.duration,
        "record": _video_task_to_dict(row),
        "message": error_message,
        "raw": payload,
    }
