import asyncio
import io
from types import SimpleNamespace
import inspect
from pathlib import Path

from starlette.datastructures import UploadFile
from starlette.background import BackgroundTasks
from starlette.requests import Request

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


def test_asset_upload_reports_backend_or_network_failure_detail():
    source = (Path(__file__).parent / "static" / "js" / "publish.js").read_text(encoding="utf-8")

    assert "return r.text().then(function(raw)" in source
    assert "uploadErrors.push" in source
    assert "失败原因：" in source
    assert "无法连接本机服务或云端上传接口" in source
    assert "isErr ? 12000 : 4000" in source


def test_asset_upload_saves_locally_and_defers_public_url_until_use():
    upload_source = inspect.getsource(assets.upload_asset)
    helper_source = inspect.getsource(assets._upload_bytes_to_auth_server)
    header_source = inspect.getsource(assets._auth_server_upload_headers)

    assert "_save_bytes" in upload_source
    assert "_upload_to_tos" not in upload_source
    assert "_upload_bytes_to_auth_server" not in upload_source
    assert 'public_url_status = "preparing" if upload_headers else "deferred_until_use"' in upload_source
    assert '"public_url_status": public_url_status' in upload_source
    assert 'source_url=None' in upload_source
    assert "range(1, _AUTH_SERVER_UPLOAD_MAX_ATTEMPTS + 1)" in helper_source
    assert "trust_env=False" in helper_source
    assert 'headers["X-Lobster-Brand"] = brand_mark' in header_source

    ui_source = (Path(__file__).parent / "static" / "js" / "publish.js").read_text(encoding="utf-8")
    assert "正在保存到本地素材库" in ui_source
    assert "本地保存完成" in ui_source


def test_manual_url_assets_stay_in_the_user_upload_library():
    body = assets.SaveAssetReq(
        url="https://cdn.example.test/manual-reference.png",
        asset_origin="user_upload",
    )

    assert assets._save_asset_origin(body) == "user_upload"
    assert "if _save_asset_origin(body) != \"generated\":" in inspect.getsource(
        assets._report_generation_record_to_server
    )
    assert '"asset_origin": asset_origin' in inspect.getsource(assets._save_asset_from_url_locked)


def test_asset_upload_returns_after_local_database_save(monkeypatch):
    saved = []

    class FakeDb:
        commits = 0

        @staticmethod
        def in_transaction():
            return True

        def add(self, row):
            saved.append(row)

        def commit(self):
            self.commits += 1

    def fake_save(data, ext):
        assert data == b"document"
        assert ext == ".pdf"
        return "local-asset", "local-asset.pdf", len(data)

    monkeypatch.setattr(assets, "_save_bytes", fake_save)
    monkeypatch.setattr(assets, "_upload_to_tos", lambda *_args: (_ for _ in ()).throw(AssertionError("must stay local")))
    request = Request({"type": "http", "method": "POST", "path": "/api/assets/upload", "headers": []})
    upload = UploadFile(filename="intro.pdf", file=io.BytesIO(b"document"))
    db = FakeDb()

    result = asyncio.run(
        assets.upload_asset(
            request=request,
            background_tasks=BackgroundTasks(),
            file=upload,
            current_user=SimpleNamespace(id=7),
            db=db,
        )
    )

    assert result["asset_id"] == "local-asset"
    assert result["local_only"] is True
    assert result["source_url"] is None
    assert saved[0].source_url is None
    assert saved[0].meta["public_url_status"] == "deferred_until_use"


def test_top_navigation_buttons_are_excluded_from_drag_capture():
    source = (Path(__file__).parent / "static" / "js" / "init.js").read_text(encoding="utf-8")

    assert "event.target.closest('button, a, input, select, textarea, [role=\"button\"]')" in source
