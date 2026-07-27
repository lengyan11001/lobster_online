from __future__ import annotations

from mcp.http_server import _normalize_video_generate_payload
from mcp.video_model_resolve import resolve_default_video_model_id


def test_online_mcp_routes_apiz_veo_by_reference_count():
    text = _normalize_video_generate_payload({"prompt": "text video", "duration": 12})
    image = _normalize_video_generate_payload(
        {"prompt": "image video", "image_url": "https://example.com/a.png"}
    )
    references = _normalize_video_generate_payload(
        {
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

    assert chat._get_default_video_generate_model_cached(False) == "apiz/veo3.1/text-to-video"
    assert chat._get_default_video_generate_model_cached(True) == "apiz/veo3.1/image-to-video"


def test_online_chat_keeps_explicit_grok_selection():
    from backend.app.api import chat

    model, source = chat._infer_video_model_lock_for_openclaw("请明确使用 Grok 生成视频", False)

    assert model == "xai/grok-imagine-video/text-to-video"
    assert source == "user"


def test_online_mcp_migrates_stale_env_default_only():
    assert (
        resolve_default_video_model_id("xai/grok-imagine-video/text-to-video", False)
        == "apiz/veo3.1/text-to-video"
    )
