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
