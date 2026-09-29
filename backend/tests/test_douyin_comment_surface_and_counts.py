"""评论采集兜底 + 采集口径回归。

线上案例（2026-09-15，user 54，task 16467）：

- 8 个视频里 6 个报“评论区已打开但连续 2 次未读取到可验证评论……评论区可见但未提取到有效评论节点”，
  进度里 visible_comments 一直是 0；
- 同一次运行的汇总写成“已采集 8 个视频的客户 823 人”，实际只有 2 个视频采到了评论，
  而多关键词聚合后的 selected_count 还停留在第一个关键词的 5。
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "douyin_origin"))

import pytest

from backend.app.api import h5_chat_channel
from backend.douyin_origin import douyin_comment_scraper as scraper_module
from backend.douyin_origin.douyin_comment_scraper import DouyinCommentScraper


def _resolved(value):
    async def _awaitable():
        return value

    return _awaitable()


class _FakeKeyboard:
    def __init__(self, presses):
        self._presses = presses

    async def press(self, key):
        self._presses.append(key)


class _FakePage:
    """Enough of the playwright Page surface for the collection flow."""

    url = "https://www.douyin.com/video/123"

    def __init__(self, extraction_rows=None):
        self.evaluations = []
        self.key_presses = []
        self.keyboard = _FakeKeyboard(self.key_presses)
        self._extraction_rows = extraction_rows if extraction_rows is not None else []

    async def goto(self, *_args, **_kwargs):
        return None

    async def wait_for_timeout(self, *_args, **_kwargs):
        return None

    async def close(self):
        return None

    async def evaluate(self, script, *_args, **_kwargs):
        self.evaluations.append(script)
        if "const itemSelectors" in script:
            return {"counts": {}, "total": 0, "listFound": True, "listVisible": True}
        return list(self._extraction_rows)


def _build_scraper(page, *, batch_rows):
    scraper = DouyinCommentScraper.__new__(DouyinCommentScraper)
    scraper._new_page = lambda logger=None: _resolved(page)
    scraper._raise_if_login_intercept = lambda _page: _resolved(None)
    scraper._open_video_comment_panel_by_shortcuts = lambda *_a, **_k: _resolved(None)
    scraper._wait_for_comment_collection_surface = lambda *_a, **_k: _resolved(
        {"ready": True, "empty": False}
    )
    scraper._read_comment_surface_counts = lambda *_a, **_k: _resolved(
        {"counts": {".comment-item-info-wrap": 0, '[data-e2e="comment-item"]': 0}, "total": 0, "listFound": True}
    )
    scraper._wake_comment_list = lambda *_a, **_k: _resolved({"actions": ["click_list"]})

    async def _extract(*_args, **_kwargs):
        return [dict(row) for row in batch_rows]

    scraper._extract_visible_comment_batch = _extract
    return scraper


def test_comment_probe_owns_legacy_and_structural_selectors():
    """类名一改就整片“0 条”的根因：探测只认一个历史类名。"""
    probe_js = scraper_module.build_comment_surface_probe_js()

    def embedded(name):
        match = re.search(rf"const {name} = (\[.*?\]);", probe_js)
        assert match, f"{name} 没有写进探测脚本"
        return json.loads(match.group(1))

    item_selectors = embedded("itemSelectors")
    list_selectors = embedded("listSelectors")
    assert ".comment-item-info-wrap" in item_selectors
    assert '[data-e2e="comment-item"]' in item_selectors
    assert set(item_selectors) == set(scraper_module.COMMENT_ITEM_SELECTORS)
    assert set(list_selectors) == set(scraper_module.COMMENT_LIST_SELECTORS)


def test_scrape_video_comments_falls_back_to_batch_extractor():
    """主提取为空但列表可见时，兜底提取器必须救回评论，而不是直接判失败。"""
    page = _FakePage(extraction_rows=[])
    rows = [
        {"comment_index": 1, "username": "u1", "profile_url": "https://www.douyin.com/user/1", "content": "c1"},
        {"comment_index": 2, "username": "u2", "profile_url": "https://www.douyin.com/user/2", "content": "c2"},
    ]
    scraper = _build_scraper(page, batch_rows=rows)
    collected = asyncio.run(
        scraper.scrape_video_comments(
            "https://www.douyin.com/video/123",
            max_comments=10,
            max_scroll_rounds=2,
        )
    )
    assert [row["username"] for row in collected] == ["u1", "u2"]


def test_scrape_video_comments_wakes_visible_but_unrendered_list():
    """列表在屏但没渲染出行时，要先尝试唤醒列表，再判断失败。"""
    page = _FakePage(extraction_rows=[])
    scraper = _build_scraper(page, batch_rows=[])
    wake_calls = []
    scraper._wake_comment_list = lambda *_a, **_k: wake_calls.append("wake") or _resolved({"actions": ["wake"]})

    with pytest.raises(RuntimeError):
        asyncio.run(
            scraper.scrape_video_comments(
                "https://www.douyin.com/video/123",
                max_comments=10,
                max_scroll_rounds=3,
            )
        )
    assert wake_calls, "评论行一直为 0 时必须触发列表唤醒"


def test_multi_keyword_aggregate_sums_started_count(monkeypatch):
    """多关键词聚合不能沿用第一个关键词的 selected_count。"""

    def _single(keyword, selected_ids):
        return {
            "code": 200,
            "keyword": keyword,
            "search_total": 50,
            "selected_count": len(selected_ids),
            "skipped_completed": 0,
            "selected_videos_total": len(selected_ids),
            "selected_task_ids": list(selected_ids),
            "selected_item_keys": [f"video:{task_id}" for task_id in selected_ids],
            "items": [],
            "session_id": f"session-{keyword}",
            "account_id": 1,
            "account_ids": [1],
            "msg": "",
        }

    async def fake_single(params):
        keyword = str((params or {}).get("keyword") or "")
        if keyword == "kw1":
            return _single("kw1", [17, 18, 19, 20, 21])
        return _single("kw2", [22, 23, 24])

    async def fake_wait(*_args, **_kwargs):
        return {"status": "done", "tasks": []}

    monkeypatch.setattr(h5_chat_channel, "_run_scheduled_douyin_single_search_collect_action", fake_single)
    monkeypatch.setattr(h5_chat_channel, "_wait_for_douyin_collect_completion", fake_wait)

    result = asyncio.run(
        h5_chat_channel._run_scheduled_douyin_search_collect_action({"keywords": ["kw1", "kw2"]})
    )
    assert result["selected_videos_total"] == 8
    assert result["selected_count"] == 8


def test_collect_summary_text_reports_started_completed_failed():
    """汇总必须说清 选中/启动/完成/失败，而不是把选中数当成采集数。"""
    tasks = [{"status": "completed"}, {"status": "completed"}]
    tasks.extend({"status": "failed"} for _ in range(6))
    payload = {
        "keywords": ["AI获客", "AI赚钱"],
        "search_total": 100,
        "selected_task_ids": list(range(17, 25)),
        "selected_videos_total": 8,
        "selected_count": 8,
        "skipped_completed": 0,
        "final_state": {"status": "done", "tasks": tasks},
        "total_customers": 823,
        "total_high_intent": 63,
        "precise_customers": [{"username": f"u{index}"} for index in range(63)],
    }
    text = h5_chat_channel._douyin_collect_summary_text(payload)
    assert "找到 100 个视频" in text
    assert "选中 8 个视频" in text
    assert "实际启动 8 个" in text
    assert "完成 2 个" in text
    assert "失败 6 个" in text
    assert "采集客户 823 人" in text
    assert "精准客户 63 人" in text
