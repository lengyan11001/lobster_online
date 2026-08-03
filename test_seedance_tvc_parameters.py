from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.api.comfly_seedance_tvc import ComflySeedancePipelinePayload, _validate_payload
from backend.app.services.comfly_seedance_tvc_pipeline_runner import (
    _load_pipeline_module,
    build_pipeline_input,
)
from mcp.http_server import _normalize_invoke_seedance_tvc_pipeline_args


def test_multiple_reference_parameters_reach_pipeline_in_order():
    pipeline_input = build_pipeline_input(
        reference_image="https://example.com/person.jpg",
        reference_images=[
            "https://example.com/person.jpg",
            "https://example.com/product.jpg",
            "https://example.com/style.jpg",
        ],
        reference_purposes=["person", "product", "style"],
        api_key="test-key",
        api_base="https://example.com/v1",
        merge_clips=False,
        storyboard_count=None,
        segment_count=2,
        segment_duration_seconds=8,
        total_duration_seconds=None,
        output_dir=None,
        platform="douyin",
        country="China",
        language="zh-CN",
        task_text="突出人物使用产品的过程",
        video_model="veo3.1",
        video_channel="yunwu",
        aspect_ratio="4:5",
        visual_tone="cinematic_contrast",
        rhythm="storytelling",
        generate_audio=False,
        watermark=True,
    )

    assert pipeline_input["reference_images"] == [
        "https://example.com/person.jpg",
        "https://example.com/product.jpg",
        "https://example.com/style.jpg",
    ]
    assert pipeline_input["reference_purposes"] == ["person", "product", "style"]
    assert pipeline_input["merge_clips"] is False
    assert pipeline_input["total_duration_seconds"] == 16
    assert pipeline_input["segment_duration_seconds"] == 8
    assert pipeline_input["aspect_ratio"] == "4:5"
    assert pipeline_input["visual_tone"] == "cinematic_contrast"
    assert pipeline_input["rhythm"] == "storytelling"
    assert pipeline_input["generate_audio"] is False
    assert pipeline_input["watermark"] is True


def test_storyboard_and_direct_prompts_use_reference_purposes_and_ui_parameters():
    pipeline = _load_pipeline_module()
    config = pipeline._build_config(
        {
            "apikey": "test-key",
            "base_url": "https://example.com",
            "reference_purposes": ["person", "product", "scene"],
            "visual_tone": "luxury_refined",
            "rhythm": "dynamic",
            "merge_clips": "false",
            "generate_audio": "false",
            "aspect_ratio": "16:9",
            "total_duration_seconds": 20,
            "segment_duration_seconds": 10,
            "video_model": "doubao-seedance-2-0-260128",
        }
    )

    analysis_prompt = pipeline._analysis_prompt(config)
    direct_prompt = pipeline._direct_video_prompt(config)

    assert config.merge_clips is False
    assert config.aspect_ratio == "16:9"
    assert "Reference image 1: person" in analysis_prompt
    assert "Reference image 2: product" in analysis_prompt
    assert "Reference image 3: scene" in analysis_prompt
    assert pipeline.VISUAL_TONE_GUIDANCE["luxury_refined"] in analysis_prompt
    assert pipeline.RHYTHM_GUIDANCE["dynamic"] in analysis_prompt
    assert "voiceover_cn must be empty" in analysis_prompt
    assert "Reference image 2: product" in direct_prompt
    assert "Do not request generated voice" in direct_prompt

    second_segment = pipeline._build_direct_segment_plan(
        config,
        ["https://example.com/person.jpg", "https://example.com/product.jpg"],
        2,
    )
    assert second_segment["segment_reference_result"]["url"] == "https://example.com/person.jpg"


def test_reference_purpose_count_must_match_reference_count():
    payload = ComflySeedancePipelinePayload(
        asset_id="asset-1",
        reference_asset_ids=["asset-2"],
        reference_purposes=["person"],
        task_text="生成分镜视频",
    )

    with pytest.raises(HTTPException, match="一一对应"):
        _validate_payload(payload)


def test_mcp_normalizer_keeps_all_visible_storyboard_parameters():
    normalized = _normalize_invoke_seedance_tvc_pipeline_args(
        {
            "capability_id": "comfly.seedance.tvc.pipeline",
            "payload": {"action": "start_pipeline", "asset_id": "asset-1"},
            "reference_asset_ids": ["asset-2", "asset-3"],
            "reference_purposes": ["person", "product", "style"],
            "merge_clips": False,
            "aspect_ratio": "1:1",
            "visual_tone": "clean_bright",
            "rhythm": "product_focus",
            "generate_audio": False,
            "watermark": True,
        }
    )

    payload = normalized["payload"]
    assert payload["reference_asset_ids"] == ["asset-2", "asset-3"]
    assert payload["reference_purposes"] == ["person", "product", "style"]
    assert payload["merge_clips"] is False
    assert payload["aspect_ratio"] == "1:1"
    assert payload["visual_tone"] == "clean_bright"
    assert payload["rhythm"] == "product_focus"
    assert payload["generate_audio"] is False
    assert payload["watermark"] is True


def test_frontend_payload_includes_structured_reference_parameters():
    script = Path("static/js/comfly-seedance-tvc-studio.js").read_text(encoding="utf-8")

    assert "reference_purposes: uploaded.map" in script
    assert "visual_tone: values.visualTone" in script
    assert "rhythm: values.rhythm" in script
    assert "task_text: values.prompt" in script
