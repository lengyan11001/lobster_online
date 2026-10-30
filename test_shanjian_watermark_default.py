# -*- coding: utf-8 -*-
"""闪剪 AI 水印默认关闭：请求默认值 + 前端两处入口都要是关的。"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.app.api.shanjian_smart_clip import SubmitClipBody  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent


def test_backend_default_is_off():
    body = SubmitClipBody(style_id="tpl-1")
    assert body.watermark_show is False


def test_backend_explicit_true_still_supported():
    assert SubmitClipBody(style_id="tpl-1", watermark_show=True).watermark_show is True


def test_multi_clip_mixer_hardcodes_off():
    src = (ROOT / "static/js/multi-clip-mixer.js").read_text(encoding="utf-8")
    assert "watermark_show: false" in src
    assert "watermark_show: true" not in src


def test_smart_clip_switch_unchecked_by_default():
    src = (ROOT / "static/js/shanjian-smart-clip.js").read_text(encoding="utf-8")
    assert '<input id="sjClipWatermarkSwitch" type="checkbox">' in src
    assert '<input id="sjClipWatermarkSwitch" type="checkbox" checked>' not in src