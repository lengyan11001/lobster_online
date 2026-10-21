# -*- coding: utf-8 -*-
"""朋友圈图文父素材只取图片，绝不回退视频（2026-10-21）。"""
from __future__ import annotations

from backend.app.api import h5_chat_channel as ch


def test_image_preference_never_falls_back_to_video():
    payload = {
        "publish_draft": {
            "media_type": "video",
            "video_url": "https://example.test/196s.mp4",
            "video_asset_id": "vid-1",
        }
    }
    assert ch._extract_parent_material(payload, preferred_media_type="image") == {}


def test_image_material_is_returned():
    payload = {"publish_draft": {"media_type": "image_text", "image_urls": ["https://example.test/a.png"]}}
    material = ch._extract_parent_material(payload, preferred_media_type="image")
    assert material.get("media_type") == "image"
    assert material.get("url") == "https://example.test/a.png"