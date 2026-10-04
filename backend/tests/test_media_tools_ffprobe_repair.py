"""ffprobe 缺失这条链路的回归：安装脚本要同时装 ffprobe，素材探针要能只用 ffmpeg 兜底。

背景（2026-10-04 diag_20261004083756_12ea0605）：客户机 deps/ffmpeg 里只有
ffmpeg.exe、没有 ffprobe.exe，闪剪素材分辨率校验全部报
「本机缺少 ffprobe，无法校验素材分辨率」，数字人口播视频的素材准备被整批跳过。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import types
import zipfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.api import assets as assets_api  # noqa: E402
from backend.app.services import runtime_dependency_repair as repair_api  # noqa: E402


FFMPEG_I_STDERR = """
ffmpeg version 7.1 Copyright (c) 2000-2024 the FFmpeg developers
  built with gcc 13.2.0 (GCC)
  Stream #0:0(und): Video: h264 (High) (avc1 / 0x31637661), yuv420p, 1080x1920 [SAR 1:1 DAR 9:16], 2500 kb/s, 30 fps, 30 tbr, 15360 tbn
  Stream #0:1(und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, stereo, fltp, 128 kb/s
At least one output file must be specified
"""


def _load_ensure_script() -> types.ModuleType:
    script = ROOT / "scripts" / "ensure_ffmpeg_windows.py"
    spec = importlib.util.spec_from_file_location("_lobster_ensure_ffmpeg", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_parse_ffmpeg_probe_output_reads_resolution_and_duration():
    text = "  Duration: 00:00:05.02, start: 0.000000, bitrate: 2612 kb/s\n" + FFMPEG_I_STDERR
    width, height, duration = assets_api.parse_ffmpeg_probe_output(text)
    assert (width, height) == (1080, 1920)
    assert duration == pytest.approx(5.02)


def test_parse_ffmpeg_probe_output_ignores_hex_codec_ids():
    """"avc1 / 0x31637661" 里的十六进制不能被当成分辨率。"""
    width, height, _duration = assets_api.parse_ffmpeg_probe_output(
        "  Stream #0:0: Video: h264 (avc1 / 0x31637661), yuv420p, 1920x1080, 25 fps"
    )
    assert (width, height) == (1920, 1080)


def test_probe_media_dimensions_falls_back_to_ffmpeg_without_ffprobe(monkeypatch, tmp_path):
    monkeypatch.setattr(assets_api, "_shanjian_tools", lambda: (r"C:\\deps\\ffmpeg\\ffmpeg.exe", ""))
    monkeypatch.setattr(assets_api, "_SHANJIAN_TOOL_CACHE", {})

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "  Duration: 00:00:05.02, start: 0.000000, bitrate: 2612 kb/s\n" + FFMPEG_I_STDERR

    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return _Proc()

    monkeypatch.setattr(assets_api.subprocess, "run", fake_run)
    source = tmp_path / "big.mp4"
    source.write_bytes(b"x")

    width, height, duration = assets_api.probe_media_dimensions(source)

    assert (width, height) == (1080, 1920)
    assert duration == pytest.approx(5.02)
    assert calls and calls[0][0].endswith("ffmpeg.exe")


def test_probe_media_dimensions_raises_when_no_tool_at_all(monkeypatch, tmp_path):
    monkeypatch.setattr(assets_api, "_shanjian_tools", lambda: ("", ""))
    monkeypatch.setattr(assets_api, "_SHANJIAN_TOOL_CACHE", {})
    source = tmp_path / "big.mp4"
    source.write_bytes(b"x")

    with pytest.raises(RuntimeError) as excinfo:
        assets_api.probe_media_dimensions(source)

    assert "ffmpeg/ffprobe" in str(excinfo.value)


def test_ensure_ffmpeg_script_extracts_both_binaries(tmp_path, monkeypatch):
    module = _load_ensure_script()
    archive = tmp_path / "ffmpeg.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe", b"f" * 700_000)
        zf.writestr("ffmpeg-master-latest-win64-gpl/bin/ffprobe.exe", b"p" * 700_000)
        zf.writestr("ffmpeg-master-latest-win64-gpl/bin/ffplay.exe", b"q" * 700_000)

    with zipfile.ZipFile(archive) as zf:
        assert module._find_binary_in_zip(zf, "ffmpeg.exe").endswith("/bin/ffmpeg.exe")
        assert module._find_binary_in_zip(zf, "ffprobe.exe").endswith("/bin/ffprobe.exe")

    dest = tmp_path / "deps" / "ffmpeg"
    monkeypatch.setattr(module, "DEST_DIR", dest)
    assert module._missing_binaries() == ["ffmpeg.exe", "ffprobe.exe"]

    (dest).mkdir(parents=True, exist_ok=True)
    (dest / "ffmpeg.exe").write_bytes(b"f" * 700_000)
    assert module._missing_binaries() == ["ffprobe.exe"]

    written = module._extract_binaries(archive, ["ffprobe.exe"])
    assert [p.name for p in written] == ["ffprobe.exe"]
    assert (dest / "ffprobe.exe").stat().st_size >= 700_000


def test_repair_media_tools_reports_ready_when_nothing_missing(monkeypatch):
    monkeypatch.setattr(repair_api, "_media_tools_missing", lambda: [])
    result = repair_api._repair_media_tools(300)

    assert result["ok"] is True
    assert result["missing"] == []
    assert "就绪" in result["message"]


def test_repair_media_tools_reports_missing_binaries(monkeypatch):
    monkeypatch.setattr(repair_api, "_media_tools_missing", lambda: ["ffprobe"])
    monkeypatch.setenv("LOBSTER_OFFLINE_ONLY", "1")
    result = repair_api._repair_media_tools(300)

    assert result["ok"] is False
    assert result["missing"] == ["ffprobe"]


def test_runtime_repair_reports_media_check_group():
    source = (ROOT / "backend" / "app" / "services" / "runtime_dependency_repair.py").read_text(encoding="utf-8")
    assert "音视频工具（ffmpeg/ffprobe）" in source
    assert "_repair_media_tools(timeout)" in source


def test_install_scripts_require_ffprobe_too():
    for name in ("install.bat", "install_slim.bat"):
        text = (ROOT / name).read_bytes().decode("utf-8", errors="replace")
        assert 'deps\\ffmpeg\\ffprobe.exe' in text, name
