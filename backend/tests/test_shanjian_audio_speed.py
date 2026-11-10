"""数字人2.0 音频驱动：语速必须真的作用到驱动音频上。

闪剪 /v1/virtualman/video 的 audioUrl 分支没有 speedRatio 参数，之前前端传的
speed_ratio 在音频分支被静默丢掉，用户调了语速结果一模一样。
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from backend.app.api import shanjian_digital_human as dh


def _make_tone(tmp: Path, seconds: float = 2.0) -> bytes:
    ffmpeg = dh._ffmpeg_path()
    src = tmp / "tone.mp3"
    subprocess.run(
        [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-ar", "32000", "-ac", "1", str(src),
        ],
        check=True,
    )
    return src.read_bytes()


def _duration(data: bytes, tmp: Path) -> float:
    ffprobe = dh._ffprobe_path()
    probe = tmp / "probe.mp3"
    probe.write_bytes(data)
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(probe)],
        capture_output=True,
        check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


pytestmark = pytest.mark.skipif(
    not dh._ffmpeg_path() or not dh._ffprobe_path(),
    reason="需要本机 ffmpeg/ffprobe 才能验证音频变速",
)


def test_speed_ratio_changes_audio_duration():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        raw = _make_tone(tmp, 2.0)
        base = _duration(raw, tmp)
        faster, media_type = dh._apply_audio_speed(raw, "audio/mpeg", 1.5)
        slower, _ = dh._apply_audio_speed(raw, "audio/mpeg", 0.5)
        assert abs(_duration(faster, tmp) - base / 1.5) < 0.12
        assert abs(_duration(slower, tmp) - base / 0.5) < 0.12
        assert faster != raw and slower != raw
        assert media_type == "audio/mpeg"


def test_speed_ratio_one_is_passthrough():
    raw = b"not-audio-but-must-pass-through"
    out, media_type = dh._apply_audio_speed(raw, "audio/mpeg", 1.0)
    assert out == raw
    assert media_type == "audio/mpeg"


def test_persist_audio_applies_speed_ratio(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        raw = _make_tone(tmp, 2.0)
        base = _duration(raw, tmp)
        captured = {}

        async def fake_download(url: str):
            return raw, "audio/mpeg"

        def fake_save(data: bytes, ext: str, media_type: str):
            captured["data"] = data
            return "asset-1", "asset-1.mp3", len(data), "https://example.com/a.mp3"

        monkeypatch.setattr(dh, "_download_audio_bytes", fake_download)
        monkeypatch.setattr(dh, "_save_bytes_or_tos", fake_save)

        class _DB:
            def add(self, _obj):
                pass

            def flush(self):
                pass

        class _User:
            id = 1

        public_url, asset_id = asyncio.run(
            dh._persist_audio_for_shanjian(
                audio_url="https://example.com/src.mp3",
                db=_DB(),
                current_user=_User(),
                title="测试",
                speed_ratio=2.0,
            )
        )
        assert public_url == "https://example.com/a.mp3"
        assert asset_id == "asset-1"
        assert abs(_duration(captured["data"], tmp) - base / 2.0) < 0.12
