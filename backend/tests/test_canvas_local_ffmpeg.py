"""画布本机 ffmpeg：工具解析 + 参数拼装（不依赖网络，不真跑长任务）。"""
from __future__ import annotations

import subprocess

from backend.app.api import canvas_local


def test_ffmpeg_tools_resolve_to_client_bundle():
    tools = canvas_local._ffmpeg_tools()
    assert tools["ffmpeg"], "应当能在客户端找到 deps/ffmpeg/ffmpeg.exe"
    out = subprocess.run([tools["ffmpeg"], "-version"], capture_output=True, timeout=60)
    assert out.returncode == 0
    assert b"ffmpeg version" in (out.stdout or b"")


def test_status_endpoint_reports_local_where():
    payload = canvas_local.canvas_local_ffmpeg_status()
    assert payload["ok"] is True
    assert payload["where"].startswith("本机")
    assert payload["ffmpeg"]


def test_merge_args_and_trim_args():
    body = canvas_local.CanvasFfmpegIn(op="merge_video_audio", video_url="v.mp4", audio_url="a.mp3", mode="replace")
    args = canvas_local.build_ffmpeg_args(body, video="v.mp4", audio="a.mp3", out_path="out.mp4")
    assert args[:1] == ["-y"]
    assert "-i" in args and "v.mp4" in args and "a.mp3" in args
    assert "out.mp4" in args

    trim = canvas_local.CanvasFfmpegIn(op="trim_audio", audio_url="a.mp3", start=1.5, end=4.0, output_format="mp3")
    targs = canvas_local.build_ffmpeg_args(trim, audio="a.mp3", out_path="t.mp3")
    assert "-ss" in targs and "1.5" in targs and "-to" in targs and "4.0" in targs

    concat = canvas_local.CanvasFfmpegIn(op="concat_videos", urls=["a.mp4", "b.mp4"])
    cargs = canvas_local.build_ffmpeg_args(concat, out_path="c.mp4")
    assert "-f" in cargs and "concat" in cargs and "c.mp4.txt" in cargs
