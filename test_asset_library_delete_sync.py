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

def test_optional_upload_labels_clean_without_rejecting_blank():
    from fastapi import Form

    assert assets._clean_creative_group_name_optional("  spring   hero  ") == "spring hero"
    assert assets._clean_creative_group_name_optional("   ") == ""
    assert assets._clean_creative_group_name_optional(Form("")) == ""
    assert assets._clean_creative_group_name_optional(None) == ""
    assert assets._clean_creative_group_name_optional("g" * 50) == "g" * 40
    assert assets._clean_upload_tags(Form("")) is None
    assert assets._clean_upload_tags("  ") is None
    assert assets._clean_upload_tags("hot, hot, cover; hero detail") == "hot,cover,hero,detail"
    assert assets._clean_upload_tags(",".join(f"t{i}" for i in range(20))) == ",".join(f"t{i}" for i in range(12))
    assert assets._clean_upload_tags("x" * 80) == "x" * 40
    auto = "auto," + ("y" * 3000)
    assert assets._clean_upload_tags(auto) == auto[:2048]
    try:
        assets._clean_creative_group_name("   ")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
    else:
        raise AssertionError("required group name must still reject blanks")


def test_asset_upload_stores_optional_group_and_tags(monkeypatch):
    saved = []

    class FakeDb:
        @staticmethod
        def in_transaction():
            return False

        def add(self, row):
            saved.append(row)

        def commit(self):
            return None

    monkeypatch.setattr(assets, "_save_bytes", lambda data, ext: ("labeled", "labeled.png", len(data)))
    request = Request({"type": "http", "method": "POST", "path": "/api/assets/upload", "headers": []})

    result = asyncio.run(
        assets.upload_asset(
            request=request,
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="shot.png", file=io.BytesIO(b"png")),
            creative_candidate_group="  spring   hero  ",
            tags="hot, hot, cover",
            current_user=SimpleNamespace(id=7),
            db=FakeDb(),
        )
    )

    assert result["creative_candidate_group"] == "spring hero"
    assert saved[0].tags == "hot,cover"
    assert saved[0].meta["creative_candidate_group"] == "spring hero"
    assert saved[0].meta["creative_candidate_groups"] == ["spring hero"]
    assert saved[0].meta["asset_origin"] == "user_upload"


def test_asset_upload_ignores_omitted_form_defaults(monkeypatch):
    saved = []

    class FakeDb:
        @staticmethod
        def in_transaction():
            return False

        def add(self, row):
            saved.append(row)

        def commit(self):
            return None

    monkeypatch.setattr(assets, "_save_bytes", lambda data, ext: ("plain", "plain.pdf", 3))
    request = Request({"type": "http", "method": "POST", "path": "/api/assets/upload", "headers": []})

    asyncio.run(
        assets.upload_asset(
            request=request,
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="intro.pdf", file=io.BytesIO(b"pdf")),
            current_user=SimpleNamespace(id=7),
            db=FakeDb(),
        )
    )

    assert saved[0].tags is None
    assert "creative_candidate_group" not in saved[0].meta
    assert "creative_candidate_groups" not in saved[0].meta


def test_user_upload_save_url_cleans_labels_without_touching_generated_tags():
    source = inspect.getsource(assets._save_asset_from_url_locked)
    assert 'if asset_origin == "user_upload":' in source
    assert "stored_tags = _clean_upload_tags(body.tags)" in source
    assert "tags=stored_tags" in source
    backfill = inspect.getsource(assets._maybe_backfill_prompt_model_on_dedupe)
    assert "tags" not in backfill
    assert "creative_candidate_group" not in backfill


def test_creative_group_summaries_count_images_and_keep_other_media():
    rows = [
        SimpleNamespace(media_type="image", model="", meta={"creative_candidate_group": "A", "asset_origin": "user_upload"}),
        SimpleNamespace(media_type="video", model="", meta={"creative_candidate_group": "A", "asset_origin": "user_upload"}),
        SimpleNamespace(media_type="image", model="", meta={"creative_candidate_group": "B", "content_visibility": "hidden"}),
        SimpleNamespace(media_type="image", model="shanjian-digital-human-template-media", meta={"creative_candidate_group": "C"}),
        SimpleNamespace(media_type="document", model="", meta={"creative_candidate_group": "D", "asset_origin": "user_upload"}),
        SimpleNamespace(media_type="image", model="", meta={"asset_origin": "user_upload"}),
    ]

    groups = {item["name"]: item for item in assets._creative_candidate_group_summaries(rows)}

    assert groups["A"]["count"] == 1
    assert groups["D"]["count"] == 0
    assert "B" not in groups
    assert "C" not in groups


def test_upload_form_keeps_shared_optional_labels():
    view = (Path(__file__).parent / "static" / "views" / "assets.html").read_text(encoding="utf-8")
    script = (Path(__file__).parent / "static" / "js" / "publish.js").read_text(encoding="utf-8")
    assert 'id="assetUploadGroup"' in view
    assert 'id="assetUploadTags"' in view
    assert "asset-upload-control" in view
    assert "var uploadLabels = _assetUploadOptionalLabels();" in script
    assert "if (uploadLabels.group) fd.append('creative_candidate_group', uploadLabels.group);" in script
    assert "assetUploadGroup.value = ''" not in script
    assert "assetUploadTags.value = ''" not in script
