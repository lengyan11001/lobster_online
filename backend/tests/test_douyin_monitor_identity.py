from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "douyin_origin"))

from backend.douyin_origin.douyin_api import (  # noqa: E402
    extract_monitor_video_author_sec_user_id,
    filter_monitor_videos_for_target,
    merge_monitor_videos,
)


def test_monitor_video_filter_keeps_only_the_current_sec_user_id():
    target = {"sec_user_id": "target-a", "username": "同名账号"}
    videos = [
        {"aweme_id": "a-1", "author": "同名账号", "author_sec_user_id": "target-a"},
        {"aweme_id": "b-1", "author": "同名账号", "author_sec_user_id": "target-b"},
        {"aweme_id": "unknown", "author": "其他账号"},
    ]

    assert [row["aweme_id"] for row in filter_monitor_videos_for_target(videos, target)] == ["a-1"]


def test_monitor_video_filter_reads_nested_author_identity():
    assert extract_monitor_video_author_sec_user_id(
        {"raw": {"author": {"sec_uid": "target-a"}}}
    ) == "target-a"


def test_monitor_merge_does_not_reintroduce_videos_from_another_target():
    target = {"sec_user_id": "target-a", "username": "账号 A"}
    existing = [
        {"aweme_id": "a-old", "author": "账号 A", "author_sec_user_id": "target-a"},
        {"aweme_id": "b-old", "author": "账号 B", "author_sec_user_id": "target-b"},
    ]
    fetched = [
        {"aweme_id": "a-new", "author": "账号 A", "author_sec_user_id": "target-a"},
        {"aweme_id": "b-new", "author": "账号 B", "author_sec_user_id": "target-b"},
    ]

    existing = filter_monitor_videos_for_target(existing, target)
    fetched = filter_monitor_videos_for_target(fetched, target)
    merged = merge_monitor_videos(
        existing,
        fetched,
        initial_sync=False,
        auto_collect_new=True,
    )

    assert [row["aweme_id"] for row in merged] == ["a-new", "a-old"]
