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

def test_moments_image_text_run_detected_from_parent_payload():
    assert ch._parent_run_is_moments_image_text({"ip_content_daily": {}}) is True
    assert ch._parent_run_is_moments_image_text({"publish_draft": {"source_task": "moments_candidate"}}) is True
    assert ch._parent_run_is_moments_image_text({"local_result": {"mode": "daily_video"}}) is False


def test_image_text_never_picks_video_from_other_urls():
    payload = {
        "sync_results": {"items": [{"media_type": "video", "public_url": "http://wxapp.tc.qq.com/x.mp4"}]},
        "publish_draft": {"media_type": "video", "video_url": "https://example.test/45m.mp4"},
    }
    assert ch._extract_parent_material(payload, preferred_media_type="image") == {}


def test_image_asset_without_image_url_does_not_borrow_video_url():
    payload = {
        "records_by_task": {"moments_candidate": [{"image_asset_id": "img-1"}]},
        "sync_results": {"items": [{"media_type": "video", "public_url": "http://wxapp.tc.qq.com/x.mp4"}]},
    }
    material = ch._extract_parent_material(payload, preferred_media_type="image")
    assert material.get("asset_id") == "img-1"
    assert "url" not in material
