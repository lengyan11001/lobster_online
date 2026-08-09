import asyncio
import inspect
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from starlette.requests import Request

from backend.app.api import assets


def _asset(**overrides):
    values = {
        "asset_id": "local-image-001",
        "source_url": "https://cdn.example.test/generated/image-001.png",
        "media_type": "image",
        "filename": "image-001.png",
        "file_size": 1234,
        "prompt": "street-level storefront photo",
        "model": "image-model",
        "tags": "campaign",
        "meta": {
            "asset_origin": "generated",
            "content_context": {
                "title": "Campaign image",
                "description": "Ready to publish",
                "creative_prompt": "bright storefront",
            },
            "generation_task_id": "task-001",
        },
        "created_at": datetime(2026, 8, 8, 8, 30, 0),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_asset_sync_payload_preserves_content_context():
    payload = assets._asset_sync_payload(_asset())

    assert payload["url"].endswith("image-001.png")
    assert payload["source_asset_id"] == "local-image-001"
    assert payload["asset_origin"] == "generated"
    assert payload["title"] == "Campaign image"
    assert payload["description"] == "Ready to publish"
    assert payload["creative_prompt"] == "bright storefront"
    assert payload["generation_task_id"] == "task-001"
    assert payload["source_created_at"] == "2026-08-08T08:30:00"


def test_asset_sync_payload_excludes_local_hidden_and_template_media():
    assert assets._asset_sync_payload(_asset(source_url="http://127.0.0.1:8000/file.png")) is None
    assert assets._asset_sync_payload(_asset(meta={"asset_origin": "generated", "content_visibility": "hidden"})) is None
    assert assets._asset_sync_payload(_asset(meta={"asset_origin": "intermediate"})) is None
    assert assets._asset_sync_payload(_asset(model="shanjian-digital-human-template-media")) is None


def test_forwarded_asset_sync_headers_keep_oem_brand():
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/assets/sync-library",
        "headers": [
            (b"authorization", b"Bearer test-token"),
            (b"x-installation-id", b"device-001"),
            (b"x-lobster-brand", b"hikong"),
        ],
    })

    assert assets._forward_auth_headers(request) == {
        "Authorization": "Bearer test-token",
        "X-Installation-Id": "device-001",
        "X-Lobster-Brand": "hikong",
    }


def test_batch_sync_marks_local_asset_after_cloud_registration(monkeypatch):
    payload = assets._asset_sync_payload(_asset())
    calls = []
    row = _asset(meta={"asset_origin": "generated"})

    class Response:
        status_code = 200
        content = b"{}"
        text = ""

        @staticmethod
        def json():
            return {
                "ok": True,
                "created": 1,
                "updated": 0,
                "items": [{"source_asset_id": "local-image-001", "asset_id": "cloud-image-001"}],
            }

    class AsyncClient:
        def __init__(self, *args, **kwargs):
            assert kwargs["trust_env"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    class Query:
        def filter(self, *args):
            return self

        @staticmethod
        def all():
            return [row]

    class Db:
        committed = False
        closed = False

        @staticmethod
        def query(*args):
            return Query()

        @staticmethod
        def add(item):
            assert item is row

        def commit(self):
            self.committed = True

        def close(self):
            self.closed = True

    db = Db()
    monkeypatch.setattr(assets, "_auth_server_base_url", lambda: "https://server.example.test")
    monkeypatch.setattr(assets, "_pending_asset_sync_payloads", lambda *args, **kwargs: [payload])
    monkeypatch.setattr(assets.httpx, "AsyncClient", AsyncClient)
    monkeypatch.setattr(assets, "SessionLocal", lambda: db)
    assets._asset_library_sync_locks.clear()

    result = asyncio.run(assets._sync_local_asset_library_to_auth_server(
        7,
        {"Authorization": "Bearer token", "X-Lobster-Brand": "hikong"},
        max_batches=1,
        batch_size=100,
    ))

    assert result == {"ok": True, "synced": 1, "created": 1, "updated": 0, "has_more": False}
    assert calls[0][0] == "https://server.example.test/api/assets/register-batch"
    assert calls[0][1]["headers"]["X-Lobster-Brand"] == "hikong"
    assert calls[0][1]["json"]["assets"] == [payload]
    assert row.meta["remote_asset_id"] == "cloud-image-001"
    assert db.committed is True
    assert db.closed is True


def test_asset_listing_schedules_reconciliation_after_local_response():
    source = inspect.getsource(assets.list_assets)

    assert not inspect.iscoroutinefunction(assets.list_assets)
    assert "background_tasks.add_task" in source
    assert "_sync_local_asset_library_to_auth_server" in source
    assert source.index("out.append") < source.index("background_tasks.add_task") < source.index("return")


def test_login_starts_non_blocking_asset_reconciliation():
    source = (Path(__file__).parent / "static" / "js" / "init.js").read_text(encoding="utf-8")

    assert "function syncAssetLibraryFromLocalIfOnline()" in source
    assert "/api/assets/sync-library?origin=generated&max_batches=5&batch_size=100" in source
    assert "syncAssetLibraryFromLocalIfOnline();" in source
    assert "result.has_more && rounds < 10" in source
    assert "function scheduleNextAssetLibrarySync(userId, delay)" in source
    assert "scheduleNextAssetLibrarySync(userId, 120000)" in source
