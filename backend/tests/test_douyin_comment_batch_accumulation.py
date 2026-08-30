from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "douyin_origin"))
from backend.douyin_origin.douyin_comment_scraper import DouyinCommentScraper
from backend.douyin_origin.douyin_api import (
    _protocol_comment_result_confirmed,
    douyin_collection_requires_serial_reply,
    select_douyin_search_result_keys,
    select_monitor_videos_for_collection,
)


def test_process_video_comment_batches_accumulates_small_visible_batches():
    class FakePage:
        url = "https://www.douyin.com/user/self?modal_id=123"

        async def goto(self, *_args, **_kwargs):
            return None

        async def wait_for_timeout(self, *_args, **_kwargs):
            return None

        async def close(self):
            return None

    scraper = DouyinCommentScraper.__new__(DouyinCommentScraper)
    page = FakePage()
    scraper._new_page = lambda logger=None: _resolved(page)
    scraper._raise_if_login_intercept = lambda _page: _resolved(None)
    scraper._open_video_comment_panel_by_shortcuts = lambda *_args, **_kwargs: _resolved(None)
    scraper._wait_for_comment_collection_surface = lambda *_args, **_kwargs: _resolved({})
    scraper._scroll_comment_panel_once = lambda *_args, **_kwargs: _resolved({"top": 1, "before": 0})

    extracted = [
        {"comment_index": 1, "username": "u1", "profile_url": "https://www.douyin.com/user/1", "content": "c1"},
        {"comment_index": 2, "username": "u2", "profile_url": "https://www.douyin.com/user/2", "content": "c2"},
        {"comment_index": 3, "username": "u3", "profile_url": "https://www.douyin.com/user/3", "content": "c3"},
    ]
    calls = []
    extract_index = 0

    async def extract(_page, _seen_keys, *, mark_seen=True, **_kwargs):
        nonlocal extract_index
        if not mark_seen:
            return []
        row = extracted[extract_index]
        extract_index += 1
        return [row]

    async def on_batch(_page, batch, batch_index):
        calls.append((batch_index, [row["username"] for row in batch]))

    scraper._extract_visible_comment_batch = extract
    result = asyncio.run(
        scraper.process_video_comment_batches(
            "https://www.douyin.com/video/123",
            max_comments=3,
            batch_size=2,
            max_scroll_rounds=10,
            on_batch=on_batch,
        )
    )

    assert [row["username"] for row in result] == ["u1", "u2", "u3"]
    assert calls == [(1, ["u1", "u2"]), (2, ["u3"])]


def test_process_video_comment_batches_stops_before_next_scroll_or_batch():
    class FakePage:
        url = "https://www.douyin.com/user/self?modal_id=123"

        async def goto(self, *_args, **_kwargs):
            return None

        async def wait_for_timeout(self, *_args, **_kwargs):
            return None

        async def close(self):
            return None

    scraper = DouyinCommentScraper.__new__(DouyinCommentScraper)
    page = FakePage()
    scraper._new_page = lambda logger=None: _resolved(page)
    scraper._raise_if_login_intercept = lambda _page: _resolved(None)
    scraper._open_video_comment_panel_by_shortcuts = lambda *_args, **_kwargs: _resolved(None)
    scraper._wait_for_comment_collection_surface = lambda *_args, **_kwargs: _resolved({})
    scraper._scroll_comment_panel_once = lambda *_args, **_kwargs: _resolved({"top": 1, "before": 0})

    extracted = [
        {"comment_index": 1, "username": "u1", "profile_url": "https://www.douyin.com/user/1", "content": "c1"},
        {"comment_index": 2, "username": "u2", "profile_url": "https://www.douyin.com/user/2", "content": "c2"},
    ]
    extract_index = 0
    calls = []
    stop = False

    async def extract(_page, _seen_keys, *, mark_seen=True, **_kwargs):
        nonlocal extract_index
        if not mark_seen or extract_index >= len(extracted):
            return []
        row = extracted[extract_index]
        extract_index += 1
        return [row]

    async def on_batch(_page, batch, _batch_index):
        nonlocal stop
        calls.append([row["username"] for row in batch])
        stop = True

    scraper._extract_visible_comment_batch = extract
    result = asyncio.run(
        scraper.process_video_comment_batches(
            "https://www.douyin.com/video/123",
            max_comments=2,
            batch_size=1,
            max_scroll_rounds=10,
            on_batch=on_batch,
            should_stop=lambda: stop,
        )
    )

    assert [row["username"] for row in result] == ["u1"]
    assert calls == [["u1"]]


