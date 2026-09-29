"""灵感画布（canvas_web）在客户端本机处理的部分。

规矩：**所有素材处理（合并音视频 / 裁剪音频 / 拼接视频 / 探测）都在本机跑**，
用的是客户端自带的 deps/ffmpeg/ffmpeg.exe（与数字人、Hypit 同一套取法），
不往服务器发 ffmpeg 任务。服务器只负责模型中转与算力计费。
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

_DOWNLOAD_TIMEOUT = 300.0
_OPS = ("merge_video_audio", "trim_audio", "concat_videos", "probe")


def _runtime_dir() -> Path:
    base = os.environ.get("LOBSTER_RUNTIME_DIR")
    root = Path(base) if base else Path(__file__).resolve().parents[3] / "_lobster_runtime"
    target = root / "canvas_local"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _ffmpeg_tools() -> Dict[str, Optional[str]]:
    """复用 Hypit 那套解析：优先 包内 deps/ffmpeg，其次 LOBSTER_FFMPEG_PATH，最后 PATH。"""
    from .hypit_local import _ffmpeg_executable, _ffprobe_executable

    return {"ffmpeg": _ffmpeg_executable(), "ffprobe": _ffprobe_executable()}


def _require_ffmpeg() -> str:
    ffmpeg = _ffmpeg_tools().get("ffmpeg")
    if not ffmpeg:
        raise HTTPException(
            status_code=503,
            detail="未找到本机 ffmpeg（应在客户端 deps/ffmpeg/ffmpeg.exe，或设置 LOBSTER_FFMPEG_PATH）",
        )
    return str(ffmpeg)


class CanvasFfmpegIn(BaseModel):
    op: str
    video_url: str = ""
    audio_url: str = ""
    urls: List[str] = []
    output_name: str = ""
    start: float = 0.0
    end: Optional[float] = None
    duration: Optional[float] = None
    mode: str = "mix"
    audio_offset: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    duration_mode: str = "video"
    audio_volume: float = 1.0
    output_format: str = "mp4"
    audio_format: str = "mp3"


def build_ffmpeg_args(payload: CanvasFfmpegIn, *, video: str = "", audio: str = "", inputs: Optional[List[str]] = None, out_path: str = "") -> List[str]:
    """把画布的操作翻译成 ffmpeg 参数（纯函数，便于测试）。"""
    op = (payload.op or "").strip()
    args: List[str] = ["-y", "-hide_banner", "-loglevel", "error"]
    if op == "merge_video_audio":
        args += ["-i", video, "-i", audio]
        filters = []
        if payload.mode == "replace":
            args += ["-map", "0:v:0", "-map", "1:a:0"]
        else:
            args += ["-map", "0:v:0", "-map", "0:a?", "-map", "1:a:0"]
            filters.append("amix=inputs=2:duration=first:dropout_transition=0")
        if payload.audio_offset > 0:
            filters.append("adelay=%d|%d" % (int(payload.audio_offset * 1000), int(payload.audio_offset * 1000)))
        if payload.fade_in > 0:
            filters.append("afade=t=in:st=0:d=%s" % payload.fade_in)
        if payload.fade_out > 0:
            filters.append("afade=t=out:st=%s:d=%s" % (max(0.0, (payload.duration or 0) - payload.fade_out), payload.fade_out))
        if filters:
            args += ["-af", ",".join(filters)]
        if payload.duration_mode == "shortest":
            args += ["-shortest"]
        args += ["-c:v", "copy", out_path]
        return args
    if op == "trim_audio":
        args += ["-i", audio, "-ss", str(payload.start)]
        if payload.end is not None:
            args += ["-to", str(payload.end)]
        elif payload.duration:
            args += ["-t", str(payload.duration)]
        args += ["-vn", out_path]
        return args
    if op == "concat_videos":
        list_path = out_path + ".txt"
        args = ["-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", out_path]
        return args
    if op == "probe":
        return ["-hide_banner", "-i", video, "-f", "null", "-"]
    raise HTTPException(status_code=400, detail="不支持的操作：%s" % (op or "(空)"))


async def _download(url: str, target: Path) -> Path:
    if not url:
        raise HTTPException(status_code=400, detail="缺少素材地址")
    if url.startswith("file://"):
        src = Path(url[7:])
        if not src.exists():
            raise HTTPException(status_code=404, detail="本机文件不存在：%s" % src)
        shutil.copyfile(src, target)
        return target
    if not url.lower().startswith(("http://", "https://")):
        src = Path(url)
        if src.exists():
            shutil.copyfile(src, target)
            return target
        raise HTTPException(status_code=400, detail="素材地址不支持：%s" % url)
    try:
        async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT, trust_env=False) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            target.write_bytes(resp.content)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="下载素材失败：%s" % exc) from exc
    return target


def _run(ffmpeg: str, args: List[str], cwd: Path) -> None:
    proc = subprocess.run([ffmpeg] + args, cwd=str(cwd), capture_output=True)
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode("utf-8", "replace")[-600:]
        raise HTTPException(status_code=500, detail="本机 ffmpeg 处理失败：%s" % tail)


@router.get("/api/canvas-local/ffmpeg/status", summary="画布本机 ffmpeg 是否就绪")
def canvas_local_ffmpeg_status() -> Dict[str, Any]:
    tools = _ffmpeg_tools()
    ffmpeg = tools.get("ffmpeg")
    version = ""
    if ffmpeg:
        try:
            out = subprocess.run([str(ffmpeg), "-version"], capture_output=True, timeout=30)
            version = (out.stdout or b"").decode("utf-8", "replace").splitlines()[0] if out.stdout else ""
        except Exception as exc:  # noqa: BLE001
            version = "调用失败：%s" % exc
    return {
        "ok": bool(ffmpeg),
        "ffmpeg": str(ffmpeg or ""),
        "ffprobe": str(tools.get("ffprobe") or ""),
        "version": version,
        "where": "本机客户端（deps/ffmpeg）",
    }


@router.post("/api/canvas-local/ffmpeg", summary="画布素材处理（全部在本机 ffmpeg 执行）")
async def canvas_local_ffmpeg(body: CanvasFfmpegIn) -> Dict[str, Any]:
    op = (body.op or "").strip()
    if op not in _OPS:
        raise HTTPException(status_code=400, detail="不支持的操作：%s" % (op or "(空)"))
    ffmpeg = _require_ffmpeg()
    work = _runtime_dir() / uuid.uuid4().hex
    work.mkdir(parents=True, exist_ok=True)
    try:
        if op == "merge_video_audio":
            video = await _download(body.video_url, work / ("video." + (body.output_format or "mp4")))
            audio = await _download(body.audio_url, work / ("audio." + (body.audio_format or "mp3")))
            out = work / (body.output_name or "merged.mp4")
            args = build_ffmpeg_args(body, video=str(video), audio=str(audio), out_path=str(out))
            await asyncio.to_thread(_run, ffmpeg, args, work)
            return {"ok": True, "output": str(out), "where": "client"}
        if op == "trim_audio":
            audio = await _download(body.audio_url, work / ("audio." + (body.audio_format or "mp3")))
            out = work / (body.output_name or ("trimmed." + (body.output_format or "mp3")))
            args = build_ffmpeg_args(body, audio=str(audio), out_path=str(out))
            await asyncio.to_thread(_run, ffmpeg, args, work)
            return {"ok": True, "output": str(out), "where": "client"}
        if op == "concat_videos":
            if len(body.urls) < 2:
                raise HTTPException(status_code=400, detail="拼接至少需要 2 个视频")
            names = []
            for idx, url in enumerate(body.urls):
                name = "part%02d.mp4" % idx
                await _download(url, work / name)
                names.append(name)
            list_path = work / "list.txt"
            list_path.write_text("\n".join("file '%s'" % n for n in names), encoding="utf-8")
            out = work / (body.output_name or "concat.mp4")
            args = build_ffmpeg_args(body, inputs=names, out_path=str(out))
            await asyncio.to_thread(_run, ffmpeg, args, work)
            return {"ok": True, "output": str(out), "where": "client"}
        video = await _download(body.video_url, work / "probe.bin")
        args = build_ffmpeg_args(body, video=str(video))
        await asyncio.to_thread(_run, ffmpeg, args, work)
        return {"ok": True, "output": "", "where": "client"}
    finally:
        pass
