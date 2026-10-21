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


MODULE = "skills/comfly_seedance_tvc_video/scripts/comfly_seedance_storyboard_pipeline.py"


def _client(tmp_path: Path):
    mod = _load_module(MODULE)
    config = mod.PipelineConfig(base_url="https://bhzn.top/api/comfly-proxy", api_key="secret-key")
    client = mod.ComflySeedanceClient(config, mod.RunLogger(str(tmp_path), config, {}))
    return mod, client


def test_media_session_has_no_api_authorization(tmp_path: Path) -> None:
    """参考图下载不能带 Comfly 的 Bearer；TOS 见到 Authorization 会直接 400。"""
    _, client = _client(tmp_path)
    media = client._media_session()
    assert "Authorization" not in media.headers
    assert media.headers.get("Accept") == "*/*"
    # API session 本身必须保持带 token，不能把两者混在一起
    assert client.session.headers.get("Authorization") == "Bearer secret-key"


def test_image_url_uses_media_download(tmp_path: Path, monkeypatch) -> None:
    _, client = _client(tmp_path)
    calls = []

    class FakeResp:
        status_code = 200
        content = b"png-bytes"
        headers = {"content-type": "image/png"}

    def fake_download(url, *, retries=3):
        calls.append(url)
        return FakeResp()

    monkeypatch.setattr(client, "_download_media_bytes", fake_download)
    data_url = client._image_url_to_data_url("https://tos.example.com/a.png")
    assert calls == ["https://tos.example.com/a.png"]
    assert data_url.startswith("data:image/png;base64,")


def test_media_download_retries_transient_400(tmp_path: Path, monkeypatch) -> None:
    _, client = _client(tmp_path)
    seq = [400, 200]
    seen_headers = []

    class FakeResp:
        def __init__(self, code):
            self.status_code = code
            self.text = "bad"
            self.content = b"ok"

    class FakeSession:
        headers = {}
        def get(self, url, timeout=120, allow_redirects=True):
            seen_headers.append(dict(self.headers))
            return FakeResp(seq.pop(0) if seq else 200)

    monkeypatch.setattr(client, "_media_session", lambda: FakeSession())
    monkeypatch.setattr("time.sleep", lambda *_: None)
    resp = client._download_media_bytes("https://tos.example.com/a.png", retries=2)
    assert resp.status_code == 200
    assert len(seen_headers) == 2