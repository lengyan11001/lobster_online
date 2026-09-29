"""评论采集"面板在屏但没渲染"的重试/唤醒 + AI 筛选兜底日志（2026-09-21）。

现场：
- 3 条视频报「评论区已打开但未提取到评论，结果未确认」直接判失败，实际是抖音虚拟列表没铺数据。
- `[抖音筛选] ai_fallback fallback_used=True` 只说了"用了兜底"，没说是接口 5xx、异常还是解析失败。
"""

import asyncio

import backend.app.api.h5_chat_channel as h5_chat_channel

h5_chat_channel._install_douyin_origin_import_path()
import douyin_comment_scraper as scraper_module  # type: ignore  # noqa: E402
import douyin_api  # type: ignore  # noqa: E402
from douyin_comment_scraper import (  # type: ignore  # noqa: E402
    DouyinCommentCollectionUnconfirmed,
    DouyinCommentScraper,
)


class _FakeKeyboard:
    def __init__(self):
        self.presses = []

    async def press(self, key):
        self.presses.append(key)


class _FakePage:
    def __init__(self):
        self.keyboard = _FakeKeyboard()
        self.waits = []

    async def goto(self, *_args, **_kwargs):
        return None

    async def wait_for_timeout(self, ms):
        self.waits.append(ms)

    async def evaluate(self, _script, *_args, **_kwargs):
        return {"clickedList": True, "expanded": 0}

    async def close(self):
        return None


def _scraper(monkeypatch, page):
    scraper = DouyinCommentScraper(account_id=1, cdp_port=9332)

    async def fake_new_page(logger=None):
        return page

    async def noop(*_args, **_kwargs):
        return None

    async def surface(*_args, **_kwargs):
        return {"empty": False, "unconfirmed": False, "listFound": True}

    async def empty_batch(*_args, **_kwargs):
        return []

    async def scroll(*_args, **_kwargs):
        return {"top": 0, "before": 0}

    monkeypatch.setattr(scraper, "_new_page", fake_new_page)
    monkeypatch.setattr(scraper, "_pause_page_videos", noop)
    monkeypatch.setattr(scraper, "_raise_if_login_intercept", noop)
    monkeypatch.setattr(scraper, "_open_video_comment_panel_by_shortcuts", noop)
    monkeypatch.setattr(scraper, "_wait_for_comment_collection_surface", surface)
    monkeypatch.setattr(scraper, "_extract_visible_comment_batch", empty_batch)
    monkeypatch.setattr(scraper, "_scroll_comment_panel_once", scroll)
    return scraper


def test_unconfirmed_collection_retries_then_reports_attempts(monkeypatch):
    scraper = DouyinCommentScraper(account_id=1, cdp_port=9332)
    calls = []

    async def always_unconfirmed(*_args, **_kwargs):
        calls.append(1)
        raise DouyinCommentCollectionUnconfirmed("评论区可见但本轮未提取到有效评论节点")

    monkeypatch.setattr(scraper, "_process_video_comment_batches_once", always_unconfirmed)
    monkeypatch.setattr(scraper_module, "DOUYIN_COMMENT_COLLECTION_ATTEMPTS", 3)
    monkeypatch.setattr(scraper_module, "DOUYIN_COMMENT_COLLECTION_RETRY_BACKOFF_SECONDS", (0, 0))

    try:
        asyncio.run(
            scraper.process_video_comment_batches("https://www.douyin.com/video/1", max_comments=10)
        )
    except RuntimeError as exc:
        assert "连续 3 次" in str(exc), str(exc)
    else:  # pragma: no cover
        raise AssertionError("三次都未确认时必须抛错")

    assert len(calls) == 3, f"应重试 3 次，实际 {len(calls)}"


def test_batch_collection_wakes_virtual_list_before_giving_up(monkeypatch):
    page = _FakePage()
    scraper = _scraper(monkeypatch, page)
    wakes = []

    async def fake_wake(target_page, logger=None):
        wakes.append(1)
        return {"actions": ["click_list"]}

    monkeypatch.setattr(scraper, "_wake_comment_list", fake_wake)
    monkeypatch.setattr(scraper_module, "DOUYIN_COMMENT_WAKE_LIMIT", 2)

    try:
        asyncio.run(
            scraper._process_video_comment_batches_once(
                "https://www.douyin.com/video/1", max_comments=10, max_scroll_rounds=6
            )
        )
    except DouyinCommentCollectionUnconfirmed as exc:
        assert "唤醒列表 2 次" in str(exc), str(exc)
    else:  # pragma: no cover
        raise AssertionError("一行评论都没渲染时应该报未确认")

    assert len(wakes) == 2, f"应该先唤醒虚拟列表再判失败，实际唤醒 {len(wakes)} 次"
    assert page.keyboard.presses == [], "唤醒走的是页面点击/按键，不应污染其它键盘动作"


def test_ai_filter_fallback_logs_reason_and_raw_response(monkeypatch):
    from ai_client import AIClient

    client = AIClient(api_url="https://ai.example/v1/chat/completions", api_key="k", model="m")
    events = []

    def boom(*_args, **_kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("ai_client.requests.post", boom)

    rows = client._filter_comments_batch_v2(
        "标题",
        [{"username": "甲", "content": "怎么收费", "profile_url": ""}],
        intent_profile="douyin_transactional",
        event_logger=lambda event, **fields: events.append((event, fields)),
    )

    assert rows is not None
    fallback_events = [fields for event, fields in events if event == "ai_fallback"]
    assert fallback_events, events
    assert "connection reset" in str(fallback_events[-1].get("reason"))
    assert fallback_events[-1].get("fallback_used") is True


def test_filter_summary_line_includes_reason(monkeypatch, tmp_path):
    lines = []
    monkeypatch.setattr(douyin_api, "_douyin_filter_log_path", lambda: tmp_path / "filter.jsonl")
    monkeypatch.setattr(douyin_api, "douyin_log", lambda message, level="info": lines.append(message))

    douyin_api.log_douyin_filter_event(
        "ai_fallback",
        comments_in=10,
        precise_out=0,
        fallback_used=True,
        reason="AI 接口 HTTP 500：upstream error",
    )

    assert lines, "应该有一行摘要日志"
    assert "fallback_used=True" in lines[-1]
    assert "reason=AI 接口 HTTP 500" in lines[-1], lines[-1]