class _LabelDb:
    def __init__(self, row):
        self.row = row
        self.added = []
        self.commits = 0

    def query(self, model):
        return self

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.row

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commits += 1


def _label_request():
    return Request({"type": "http", "method": "POST", "path": "/api/assets/local-asset/labels", "headers": []})


def _patch_label_sync(monkeypatch, calls):
    monkeypatch.setattr(assets, "_auth_server_base_url", lambda: "https://server.example.test")
    monkeypatch.setattr(assets, "_forward_auth_headers", lambda request: {"Authorization": "Bearer test"})
    monkeypatch.setattr(assets.httpx, "Client", lambda *args, **kwargs: _Client(calls, *args, **kwargs))


def test_label_sync_registers_when_remote_id_missing(monkeypatch):
    calls = []
    _patch_label_sync(monkeypatch, calls)
    row = _asset({"asset_origin": "user_upload", "keep": 1})
    db = _LabelDb(row)

    result = assets.update_asset_labels(
        "local-asset",
        assets.AssetLabelsReq(creative_candidate_group="spring hero", tags="hot,cover"),
        _label_request(),
        SimpleNamespace(id=1),
        db,
    )

    assert result["ok"] is True
    assert result["creative_candidate_group"] == "spring hero"
    assert result["tags"] == "hot,cover"
    assert [call[0] for call in calls] == ["POST"]
    assert calls[0][1].endswith("/api/assets/register-url")
    assert calls[0][2]["json"]["creative_candidate_group"] == "spring hero"
    assert calls[0][2]["json"]["tags"] == "hot,cover"
    assert "creative_candidate_groups" in calls[0][2]["json"]
    assert row.meta["remote_asset_id"] == "remote-asset"
    assert row.meta["keep"] == 1
    assert row.tags == "hot,cover"
    assert db.commits >= 1


def test_label_sync_clears_existing_remote_asset(monkeypatch):
    calls = []
    _patch_label_sync(monkeypatch, calls)
    row = _asset({
        "asset_origin": "user_upload",
        "remote_asset_id": "remote-9",
        "keep": 1,
        "creative_candidate_group": "old",
        "creative_candidate_groups": ["old"],
    })
    row.tags = "old"
    db = _LabelDb(row)

    result = assets.update_asset_labels(
        "local-asset",
        assets.AssetLabelsReq(creative_candidate_group="", tags=""),
        _label_request(),
        SimpleNamespace(id=1),
        db,
    )

    assert result["creative_candidate_group"] == ""
    assert result["tags"] == ""
    assert [call[0] for call in calls] == ["POST"]
    assert calls[0][1].endswith("/api/assets/remote-9/labels")
    assert calls[0][2]["json"] == {"creative_candidate_group": "", "tags": ""}
    assert "creative_candidate_group" not in row.meta
    assert "creative_candidate_groups" not in row.meta
    assert row.meta["remote_asset_id"] == "remote-9"
    assert row.meta["keep"] == 1
    assert row.tags is None


def test_label_sync_skips_request_when_blank_and_unregistered(monkeypatch):
    calls = []
    _patch_label_sync(monkeypatch, calls)
    row = _asset({"asset_origin": "user_upload", "keep": 1})
    db = _LabelDb(row)

    result = assets.update_asset_labels(
        "local-asset",
        assets.AssetLabelsReq(creative_candidate_group="   ", tags="  "),
        _label_request(),
        SimpleNamespace(id=1),
        db,
    )

    assert result["ok"] is True
    assert calls == []
    assert "remote_asset_id" not in row.meta
    assert "creative_candidate_group" not in row.meta
    assert row.tags is None
    assert row.meta["keep"] == 1


def test_label_sync_failure_keeps_local_save(monkeypatch):
    class _BoomClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            raise RuntimeError("sync down")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(assets, "_auth_server_base_url", lambda: "https://server.example.test")
    monkeypatch.setattr(assets, "_forward_auth_headers", lambda request: {"Authorization": "Bearer test"})
    monkeypatch.setattr(assets.httpx, "Client", _BoomClient)
    row = _asset({"asset_origin": "user_upload", "remote_asset_id": "remote-9", "keep": 1})
    db = _LabelDb(row)

    result = assets.update_asset_labels(
        "local-asset",
        assets.AssetLabelsReq(creative_candidate_group="spring hero", tags="hot"),
        _label_request(),
        SimpleNamespace(id=1),
        db,
    )

    assert result["ok"] is True
    assert result["creative_candidate_group"] == "spring hero"
    assert row.meta["creative_candidate_group"] == "spring hero"
    assert row.meta["keep"] == 1
    assert row.tags == "hot"
    assert db.commits >= 1
