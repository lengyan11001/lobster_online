"""分镜台成片比例：自动纠偏 + 代理下载鉴权 的回归测试。

背景（2026-09-18 user 23 的诊断包）：
  参考图 541x720（3:4），分镜台请求 9:16，图生视频模型跟着参考图出了 3:4 的片子，
  客户端 `_aspect_matches_request`（9:16 容差 ±0.12）判失败 → 报 "video aspect ratio mismatch"，
  整段作废后把 xai（余额用尽 502）/ openmind（成片下载 401）/ comfly（上游 model_not_found）全跑一遍，
  4 分钟后整个任务失败。

覆盖：
1. 走本服务代理的 /content 下载要带 Authorization（以前只给 xing 带 → openmind 必然 401）；
2. 比例判定支持 4:5 / 3:4 等常见比例，且竖版/横版方向不能搞反；
3. 成片比例不符时自动裁/补到目标比例后继续（不再整段报废），纠不了才失败；
4. 合成阶段复用校验时纠好的本地成片，不再重新下载。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

ROOT = Path(__file__).resolve().parents[2]
PIPELINE_REL = "skills/comfly_seedance_tvc_video/scripts/comfly_seedance_storyboard_pipeline.py"


def _load_module(rel_path: str = PIPELINE_REL):
    path = ROOT / rel_path
    module_name = "test_tvc_aspect_" + rel_path.replace("/", "_").replace(".", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _client_and_logger(mod, tmp_path: Path, *, aspect_ratio: str = "9:16"):
    config = mod.PipelineConfig(
        base_url="https://bhzn.top/api/comfly-proxy",
        api_key="token",
        aspect_ratio=aspect_ratio,
        ffmpeg_path="ffmpeg",
    )
    logger_obj = mod.RunLogger(str(tmp_path), config, {})
    return mod.ComflySeedanceClient(config, logger_obj), logger_obj


def _segment_result(**overrides: Any) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "index": 3,
        "mp4url": "https://webstatic.aiproxy.vip/output/clip.mp4",
        "video_channel": "comfly",
        "video_model": "grok-imagine-video-1.5",
        "video_base_url": "https://bhzn.top/api/comfly-proxy",
        "video_task_id": "task_1",
        "video_provider_stage_role": "primary",
    }
    data.update(overrides)
    return data


# —— 1. 代理下载鉴权 ——

def test_openmind_content_download_carries_auth_header() -> None:
    mod = _load_module()
    url = "https://bhzn.top/api/comfly-proxy/openmind/v1/videos/task_ab/content"
    headers = mod._download_headers_for_url(url, "tk-123")
    assert headers is not None
    assert headers.get("Authorization") == "Bearer tk-123"


def test_xing_content_download_still_carries_auth_header() -> None:
    mod = _load_module()
    url = "https://bhzn.top/api/comfly-proxy/xing/v1/videos/task_ab/content"
    assert mod._download_headers_for_url(url, "tk-123")["Authorization"] == "Bearer tk-123"


def test_plain_cdn_download_has_no_auth_header() -> None:
    mod = _load_module()
    assert mod._download_headers_for_url("https://webstatic.aiproxy.vip/output/a.mp4", "tk-123") is None


# —— 2. 比例判定 ——

@pytest.mark.parametrize(
    "dimensions,aspect_ratio,expected",
    [
        ({"width": 1080, "height": 1920}, "9:16", True),
        ({"width": 720, "height": 1280}, "9:16", True),
        ({"width": 541, "height": 720}, "9:16", False),   # 用户这次的参考图比例 3:4
        ({"width": 541, "height": 720}, "3:4", True),
        ({"width": 864, "height": 1080}, "4:5", True),
        ({"width": 1280, "height": 720}, "9:16", False),  # 方向反了
        ({"width": 720, "height": 1280}, "16:9", False),
        ({"width": 1024, "height": 1024}, "1:1", True),
        ({"width": 1024, "height": 1024}, "9:16", False),
        ({"width": 0, "height": 0}, "9:16", True),        # 探测不到就不拦
    ],
)
def test_aspect_matches_request(dimensions, aspect_ratio, expected) -> None:
    mod = _load_module()
    assert mod._aspect_matches_request(dimensions, aspect_ratio) is expected


# —— 3. 不符时自动纠偏 ——

def test_aspect_mismatch_is_auto_fitted(monkeypatch, tmp_path: Path) -> None:
    mod = _load_module()
    client, logger_obj = _client_and_logger(mod, tmp_path)

    def fake_download(url, path, timeout_seconds, headers=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"raw-clip")
        return path

    def fake_probe(media_path, ffmpeg_path):
        # 原始成片是参考图的 3:4；纠偏后的文件是 9:16
        if "_fitted" in str(media_path):
            return {"width": 720, "height": 1280}
        return {"width": 541, "height": 720}

    def fake_conform(src_path, aspect_ratio, ffmpeg_path, out_path, mode=""):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"fitted-clip")
        return out_path

    monkeypatch.setattr(mod, "_download_file", fake_download)
    monkeypatch.setattr(mod, "_probe_video_dimensions", fake_probe)
    monkeypatch.setattr(mod, "_conform_video_to_ratio", fake_conform)

    segment_result = _segment_result()
    mod._validate_segment_video_aspect(client, logger_obj, segment_result)

    assert segment_result["aspect_fitted"] is True
    assert Path(segment_result["local_clip_path"]).name.endswith("_fitted.mp4")
    assert segment_result["fitted_dimensions"] == {"width": 720, "height": 1280}
    events = logger_obj.manifest["segments"]["3"]
    assert events["aspect_check_primary"]["status"] == "adjusted"


def test_aspect_mismatch_still_fails_when_fit_not_possible(monkeypatch, tmp_path: Path) -> None:
    mod = _load_module()
    client, logger_obj = _client_and_logger(mod, tmp_path)

    def fake_download(url, path, timeout_seconds, headers=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"raw-clip")
        return path

    monkeypatch.setattr(mod, "_download_file", fake_download)
    monkeypatch.setattr(mod, "_probe_video_dimensions", lambda p, f: {"width": 541, "height": 720})
    monkeypatch.setattr(mod, "_conform_video_to_ratio", lambda *a, **k: None)

    with pytest.raises(mod.PipelineError) as excinfo:
        mod._validate_segment_video_aspect(client, logger_obj, _segment_result())
    assert "video aspect ratio mismatch" in str(excinfo.value)
    assert logger_obj.manifest["segments"]["3"]["aspect_check_primary"]["status"] == "failed"


def test_matching_aspect_is_untouched(monkeypatch, tmp_path: Path) -> None:
    mod = _load_module()
    client, logger_obj = _client_and_logger(mod, tmp_path)

    def fake_download(url, path, timeout_seconds, headers=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"raw-clip")
        return path

    monkeypatch.setattr(mod, "_download_file", fake_download)
    monkeypatch.setattr(mod, "_probe_video_dimensions", lambda p, f: {"width": 864, "height": 1536})
    monkeypatch.setattr(
        mod, "_conform_video_to_ratio",
        lambda *a, **k: pytest.fail("比例已经对了，不该再纠偏"),
    )

    segment_result = _segment_result()
    mod._validate_segment_video_aspect(client, logger_obj, segment_result)
    assert "local_clip_path" not in segment_result
    assert logger_obj.manifest["segments"]["3"]["aspect_check_primary"]["status"] == "success"


# —— 4. 合成复用纠偏后的本地成片 ——

def test_merge_reuses_validated_local_clip(monkeypatch, tmp_path: Path) -> None:
    mod = _load_module()
    config = mod.PipelineConfig(
        base_url="https://bhzn.top/api/comfly-proxy",
        api_key="token",
        aspect_ratio="9:16",
        ffmpeg_path="ffmpeg",
        merge_clips=True,
    )
    logger_obj = mod.RunLogger(str(tmp_path), config, {})
    local_clip = tmp_path / "segment_01_fitted.mp4"
    local_clip.write_bytes(b"fitted-clip")

    def boom(*args, **kwargs):
        raise AssertionError("已经校验并纠偏过的成片不该再下载")

    monkeypatch.setattr(mod, "_download_file", boom)
    monkeypatch.setattr(mod, "_probe_video_dimensions", lambda p, f: {"width": 720, "height": 1280})

    result = mod._merge_completed_segments(
        config,
        logger_obj,
        [{"index": 1, "mp4url": "https://cdn.example/clip.mp4", "local_clip_path": str(local_clip),
          "video_channel": "comfly"}],
    )
    assert result["status"] == "success"
    assert Path(result["merged_video_path"]).read_bytes() == b"fitted-clip"


# —— 5. 入库时优先用纠偏后的本地文件 ——

def test_collect_video_urls_prefers_fitted_local_clip(tmp_path: Path) -> None:
    from backend.app.services.comfly_seedance_tvc_pipeline_runner import (
        collect_video_urls_from_pipeline_result,
    )

    local_clip = tmp_path / "segment_03_fitted.mp4"
    local_clip.write_bytes(b"fitted-clip")
    result = {
        "final_video": {"url": None, "path": None},
        "completed_shots": [
            {
                "index": 3,
                "mp4url": "https://webstatic.aiproxy.vip/output/clip.mp4",
                "local_clip_path": str(local_clip),
                "video_task_id": "task_1",
            }
        ],
    }
    assert collect_video_urls_from_pipeline_result(result) == [(str(local_clip), "task_1", "shot_3")]


# —— 6. 视频提交带「分镜段标识」（服务端做同段只扣一次）——

def test_video_submit_headers_carry_segment_key() -> None:
    mod = _load_module()
    assert mod._video_submit_headers("") == {"Content-Type": "application/json"}
    assert mod._video_submit_headers("  ") == {"Content-Type": "application/json"}
    assert mod._video_submit_headers("run_x:seg01") == {
        "Content-Type": "application/json",
        "X-Lobster-Video-Segment": "run_x:seg01",
    }


def test_submit_seedance_video_sends_segment_header(monkeypatch, tmp_path: Path) -> None:
    mod = _load_module()
    client, _logger = _client_and_logger(mod, tmp_path)
    seen: Dict[str, Any] = {}

    class _FakeResponse:
        status_code = 200
        content = b'{"task_id": "t1"}'
        text = '{"task_id": "t1"}'

        def json(self):
            return {"task_id": "t1"}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        seen["headers"] = dict(headers or {})
        return _FakeResponse()

    monkeypatch.setattr(client.session, "post", fake_post)
    client.submit_seedance_video(
        "prompt",
        "https://cdn.example/ref.png",
        [],
        10,
        "segment_01_submit_primary",
        channel="dashscope",
        model="wan3.0-video",
        base_url="https://bhzn.top/api/comfly-proxy",
        segment_key="run_20260918_120000:seg01",
    )
    assert seen["headers"].get("X-Lobster-Video-Segment") == "run_20260918_120000:seg01"


def test_submit_seedance_video_without_segment_key_keeps_plain_headers(monkeypatch, tmp_path: Path) -> None:
    mod = _load_module()
    client, _logger = _client_and_logger(mod, tmp_path)
    seen: Dict[str, Any] = {}

    class _FakeResponse:
        status_code = 200
        content = b'{"task_id": "t2"}'
        text = '{"task_id": "t2"}'

        def json(self):
            return {"task_id": "t2"}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["headers"] = dict(headers or {})
        return _FakeResponse()

    monkeypatch.setattr(client.session, "post", fake_post)
    client.submit_seedance_video(
        "prompt", "", [], 10, "segment_01_submit_primary",
        channel="dashscope", model="wan3.0-video", base_url="https://bhzn.top/api/comfly-proxy",
    )
    assert "X-Lobster-Video-Segment" not in seen["headers"]
