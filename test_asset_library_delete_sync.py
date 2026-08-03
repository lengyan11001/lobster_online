from types import SimpleNamespace
import inspect
from pathlib import Path

from backend.app.api import assets


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload


class _Client:
    def __init__(self, calls, *args, **kwargs):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return _Response(payload={"asset_id": "remote-asset"})

    def delete(self, url, **kwargs):
        self.calls.append(("DELETE", url, kwargs))
        return _Response()


def _asset(meta):
    return SimpleNamespace(
        asset_id="local-asset",
        media_type="image",
        filename="photo.jpg",
        file_size=123,
        source_url="https://cdn.example.test/photo.jpg",
        meta=meta,
    )


def test_user_upload_delete_removes_known_cloud_copy(monkeypatch):
    calls = []
    monkeypatch.setattr(assets, "_auth_server_base_url", lambda: "https://server.example.test")
    monkeypatch.setattr(assets, "_forward_auth_headers", lambda request: {"Authorization": "Bearer test"})
    monkeypatch.setattr(assets.httpx, "Client", lambda *args, **kwargs: _Client(calls, *args, **kwargs))

    assets._delete_remote_user_upload_asset(
        _asset({"asset_origin": "user_upload", "remote_asset_id": "remote-asset"}),
        object(),
    )

    assert [call[0] for call in calls] == ["DELETE"]
    assert calls[0][1].endswith("/api/assets/remote-asset")


def test_user_upload_delete_resolves_legacy_cloud_copy_before_delete(monkeypatch):
    calls = []
    monkeypatch.setattr(assets, "_auth_server_base_url", lambda: "https://server.example.test")
    monkeypatch.setattr(assets, "_forward_auth_headers", lambda request: {"Authorization": "Bearer test"})
    monkeypatch.setattr(assets.httpx, "Client", lambda *args, **kwargs: _Client(calls, *args, **kwargs))

    assets._delete_remote_user_upload_asset(_asset({"asset_origin": "user_upload"}), object())

    assert [call[0] for call in calls] == ["POST", "DELETE"]
    assert calls[0][2]["json"]["source_asset_id"] == "local-asset"
    assert calls[1][1].endswith("/api/assets/remote-asset")


def test_generated_asset_delete_does_not_call_cloud(monkeypatch):
    monkeypatch.setattr(
        assets.httpx,
        "Client",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cloud must not be called")),
    )

    assets._delete_remote_user_upload_asset(_asset({"asset_origin": "generated"}), object())


def test_asset_listing_never_probes_or_repairs_remote_urls():
    source = inspect.getsource(assets.list_assets)

    assert "get_asset_public_url" not in source
    assert "_source_url_is_fetchable_for_upstream" not in source
    assert "has_external_source" in source


def test_asset_library_starts_list_before_optional_group_refresh():
    source = (Path(__file__).parent / "static" / "js" / "publish.js").read_text(encoding="utf-8")
    start = source.index("function initAssetLibraryView()")
    end = source.index("window.initAssetLibraryView", start)
    body = source[start:end]

    assert body.index("loadAssets(_currentAssetSearchQuery())") < body.index("loadCreativeCandidateGroups()")


def test_top_navigation_buttons_are_excluded_from_drag_capture():
    source = (Path(__file__).parent / "static" / "js" / "init.js").read_text(encoding="utf-8")

    assert "event.target.closest('button, a, input, select, textarea, [role=\"button\"]')" in source
