from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
import re
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
_PLAYBACK_CACHE_DIR = Path(__file__).resolve().parents[3] / "cache" / "multi_clip_mixer_playback"


class ClipSegment(BaseModel):
    asset_id: str = Field(..., min_length=1, max_length=64)
    start_sec: float = Field(..., ge=0)
    end_sec: float = Field(..., gt=0)


class MultiClipRenderBody(BaseModel):
    clips: List[ClipSegment] = Field(..., min_length=1, max_length=20)
    title: str = Field("剪辑大师", max_length=80)
    keep_original_audio: bool = True
    audio_asset_id: Optional[str] = Field(default=None, max_length=64)
    target_duration: Optional[float] = Field(default=None, ge=0.1, le=3600)
    audio_volume: float = Field(1.0, ge=0, le=2)
    bgm_url: Optional[str] = None
    bgm_name: Optional[str] = None
    bgm_volume: float = Field(0.24, ge=0, le=1)
    clip_mode: str = Field("fixed", max_length=32)
    output_index: int = Field(1, ge=1, le=50)
    # 2026-10-15: 成片画布与填充方式交给用户选。
    # output_ratio 例 "9:16" / "16:9"；空 = 跟随第一个片段（旧行为）。
    output_ratio: str = Field("", max_length=16)
    # fill_mode: cover = 铺满（裁切） / blur = 模糊铺底 + 完整画面 / fit = 完整 + 黑边；空 = 旧行为
    fill_mode: str = Field("", max_length=16)
    # 中间产物（后面还会套模板/拼接）：标记后不进「内容记录」，避免和最终成片混在一起
    intermediate: bool = False


MULTI_CLIP_OUTPUT_CREDITS = 100.0


async def _charge_multi_clip_output(
    request: Request,
    current_user: _ServerUser,
    *,
    ref_id: str,
    title: str,
) -> None:
    """每产出一条最终成片扣 100 积分（认证中心记账，幂等键=ref_id）。"""
    from ..core.config import get_settings

    settings = get_settings()
    base = (settings.auth_server_base or "").strip().rstrip("/")
    auth = (request.headers.get("Authorization") or "").strip()
    if not base or not auth or not ref_id:
        return
    headers = {
        "Authorization": auth if auth.lower().startswith("bearer ") else f"Bearer {auth}",
        "Content-Type": "application/json",
    }
    internal_key = (getattr(settings, "lobster_mcp_billing_internal_key", "") or "").strip()
    if internal_key:
        headers["X-Lobster-Mcp-Billing"] = internal_key
    payload = {
        "feature": "multi_clip_mixer",
        "credits": MULTI_CLIP_OUTPUT_CREDITS,
        "ref_id": ref_id,
        "description": ("剪辑大师成片：" + str(title or ""))[:180],
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
            resp = await client.post(f"{base}/api/billing/feature-charge", json=payload, headers=headers)
    except Exception:
        logger.warning("[multi-clip-mixer] charge failed ref=%s", ref_id, exc_info=True)
        return
    if resp.status_code == 402:
        detail = "积分不足：本次成片需要 %d 积分" % int(MULTI_CLIP_OUTPUT_CREDITS)
        try:
            detail = (resp.json() or {}).get("detail") or detail
        except Exception:
            pass
        raise HTTPException(status_code=402, detail=detail)
    if resp.status_code >= 400:
        logger.warning(
            "[multi-clip-mixer] charge rejected ref=%s status=%s body=%s",
            ref_id,
            resp.status_code,
            resp.text[:200],
        )


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


def _video_playback_path(asset_id: str, path: Path) -> Path:
    stat = path.stat()
    _PLAYBACK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = _safe_cache_key(asset_id)
    current = _PLAYBACK_CACHE_DIR / f"{safe_id}_{stat.st_size}_{int(stat.st_mtime)}.mp4"
    for old in _PLAYBACK_CACHE_DIR.glob(f"{safe_id}_*.mp4"):
        if old != current:
            try:
                old.unlink()
            except OSError:
                pass
    return current


def _ensure_video_playback(asset_id: str, path: Path) -> Path:
    """Create an H.264/AAC proxy that Chromium/WebView can always decode."""
    target = _video_playback_path(asset_id, path)
    if target.is_file() and target.stat().st_size > 0:
        return target
    ffmpeg = find_ffmpeg()
    _run_process(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
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
            str(target),
        ],
        timeout=900,
    )
    if not target.is_file() or target.stat().st_size <= 0:
        raise RuntimeError("视频兼容预览生成失败")
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


