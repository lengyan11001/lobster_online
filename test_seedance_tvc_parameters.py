from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.api import h5_chat_channel as channel
from backend.app.api.comfly_seedance_tvc import ComflySeedancePipelinePayload, _validate_payload
from backend.app.services.comfly_seedance_tvc_pipeline_runner import (
    _load_pipeline_module,
    build_pipeline_input,
    resolve_reference_images_for_pipeline_async,
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


def test_h5_seedance_payload_matches_online_workbench_defaults():
    payload = channel._normalize_seedance_tvc_scheduled_payload(
        {
            "image_url": "https://example.com/main.png",
            "reference_image_urls": ["https://example.com/style.png"],
            "task_text": "做一条高质感品牌分镜头视频",
            "total_duration_seconds": 15,
            "aspect_ratio": "16:9",
        }
    )

    assert payload["video_model"] == "grok-imagine-video-1.5-preview"
    assert payload["video_channel"] == "openmind"
    assert payload["segment_duration_seconds"] == 10
    assert payload["segment_count"] == 2
    assert payload["total_duration_seconds"] == 20
    assert payload["workflow_mode"] == "storyboard"
    assert payload["merge_clips"] is True
    assert payload["auto_save"] is True
    assert payload["generate_audio"] is True
    assert payload["image_model_fallback"] == "gpt-image-2-yunwu"
    assert payload["reference_purposes"] == ["storyboard", "storyboard"]


def test_seedance_reference_url_is_transferred_before_pipeline(monkeypatch):
    async def fake_transfer(url, media_type="image", user=None, request=None):
        assert media_type == "image"
        assert "token=old" in url
        return "https://cdn.example.com/reference.png"

    from backend.app.services import comfly_seedance_tvc_pipeline_runner as runner

    monkeypatch.setattr(runner, "_is_internal_asset_http_url", lambda url: "bhzn.top" in str(url))
    monkeypatch.setattr(runner, "_source_url_is_fetchable_for_upstream", lambda url: "cdn.example.com" in str(url))
    monkeypatch.setattr(runner, "_transfer_url_via_sutui", fake_transfer)

    urls = asyncio.run(
        resolve_reference_images_for_pipeline_async(
            user_id=1,
            db=object(),
            request=object(),
            asset_id=None,
            image_url="https://bhzn.top/api/assets/file/a?token=old",
            reference_image_urls=["https://cdn.example.com/second.png"],
            user=object(),
        )
    )

    assert urls == ["https://cdn.example.com/reference.png", "https://cdn.example.com/second.png"]


def test_seedance_reference_url_blocks_when_transfer_fails(monkeypatch):
    async def fake_transfer(*_args, **_kwargs):
        return None

    from backend.app.services import comfly_seedance_tvc_pipeline_runner as runner

    monkeypatch.setattr(runner, "_is_internal_asset_http_url", lambda _url: True)
    monkeypatch.setattr(runner, "_transfer_url_via_sutui", fake_transfer)

    with pytest.raises(HTTPException, match="上游可访问"):
        asyncio.run(
            resolve_reference_images_for_pipeline_async(
                user_id=1,
                db=object(),
                request=object(),
                asset_id=None,
                image_url="http://127.0.0.1:8000/api/assets/file/a?token=old",
                user=object(),
            )
        )


def test_h5_seedance_scheduled_pipeline_uses_online_workbench_api(monkeypatch):
    posted = []
    polled = []

    async def fake_event(*_args, **_kwargs):
        return None

    async def fake_post(path, body, *, headers, timeout_seconds=7200.0):
        posted.append((path, body, headers, timeout_seconds))
        return {
            "ok": True,
            "job_id": "job-seedance",
            "poll_path": "/api/comfly-seedance-tvc/pipeline/jobs/job-seedance",
        }

    async def fake_get(path, *, headers, timeout_seconds=120.0):
        polled.append((path, headers, timeout_seconds))
        return {
            "ok": True,
            "job_id": "job-seedance",
            "status": "completed",
            "result": {"final_video": {"url": "https://cdn.example.com/final.mp4"}},
            "saved_assets": [],
        }

    monkeypatch.setattr(channel, "_post_task_event", fake_event)
    monkeypatch.setattr(channel, "_post_local_api_json", fake_post)
    monkeypatch.setattr(channel, "_get_local_api_json", fake_get)

    result = asyncio.run(
        channel._run_seedance_tvc_scheduled_pipeline(
            cap_payload={
                "task_text": "用参考图做一条分镜头短视频",
                "image_url": "https://example.com/main.png",
                "total_duration_seconds": 10,
            },
            headers={"Authorization": "Bearer test"},
            cloud=object(),
            base="https://cloud.example.com",
            run_id="run-seedance",
        )
    )

    assert posted[0][0] == "/api/comfly-seedance-tvc/pipeline/start"
    assert posted[0][1]["payload"]["video_model"] == "grok-imagine-video-1.5-preview"
    assert posted[0][1]["payload"]["video_channel"] == "openmind"
    assert polled[0][0].endswith("compact=false")
    assert result["source_mode"] == "seedance_tvc_studio"
    assert result["job_id"] == "job-seedance"


def test_scheduled_seedance_capability_dispatches_to_workbench(monkeypatch):
    completed = []
    invoked = []

    async def fake_event(*_args, **_kwargs):
        return None

    async def fake_caption(*_args, **_kwargs):
        return "发布文案"

    async def fake_run_seedance(*, cap_payload, **_kwargs):
        invoked.append(cap_payload)
        return {
            "ok": True,
            "status": "completed",
            "source_mode": "seedance_tvc_studio",
            "final_video": {"url": "https://cdn.example.com/final.mp4"},
            "saved_assets": [],
        }

    async def fail_local_capability(**_kwargs):
        raise AssertionError("seedance scheduled tasks must not call MCP invoke_capability")

    async def fake_complete(_cloud, _base, _headers, run_id, **kwargs):
        completed.append((run_id, kwargs))

    monkeypatch.setattr(channel, "_post_task_event", fake_event)
    monkeypatch.setattr(channel, "_generate_scheduled_caption", fake_caption)
    monkeypatch.setattr(channel, "_run_seedance_tvc_scheduled_pipeline", fake_run_seedance)
    monkeypatch.setattr(channel, "_invoke_local_capability", fail_local_capability)
    monkeypatch.setattr(channel, "_complete_task_run", fake_complete)

    asyncio.run(
        channel._run_scheduled_capability(
            object(),
            "https://cloud.example.com",
            {"Authorization": "Bearer test"},
            {
                "id": "run-seedance",
                "title": "创意分镜头视频",
                "payload": {
                    "capability_id": "comfly.seedance.tvc.pipeline",
                    "payload": {
                        "task_text": "做一条品牌分镜头视频",
                        "image_url": "https://example.com/main.png",
                        "total_duration_seconds": 10,
                    },
                },
            },
            jwt_token="token",
            installation_id="installation",
        )
    )

    assert invoked
    assert invoked[0]["video_model"] == "grok-imagine-video-1.5-preview"
    assert completed[0][0] == "run-seedance"
    assert completed[0][1]["result_payload"]["capability_id"] == "comfly.seedance.tvc.pipeline"
