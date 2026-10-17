from __future__ import annotations

import asyncio
from types import SimpleNamespace

import backend.app.api.assets as assets_mod
import backend.app.api.publish as publish_mod
import backend.app.db as db_mod


class _DummySession:
    def close(self) -> None:
        pass


def test_existing_public_url_skips_refresh(monkeypatch):
    called = []

    def _fake_get(*args, **kwargs):
        called.append(args)
        return "https://cdn.example/new.mp4"

    monkeypatch.setattr(assets_mod, "get_asset_public_url", _fake_get)
    asset = SimpleNamespace(asset_id="0c40cc40785d", source_url="https://cdn.example/old.mp4")
    out = asyncio.run(publish_mod._ensure_publish_asset_public_url(asset, 260, None))
    assert out == "https://cdn.example/old.mp4"
    assert called == []


def test_missing_public_url_refreshes_from_local_file(monkeypatch):
    monkeypatch.setattr(db_mod, "SessionLocal", lambda: _DummySession())
    monkeypatch.setattr(
        assets_mod,
        "get_asset_public_url",
        lambda asset_id, user_id, request, db: "https://cdn.example/new.mp4",
    )
    asset = SimpleNamespace(asset_id="0c40cc40785d", source_url=None)
    out = asyncio.run(publish_mod._ensure_publish_asset_public_url(asset, 260, None))
    assert out == "https://cdn.example/new.mp4"
    assert asset.source_url == "https://cdn.example/new.mp4"


def test_refresh_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(db_mod, "SessionLocal", lambda: _DummySession())
    monkeypatch.setattr(assets_mod, "get_asset_public_url", lambda *args, **kwargs: None)
    asset = SimpleNamespace(asset_id="0c40cc40785d", source_url="")
    out = asyncio.run(publish_mod._ensure_publish_asset_public_url(asset, 260, None))
    assert out == ""
    assert asset.source_url == ""


def test_internal_signed_url_is_refreshed(monkeypatch):
    monkeypatch.setattr(db_mod, "SessionLocal", lambda: _DummySession())
    monkeypatch.setattr(
        assets_mod,
        "get_asset_public_url",
        lambda asset_id, user_id, request, db: "https://cdn.example/public.mp4",
    )
    asset = SimpleNamespace(
        asset_id="0c40cc40785d",
        source_url="http://127.0.0.1:8000/api/assets/file/0c40cc40785d?token=x",
    )
    out = asyncio.run(publish_mod._ensure_publish_asset_public_url(asset, 260, None))
    assert out == "https://cdn.example/public.mp4"
    assert asset.source_url == "https://cdn.example/public.mp4"
