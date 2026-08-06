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
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..models import Asset
from ..services.media_edit_exec import find_ffmpeg, resolve_asset_path
from .assets import _save_bytes_or_tos, _upload_bytes_to_auth_server
from .auth import _ServerUser, get_current_user_for_local

logger = logging.getLogger(__name__)
router = APIRouter()


class ClipSegment(BaseModel):
    asset_id: str = Field(..., min_length=1, max_length=64)
    start_sec: float = Field(..., ge=0)
    end_sec: float = Field(..., gt=0)


class MultiClipRenderBody(BaseModel):
    clips: List[ClipSegment] = Field(..., min_length=1, max_length=20)
    title: str = Field("多段视频混剪", max_length=80)
    keep_original_audio: bool = True
    bgm_url: Optional[str] = None
    bgm_name: Optional[str] = None
    bgm_volume: float = Field(0.24, ge=0, le=1)


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

        target_width, target_height = _output_size(normalized[0]["width"], normalized[0]["height"])
        command: List[str] = [ffmpeg, "-y"]
        for path in resolved_paths:
            command.extend(["-i", str(path)])

        filters: List[str] = []
        concat_inputs: List[str] = []
        for index, item in enumerate(normalized):
            start = item["start_sec"]
            end = item["end_sec"]
            duration = item["duration"]
            filters.append(
                f"[{index}:v:0]trim=start={start}:end={end},setpts=PTS-STARTPTS,"
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black,"
                "setsar=1,fps=30,format=yuv420p[v%d]" % index
            )
            if body.keep_original_audio and item["has_audio"]:
                filters.append(
                    f"[{index}:a:0]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,"
                    "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a%d]"
                    % index
                )
            else:
                filters.append(
                    f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration={duration},"
                    "asetpts=PTS-STARTPTS[a%d]" % index
                )
            concat_inputs.extend([f"[v{index}]", f"[a{index}]"])

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
        if bgm_url:
            bgm_path = _download_bgm(bgm_url, work_dir)
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
