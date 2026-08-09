from __future__ import annotations

from mcp.http_server import _normalize_video_generate_payload
from mcp.video_model_resolve import resolve_default_video_model_id


def test_online_mcp_leaves_unspecified_video_model_to_server():
    text = _normalize_video_generate_payload({"prompt": "text video", "duration": 12})
    image = _normalize_video_generate_payload(
        {"prompt": "image video", "image_url": "https://example.com/a.png"}
    )

    assert "model" not in text
    assert text["duration"] == 12
    assert "model" not in image
    assert "duration" not in image
    assert image["image_url"] == "https://example.com/a.png"


def test_online_mcp_routes_explicit_apiz_veo_by_reference_count():
    text = _normalize_video_generate_payload({"model": "veo3.1", "prompt": "text video", "duration": 12})
    image = _normalize_video_generate_payload(
        {"model": "veo3.1", "prompt": "image video", "image_url": "https://example.com/a.png"}
    )
    references = _normalize_video_generate_payload(
        {
            "model": "veo3.1",
            "prompt": "reference video",
            "image_urls": ["https://example.com/a.png", "https://example.com/b.png"],
        }
    )

    assert text["model"] == "apiz/veo3.1/text-to-video"
    assert text["duration"] == 8
    assert image["model"] == "apiz/veo3.1/image-to-video"
    assert references["model"] == "apiz/veo3.1/reference-to-video"
    assert references["duration"] == 8


def test_online_chat_migrates_stale_remote_video_default(monkeypatch):
    from backend.app.api import chat

    monkeypatch.setattr(chat, "_refresh_remote_generation_config_background", lambda: None)
    monkeypatch.setattr(
        chat,
        "_remote_video_generate_default_model_cache",
        "xai/grok-imagine-video/text-to-video",
    )

    assert chat._get_default_video_generate_model_cached(False) == "xai/grok-imagine-video/text-to-video"
    assert chat._get_default_video_generate_model_cached(True) == "xai/grok-imagine-video-1.5/image-to-video"


def test_online_chat_keeps_explicit_grok_selection():
    from backend.app.api import chat

    model, source = chat._infer_video_model_lock_for_openclaw("please use grok to generate a video", False)

    assert model == "xai/grok-imagine-video/text-to-video"
    assert source == "user"


def test_online_chat_does_not_lock_default_video_model_for_openclaw():
    from backend.app.api import chat

    model, source = chat._infer_video_model_lock_for_openclaw("generate a product promo video", False)

    assert model == ""
    assert source == ""


def test_online_mcp_migrates_stale_env_default_only():
    assert (
        resolve_default_video_model_id("xai/grok-imagine-video/text-to-video", False)
        == "xai/grok-imagine-video/text-to-video"
    )
    assert (
        resolve_default_video_model_id("apiz/veo3.1/image-to-video", True)
        == "xai/grok-imagine-video-1.5/image-to-video"
    )
