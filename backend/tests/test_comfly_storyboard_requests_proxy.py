from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _load_module(rel_path: str):
    path = ROOT / rel_path
    module_name = "test_" + rel_path.replace("\\", "_").replace("/", "_").replace(".", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_seedance_storyboard_client_ignores_system_proxy(tmp_path: Path) -> None:
    mod = _load_module("skills/comfly_seedance_tvc_video/scripts/comfly_seedance_storyboard_pipeline.py")
    config = mod.PipelineConfig(base_url="https://bhzn.top/api/comfly-proxy", api_key="token")
    client = mod.ComflySeedanceClient(config, mod.RunLogger(str(tmp_path), config, {}))

    assert client.session.trust_env is False


def test_veo_storyboard_client_ignores_system_proxy(tmp_path: Path) -> None:
    mod = _load_module("skills/comfly_veo3_daihuo_video/scripts/comfly_storyboard_pipeline.py")
    config = mod.PipelineConfig(base_url="https://bhzn.top/api/comfly-proxy", api_key="token")
    client = mod.ComflyClient(config, mod.RunLogger(str(tmp_path), config, {}))

    assert client.session.trust_env is False


def test_ecommerce_detail_client_ignores_system_proxy(tmp_path: Path) -> None:
    mod = _load_module("skills/comfly_ecommerce_detail/scripts/comfly_ecommerce_detail_pipeline.py")
    config = mod.PipelineConfig(base_url="https://bhzn.top/api/comfly-proxy", api_key="token")
    client = mod.ComflyClient(config, mod.RunLogger(str(tmp_path), config, {}))

    assert client.session.trust_env is False


def test_seedance_storyboard_analysis_retries_remote_disconnect_beyond_default(tmp_path: Path) -> None:
    mod = _load_module("skills/comfly_seedance_tvc_video/scripts/comfly_seedance_storyboard_pipeline.py")
    config = mod.PipelineConfig(
        base_url="https://bhzn.top/api/comfly-proxy",
        api_key="token",
        analysis_model="gpt-5.4",
        analysis_model_fallback="off",
        analysis_retries=2,
        network_retry_delay_seconds=0,
    )
    client = mod.ComflySeedanceClient(config, mod.RunLogger(str(tmp_path), config, {}))

    class _Response:
        status_code = 200
        text = "{}"

        def json(self):
            return {"choices": [{"message": {"content": "{\"ok\": true}"}}], "usage": {}}

    calls = {"count": 0}

    def _post(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] <= 2:
            raise mod.requests.exceptions.ConnectionError(
                "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"
            )
        return _Response()

    client.session.post = _post

    result, attempts = client.analyze(["https://cdn.example.test/ref.jpg"])

    assert result["ok"] is True
    assert attempts == 3
    assert calls["count"] == 3


def test_seedance_storyboard_ffprobe_missing_degrades_cleanly(tmp_path: Path, monkeypatch) -> None:
    mod = _load_module("skills/comfly_seedance_tvc_video/scripts/comfly_seedance_storyboard_pipeline.py")
    config = mod.PipelineConfig(base_url="https://bhzn.top/api/comfly-proxy", api_key="token", ffmpeg_path="ffmpeg")
    client = mod.ComflySeedanceClient(config, mod.RunLogger(str(tmp_path), config, {}))

    monkeypatch.setattr(mod.shutil, "which", lambda _name: "")

    def _boom(*_args, **_kwargs):
        raise AssertionError("subprocess.run should not be called when ffprobe is unavailable")

    monkeypatch.setattr(mod.subprocess, "run", _boom)

    assert mod._probe_video_dimensions("C:/missing.mp4", client.config.ffmpeg_path) is None
    assert mod._probe_stream_types("C:/missing.mp4", client.config.ffmpeg_path) == []
