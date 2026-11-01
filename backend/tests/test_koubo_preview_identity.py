from __future__ import annotations

import os

import pytest

from backend.app.api import skill_deps


def test_summary_video_version_uses_milliseconds(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_deps, "OUTPUT_DIR", tmp_path)
    project_dir = tmp_path / "p1"
    project_dir.mkdir()
    video = project_dir / "final.mp4"
    video.write_bytes(b"x")
    os.utime(video, ns=(1234567890123456789, 1234567890123456789))

    row = skill_deps._summary_row({"id": "p1", "status": "generated"}, set())

    assert row["has_video"] is True
    assert row["video_v"] == 1234567890123


def test_asset_cache_is_reused_only_for_the_same_photo(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_deps, "UPLOAD_DIR", tmp_path)

    same = {
        "photo_id": "same.jpg",
        "photo_asset_id": "asset-1",
        "photo_public_url": "https://example.test/same.jpg",
        "photo_asset_source_id": "same.jpg",
    }
    assert skill_deps._ensure_asset("token", same) == ("asset-1", "https://example.test/same.jpg")

    changed = {
        "photo_id": "new.jpg",
        "photo_asset_id": "asset-old",
        "photo_public_url": "https://example.test/old.jpg",
        "photo_asset_source_id": "old.jpg",
    }
    with pytest.raises(RuntimeError, match="请先在第一步上传人物照片"):
        skill_deps._ensure_asset("token", changed)
    assert "photo_asset_id" not in changed
    assert "photo_public_url" not in changed


def test_download_media_retries_ssl_eof_and_disables_proxy(tmp_path, monkeypatch):
    import ssl

    calls = {"n": 0}
    client_kwargs = {}

    class _Response:
        def __init__(self, chunks=None, fail=False):
            self.chunks = chunks or []
            self.fail = fail

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            if self.fail:
                raise ssl.SSLEOFError("EOF occurred in violation of protocol")

        def iter_bytes(self, chunk_size=262144):
            for chunk in self.chunks:
                yield chunk

    class _Client:
        def __init__(self, **kwargs):
            client_kwargs.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, _method, _url):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Response(fail=True)
            return _Response([b"abc", b"def"])

    monkeypatch.setattr(skill_deps.httpx, "Client", _Client)
    monkeypatch.setattr(skill_deps.time, "sleep", lambda _seconds: None)

    dest = tmp_path / "final.mp4"
    written = skill_deps._download_media("https://example.test/v.mp4", dest, attempts=3)

    assert written == 6
    assert dest.read_bytes() == b"abcdef"
    assert calls["n"] == 2
    assert client_kwargs["trust_env"] is False
    assert not list(tmp_path.glob("*.part"))

