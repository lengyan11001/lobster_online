from pathlib import Path

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
