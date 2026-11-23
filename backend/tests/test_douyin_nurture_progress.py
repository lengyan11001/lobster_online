from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "douyin_origin"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.api import h5_chat_channel as channel  # noqa: E402
from backend.douyin_origin import douyin_account_nurture as nurture  # noqa: E402
from backend.douyin_origin import douyin_api  # noqa: E402


def test_nurture_merge_keeps_h5_duration_and_accounts():
    merged = channel._merge_scheduled_douyin_nurture_params(
        {"nurture_duration_min": 20, "nurture_duration_max": 40, "account_id": 9},
        {"duration_minutes": 5, "account_ids": [1], "account_id": 1},
    )
    assert merged["duration_minutes"] == 5
    assert merged["account_ids"] == [1]
    assert merged["account_id"] == 1
    assert merged["nurture_duration_min"] == 20


def test_nurture_scheduler_emit_progress_supports_sync_and_async_callbacks():
    seen = []

    def sync_cb(payload):
        seen.append(("sync", payload))

    scheduler = nurture.DouyinAccountNurtureScheduler(
        accounts=[],
        config={},
        broadcast_log=lambda *_args: None,
        progress_callback=sync_cb,
    )
    asyncio.run(scheduler._emit_progress({"account_id": 1, "watched_count": 3}))
    assert seen == [("sync", {"account_id": 1, "watched_count": 3})]

    async def async_cb(payload):
        seen.append(("async", payload))

    scheduler.progress_callback = async_cb
    asyncio.run(scheduler._emit_progress({"account_id": 2, "likes_sent": 4}))
    assert seen[-1] == ("async", {"account_id": 2, "likes_sent": 4})


class _FakePage:
    def __init__(self, url: str = "", title: str = "") -> None:
        self.url = url
        self._title = title

    async def title(self) -> str:
        return self._title


def test_nurture_detects_live_room_by_url_and_title():
    scheduler = nurture.DouyinAccountNurtureScheduler(
        accounts=[], config={}, broadcast_log=lambda *_args: None
    )
    assert asyncio.run(scheduler._is_live_room_page(_FakePage(url="https://www.douyin.com/root/live/123"))) is True
    assert asyncio.run(scheduler._is_live_room_page(_FakePage(title="兔娘的抖音直播间 - 抖音直播"))) is True
    assert asyncio.run(scheduler._is_live_room_page(_FakePage(url="https://www.douyin.com/jingxuan", title="抖音精选电脑版"))) is False


def test_nurture_skips_and_recovers_from_live_rooms():
    source = inspect.getsource(nurture.DouyinAccountNurtureScheduler)
    assert "打开到直播间，已返回精选页重新选择视频" in source
    assert "当前页面是直播间，已返回精选页继续养号" in source
    assert "href.includes('/root/live/')" in source


def test_nurture_once_accepts_progress_callback():
    sig = inspect.signature(douyin_api.douyin_run_account_nurture_once)
    assert "progress_callback" in sig.parameters


def test_sales_action_receives_run_id_and_passes_nurture_progress():
    sig = inspect.signature(channel._run_scheduled_douyin_sales_action)
    assert "run_id" in sig.parameters
    source = inspect.getsource(channel._run_scheduled_douyin_sales_action)
    assert "douyin_run_account_nurture_once(payload, progress_callback=nurture_progress)" in source
    assert '"stage": "account_nurture"' in source


def test_watchdog_message_uses_actual_timeout():
    source = inspect.getsource(channel._scheduled_inactivity_watchdog)
    assert "max(1, int(round(timeout_seconds / 60)))" in source
    assert "执行无进展超过 40 分钟" not in source
