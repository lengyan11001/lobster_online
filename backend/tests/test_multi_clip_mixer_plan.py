from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend.app.api import cutcli_templates_local
from backend.app.api import multi_clip_mixer


def test_extend_visual_segments_to_target_extends_last_clip_when_source_allows():
    segments = [
        {"asset_id": "a", "start_sec": 0.0, "end_sec": 3.0, "duration": 3.0, "source_duration": 10.0},
        {"asset_id": "b", "start_sec": 0.0, "end_sec": 3.0, "duration": 3.0, "source_duration": 20.0},
    ]

    duration = multi_clip_mixer._extend_visual_segments_to_target(segments, 9.0)

    assert duration == 9.0
    assert segments[-1]["end_sec"] == 6.0
    assert segments[-1]["duration"] == 6.0
    assert segments[-1].get("pad_after", 0) == 0


def test_extend_visual_segments_to_target_freezes_last_frame_when_source_is_short():
    segments = [
        {"asset_id": "a", "start_sec": 0.0, "end_sec": 3.0, "duration": 3.0, "source_duration": 10.0},
        {"asset_id": "b", "start_sec": 0.0, "end_sec": 3.0, "duration": 3.0, "source_duration": 4.0},
    ]

    duration = multi_clip_mixer._extend_visual_segments_to_target(segments, 9.0)

    assert duration == 9.0
    assert segments[-1]["end_sec"] == 4.0
    assert segments[-1]["duration"] == 4.0
    assert segments[-1]["pad_after"] == 2.0


def test_marked_audio_multi_clip_uses_final_audio_for_template_captions():
    asset = SimpleNamespace(
        meta={
            "keep_original_audio": True,
            "audio_source": "marked_video",
            "audio_source_asset_id": "audio-video-1",
            "segments": [
                {
                    "asset_id": "visual-1",
                    "start_sec": 0,
                    "end_sec": 3,
                    "duration": 3,
                    "has_audio": True,
                }
            ],
        }
    )

    class Query:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return asset

    class Db:
        def query(self, model):
            return Query()

    assert cutcli_templates_local._multi_clip_audio_segments(Db(), 1, "multi-clip-result") == []


def test_multi_clip_shanjian_calls_follow_digital_human_cloud_routing():
    root = Path(__file__).resolve().parents[2]
    js = (root / "static" / "js" / "multi-clip-mixer.js").read_text(encoding="utf-8")

    assert "function shanjianApiBase(path)" in js
    assert "requestTo(shanjianApiBase(path), path" in js
    assert "indexOf('/api/shanjian-') === 0" in js
