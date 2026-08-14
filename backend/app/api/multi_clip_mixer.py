from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..models import Asset
from ..services.media_edit_exec import find_ffmpeg, resolve_asset_path
from .assets import _save_bytes_or_tos, _upload_bytes_to_auth_server
from .auth import _ServerUser, get_current_user_for_local

logger = logging.getLogger(__name__)
router = APIRouter()
_POSTER_CACHE_DIR = Path(__file__).resolve().parents[3] / "cache" / "multi_clip_mixer_posters"


class ClipSegment(BaseModel):
    asset_id: str = Field(..., min_length=1, max_length=64)
    start_sec: float = Field(..., ge=0)
    end_sec: float = Field(..., gt=0)


class MultiClipRenderBody(BaseModel):
    clips: List[ClipSegment] = Field(..., min_length=1, max_length=20)
    title: str = Field("多段视频混剪", max_length=80)
    keep_original_audio: bool = True
    audio_asset_id: Optional[str] = Field(default=None, max_length=64)
    target_duration: Optional[float] = Field(default=None, ge=0.1, le=3600)
    audio_volume: float = Field(1.0, ge=0, le=2)
    bgm_url: Optional[str] = None
    bgm_name: Optional[str] = None
    bgm_volume: float = Field(0.24, ge=0, le=1)
    clip_mode: str = Field("fixed", max_length=32)
    output_index: int = Field(1, ge=1, le=50)


def _find_ffprobe(ffmpeg: str) -> str:
    executable = Path(ffmpeg)
    sibling = executable.with_name("ffprobe.exe" if executable.suffix.lower() == ".exe" else "ffprobe")
    if sibling.is_file():
        return str(sibling)
    root = Path(__file__).resolve().parents[3]
    bundled_candidates = (
        root / "deps" / "ffmpeg" / "ffprobe.exe",
        root / "skills" / "comfly_veo3_daihuo_video" / "tools" / "ffmpeg" / "windows" / "ffprobe.exe",
    )
    for candidate in bundled_candidates:
        if candidate.is_file():
            return str(candidate)
    discovered = shutil.which("ffprobe")
    if discovered:
        return discovered
    raise RuntimeError("未找到 ffprobe，无法读取视频时长")


def _run_process(args: List[str], *, timeout: int = 3600) -> None:
    process = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if process.returncode != 0:
        error = (process.stderr or process.stdout or "ffmpeg 执行失败").strip()
        raise RuntimeError(error[-4000:])


def _safe_cache_key(value: str) -> str:
    raw = str(value or "").strip()
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in raw)[:96] or "asset"


def _video_poster_path(asset_id: str, path: Path) -> Path:
    stat = path.stat()
    _POSTER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = _safe_cache_key(asset_id)
    current = _POSTER_CACHE_DIR / f"{safe_id}_{stat.st_size}_{int(stat.st_mtime)}.jpg"
    for old in _POSTER_CACHE_DIR.glob(f"{safe_id}_*.jpg"):
        if old != current:
            try:
                old.unlink()
            except OSError:
                pass
    return current


def _ensure_video_poster(asset_id: str, path: Path) -> Path:
    target = _video_poster_path(asset_id, path)
    if target.is_file() and target.stat().st_size > 0:
        return target
    ffmpeg = find_ffmpeg()
    base_args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    try:
        _run_process(
            base_args
            + [
                "-ss",
                "0.2",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                "scale=640:-2:force_original_aspect_ratio=decrease",
                "-q:v",
                "3",
                str(target),
            ],
            timeout=120,
        )
    except Exception:
        _run_process(
            base_args
            + [
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                "scale=640:-2:force_original_aspect_ratio=decrease",
                "-q:v",
                "3",
                str(target),
            ],
            timeout=120,
        )
    if not target.is_file() or target.stat().st_size <= 0:
        raise RuntimeError("视频封面生成失败")
    return target


