"""过程素材不给用户展示：素材库与内容库都只留最终交付件（2026-09-22 口径）。

背景（uid 54 实测）：数字人配音 164、同城爆款加字幕件 123、试听/转存件 66 …
这些过程件没被标中间产物，于是既有 素材库「生成素材」tab，也会被同步进云端内容库。
"""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import backend.app.api.h5_chat_channel as h5_chat_channel

h5_chat_channel._install_douyin_origin_import_path()
import douyin_comment_scraper  # noqa: F401,E402  (保证导入路径就绪)

from backend.app.api import assets as assets_api  # noqa: E402
from backend.app.api import comfly_seedance_tvc as seedance  # noqa: E402
from backend.app.db import Base  # noqa: E402
from backend.app.models import Asset  # noqa: E402


def _memory_session(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(seedance, "SessionLocal", session_factory)
    return session_factory


def test_process_capabilities_are_marked_intermediate():
    import mcp.http_server as mcp_server

    assert mcp_server._auto_save_is_process_asset("sutui.transfer_url", {}) is True
    assert mcp_server._auto_save_is_process_asset("sutui.speak", {}) is True
    assert (
        mcp_server._auto_save_is_process_asset(
            "task.get_result", {"capability_id": "sutui.transfer_url"}
        )
        is True
    )
    assert mcp_server._auto_save_is_process_asset("image.generate", {}) is False
    assert mcp_server._auto_save_is_process_asset("video.generate", {}) is False


def test_remote_process_items_are_not_mirrored_into_library():
    assert assets_api._remote_asset_item_is_process({"asset_origin": "intermediate"}) is True
    assert assets_api._remote_asset_item_is_process({"content_visibility": "hidden"}) is True
    assert assets_api._remote_asset_item_is_process({"library_visibility": "internal"}) is True
    assert assets_api._remote_asset_item_is_process({"asset_origin": "generated"}) is False
    assert assets_api._remote_asset_item_is_process({}) is False


def test_caption_and_post_assets_are_internal_by_default(tmp_path, monkeypatch):
    _memory_session(monkeypatch)
    monkeypatch.setattr(seedance, "ASSETS_DIR", tmp_path)
    monkeypatch.setattr(seedance, "_upload_to_tos", lambda *_a, **_k: "https://tos.example/x.mp4")
    source = tmp_path / "out.mp4"
    source.write_bytes(b"\x00\x01\x02")

    seedance._save_local_bestseller_caption_asset(
        user_id=54,
        output_path=source,
        source_video_url="https://tos.example/raw.mp4",
        subtitle_text="字幕",
        job_id="job-1",
        day=1,
    )
    seedance._save_local_bestseller_post_asset(
        user_id=54,
        output_path=source,
        source_video_url="https://tos.example/raw.mp4",
        subtitle_text="字幕",
        job_id="job-1",
        day=1,
        bgm_url="https://tos.example/bgm.m4a",
    )

    db = seedance.SessionLocal()
    try:
        rows = db.query(Asset).all()
        assert len(rows) == 2
        for row in rows:
            assert row.meta["content_visibility"] == "internal", row.meta
            # 过程件默认不进云端内容库（同步会跳过 internal/hidden/intermediate）
            assert assets_api._asset_sync_payload(row) is None
    finally:
        db.close()


def test_final_asset_demotes_job_process_assets(tmp_path, monkeypatch):
    _memory_session(monkeypatch)
    monkeypatch.setattr(seedance, "ASSETS_DIR", tmp_path)
    monkeypatch.setattr(seedance, "_upload_to_tos", lambda *_a, **_k: "https://tos.example/x.mp4")
    source = tmp_path / "out.mp4"
    source.write_bytes(b"\x00\x01\x02")

    caption = seedance._save_local_bestseller_caption_asset(
        user_id=54,
        output_path=source,
        source_video_url="https://tos.example/raw.mp4",
        subtitle_text="字幕",
        job_id="job-2",
        day=1,
    )
    seedance._save_local_bestseller_post_asset(
        user_id=54,
        output_path=source,
        source_video_url="https://tos.example/raw.mp4",
        subtitle_text="字幕",
        job_id="job-other",
        day=1,
        bgm_url="https://tos.example/bgm.m4a",
    )

    changed = seedance._demote_process_assets_for_job("job-2", user_id=54)
    assert changed == 1

    db = seedance.SessionLocal()
    try:
        demoted = db.query(Asset).filter(Asset.asset_id == caption["asset_id"]).one()
        assert demoted.meta["asset_origin"] == "intermediate"
        assert demoted.meta["content_visibility"] == "hidden"
        # 别的任务的过程件不受影响
        others = db.query(Asset).filter(Asset.asset_id != caption["asset_id"]).all()
        assert all(row.meta["content_visibility"] == "internal" for row in others)
    finally:
        db.close()


def test_final_video_asset_is_visible():
    source = open(seedance.__file__, encoding="utf-8", errors="replace").read()
    marker = source.index("seedance_final_video")
    window = source[marker : marker + 400]

    assert '"asset_origin": "generated"' in window
    assert '"content_visibility": "visible"' in window
