"""Local video compatibility helpers for the Online desktop client."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict

from .media_edit_exec import find_ffmpeg


def find_ffprobe(ffmpeg: str = "") -> str:
    ffmpeg_path = Path(ffmpeg) if ffmpeg else None
    candidates = []
    if ffmpeg_path:
        candidates.append(ffmpeg_path.with_name("ffprobe.exe" if ffmpeg_path.suffix.lower() == ".exe" else "ffprobe"))
    root = Path(__file__).resolve().parents[3]
    candidates.extend(
        (
            shutil.which("ffprobe"),
            shutil.which("ffprobe.exe"),
            root / "deps" / "ffmpeg" / "ffprobe.exe",
            root / "deps" / "ffmpeg" / "ffprobe",
            root / "skills" / "comfly_veo3_daihuo_video" / "tools" / "ffmpeg" / "windows" / "ffprobe.exe",
        )
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return ""


def probe_video(path: Path) -> Dict[str, Any]:
    try:
        ffmpeg = find_ffmpeg()
    except (OSError, RuntimeError):
        return {}
    ffprobe = find_ffprobe(ffmpeg)
    if not ffprobe:
        return {}
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=format_name:stream=codec_type,codec_name,pix_fmt",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        return {}
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    video = next((row for row in streams if row.get("codec_type") == "video"), {})
    audio = next((row for row in streams if row.get("codec_type") == "audio"), {})
    return {
        "format": str((payload.get("format") or {}).get("format_name") or "").lower(),
        "video_codec": str(video.get("codec_name") or "").lower(),
        "pixel_format": str(video.get("pix_fmt") or "").lower(),
        "audio_codec": str(audio.get("codec_name") or "").lower(),
        "has_video": bool(video),
    }


def is_browser_compatible(info: Dict[str, Any]) -> bool:
    formats = set(str(info.get("format") or "").split(","))
    return bool(
        info.get("has_video")
        and "mp4" in formats
        and info.get("video_codec") == "h264"
        and info.get("pixel_format") in {"yuv420p", "yuvj420p"}
        and info.get("audio_codec") in {"", "aac", "mp3"}
    )


def transcode_video(source: Path, target: Path) -> bool:
    try:
        ffmpeg = find_ffmpeg()
    except (OSError, RuntimeError):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    # Keep .mp4 as the final suffix; ffmpeg uses the last suffix to select
    # the muxer and would otherwise treat ``output.mp4.part`` as unknown.
    partial = target.parent / f".{target.stem}.part{target.suffix}"
    try:
        partial.unlink()
    except OSError:
        pass
    proc = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(partial),
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if proc.returncode != 0 or not partial.is_file() or partial.stat().st_size <= 0:
        try:
            partial.unlink()
        except OSError:
            pass
        return False
    partial.replace(target)
    return True


def create_poster(source: Path, target: Path) -> bool:
    try:
        ffmpeg = find_ffmpeg()
    except (OSError, RuntimeError):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    attempts = [
        ["-ss", "0.2", "-i", str(source)],
        ["-i", str(source)],
    ]
    for prefix in attempts:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                *prefix,
                "-frames:v",
                "1",
                "-vf",
                "scale=640:-2:force_original_aspect_ratio=decrease",
                "-q:v",
                "3",
                str(target),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0 and target.is_file() and target.stat().st_size > 0:
            return True
    return False


def prepare_playback(source: Path, target: Path) -> bool:
    """Copy compatible MP4 input or convert it to the WebView-safe profile."""
    if is_browser_compatible(probe_video(source)):
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.parent / f".{target.stem}.part{target.suffix}"
        shutil.copyfile(source, partial)
        partial.replace(target)
        return True
    return transcode_video(source, target)
