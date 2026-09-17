"""抖音会话健康跟踪 / 登录拦截取证 / 掉线即停 的回归测试。

线上案例（2026-09-16 user 54）：08:00:26 最后一次写操作成功 → 08:05:32 取不到账号身份
→ 08:16:04 私信第一条报"页面出现登录拦截"，之后 16 条私信全失败。当时的日志没有 URL、
没有验证类型、没有截图，只能靠时间窗反推，也拦不住后续的无效尝试。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "douyin_origin"))

from backend.douyin_origin import douyin_session_health as health


class _FakePage:
    def __init__(self, url: str, text: str, title: str = "抖音") -> None:
        self.url = url
        self._text = text
        self._title = title
        self.screenshot_called = False

    async def title(self) -> str:
        return self._title

    async def evaluate(self, _script: str) -> str:
        return self._text

    async def screenshot(self) -> bytes:
        self.screenshot_called = True
        return b"\x89PNG\r\n\x1a\n" + b"0" * 32


def test_detect_login_wall_recognises_login_page_and_captcha():
    verdict = health.detect_login_wall(
        "https://www.douyin.com/login?redirect=/",
        "请扫码登录后继续使用，或完成滑块验证",
    )
    assert verdict["login_wall"] is True
    assert verdict["url_signal"] is True
    assert verdict["captcha"]["type"] == "slider"


def test_detect_login_wall_ignores_normal_page():
    verdict = health.detect_login_wall(
        "https://www.douyin.com/user/self?from_tab_name=main",
        "作品 评论 私信 数据中心",
    )
    assert verdict["login_wall"] is False
    assert verdict["captcha"]["type"] == "none"


def test_capture_login_wall_writes_event_and_screenshot(tmp_path: pathlib.Path):
    page = _FakePage(
        "https://www.douyin.com/login",
        "请输入短信验证码完成安全验证",
    )
    event = asyncio.run(
        health.capture_login_wall(
            page,
            account_id=1,
            action="direct_message",
            reason="login_intercept",
            root=tmp_path,
        )
    )
    assert event["login_wall"] is True
    assert event["captcha"]["type"] == "sms"
    assert event["account_id"] == 1
    assert page.screenshot_called is True
    shot = event.get("screenshot") or ""
    assert shot and pathlib.Path(shot).exists()
    lines = [
        json.loads(line)
        for line in health.login_events_path(tmp_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 1
    assert lines[0]["action"] == "direct_message"
    assert lines[0]["captcha"]["type"] == "sms"


def test_need_relogin_state_roundtrip(tmp_path: pathlib.Path):
    assert health.session_state(7, root=tmp_path)["need_relogin"] is False
    entry = health.mark_need_relogin(7, reason="page_shows_login_wall", url="https://www.douyin.com/login", root=tmp_path)
    assert entry["need_relogin"] is True
    state = health.session_state(7, root=tmp_path)
    assert state["need_relogin"] is True and state["since"]
    cleared = health.clear_need_relogin(7, root=tmp_path)
    assert cleared["need_relogin"] is False
    assert health.session_state(7, root=tmp_path)["need_relogin"] is False


def test_is_login_wall_error_matches_new_type_and_legacy_text():
    assert health.is_login_wall_error(health.DouyinLoginWallError("x", account_id=1)) is True
    legacy = RuntimeError("当前抖音浏览器未登录，或登录态已失效，页面出现登录拦截")
    assert health.is_login_wall_error(legacy) is True
    assert health.is_login_wall_error(RuntimeError("评论区可见但未提取到有效评论节点")) is False


def test_allow_write_action_throttles_by_interval_and_hourly_cap(tmp_path: pathlib.Path):
    first = health.allow_write_action(1, "direct_message", min_interval_seconds=60, root=tmp_path, now=1000.0)
    assert first["allowed"] is True
    second = health.allow_write_action(1, "direct_message", min_interval_seconds=60, root=tmp_path, now=1010.0)
    assert second["allowed"] is False
    assert second["reason"] == "min_interval"
    assert second["wait_seconds"] > 0
    third = health.allow_write_action(1, "direct_message", min_interval_seconds=60, root=tmp_path, now=1070.0)
    assert third["allowed"] is True
    health.allow_write_action(1, "follow_comment", max_per_hour=2, root=tmp_path, now=2000.0)
    health.allow_write_action(1, "follow_comment", max_per_hour=2, root=tmp_path, now=2010.0)
    capped = health.allow_write_action(1, "follow_comment", max_per_hour=2, root=tmp_path, now=2020.0)
    assert capped["allowed"] is False
    assert capped["reason"] == "hourly_cap"


def test_record_session_health_appends_jsonl(tmp_path: pathlib.Path):
    health.record_session_health(
        tmp_path,
        account_id=1,
        action="precise_touch",
        phase="before",
        sec_user_id="MS4wLjABAAAA",
    )
    health.record_session_health(tmp_path, account_id=1, action="precise_touch", phase="after", login_wall=False)
    rows = [
        json.loads(line)
        for line in health.session_health_path(tmp_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 2
    assert rows[0]["phase"] == "before"
    assert rows[0]["sec_user_id"] == "MS4wLjABAAAA"
    assert rows[1]["login_wall"] is False
