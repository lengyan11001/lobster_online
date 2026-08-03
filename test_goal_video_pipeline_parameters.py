from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.api.goal_video_pipeline import (
    LONG_FORM_VIDEO_MODEL,
    GoalVideoPipelinePayload,
    _build_video_generation_payload,
)
from mcp.http_server import (
    _normalize_invoke_goal_video_pipeline_args,
    _normalize_video_generate_payload,
)


def test_explicit_30_seconds_overrides_stale_six_second_payload():
    payload = GoalVideoPipelinePayload(
        goal="为产品生成30秒竖屏宣传视频",
        duration=6,
        video_model="xai/grok-imagine-video/image-to-video",
    )

    assert payload.duration == 30
    assert payload.aspect_ratio == "9:16"
    assert payload.video_model == "xai/grok-imagine-video/image-to-video"
    assert payload.function_mode == "reference"


def test_goal_parameters_and_reference_inputs_reach_video_generate():
    payload = GoalVideoPipelinePayload(
        goal="生成 30s、16:9、480p 的宣传视频",
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
    assert request["image_urls"] == ["https://example.com/frame.png"]
    assert request["video_urls"] == ["https://example.com/reference.mp4"]
    assert request["audio_urls"] == ["https://example.com/reference.mp3"]
    assert request["seed"] == 42
    assert request["negative_prompt"] == "no text"
    assert request["audio"] is True


def test_bihuo_25_normalizer_keeps_exact_30_second_duration():
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


def test_non_default_task_duration_beats_generated_six_second_copy():
    payload = GoalVideoPipelinePayload(
        goal="生成6秒抖音宣传视频",
        duration=30,
        video_model="xai/grok-imagine-video/image-to-video",
    )

    assert payload.duration == 30
    assert payload.video_model == "xai/grok-imagine-video/image-to-video"


def test_duration_aliases_are_normalized_before_validation():
    model = "xai/grok-imagine-video/image-to-video"
    seconds = GoalVideoPipelinePayload(goal="生成产品宣传片", duration="30s", video_model=model)
    chinese = GoalVideoPipelinePayload(goal="生成产品宣传片", duration="30秒", video_model=model)
    minutes = GoalVideoPipelinePayload(goal="生成产品宣传片", duration="0.5分钟", video_model=model)

    assert seconds.duration == chinese.duration == minutes.duration == 30
    assert seconds.video_model == chinese.video_model == minutes.video_model == model


def test_incompatible_default_model_does_not_auto_switch_to_bihuo_25():
    with pytest.raises(ValidationError, match="不会自动切换"):
        GoalVideoPipelinePayload(goal="生成30秒宣传视频")


def test_explicit_bihuo_25_selection_is_still_supported():
    payload = GoalVideoPipelinePayload(
        goal="生成30秒720p宣传视频",
        video_model=LONG_FORM_VIDEO_MODEL,
    )

    assert payload.video_model == LONG_FORM_VIDEO_MODEL
    assert payload.function_mode == "omini"


@pytest.mark.parametrize("goal", ["生成3秒宣传视频", "生成6秒4K宣传视频"])
def test_unsupported_explicit_parameters_are_rejected_instead_of_coerced(goal):
    with pytest.raises(ValidationError):
        GoalVideoPipelinePayload(goal=goal)


def test_top_level_goal_parameters_are_forwarded_into_nested_payload():
    normalized = _normalize_invoke_goal_video_pipeline_args(
        {
            "capability_id": "goal.video.pipeline",
            "payload": {"goal": "生成30秒视频"},
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


def test_duration_above_supported_limit_is_rejected_instead_of_shortened():
    with pytest.raises(ValidationError, match="最长支持 30 秒"):
        GoalVideoPipelinePayload(goal="生成31秒宣传视频", duration=6)
