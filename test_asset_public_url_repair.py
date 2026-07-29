from types import SimpleNamespace

from backend.app.api import assets


class _Query:
    def __init__(self, row):
        self.row = row

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.row


class _Db:
    def __init__(self, row):
        self.row = row

    def query(self, *args, **kwargs):
        return _Query(self.row)


def test_missing_source_url_uploads_existing_local_asset(monkeypatch):
    row = SimpleNamespace(asset_id="photo-asset", source_url=None)
    calls = []

    def refresh(asset, request, db, *, reason):
        calls.append((asset, request, db, reason))
        return "https://cdn.example.com/photo.jpg"

    monkeypatch.setattr(assets, "_refresh_asset_source_url_from_local_file", refresh)
    request = object()
    db = _Db(row)

    result = assets.get_asset_public_url("photo-asset", 54, request, db)

    assert result == "https://cdn.example.com/photo.jpg"
    assert calls == [(row, request, db, "missing_source_url")]


def test_missing_asset_does_not_attempt_upload(monkeypatch):
    called = False

    def refresh(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(assets, "_refresh_asset_source_url_from_local_file", refresh)

    assert assets.get_asset_public_url("missing", 54, object(), _Db(None)) is None
    assert called is False
