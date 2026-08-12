from pathlib import Path
import os

import pytest

from backend.app.api import assets
from backend.app.models import Asset


class _FakeQuery:
    def __init__(self, row):
        self._row = row

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._row


class _FakeDb:
    def __init__(self, row):
        self.row = row
        self.added = []
        self.commit_count = 0

    def query(self, _model):
        return _FakeQuery(self.row)

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commit_count += 1


def _request():
    class _Request:
        headers = {"Authorization": "Bearer user-token"}

    return _Request()


def test_get_asset_public_url_retries_local_refresh_after_transient_disconnect(monkeypatch, tmp_path):
    local_file = tmp_path / "demo.png"
    local_file.write_bytes(b"fake-image")
    row = Asset(
        asset_id="88d4bbb0b45d",
        user_id=31,
        filename="88d4bbb0b45d.png",
        media_type="image",
        file_size=10,
        source_url=None,
        meta={},
    )
    db = _FakeDb(row)
    calls = []

    monkeypatch.setattr(assets, "_asset_local_path", lambda _row: local_file)

    def _fake_upload(path: Path, filename: str, content_type: str, request):
        calls.append((path, filename, content_type, request))
        if len(calls) == 1:
            return None, {"error": "RemoteProtocolError: Server disconnected without sending a response."}
        return "https://cdn.example.test/assets/88d4bbb0b45d.png", {"http_status": 200}

    monkeypatch.setattr(assets, "_upload_local_asset_to_auth_server_sync", _fake_upload)

    assert assets.get_asset_public_url("88d4bbb0b45d", 31, _request(), db) == (
        "https://cdn.example.test/assets/88d4bbb0b45d.png"
    )
    assert len(calls) == 2
    assert row.source_url == "https://cdn.example.test/assets/88d4bbb0b45d.png"
    assert db.commit_count == 1


def test_local_refresh_optimizes_large_image_and_uses_bounded_timeout(monkeypatch, tmp_path):
    pil_image = pytest.importorskip("PIL.Image")
    from PIL import Image

    source = tmp_path / "large.png"
    Image.frombytes("RGB", (1200, 1200), os.urandom(1200 * 1200 * 3)).save(source, "PNG")
    original_size = source.stat().st_size
    assert original_size > assets._AUTH_SERVER_IMAGE_UPLOAD_MAX_BYTES

    class _Settings:
        auth_server_base = "https://bhzn.top"

    class _Response:
        status_code = 200
        content = b"{}"
        text = "{}"

        def json(self):
            return {"public_url": "https://cdn.example.test/assets/large.jpg", "storage": "tos"}

    captured = {}

    class _Client:
        def __init__(self, *, timeout, follow_redirects, trust_env):
            captured["timeout"] = timeout
            captured["follow_redirects"] = follow_redirects
            captured["trust_env"] = trust_env

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, upload_url, *, files, headers):
            upload_filename, file_obj, upload_content_type = files["file"]
            blob = file_obj.read()
            captured.update(
                {
                    "upload_url": upload_url,
                    "filename": upload_filename,
                    "content_type": upload_content_type,
                    "upload_size": len(blob),
                    "headers": headers,
                }
            )
            return _Response()

    monkeypatch.setattr(assets, "get_settings", lambda: _Settings())
    monkeypatch.setattr(assets.httpx, "Client", _Client)

    public_url, diag = assets._upload_local_asset_to_auth_server_sync(
        source,
        "large.png",
        "image/png",
        _request(),
    )

    assert public_url == "https://cdn.example.test/assets/large.jpg"
    assert captured["filename"].endswith(".jpg")
    assert captured["content_type"] == "image/jpeg"
    assert captured["upload_size"] < original_size
    assert captured["trust_env"] is False
    assert captured["timeout"].read <= 60
    assert diag["optimized_for_cloud_upload"] is True
