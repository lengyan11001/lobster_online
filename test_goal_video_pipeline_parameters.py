from __future__ import annotations

import pytest

from backend.app.api.goal_video_pipeline import (
    LONG_FORM_VIDEO_MODEL,
    GoalVideoPipelinePayload,
    _build_video_generation_payload,
)
from backend.app.api.create_video_pipeline import (
    CreateVideoPipelinePayload,
    _create_video_billing_payload,
)
from mcp.http_server import (
    _normalize_invoke_goal_video_pipeline_args,
    _normalize_video_generate_payload,
)


def test_goal_duration_overrides_stale_six_second_payload():
    payload = GoalVideoPipelinePayload(
        goal="Generate a 30s vertical 480p product video",
        duration=6,
        video_model="xai/grok-imagine-video/image-to-video",
    )

    assert payload.duration == 30
    assert payload.aspect_ratio == "9:16"
    assert payload.resolution == "480p"
    assert payload.video_model == "xai/grok-imagine-video/image-to-video"


@pytest.mark.parametrize("duration", [3, 4, 6, 8, 10, 15, 30, 60])
def test_goal_video_payload_does_not_reject_duration_in_online(duration):
    payload = GoalVideoPipelinePayload(goal="Generate a product promo video", duration=duration)

    assert payload.duration == duration
    assert payload.video_model is None


def test_goal_without_duration_or_model_stays_server_controlled():
    payload = GoalVideoPipelinePayload(goal="Generate a product promo video")
    request = _build_video_generation_payload(
        pl=payload,
        prompt="slow camera push in",
        generated_image_urls=["https://example.com/frame.png"],
    )

    assert payload.duration is None
    assert payload.video_model is None
    assert "duration" not in request
    assert "model" not in request
    assert request["prompt"] == "slow camera push in"
    assert request["image_url"] == "https://example.com/frame.png"


def test_video_generate_normalizer_omits_model_when_server_controls_default():
    normalized = _normalize_video_generate_payload(
        {
            "prompt": "slow camera push in",
            "image_url": "https://example.com/frame.png",
            "duration": 15,
            "aspect_ratio": "9:16",
        }
    )

    assert "model" not in normalized
    assert normalized["duration"] == 15
    assert normalized["aspect_ratio"] == "9:16"
    assert normalized["image_url"] == "https://example.com/frame.png"


def test_video_generate_normalizer_does_not_inject_duration_without_user_input():
    normalized = _normalize_video_generate_payload(
        {
            "prompt": "slow camera push in",
            "image_url": "https://example.com/frame.png",
        }
    )

    assert "model" not in normalized
    assert "duration" not in normalized
    assert normalized["image_url"] == "https://example.com/frame.png"


def test_video_generate_server_gateway_preserves_explicit_model_and_duration():
    normalized = _normalize_video_generate_payload(
        {
            "model": "apiz/veo3.1/image-to-video",
            "prompt": "slow camera push in",
            "image_url": "https://example.com/frame.png",
            "duration": 15,
            "aspect_ratio": "9:16",
            "resolution": "720p",
        },
        server_controlled=True,
    )

    assert normalized["model"] == "apiz/veo3.1/image-to-video"
    assert normalized["duration"] == 15
    assert normalized["aspect_ratio"] == "9:16"
    assert normalized["resolution"] == "720p"
    assert normalized["image_url"] == "https://example.com/frame.png"


def test_goal_parameters_and_reference_inputs_reach_video_generate():
    payload = GoalVideoPipelinePayload(
        goal="Generate a 30s 16:9 480p product video",
        duration=6,
        seed=42,
        negative_prompt="no text",
        audio=True,
        video_model=LONG_FORM_VIDEO_MODEL,
        reference_video_urls=["https://example.com/reference.mp4"],
        reference_audio_urls=["https://example.com/reference.mp3"],
    )

    request = _build_video_generation_payload(
        pl=payload,
        prompt="camera pushes toward the product",
        image_asset_id="asset-1",
        generated_image_urls=["https://example.com/frame.png"],
    )

    assert request["duration"] == 30
    assert request["aspect_ratio"] == "16:9"
    assert request["resolution"] == "480p"
    assert request["model"] == LONG_FORM_VIDEO_MODEL
    assert request["asset_id"] == "asset-1"
    assert request["image_urls"] == ["https://example.com/frame.png"]
    assert request["video_urls"] == ["https://example.com/reference.mp4"]
    assert request["audio_urls"] == ["https://example.com/reference.mp3"]
    assert request["seed"] == 42
    assert request["negative_prompt"] == "no text"
    assert request["audio"] is True


def test_previous_apiz_or_veo_model_is_not_migrated_by_online():
    previous_model = "apiz/veo3.1/image-to-video"

    payload = GoalVideoPipelinePayload(
        goal="Generate an 8s product video",
        video_model=previous_model,
    )

    assert payload.duration == 8
    assert payload.video_model == previous_model


def test_explicit_unsupported_parameters_are_forwarded_to_server_decision():
    payload = GoalVideoPipelinePayload(goal="Generate a 3s 4k product promo video")

    assert payload.duration == 3
    assert payload.resolution == "2160p"


def test_duration_aliases_are_normalized_before_server_forwarding():
    seconds = GoalVideoPipelinePayload(goal="Generate a product promo", duration="30s")
    minutes = GoalVideoPipelinePayload(goal="Generate a product promo", duration="0.5 minutes")

    assert seconds.duration == minutes.duration == 30


def test_bihuo_25_video_generate_normalizer_keeps_exact_duration():
    normalized = _normalize_video_generate_payload(
        {
            "model": LONG_FORM_VIDEO_MODEL,
            "prompt": "camera pushes toward the product",
            "functionMode": "omini",
            "image_url": "https://example.com/frame.png",
            "duration": 30,
            "aspect_ratio": "9:16",
            "resolution": "720p",
        }
    )

    assert normalized["duration"] == 30
    assert normalized["ratio"] == "9:16"
    assert normalized["resolution"] == "720p"
    assert normalized["filePaths"] == ["https://example.com/frame.png"]


def test_top_level_goal_parameters_are_forwarded_into_nested_payload():
    normalized = _normalize_invoke_goal_video_pipeline_args(
        {
            "capability_id": "goal.video.pipeline",
            "payload": {"goal": "Generate a 30s video"},
            "duration": 30,
            "resolution": "720p",
            "function_mode": "reference",
            "negative_prompt": "no text",
            "reference_audio_urls": ["https://example.com/reference.mp3"],
            "camera_fixed": True,
            "video_options": {"custom": "value"},
        }
    )

    assert normalized["payload"]["duration"] == 30
    assert normalized["payload"]["resolution"] == "720p"
    assert normalized["payload"]["function_mode"] == "reference"
    assert normalized["payload"]["negative_prompt"] == "no text"
    assert normalized["payload"]["reference_audio_urls"] == ["https://example.com/reference.mp3"]
    assert normalized["payload"]["camera_fixed"] is True
    assert normalized["payload"]["video_options"] == {"custom": "value"}


def test_create_video_pipeline_does_not_preselect_video_model():
    payload = CreateVideoPipelinePayload(prompt="Generate a product promo video")
    billing_payload = _create_video_billing_payload(payload)

    assert payload.duration == 10
    assert payload.video_model is None
    assert "video_model" not in billing_payload