def test_process_video_comment_batches_does_not_turn_unverified_empty_into_success():
    class FakePage:
        url = "https://www.douyin.com/video/123"

        async def goto(self, *_args, **_kwargs):
            return None

        async def wait_for_timeout(self, *_args, **_kwargs):
            return None

        async def close(self):
            return None

    scraper = DouyinCommentScraper.__new__(DouyinCommentScraper)
    pages = []

    async def new_page(logger=None):
        page = FakePage()
        pages.append(page)
        return page

    scraper._new_page = new_page
    scraper._raise_if_login_intercept = lambda _page: _resolved(None)
    scraper._open_video_comment_panel_by_shortcuts = lambda *_args, **_kwargs: _resolved(None)
    scraper._wait_for_comment_collection_surface = lambda *_args, **_kwargs: _resolved(
        {"hasVisibleList": True, "empty": False}
    )
    scraper._extract_visible_comment_batch = lambda *_args, **_kwargs: _resolved([])
    scraper._scroll_comment_panel_once = lambda *_args, **_kwargs: _resolved({"top": 0, "before": 0})

    try:
        asyncio.run(
            scraper.process_video_comment_batches(
                "https://www.douyin.com/video/123",
                max_comments=3,
                max_scroll_rounds=1,
            )
        )
    except RuntimeError as exc:
        assert "连续 2 次未读取到可验证评论" in str(exc)
    else:
        raise AssertionError("unverified empty comment reads must fail, not complete")

    assert len(pages) == 2


def test_protocol_empty_result_requires_explicit_confirmation():
    assert _protocol_comment_result_confirmed(
        {"empty_confirmed": True, "declared_total": 0}, [], expected_total=None
    )
    assert not _protocol_comment_result_confirmed({}, [], expected_total=0)
    assert not _protocol_comment_result_confirmed({}, [], expected_total=None)
    assert _protocol_comment_result_confirmed({}, [{"comment": "hello"}], expected_total=None)


def test_failed_monitor_video_remains_eligible_without_requeueing_legacy_video():
    assert select_monitor_videos_for_collection(
        [{"aweme_id": "failed", "collection_status": "failed", "last_collected_at": ""}],
        auto_collect_new=True,
    )[0]["aweme_id"] == "failed"
    assert select_monitor_videos_for_collection(
        [{"aweme_id": "legacy", "is_new": False, "last_collected_at": ""}],
        auto_collect_new=True,
    ) == []


def test_precise_collection_selects_all_unique_videos_with_default_limit():
    results = [
        {"source_item_key": "video-1", "export_selected": True},
        {"source_item_key": "video-1", "export_selected": True},
        {"source_item_key": "video-2", "export_selected": True},
        {"source_item_key": "video-3", "export_selected": True},
    ]

    assert select_douyin_search_result_keys(results, 50) == [
        "video-1",
        "video-2",
        "video-3",
    ]


def test_precise_collection_reply_flag_requires_serial_batch():
    assert douyin_collection_requires_serial_reply(
        [{"precise_reply_enabled": False}, {"precise_reply_enabled": True}]
    )
    assert not douyin_collection_requires_serial_reply([{"precise_reply_enabled": False}])


def _resolved(value):
    async def resolve():
        return value

    return resolve()
