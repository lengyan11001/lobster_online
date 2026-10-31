# -*- coding: utf-8 -*-
"""多段混剪「目标时长」：显式给目标时要裁剪，跟随主音轨时只延长不裁剪。"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.app.api.multi_clip_mixer import _extend_visual_segments_to_target  # noqa: E402


def _segs(*durs):
    out = []
    t = 0.0
    for d in durs:
        out.append({"start_sec": round(t, 3), "end_sec": round(t + d, 3), "duration": float(d),
                    "source_duration": 60.0})
        t += d
    return out


def _total(segs):
    return round(sum(float(s["duration"]) for s in segs), 3)


def test_trim_to_target_proportionally():
    segs = _segs(10, 10, 10)
    got = _extend_visual_segments_to_target(segs, 15, allow_trim=True)
    assert got == pytest.approx(15, abs=0.05)
    assert _total(segs) == pytest.approx(15, abs=0.05)
    assert len(segs) == 3
    for s in segs:
        assert s["duration"] == pytest.approx(5, abs=0.05)
        assert s["end_sec"] == pytest.approx(s["start_sec"] + s["duration"], abs=0.01)


def test_extend_when_shorter():
    segs = _segs(5, 5)
    got = _extend_visual_segments_to_target(segs, 12, allow_trim=True)
    assert got == pytest.approx(12, abs=0.05)
    assert _total(segs) == pytest.approx(12, abs=0.05)


def test_no_trim_when_audio_follow_mode():
    segs = _segs(10, 10, 10)
    got = _extend_visual_segments_to_target(segs, 12, allow_trim=False)
    assert got == pytest.approx(30, abs=0.05)
    assert _total(segs) == pytest.approx(30, abs=0.05)


def test_zero_target_keeps_all():
    segs = _segs(4, 4)
    assert _extend_visual_segments_to_target(segs, 0) == pytest.approx(8, abs=0.01)


def test_many_clips_small_target_drops_extras():
    segs = _segs(*([10] * 10))
    got = _extend_visual_segments_to_target(segs, 5, allow_trim=True)
    assert got == pytest.approx(5, abs=0.1)
    assert 0 < len(segs) <= 10


def test_empty_segments():
    assert _extend_visual_segments_to_target([], 10) == 0.0