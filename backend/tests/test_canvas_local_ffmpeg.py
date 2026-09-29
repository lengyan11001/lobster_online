"""画布本机 ffmpeg：工具解析 + 参数拼装 + 一次真实的本机合并往返。"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

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


def _local_client() -> TestClient:
    app = FastAPI()
    app.include_router(canvas_local.router)
    return TestClient(app)


def _make_media(ffmpeg: str, work: Path, name: str, args: list) -> Path:
    out = work / name
    proc = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"] + args + [str(out)],
        capture_output=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-400:]
    assert out.is_file() and out.stat().st_size > 0
    return out


def test_merge_round_trip_over_http(tmp_path, monkeypatch):
    """真跑一次：本机 ffmpeg 合并 -> 接口给 URL -> 页面按 URL 取回产物。"""
    monkeypatch.setenv("LOBSTER_RUNTIME_DIR", str(tmp_path))
    ffmpeg = canvas_local._ffmpeg_tools()["ffmpeg"]
    assert ffmpeg
    src = tmp_path / "src"
    src.mkdir()
    video = _make_media(ffmpeg, src, "v.mp4", ["-f", "lavfi", "-i", "color=c=red:s=96x96:d=1", "-pix_fmt", "yuv420p"])
    audio = _make_media(ffmpeg, src, "a.mp3", ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"])

    client = _local_client()
    resp = client.post(
        "/api/canvas-local/ffmpeg",
        json={
            "op": "merge_video_audio",
            "video_url": str(video),
            "audio_url": str(audio),
            "mode": "replace",
            "output_name": "merged.mp4",
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["ok"] is True and payload["where"] == "client"
    assert Path(payload["output"]).is_file()
    assert payload["name"].endswith("/merged.mp4")
    assert payload["url"] == "/api/canvas-local/media/" + payload["name"]

    media = client.get(payload["url"])
    assert media.status_code == 200, media.text
    assert media.headers["content-type"].startswith("video/mp4")
    assert len(media.content) > 1000
    assert b"ftyp" in media.content[:64]


def test_media_endpoint_serves_inside_and_blocks_escape(tmp_path, monkeypatch):
    monkeypatch.setenv("LOBSTER_RUNTIME_DIR", str(tmp_path))
    job = tmp_path / "canvas_local" / "job1"
    job.mkdir(parents=True)
    (job / "ok.txt").write_text("ok", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("top-secret", encoding="utf-8")

    client = _local_client()
    assert client.get("/api/canvas-local/media/job1/ok.txt").text == "ok"
    assert client.get("/api/canvas-local/media/job1/nope.txt").status_code == 404
    assert client.get("/api/canvas-local/media/..%2Fsecret.txt").status_code == 404


def test_concat_round_trip_over_http(tmp_path, monkeypatch):
    """拼接曾经因为列表文件名和 ffmpeg 参数对不上（list.txt vs <out>.txt）直接 500。"""
    monkeypatch.setenv("LOBSTER_RUNTIME_DIR", str(tmp_path))
    ffmpeg = canvas_local._ffmpeg_tools()["ffmpeg"]
    src = tmp_path / "src"
    src.mkdir()
    first = _make_media(ffmpeg, src, "a.mp4", ["-f", "lavfi", "-i", "color=c=red:s=64x64:d=1", "-pix_fmt", "yuv420p"])
    second = _make_media(ffmpeg, src, "b.mp4", ["-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1", "-pix_fmt", "yuv420p"])

    client = _local_client()
    resp = client.post(
        "/api/canvas-local/ffmpeg",
        json={"op": "concat_videos", "urls": [str(first), str(second)], "output_name": "concat.mp4"},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert (Path(payload["output"]).parent / (Path(payload["output"]).name + ".txt")).is_file()
    media = client.get(payload["url"])
    assert media.status_code == 200, media.text
    assert len(media.content) > 1000

def test_canvas_frame_seeds_canvas_session(tmp_path, monkeypatch):
    """画布入口页：把本机登录态写进画布的 localStorage['user_info']，再进画布（不再弹扫码）。"""
    from backend.app.api import canvas_cloud_proxy

    monkeypatch.setenv("LOBSTER_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(canvas_cloud_proxy, "auth_context", lambda: ("jwt-token", "inst-1"))

    async def fake_profile():
        return {"id": 7, "name": "张三", "phone": "", "email": "z@b.c", "credits": "88"}

    monkeypatch.setattr(canvas_cloud_proxy, "cloud_profile", fake_profile)

    client = _local_client()
    session = client.get("/api/canvas-local/session")
    assert session.status_code == 200
    assert session.json()["logged_in"] is True
    assert session.json()["user"]["id"] == 7
    assert session.json()["token"] == "lobster-canvas"

    frame = client.get("/api/canvas-local/canvas-frame")
    assert frame.status_code == 200
    assert "localStorage.setItem('user_info'" in frame.text
    assert "/static/canvas-web/index.html" in frame.text
    assert "张三" in frame.text

    monkeypatch.setattr(canvas_cloud_proxy, "auth_context", lambda: ("", ""))
    assert client.get("/api/canvas-local/session").json()["logged_in"] is False
    assert "请先在客户端完成登录" in client.get("/api/canvas-local/canvas-frame").text