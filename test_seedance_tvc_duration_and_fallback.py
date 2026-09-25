"""分镜台：时长向上取整（16s→20s）+ fallback 中间错误不外露 的回归测试。"""
from __future__ import annotations

import json

import pytest

from backend.app.services.comfly_seedance_tvc_pipeline_runner import _load_pipeline_module


def _config_input(**overrides):
    data = {
        "apikey": "test-key",
        "base_url": "https://example.com",
        "video_model": "grok-imagine-video-1.5-preview",
        "video_channel": "openmind",
    }
    data.update(overrides)
    return data


def test_duration_16s_rounds_up_to_20s_instead_of_failing():
    pipeline = _load_pipeline_module()
    config = pipeline._build_config(_config_input(total_duration_seconds=16))
    assert config.segment_duration_seconds == 10
    assert config.segment_count == 2
    assert config.total_duration_seconds == 20


def test_duration_15s_rounds_up_to_20s():
    pipeline = _load_pipeline_module()
    config = pipeline._build_config(_config_input(total_duration_seconds=15))
    assert (config.segment_count, config.total_duration_seconds) == (2, 20)


def test_duration_beyond_limit_still_raises():
    pipeline = _load_pipeline_module()
    with pytest.raises(pipeline.PipelineError):
        pipeline._build_config(_config_input(total_duration_seconds=999))


class _FakeClient:
    """只实现管线用到的那一个上游调用，用来驱动真实的 _submit_segment_video_to_provider。"""

    def __init__(self, config, failing_channels):
        self.config = config
        self.failing_channels = set(failing_channels)
        self.calls = []

    def submit_seedance_video(self, prompt, segment_reference_url, reference_urls, duration_seconds, action, *, channel="", model="", base_url="", segment_key=""):
        self.calls.append({"channel": channel, "model": model, "action": action})
        if channel in self.failing_channels:
            raise RuntimeError("Comfly 返回 HTTP 400: {'code': 'Arrearage'}")
        return {"id": "task-ok", "status": "submitted"}, 1


def _providers():
    return [
        {"role": "primary", "channel": "dashscope", "model": "wan3.0-video", "base_url": "https://bhzn.top/api/comfly-proxy"},
        {"role": "fallback", "channel": "comfly", "model": "grok-imagine-video-1.5", "base_url": "https://bhzn.top/api/comfly-proxy"},
    ]


def _segment_plan():
    return {
        "index": 1,
        "video_prompt": "prompt",
        "duration_seconds": 10,
        "segment_reference_result": {"url": "https://example.com/a.png"},
    }


def _prepare(pipeline, tmp_path, monkeypatch, failing_channels):
    config = pipeline._build_config(_config_input(total_duration_seconds=10))
    logger = pipeline.RunLogger(str(tmp_path), config, {"task_text": "x"})
    providers = _providers()
    monkeypatch.setattr(pipeline, "_ordered_video_providers", lambda _client: providers)
    return config, logger, _FakeClient(config, failing_channels)


def test_fallback_success_hides_intermediate_provider_error(tmp_path, monkeypatch):
    pipeline = _load_pipeline_module()
    _config, logger, client = _prepare(pipeline, tmp_path, monkeypatch, {"dashscope"})

    result = pipeline._submit_segment_video(client, logger, _segment_plan(), [])

    assert result["submit_result"]["id"] == "task-ok"
    assert [c["model"] for c in client.calls] == ["wan3.0-video", "grok-imagine-video-1.5"]

    manifest = json.loads((logger.run_dir / "manifest.json").read_text(encoding="utf-8"))
    segments = manifest.get("segments") or {}
    assert "submit_primary" not in (segments.get("1") or {}), "中间失败不能写进 segments（前端会看到）"
    assert manifest.get("errors") == [], "中间失败不能进 errors"
    assert "previous_error" not in json.dumps(segments.get("1", {}), ensure_ascii=False), "fallback 步骤不该带原始错误"
    attempts = manifest.get("provider_attempts") or []
    assert len(attempts) == 1
    assert attempts[0]["model"] == "wan3.0-video"
    assert "Arrearage" in attempts[0]["error"]


def test_all_providers_failing_still_reports_error(tmp_path, monkeypatch):
    pipeline = _load_pipeline_module()
    _config, logger, client = _prepare(pipeline, tmp_path, monkeypatch, {"dashscope", "comfly"})

    with pytest.raises(pipeline.PipelineError):
        pipeline._submit_segment_video(client, logger, _segment_plan(), [])

    manifest = json.loads((logger.run_dir / "manifest.json").read_text(encoding="utf-8"))
    stages = (manifest.get("segments") or {}).get("1") or {}
    failed_stages = [v for v in stages.values() if isinstance(v, dict) and v.get("status") == "failed"]
    assert failed_stages, "全渠道失败时必须把终态失败暴露出来"
    assert any("Arrearage" in str(v.get("error")) for v in failed_stages)
