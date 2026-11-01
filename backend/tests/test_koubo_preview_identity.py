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