@router.get("/api/multi-clip-mixer/assets/{asset_id}/playback.mp4")
def multi_clip_asset_playback(
    asset_id: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    db = SessionLocal()
    download_dir: Optional[Path] = None
    try:
        path, _, media_type = resolve_asset_path(db, current_user.id, asset_id)
        if media_type != "video":
            raise HTTPException(status_code=400, detail="该素材不是视频，无法预览")
        if path.parent.name.startswith("media_edit_dl_"):
            download_dir = path.parent
        playback = _ensure_video_playback(asset_id, path)
        return FileResponse(
            str(playback),
            media_type="video/mp4",
            headers={"Cache-Control": "private, max-age=86400", "Accept-Ranges": "bytes"},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[multi-clip-mixer] playback proxy failed asset_id=%s user_id=%s", asset_id, current_user.id)
        raise HTTPException(status_code=500, detail=f"视频兼容预览生成失败：{exc}") from exc
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


_RATIO_RE = re.compile(r"^\s*(\d{1,3})\s*:\s*(\d{1,3})\s*$")


def _ratio_pair(ratio: str) -> tuple[int, int] | None:
    match = _RATIO_RE.match(str(ratio or ""))
    if not match:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if not (1 <= width <= 100 and 1 <= height <= 100):
        return None
    return width, height


def _output_size(width: int, height: int, *, ratio: str = "") -> tuple[int, int]:
    """画布尺寸：给了 ratio（如 9:16）就按它算（长边 1280）；否则跟随第一个片段（旧行为）。"""
    pair = _ratio_pair(ratio)
    if pair:
        ratio_w, ratio_h = pair
        scale = 1280.0 / max(ratio_w, ratio_h)
        out_width = max(2, int(round(ratio_w * scale)) // 2 * 2)
        out_height = max(2, int(round(ratio_h * scale)) // 2 * 2)
        return out_width, out_height
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


def _extend_visual_segments_to_target(
    segments: List[Dict[str, Any]], target_duration: float, *, allow_trim: bool = False
) -> float:
    """把片段总时长对齐到 target_duration。

    - 总时长 < 目标：延长最后一段（不够则 clone 末帧补齐）—— 旧行为；
    - 总时长 > 目标且 allow_trim=True：按比例压缩每段，填满目标时长后丢弃多余片段。
      用户显式填「目标时长」时才会裁剪，避免主音轨对齐时把用户素材剪短。
    """
    if not segments:
        return 0.0
    clean_target = max(0.0, float(target_duration or 0))
    total = sum(max(0.0, float(item.get("duration") or 0)) for item in segments)
    if clean_target <= 0:
        return round(total, 3)
    if total > clean_target + 0.01:
        if not allow_trim:
            return round(total, 3)
        ratio = clean_target / total
        acc = 0.0
        kept: List[Dict[str, Any]] = []
        for item in segments:
            remain = clean_target - acc
            if remain <= 0.05:
                break
            d = max(0.0, float(item.get("duration") or 0))
            want = max(0.5, d * ratio) if d > 0 else 0.0
            new_d = min(remain, want)
            if new_d < 0.3:
                break
            item["end_sec"] = round(float(item.get("start_sec") or 0) + new_d, 3)
            item["duration"] = round(new_d, 3)
            acc += new_d
            kept.append(item)
        segments[:] = kept
        return round(acc, 3)
    if clean_target <= total + 0.01:
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
    logger.info(
        "[multi-clip] render user=%s clips=%d keep_original_audio=%s audio_asset=%s bgm=%s bgm_volume=%s ratio=%s fill=%s",
        user_id, len(clips or []), bool(getattr(body, "keep_original_audio", None)),
        str(getattr(body, "audio_asset_id", "") or ""), bool(str(getattr(body, "bgm_url", "") or "").strip()),
        float(getattr(body, "bgm_volume", 0) or 0), str(getattr(body, "output_ratio", "") or ""),
        str(getattr(body, "fill_mode", "") or ""),
    )
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
        user_target = float(body.target_duration or 0)
        target_duration = user_target
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
            if target_duration <= 0:
                # 没填目标时长：跟随主音轨（只延长，不裁剪，保持旧行为）
                target_duration = audio_duration

        if target_duration > 0:
            _extend_visual_segments_to_target(
                normalized, target_duration, allow_trim=user_target > 0
            )

        target_width, target_height = _output_size(
            normalized[0]["width"], normalized[0]["height"], ratio=body.output_ratio
        )
        fill_mode = str(body.fill_mode or "").strip().lower()
        if fill_mode not in {"cover", "blur", "fit"}:
            fill_mode = "cover" if _ratio_pair(body.output_ratio) else "fit"
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
            head = f"[{index}:v:0]trim=start={start}:end={end},setpts=PTS-STARTPTS,"
            tail = f"tpad=stop_mode=clone:stop_duration={pad_after}," if pad_after > 0.001 else ""
            tail += "setsar=1,fps=30,format=yuv420p[v%d]" % index
            if fill_mode == "cover":
                # 铺满（裁切）：先按长边放到盖住画布，再从中间裁
                video_filter = (
                    head
                    + f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
                    + f"crop={target_width}:{target_height},"
                    + tail
                )
            elif fill_mode == "blur":
                # 模糊铺底：同一张画面放大模糊做背景，前景完整居中
                video_filter = (
                    head
                    + f"split=2[b{index}][f{index}];"
                    + f"[b{index}]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
                    + f"crop={target_width}:{target_height},boxblur=luma_radius=24:luma_power=1,"
                    + f"eq=brightness=-0.08[bg{index}];"
                    + f"[f{index}]scale={target_width}:{target_height}:force_original_aspect_ratio=decrease[fg{index}];"
                    + f"[bg{index}][fg{index}]overlay=(W-w)/2:(H-h)/2,"
                    + tail
                )
            else:
                # 旧行为：完整画面 + 黑边
                video_filter = (
                    head
                    + f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
                    + f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black,"
                    + tail
                )
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


class MultiClipConcatBody(BaseModel):
    """把多段「已套好模板」的成片按顺序拼成一条。"""

    asset_ids: List[str] = Field(..., min_length=1, max_length=20)
    output_ratio: str = Field("", max_length=16)
    fill_mode: str = Field("", max_length=16)
    title: str = Field("剪辑大师", max_length=80)
    # 每段期望时长（与 asset_ids 对齐，可空）。给了就按它裁，防止模板把片段补齐变长
    clip_lengths: List[float] = Field(default_factory=list, max_length=20)


@router.post("/api/multi-clip-mixer/concat")
async def concat_multi_clip_videos(
    body: MultiClipConcatBody,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    """每段单独套模板后，把多段成片按顺序拼接（统一比例/填充方式）。"""
    ffmpeg = find_ffmpeg()
    ffprobe = _find_ffprobe(ffmpeg)
    clips: List[ClipSegment] = []
    for index, asset_id in enumerate(body.asset_ids, start=1):
        path, _, media_type = resolve_asset_path(db, current_user.id, asset_id)
        if media_type != "video":
            raise HTTPException(status_code=400, detail=f"第 {index} 段不是视频")
        info = _probe_video(path, ffprobe)
        duration = float(info["duration"] or 0)
        if duration <= 0.1:
            raise HTTPException(status_code=400, detail=f"第 {index} 段时长为 0，无法拼接")
        want = 0.0
        if index - 1 < len(body.clip_lengths):
            want = float(body.clip_lengths[index - 1] or 0)
        end = duration
        if want > 0.1:
            end = min(duration, want)
        clips.append(ClipSegment(asset_id=asset_id, start_sec=0, end_sec=round(end, 3)))

    render_body = MultiClipRenderBody(
        clips=clips,
        title=body.title,
        keep_original_audio=False,
        output_ratio=body.output_ratio,
        fill_mode=body.fill_mode,
    )
    try:
        rendered = await asyncio.to_thread(_render_local_video, current_user.id, clips, render_body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("[multi-clip-mixer] concat failed user_id=%s", current_user.id)
        raise HTTPException(status_code=500, detail=f"视频拼接失败：{exc}") from exc

    data = rendered.pop("data")
    asset_id, filename, file_size, source_url = await asyncio.to_thread(
        _save_bytes_or_tos, data, ".mp4", "video/mp4"
    )
    upload_diag: Dict[str, Any] = {}
    if not source_url:
        source_url, upload_diag = await _upload_bytes_to_auth_server(
            data, filename, "video/mp4", request, timeout=240.0
        )

    row = Asset(
        asset_id=asset_id,
        user_id=current_user.id,
        filename=filename,
        media_type="video",
        file_size=file_size,
        source_url=source_url,
        prompt="multi_clip_mixer_concat",
        model="local:ffmpeg-multi-clip-concat",
        tags="multi_clip_mixer,video",
        meta={
            "title": (body.title or "剪辑大师").strip()[:80],
            "segments": rendered["segments"],
            "concat_of": list(body.asset_ids),
            "output_ratio": body.output_ratio,
            "fill_mode": body.fill_mode,
        },
    )
    try:
        db.add(row)
        db.commit()
    except Exception:
        db.rollback()
        raise

    await _charge_multi_clip_output(
        request,
        current_user,
        ref_id=f"mcm:{asset_id}",
        title=body.title or "剪辑大师",
    )
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
            "title": (body.title or "剪辑大师").strip()[:80],
            "segments": rendered["segments"],
            "keep_original_audio": body.keep_original_audio,
            "audio_source_asset_id": rendered.get("audio_source_asset_id") or "",
            "audio_source": rendered.get("audio_source") or "segments",
            "target_duration": rendered.get("target_duration"),
            "clip_mode": (body.clip_mode or "fixed").strip()[:32],
            "output_index": body.output_index,
            "content_visibility": "intermediate" if body.intermediate else "",
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

    if not body.intermediate:
        await _charge_multi_clip_output(
            request,
            current_user,
            ref_id=f"mcm:{asset_id}",
            title=body.title or "剪辑大师",
        )
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


# AI 智能优化：长视频按音频剪掉无用段
_AI_TRIM_DIR = Path(__file__).resolve().parents[3] / "data" / "multi_clip_ai_trim"
_AI_TRIM_JOBS: Dict[str, Dict[str, Any]] = {}
_AI_TRIM_LOCK = threading.Lock()


class AiTrimBody(BaseModel):
    asset_id: str = ""
    video_url: str = ""
    target_seconds: float = 0
    mode: str = "smart"
    auto_color: bool = True
    color_style: str = ""


def _ai_set_status(job_id: str, **fields: Any) -> None:
    with _AI_TRIM_LOCK:
        job = _AI_TRIM_JOBS.setdefault(job_id, {})
        job.update(fields)
        snapshot = dict(job)
    try:
        if job_id != "tools":
            job_dir = _AI_TRIM_DIR / job_id
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / "status.json").write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _detect_silences(ffmpeg: str, path: Path) -> List[tuple[float, float]]:
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostdin", "-i", str(path), "-vn",
         "-af", "silencedetect=noise=-32dB:d=0.55", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    text = (proc.stderr or "") + (proc.stdout or "")
    starts = [float(x) for x in re.findall(r"silence_start:\s*([0-9.]+)", text)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", text)]
    out: List[tuple[float, float]] = []
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else None
        if end is not None and end > start:
            out.append((start, end))
    return out


def _keep_ranges(duration: float, silences: List[tuple[float, float]], target: float) -> List[tuple[float, float]]:
    ranges: List[tuple[float, float]] = []
    cursor = 0.0
    for start, end in sorted(silences):
        if start > cursor:
            ranges.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        ranges.append((cursor, duration))
    padded: List[tuple[float, float]] = []
    for start, end in ranges:
        start = max(0.0, start - 0.12)
        end = min(duration, end + 0.12)
        if end - start >= 0.35:
            padded.append((start, end))
    merged: List[tuple[float, float]] = []
    for start, end in padded:
        if merged and start - merged[-1][1] < 0.05:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    if target <= 0:
        return merged
    kept: List[tuple[float, float]] = []
    total = 0.0
    for start, end in merged:
        if total >= target:
            break
        remain = target - total
        if end - start <= remain:
            kept.append((start, end))
            total += end - start
        else:
            kept.append((start, start + remain))
            total = target
            break
    return kept or merged


def _render_ai_trim(ffmpeg: str, src: Path, ranges: List[tuple[float, float]], out: Path, has_audio: bool, extra_filter: str = "") -> None:
    parts_v: List[str] = []
    parts_a: List[str] = []
    for i, (start, end) in enumerate(ranges):
        parts_v.append(f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{i}]")
        if has_audio:
            parts_a.append(f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{i}]")
    concat_inputs = "".join(f"[v{i}][a{i}]" if has_audio else f"[v{i}]" for i in range(len(ranges)))
    graph = ";".join(parts_v + parts_a)
    graph += f";{concat_inputs}concat=n={len(ranges)}:v=1:a={1 if has_audio else 0}[vout]" + ("[aout]" if has_audio else "")
    if extra_filter:
        graph = graph.replace("[vout]", "[vpre]") + f";[vpre]{extra_filter}[vout]"
    args = [ffmpeg, "-y", "-hide_banner", "-nostdin", "-i", str(src), "-filter_complex", graph, "-map", "[vout]"]
    if has_audio:
        args += ["-map", "[aout]", "-c:a", "aac", "-b:a", "128k"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-movflags", "+faststart", str(out)]
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError("AI 优化渲染失败: " + (proc.stderr or "")[-300:])


FILLER_ONLY = re.compile(r"^[\s，。,.!！?？、~～…嗯啊哦呃额那个这个就是然后呢吧嘛哈]+$")


def _score_sentences(captions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按信息量给每句打分；分数 <= 0 表示可以删掉。"""
    out: List[Dict[str, Any]] = []
    seen: List[str] = []
    for cap in captions:
        text = str(cap.get("text") or "").strip()
        start = float(cap.get("start") or 0) / 1000.0
        end = float(cap.get("end") or 0) / 1000.0
        if end <= start:
            continue
        score = 1.0
        compact = re.sub(r"\s+", "", text)
        if len(compact) < 3 or FILLER_ONLY.match(compact):
            score -= 3.0
        if compact in seen:
            score -= 2.5
        elif any(compact and compact in prev for prev in seen[-3:]):
            score -= 1.5
        if re.search(r"\d", compact):
            score += 0.5
        if len(compact) >= 12:
            score += 0.3
        seen.append(compact)
        out.append({"text": text, "start": start, "end": end, "score": round(score, 2)})
    return out


def _pick_sentences(scored: List[Dict[str, Any]], target: float) -> List[Dict[str, Any]]:
    kept = [s for s in scored if s["score"] > 0]
    if not kept:
        kept = scored
    kept.sort(key=lambda s: s["start"])
    if target and target > 0:
        limit = target * 1.3
        total = sum(s["end"] - s["start"] for s in kept)
        if total > limit:
            for weak in sorted(kept, key=lambda s: s["score"]):
                if total <= limit:
                    break
                kept.remove(weak)
                total -= weak["end"] - weak["start"]
            kept.sort(key=lambda s: s["start"])
    return kept


_COLOR_PRESETS = {
    "fresh": "eq=brightness=0.045:contrast=1.05:saturation=1.10,unsharp=5:5:0.6",
    "soft": "eq=brightness=0.035:contrast=0.96:saturation=1.02,gblur=sigma=0.4,unsharp=5:5:0.4",
    "cinema": "eq=contrast=1.10:saturation=0.94:gamma=0.98,colorbalance=rs=0.03:bs=0.04:rh=0.02:bh=-0.02",
    "mono": "hue=s=0,eq=contrast=1.06",
}


def _auto_color_filter(ffmpeg: str, path: Path) -> str:
    """按平均亮度/对比度/饱和度生成温和调色；已经够亮够艳就不动。"""
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostdin", "-i", str(path), "-vf",
         "select='not(mod(n,45))',signalstats,metadata=print:file=-", "-frames:v", "12", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    yavg = [float(x) for x in re.findall(r"lavfi\.signalstats\.YAVG=([0-9.]+)", text)]
    yhigh = [float(x) for x in re.findall(r"lavfi\.signalstats\.YHIGH=([0-9.]+)", text)]
    ylow = [float(x) for x in re.findall(r"lavfi\.signalstats\.YLOW=([0-9.]+)", text)]
    sat = [float(x) for x in re.findall(r"lavfi\.signalstats\.SATAVG=([0-9.]+)", text)]
    if not yavg:
        return ""
    mean_y = sum(yavg) / len(yavg)
    brightness = min(0.08, (90 - mean_y) / 255.0 * 0.9) if mean_y < 90 else 0.0
    spread = (sum(yhigh) / len(yhigh) - sum(ylow) / len(ylow)) if (yhigh and ylow) else 128.0
    contrast = min(1.12, 1.0 + (110 - spread) / 255.0) if spread < 110 else 1.0
    mean_sat = (sum(sat) / len(sat)) if sat else 60.0
    saturation = min(1.12, 1.0 + (55 - mean_sat) / 120.0) if mean_sat < 55 else 1.0
    if abs(brightness) < 0.005 and abs(contrast - 1.0) < 0.005 and abs(saturation - 1.0) < 0.005:
        return ""
    return "eq=brightness=%.3f:contrast=%.3f:saturation=%.3f,unsharp=5:5:0.6" % (brightness, contrast, saturation)


def _visual_ranges(ffmpeg: str, path: Path, duration: float) -> List[tuple[float, float]]:
    """没有有效语音时按画面判断：去掉黑场和冻结画面。"""
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostdin", "-i", str(path),
         "-vf", "blackdetect=d=0.3:pix_th=0.10,freezedetect=n=0.003:d=0.8", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    text = (proc.stderr or "") + (proc.stdout or "")
    bad: List[tuple[float, float]] = []
    for a, b in re.findall(r"black_start:([0-9.]+)\s+black_end:([0-9.]+)", text):
        bad.append((float(a), float(b)))
    for a, b in re.findall(r"freeze_start:\s*([0-9.]+)\s+freeze_end:\s*([0-9.]+)", text):
        bad.append((float(a), float(b)))
    ranges: List[tuple[float, float]] = []
    cursor = 0.0
    for start, end in sorted(bad):
        if start > cursor:
            ranges.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        ranges.append((cursor, duration))
    merged = [(max(0.0, a - 0.1), min(duration, b + 0.1)) for a, b in ranges if b - a >= 0.4]
    out: List[tuple[float, float]] = []
    for start, end in merged:
        if out and start - out[-1][1] < 0.05:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out or [(0.0, duration)]


def _merge_ranges(items: List[Dict[str, Any]], gap: float = 0.35) -> List[tuple[float, float]]:
    ranges: List[tuple[float, float]] = []
    for item in items:
        start, end = float(item["start"]), float(item["end"])
        if ranges and start - ranges[-1][1] <= gap:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
        else:
            ranges.append((start, end))
    return ranges


def _ai_trim_worker(job_id: str, user_id: int, path: Path, target: float, mode: str = 'fast', auth_headers: Optional[Dict[str, str]] = None, auto_color: bool = True, color_style: str = "") -> None:
    try:
        _ai_set_status(job_id, status="running", progress=5, message="正在分析音频")
        ffmpeg = find_ffmpeg()
        ffprobe = _find_ffprobe(ffmpeg)
        info = _probe_video(path, ffprobe)
        duration = float(info.get("duration") or 0)
        has_audio = bool(info.get("has_audio"))
        if duration <= 0:
            raise RuntimeError("素材时长读取失败")
        if str(mode or "") == "visual":
            job_dir = _AI_TRIM_DIR / job_id
            job_dir.mkdir(parents=True, exist_ok=True)
            out = job_dir / "output.mp4"
            _ai_set_status(job_id, progress=40, message="正在检测画面并分析色调")
            ranges = _visual_ranges(ffmpeg, path, duration)
            style = str(color_style or "").strip().lower()
            color_filter = _COLOR_PRESETS.get(style, "")
            if style == "auto":
                color_filter = _auto_color_filter(ffmpeg, path)
            _ai_set_status(job_id, color_style=style, color_filter=color_filter)
            _render_ai_trim(ffmpeg, path, ranges, out, has_audio, color_filter)
            ae = "visual"
        if not has_audio and str(mode or "") != "visual":
            raise RuntimeError("素材没有音轨，无法按音频判断无用段")
        if str(mode or "") != "visual":
            ranges = []
            ae = _ae_bin()
        if str(mode or "") == "smart" and auth_headers:
            try:
                from .cutcli_templates_local import _call_server, _upload_temp_to_server
                _ai_set_status(job_id, progress=20, message="正在转写音频")
                audio = job_dir / "audio.wav"
                subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
                                "-vn", "-ac", "1", "-ar", "16000", str(audio)], check=True)
                audio_url = _upload_temp_to_server(
                    data=audio.read_bytes(), filename=f"{job_id}_ai_trim.wav",
                    content_type="audio/wav", auth_headers=auth_headers,
                )
                stt = _call_server("/api/cutcli/stt/transcribe", {
                    "audio_url": audio_url,
                    "caption_style": {"ass_layout": "plain"},
                    "video_duration_sec": duration,
                    "video_width": int(info.get("width") or 1080),
                    "return_captions": True,
                }, auth_headers, timeout=1200.0)
                caps = stt.get("captions") if isinstance(stt.get("captions"), list) else []
                _ai_set_status(job_id, progress=55, message="正在让模型挑出关键句")
                sentences = [
                    {
                        "text": str(cap.get("text") or ""),
                        "start": float(cap.get("start") or 0) / 1000.0,
                        "end": float(cap.get("end") or 0) / 1000.0,
                    }
                    for cap in caps
                ]
                kept: List[Dict[str, Any]] = []
                if str(mode or "") == "visual" or len(sentences) < 3:
                    _ai_set_status(job_id, progress=58, message="没有有效语音，改用画面检测")
                    ranges = _visual_ranges(ffmpeg, path, duration)
                    style = str(color_style or "").strip().lower()
                    if style in _COLOR_PRESETS:
                        color_filter = _COLOR_PRESETS[style]
                    elif style == "auto":
                        color_filter = _auto_color_filter(ffmpeg, path)
                    else:
                        color_filter = ""
                    _ai_set_status(job_id, color_style=style, color_filter=color_filter)
                    _render_ai_trim(ffmpeg, path, ranges, out, has_audio, color_filter)
                    ae = None
                else:
                    try:
                        sel = _call_server(
                            "/api/cutcli/ai-trim/select",
                            {"sentences": sentences},
                            auth_headers,
                            timeout=180.0,
                        )
                        ids = sel.get("keep") if isinstance(sel.get("keep"), list) else []
                        kept = [sentences[i] for i in ids if isinstance(i, int) and 0 <= i < len(sentences)]
                        logger.info("[multi-clip][ai-trim] llm kept %s/%s reason=%s", len(kept), len(sentences), sel.get("reason"))
                    except Exception as exc:
                        logger.warning("[multi-clip][ai-trim] llm select failed, fallback to rules: %s", exc)
                    if not kept:
                        scored = _score_sentences(caps)
                        kept = _pick_sentences(scored, 0)
                if kept:
                    ranges = _merge_ranges(kept)
                    _ai_set_status(job_id, progress=65, message=f"保留 {len(kept)} 句，正在裁剪")
                    _render_ai_trim(ffmpeg, path, ranges, out, has_audio)
                    ae = None
            except Exception as exc:
                logger.warning("[multi-clip][ai-trim] smart mode failed, fallback: %s", exc)
                _ai_set_status(job_id, progress=35, message="智能选句不可用，改用静音检测")
                ranges = []
                ae = _ae_bin()
        if ae is not None and not ranges:
            _ai_set_status(job_id, progress=25, message="正在用增强引擎剪掉停顿")
            cut = job_dir / "ae.mp4"
            proc = subprocess.run(
                [str(ae), str(path), "--edit", "audio:threshold=0.04", "--margin", "0.12sec",
                 "--no-open", "-o", str(cut)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if proc.returncode == 0 and cut.exists() and cut.stat().st_size > 10000:
                info2 = _probe_video(cut, ffprobe)
                dur2 = float(info2.get("duration") or 0)
                if target and dur2 > float(target) * 1.05:
                    _ai_set_status(job_id, progress=50, message="正在按目标时长收敛")
                    ranges = _keep_ranges(dur2, _detect_silences(ffmpeg, cut), float(target))
                    _render_ai_trim(ffmpeg, cut, ranges, out, bool(info2.get("has_audio")))
                else:
                    cut.replace(out)
                ranges = ranges or [(0.0, round(dur2, 3))]
            else:
                ae = None
        if ae is None and not ranges:
            _ai_set_status(job_id, progress=25, message="正在检测静音与停顿")
            silences = _detect_silences(ffmpeg, path)
            ranges = _keep_ranges(duration, silences, float(target or 0))
            _ai_set_status(job_id, progress=45, message="正在剪掉无用片段")
            _render_ai_trim(ffmpeg, path, ranges, out, has_audio)
        actual = sum(end - start for start, end in ranges)
        _ai_set_status(job_id, progress=85, message="正在保存优化后的视频")
        data = out.read_bytes()
        asset_id, filename, file_size, source_url = _save_bytes_or_tos(data, ".mp4", "video/mp4")
        db = SessionLocal()
        try:
            db.add(Asset(
                asset_id=asset_id,
                user_id=user_id,
                filename=filename,
                media_type="video",
                file_size=file_size,
                source_url=source_url,
                prompt="multi_clip_mixer_ai_trim",
                model="local:ffmpeg-ai-trim",
                tags="multi_clip_mixer,ai_trim",
                meta={
                    "title": "剪辑大师 AI 优化",
                    "original_duration": round(duration, 3),
                    "duration": round(actual, 3),
                    "ranges": [[round(a, 3), round(b, 3)] for a, b in ranges],
                },
            ))
            db.commit()
        finally:
            db.close()
        _ai_set_status(
            job_id, status="done", progress=100, message="优化完成",
            asset_id=asset_id,
            preview_url=f"/api/assets/{asset_id}/content",
            duration=round(actual, 3), original_duration=round(duration, 3),
            removed_seconds=round(max(0.0, duration - actual), 3),
            ranges=[[round(a, 3), round(b, 3)] for a, b in ranges],
        )
    except Exception as exc:
        logger.warning("[multi-clip][ai-trim] job=%s failed: %s", job_id, exc)
        _ai_set_status(job_id, status="error", progress=100, message=str(exc)[:300])


@router.post("/api/multi-clip-mixer/ai-trim")
def start_ai_trim(body: AiTrimBody, request: Request, user: _ServerUser = Depends(get_current_user_for_local)):
    db = SessionLocal()
    try:
        path, _, media_type = resolve_asset_path(db, int(user.id), body.asset_id)
    finally:
        db.close()
    if media_type != "video":
        raise HTTPException(status_code=400, detail="只能优化视频素材")
    job_id = uuid.uuid4().hex[:16]
    _AI_TRIM_DIR.mkdir(parents=True, exist_ok=True)
    _ai_set_status(job_id, status="queued", progress=0, message="排队中", created_at=time.time())
    auth = request.headers.get("authorization") or ""
    headers = {"Authorization": auth} if auth else None
    threading.Thread(target=_ai_trim_worker, args=(job_id, int(user.id), path, float(body.target_seconds or 0), str(body.mode or "smart"), headers, bool(body.auto_color), str(body.color_style or "")), daemon=True).start()
    return {"job_id": job_id}


@router.get("/api/multi-clip-mixer/ai-trim/{job_id}")
def ai_trim_status(job_id: str):
    with _AI_TRIM_LOCK:
        job = dict(_AI_TRIM_JOBS.get(job_id) or {})
    if not job:
        status_file = _AI_TRIM_DIR / job_id / "status.json"
        if status_file.exists():
            try:
                job = json.loads(status_file.read_text(encoding="utf-8"))
            except Exception:
                job = {}
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在（可能服务重启过，请重新点一次 AI 优化）")
    return job


@router.get("/api/multi-clip-mixer/ai-trim/{job_id}/video")
def ai_trim_video(job_id: str):
    if not re.fullmatch(r"[0-9a-f]{16}", job_id or ""):
        raise HTTPException(status_code=400, detail="任务编号无效")
    path = _AI_TRIM_DIR / job_id / "output.mp4"
    if not path.exists():
        raise HTTPException(status_code=404, detail="优化结果不存在")
    return FileResponse(path, media_type="video/mp4")


# 可选增强组件 auto-editor（点击安装，不随包分发）
_AE_DIR = Path(__file__).resolve().parents[3] / "deps" / "auto-editor"
_AE_URLS = (
    "https://lobster-online-assets-2103871705.tos-cn-guangzhou.volces.com/assets/cutcli_templates/tools/auto-editor-windows-x86_64.exe",
    "https://github.com/WyattBlue/auto-editor/releases/download/31.8.0/auto-editor-windows-x86_64.exe",
)


def _ae_bin() -> Optional[Path]:
    for name in ("auto-editor.exe", "auto-editor"):
        candidate = _AE_DIR / name
        if candidate.exists() and candidate.stat().st_size > 1000000:
            return candidate
    return None


@router.get("/api/multi-clip-mixer/ai-tools")
def ai_trim_tools():
    bin_path = _ae_bin()
    return {"auto_editor": bool(bin_path), "path": str(bin_path) if bin_path else ""}


@router.post("/api/multi-clip-mixer/ai-tools/install")
def ai_trim_install_tools():
    if _ae_bin():
        return {"status": "done", "message": "已安装"}

    def _install() -> None:
        try:
            _ai_set_status("tools", status="running", progress=5, message="正在下载增强组件")
            _AE_DIR.mkdir(parents=True, exist_ok=True)
            target = _AE_DIR / "auto-editor.exe"
            tmp = target.with_suffix(".part")
            last_error = ""
            for url in _AE_URLS:
                try:
                    with httpx.stream("GET", url, timeout=600.0, follow_redirects=True) as resp:
                        resp.raise_for_status()
                        total = int(resp.headers.get("Content-Length") or 0)
                        done = 0
                        with open(tmp, "wb") as fh:
                            for chunk in resp.iter_bytes(1024 * 256):
                                fh.write(chunk)
                                done += len(chunk)
                                if total:
                                    _ai_set_status("tools", progress=min(95, 5 + int(done * 90 / total)),
                                                   message=f"正在下载 {done // 1048576}/{total // 1048576} MB")
                    if tmp.exists() and tmp.stat().st_size > 1000000:
                        tmp.replace(target)
                        _ai_set_status("tools", status="done", progress=100, message="增强组件已安装")
                        return
                except Exception as exc:
                    last_error = str(exc)[:160]
                    try:
                        tmp.unlink(missing_ok=True)
                    except Exception:
                        pass
            raise RuntimeError("增强组件下载失败: " + last_error)
        except Exception as exc:
            logger.warning("[multi-clip][ai-trim] install failed: %s", exc)
            _ai_set_status("tools", status="error", progress=100, message=str(exc)[:200])

    threading.Thread(target=_install, daemon=True).start()
    return {"status": "running", "message": "开始下载"}


@router.get("/api/multi-clip-mixer/ai-tools/status")
def ai_trim_tools_status():
    with _AI_TRIM_LOCK:
        job = dict(_AI_TRIM_JOBS.get("tools") or {})
    return job or {"status": "idle", "progress": 0}
