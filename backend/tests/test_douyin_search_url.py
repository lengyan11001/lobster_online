from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import quote, urlencode

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "douyin_origin"))

from backend.douyin_origin import douyin_api  # noqa: E402


def _expected_url(keyword: str, extra: list[tuple[str, str]] | None = None) -> str:
    query = urlencode([("type", "video"), *(extra or [])])
    return f"https://www.douyin.com/search/{quote(keyword)}?{query}"


def test_search_url_encodes_keyword_and_query_without_requests_utils():
    url = douyin_api.build_douyin_search_url("炸鸡 加盟", [("type", "video"), ("sort_type", "2")])

    assert url == _expected_url("炸鸡 加盟", [("sort_type", "2")])
    assert "%E7%82%B8%E9%B8%A1" in url
    assert " " not in url
    assert url.endswith("?type=video&sort_type=2")


def test_keyword_search_builds_the_encoded_url_before_opening_the_browser(monkeypatch):
    captured = {}

    class StopAfterUrl(Exception):
        pass

    async def fake_browser_ready(account, start_url=""):
        captured["account"] = account
        captured["url"] = start_url
        raise StopAfterUrl

    monkeypatch.setattr(douyin_api, "ensure_douyin_account_browser_ready_async", fake_browser_ready)

    with pytest.raises(StopAfterUrl):
        asyncio.run(
            douyin_api.run_douyin_keyword_search(
                {"id": "acct-1", "port": 9222},
                "炸鸡 加盟",
                sort_type="2",
            )
        )

    assert captured["account"]["id"] == "acct-1"
    assert captured["url"] == _expected_url("炸鸡 加盟", [("sort_type", "2")])


def test_douyin_api_has_no_phantom_requests_utils_calls():
    source = (
        Path(__file__).resolve().parents[1] / "douyin_origin" / "douyin_api.py"
    ).read_text(encoding="utf-8")

    assert "requests.utils.urlencode" not in source
    assert "requests.utils.quote" not in source
