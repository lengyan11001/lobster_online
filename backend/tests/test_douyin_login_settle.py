"""浏览器刚拉起时先停在登录页、随后自动登录，不能再被判定成"已登出"。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "douyin_origin"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.douyin_origin import douyin_api  # noqa: E402
from backend.douyin_origin import douyin_comment_scraper as scraper_mod  # noqa: E402


LOGIN_PAGE_STATE = {
    "loginPrompt": True,
    "qrLoginVisible": False,
    "profileHints": False,
    "loginPagePath": True,
    "selfPath": False,
    "userPath": False,
    "profileLinkCount": 0,
    "currentPath": "/login",
}
LOGGED_IN_STATE = {
    "loginPrompt": False,
    "qrLoginVisible": False,
    "profileHints": True,
    "loginPagePath": False,
    "selfPath": True,
    "userPath": False,
    "profileLinkCount": 3,
    "currentPath": "/user/self",
}


class FakeContext:
    def __init__(self, cookies):
        self._cookies = cookies

    async def cookies(self, _urls):
        return list(self._cookies)


class FakePage:
    def __init__(self, state, cookies):
        self._state = state
        self.context = FakeContext(cookies)
        self.closed = False

    async def goto(self, *_args, **_kwargs):
        return None

    async def wait_for_timeout(self, _ms):
        return None

    async def evaluate(self, _script):
        return dict(self._state)

    async def close(self):
        self.closed = True


def _session_cookie():
    return [{"name": "sessionid", "value": "abc123"}]


def _run_login_probe(state, cookies):
    scraper = scraper_mod.DouyinCommentScraper(account_id=1, cdp_port=9332)
    page = FakePage(state, cookies)

    async def fake_new_page(logger=None):
        return page

    async def no_intercept(_page):
        return False

    scraper._new_page = fake_new_page  # type: ignore[assignment]
    scraper._has_login_intercept = no_intercept  # type: ignore[assignment]
    result = asyncio.run(scraper.check_login_state())
    return result, scraper.last_login_probe


def test_logged_in_session_is_online():
    result, probe = _run_login_probe(LOGGED_IN_STATE, _session_cookie())

    assert result is True
    assert probe["state"] == "online"


def test_login_page_with_session_cookie_is_unknown_not_waiting():
    result, probe = _run_login_probe(LOGIN_PAGE_STATE, _session_cookie())

    assert result is False
    assert probe["state"] == "unknown"
    assert probe["reason"] == "login_ui_visible_with_session_cookie"


def test_login_page_without_session_cookie_is_waiting():
    result, probe = _run_login_probe(LOGIN_PAGE_STATE, [])

    assert result is False
    assert probe["state"] == "waiting"
    assert probe["reason"] == "login_prompt_or_qr_visible"


class FakeMentionScraper:
    """第一次抓取撞登录拦截，第二次成功 —— 模拟浏览器自动登录完成。"""

    instances: list["FakeMentionScraper"] = []

    def __init__(self, *, account_id: int, cdp_port: int, **_kwargs) -> None:
        self.calls = 0
        self.closed = False
        FakeMentionScraper.instances.append(self)

    async def scrape_self_videos(self, *, max_videos: int, logger=None):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("当前抖音浏览器未登录，或登录态已失效，页面出现登录拦截")
        return {"profile": {"nickname": "demo"}, "videos": [{"url": "https://v/1"}]}

    async def close(self) -> None:
        self.closed = True


class AlwaysLoginWallScraper(FakeMentionScraper):
    async def scrape_self_videos(self, *, max_videos: int, logger=None):
        self.calls += 1
        raise RuntimeError("当前抖音浏览器未登录，或登录态已失效，页面出现登录拦截")


def _patch_mention_flow(monkeypatch, scraper_cls, probe_state):
    saved = []
    account = {"id": 1, "status": "online", "port": 9332}
    config = {"douyin_accounts": [account], "douyin_default_account_id": 1}

    monkeypatch.setattr(douyin_api, "build_douyin_nurture_conflict", lambda _label: None)
    for name in (
        "reconcile_douyin_runtime_state",
        "reconcile_douyin_video_comment_runtime_state",
        "reconcile_douyin_mention_comment_runtime_state",
        "reconcile_douyin_follow_comment_runtime_state",
        "reconcile_douyin_interaction_runtime_state",
        "reconcile_douyin_group_member_runtime_state",
        "reconcile_douyin_stranger_message_runtime_state",
    ):
        monkeypatch.setattr(douyin_api, name, lambda *_args, **_kwargs: None, raising=False)
    for flag in (
        "douyin_running",
        "douyin_video_comment_running",
        "douyin_mention_comment_running",
        "douyin_follow_comment_running",
        "douyin_interaction_running",
        "douyin_group_member_running",
        "douyin_stranger_message_running",
    ):
        monkeypatch.setattr(douyin_api, flag, False, raising=False)

    monkeypatch.setattr(douyin_api, "load_global_config", lambda: config)
    monkeypatch.setattr(douyin_api, "save_global_config", lambda value: saved.append(value))
    monkeypatch.setattr(douyin_api, "_normalize_accounts", lambda rows: [dict(row) for row in rows or []])
    monkeypatch.setattr(douyin_api, "DouyinCommentScraper", scraper_cls)
    monkeypatch.setattr(douyin_api, "save_douyin_mention_self_video_cache", lambda: None)

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def launch_browser(self, _url: str) -> bool:
            return True

    monkeypatch.setattr(douyin_api, "DouyinClient", FakeClient)

    async def fake_probe(_account):
        return {"state": probe_state, "reason": "test"}

    monkeypatch.setattr(douyin_api, "probe_douyin_account_login_state", fake_probe)
    return saved, account


def test_mention_flow_retries_when_probe_recovers_online(monkeypatch):
    FakeMentionScraper.instances = []
    saved, account = _patch_mention_flow(monkeypatch, FakeMentionScraper, "online")

    result = asyncio.run(douyin_api.douyin_get_self_videos(account_id=1))

    assert result["code"] == 200
    assert result["videos"] == [{"url": "https://v/1"}]
    assert FakeMentionScraper.instances[0].calls == 2
    assert saved == []          # 不能把账号写成 waiting


def test_mention_flow_keeps_account_online_when_probe_is_unknown(monkeypatch):
    saved, account = _patch_mention_flow(monkeypatch, AlwaysLoginWallScraper, "unknown")

    result = asyncio.run(douyin_api.douyin_get_self_videos(account_id=1))

    assert result["code"] == 400
    assert result["type"] == "account_login_unconfirmed"
    assert saved == []


def test_mention_flow_still_persists_waiting_when_probe_confirms(monkeypatch):
    saved, account = _patch_mention_flow(monkeypatch, AlwaysLoginWallScraper, "waiting")

    result = asyncio.run(douyin_api.douyin_get_self_videos(account_id=1))

    assert result["code"] == 400
    assert result["type"] == "account_waiting_login"
    assert saved and saved[0]["douyin_accounts"][0]["status"] == "waiting"