@router.get("/api/multi-clip-mixer/assets/{asset_id}/poster.jpg")
def multi_clip_asset_poster(
    asset_id: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    db = SessionLocal()
    download_dir: Optional[Path] = None
    try:
        path, _, media_type = resolve_asset_path(db, current_user.id, asset_id)
        if media_type != "video":
            raise HTTPException(status_code=400, detail="该素材不是视频，无法生成封面")
        if path.parent.name.startswith("media_edit_dl_"):
            download_dir = path.parent
        poster = _ensure_video_poster(asset_id, path)
        return FileResponse(
            str(poster),
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=86400"},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("[multi-clip-mixer] poster failed asset_id=%s user_id=%s err=%s", asset_id, current_user.id, exc)
        raise HTTPException(status_code=500, detail=f"视频封面生成失败：{exc}") from exc
    finally:
        db.close()
        if download_dir:
            shutil.rmtree(download_dir, ignore_errors=True)


def _probe_video(path: Path, ffprobe: str) -> Dict[str, Any]:
    process = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if process.returncode != 0:
        raise RuntimeError((process.stderr or process.stdout or "视频信息读取失败").strip()[-2000:])
    try:
        payload = json.loads(process.stdout or "{}")
        duration = float((payload.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("视频时长读取失败") from exc
    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), {})
    if duration <= 0 or not video_stream:
        raise RuntimeError("素材不是有效视频或视频时长为 0")
    return {
        "duration": duration,
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
        "has_audio": any(item.get("codec_type") == "audio" for item in streams),
    }


def _output_size(width: int, height: int) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        return 1280, 720
    max_side = 1280
    scale = min(1.0, max_side / max(width, height))
    out_width = max(2, int(width * scale) // 2 * 2)
    out_height = max(2, int(height * scale) // 2 * 2)
    return out_width, out_height


def _download_bgm(url: str, work_dir: Path) -> Path:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("音乐模板地址无效")
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}:
        suffix = ".mp3"
    target = work_dir / f"background_music{suffix}"
    with httpx.Client(timeout=180.0, follow_redirects=True, trust_env=False) as client:
        response = client.get(url)
        response.raise_for_status()
        target.write_bytes(response.content)
    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError("音乐模板下载为空")
    return target


def _extend_visual_segments_to_target(segments: List[Dict[str, Any]], target_duration: float) -> float:
    if not segments:
        return 0.0
    clean_target = max(0.0, float(target_duration or 0))
    total = sum(max(0.0, float(item.get("duration") or 0)) for item in segments)
    if clean_target <= 0 or clean_target <= total + 0.01:
        return round(total, 3)

    remaining = clean_target - total
    last = segments[-1]
    last_end = max(0.0, float(last.get("end_sec") or 0))
    source_duration = max(last_end, float(last.get("source_duration") or last_end))
    extend = min(remaining, max(0.0, source_duration - last_end))
    if extend > 0.001:
        last["end_sec"] = round(last_end + extend, 3)
        last["duration"] = round(float(last.get("duration") or 0) + extend, 3)
        remaining -= extend
    if remaining > 0.001:
        last["pad_after"] = round(remaining, 3)
    return round(clean_target, 3)


def _render_local_video(user_id: int, clips: List[ClipSegment], body: MultiClipRenderBody) -> Dict[str, Any]:
    ffmpeg = find_ffmpeg()
    ffprobe = _find_ffprobe(ffmpeg)
    work_dir = Path(tempfile.mkdtemp(prefix="multi_clip_mixer_"))
    resolved_paths: List[Path] = []
    download_dirs: set[Path] = set()
    normalized: List[Dict[str, Any]] = []
    db = SessionLocal()
    try:
        for index, clip in enumerate(clips, start=1):
            path, _, media_type = resolve_asset_path(db, user_id, clip.asset_id)
            if media_type != "video":
                raise ValueError(f"第 {index} 个素材不是视频")
            if path.parent.name.startswith("media_edit_dl_"):
                download_dirs.add(path.parent)
            info = _probe_video(path, ffprobe)
            start = min(float(clip.start_sec), info["duration"])
            end = min(float(clip.end_sec), info["duration"])
            if end - start < 0.1:
                raise ValueError(f"第 {index} 个视频所选片段无效，请重新选择")
            resolved_paths.append(path)
            normalized.append(
                {
                    "asset_id": clip.asset_id,
                    "start_sec": round(start, 3),
                    "end_sec": round(end, 3),
                    "duration": round(end - start, 3),
                    "source_duration": round(float(info["duration"]), 3),
                    "has_audio": bool(info["has_audio"]),
                    "width": int(info["width"]),
                    "height": int(info["height"]),
                }
            )

        audio_source_asset_id = str(body.audio_asset_id or "").strip()
        audio_source_path: Optional[Path] = None
        target_duration = float(body.target_duration or 0)
        if audio_source_asset_id:
            audio_source_path, _, audio_media_type = resolve_asset_path(db, user_id, audio_source_asset_id)
            if audio_media_type != "video":
                raise ValueError("主音轨素材必须是视频")
            if audio_source_path.parent.name.startswith("media_edit_dl_"):
                download_dirs.add(audio_source_path.parent)
            audio_info = _probe_video(audio_source_path, ffprobe)
            if not bool(audio_info["has_audio"]):
                raise ValueError("标记为主音轨的视频没有可用音频")
            audio_duration = float(audio_info["duration"] or 0)
            if target_duration <= 0 or target_duration > audio_duration:
                target_duration = audio_duration

        if target_duration > 0:
            _extend_visual_segments_to_target(normalized, target_duration)

        target_width, target_height = _output_size(normalized[0]["width"], normalized[0]["height"])
        command: List[str] = [ffmpeg, "-y"]
        for path in resolved_paths:
            command.extend(["-i", str(path)])

        filters: List[str] = []
        concat_inputs: List[str] = []
        use_main_audio = audio_source_path is not None
        for index, item in enumerate(normalized):
            start = item["start_sec"]
            end = item["end_sec"]
            duration = item["duration"]
            pad_after = max(0.0, float(item.get("pad_after") or 0))
            video_filter = (
                f"[{index}:v:0]trim=start={start}:end={end},setpts=PTS-STARTPTS,"
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black,"
            )
            if pad_after > 0.001:
                video_filter += f"tpad=stop_mode=clone:stop_duration={pad_after},"
            video_filter += "setsar=1,fps=30,format=yuv420p[v%d]" % index
            filters.append(video_filter)
            if use_main_audio:
                concat_inputs.append(f"[v{index}]")
                continue
            filters.append(
                (
                    f"[{index}:a:0]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,"
                    "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a%d]"
                    % index
                )
                if body.keep_original_audio and item["has_audio"]
                else (
                    f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration={duration},"
                    "asetpts=PTS-STARTPTS[a%d]" % index
                )
            )
            concat_inputs.extend([f"[v{index}]", f"[a{index}]"])

        if use_main_audio:
            filters.append("".join(concat_inputs) + f"concat=n={len(normalized)}:v=1:a=0[vout]")
            merged_path = work_dir / "merged.mp4"
            command.extend(
                [
                    "-filter_complex",
                    ";".join(filters),
                    "-map",
                    "[vout]",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "22",
                    "-movflags",
                    "+faststart",
                    str(merged_path),
                ]
            )
        else:
            filters.append("".join(concat_inputs) + f"concat=n={len(normalized)}:v=1:a=1[vout][aout]")
            merged_path = work_dir / "merged.mp4"
            command.extend(
                [
                    "-filter_complex",
                    ";".join(filters),
                    "-map",
                    "[vout]",
                    "-map",
                    "[aout]",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "22",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "160k",
                    "-movflags",
                    "+faststart",
                    str(merged_path),
                ]
            )
        _run_process(command)

        final_path = merged_path
        bgm_url = str(body.bgm_url or "").strip()
        bgm_path = _download_bgm(bgm_url, work_dir) if bgm_url else None
        if use_main_audio and audio_source_path is not None:
            mixed_path = work_dir / "merged_with_marked_audio.mp4"
            final_duration = max(0.1, float(target_duration or 0))
            mix_command = [
                ffmpeg,
                "-y",
                "-i",
                str(merged_path),
                "-i",
                str(audio_source_path),
            ]
            if bgm_path:
                mix_command.extend(["-stream_loop", "-1", "-i", str(bgm_path)])
                audio_filter = (
                    f"[1:a:0]atrim=start=0:duration={final_duration},asetpts=PTS-STARTPTS,volume={body.audio_volume},"
                    "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[maina];"
                    f"[2:a:0]atrim=duration={final_duration},asetpts=PTS-STARTPTS,volume={body.bgm_volume},"
                    "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[bgm];"
                    "[maina][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]"
                )
            else:
                audio_filter = (
                    f"[1:a:0]atrim=start=0:duration={final_duration},asetpts=PTS-STARTPTS,volume={body.audio_volume},"
                    "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[aout]"
                )
            mix_command.extend(
                [
                    "-filter_complex",
                    audio_filter,
                    "-map",
                    "0:v:0",
                    "-map",
                    "[aout]",
                    "-t",
                    str(round(final_duration, 3)),
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "160k",
                    "-movflags",
                    "+faststart",
                    str(mixed_path),
                ]
            )
            _run_process(mix_command)
            final_path = mixed_path
        elif bgm_path:
            mixed_path = work_dir / "merged_with_bgm.mp4"
            if body.keep_original_audio:
                audio_filter = (
                    f"[0:a:0]volume=1[original];[1:a:0]volume={body.bgm_volume},aresample=48000[bgm];"
                    "[original][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]"
                )
            else:
                audio_filter = f"[1:a:0]volume={body.bgm_volume},aresample=48000[aout]"
            _run_process(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(merged_path),
                    "-stream_loop",
                    "-1",
                    "-i",
                    str(bgm_path),
                    "-filter_complex",
                    audio_filter,
                    "-map",
                    "0:v:0",
                    "-map",
                    "[aout]",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "160k",
                    "-shortest",
                    "-movflags",
                    "+faststart",
                    str(mixed_path),
                ]
            )
            final_path = mixed_path

        output_info = _probe_video(final_path, ffprobe)
        return {
            "data": final_path.read_bytes(),
            "segments": normalized,
            "duration": round(float(output_info["duration"]), 3),
            "width": target_width,
            "height": target_height,
            "audio_source_asset_id": audio_source_asset_id,
            "audio_source": "marked_video" if audio_source_asset_id else "segments",
            "target_duration": round(float(target_duration), 3) if target_duration > 0 else None,
        }
    finally:
        db.close()
        for directory in download_dirs:
            shutil.rmtree(directory, ignore_errors=True)
        shutil.rmtree(work_dir, ignore_errors=True)


@router.post("/api/multi-clip-mixer/render")
async def render_multi_clip_video(
    body: MultiClipRenderBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    for index, clip in enumerate(body.clips, start=1):
        if clip.end_sec <= clip.start_sec:
            raise HTTPException(status_code=400, detail=f"第 {index} 个视频的结束时间必须大于开始时间")

    try:
        rendered = await asyncio.to_thread(_render_local_video, current_user.id, body.clips, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("[multi-clip-mixer] render failed user_id=%s", current_user.id)
        raise HTTPException(status_code=500, detail=f"视频拼接失败：{exc}") from exc

    data = rendered.pop("data")
    asset_id, filename, file_size, source_url = await asyncio.to_thread(
        _save_bytes_or_tos, data, ".mp4", "video/mp4"
    )
    upload_diag: Dict[str, Any] = {}
    if not source_url:
        source_url, upload_diag = await _upload_bytes_to_auth_server(
            data,
            filename,
            "video/mp4",
            request,
            timeout=240.0,
        )

    row = Asset(
        asset_id=asset_id,
        user_id=current_user.id,
        filename=filename,
        media_type="video",
        file_size=file_size,
        source_url=source_url,
        prompt="multi_clip_mixer",
        model="local:ffmpeg-multi-clip",
        tags="multi_clip_mixer,video",
        meta={
            "title": (body.title or "多段视频混剪").strip()[:80],
            "segments": rendered["segments"],
            "keep_original_audio": body.keep_original_audio,
            "audio_source_asset_id": rendered.get("audio_source_asset_id") or "",
            "audio_source": rendered.get("audio_source") or "segments",
            "target_duration": rendered.get("target_duration"),
            "clip_mode": (body.clip_mode or "fixed").strip()[:32],
            "output_index": body.output_index,
            "bgm_name": (body.bgm_name or "").strip()[:120],
            "bgm_url": (body.bgm_url or "").strip()[:1000],
            "bgm_volume": body.bgm_volume,
        },
    )
    try:
        db.add(row)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "ok": True,
        "asset_id": asset_id,
        "source_url": source_url or "",
        "preview_url": f"/api/assets/{asset_id}/content",
        "file_size": file_size,
        "duration": rendered["duration"],
        "width": rendered["width"],
        "height": rendered["height"],
        "segments": rendered["segments"],
        "public_upload_warning": "" if source_url else str(upload_diag.get("error") or "成片已保存在本机，但暂时没有公网链接"),
    }
