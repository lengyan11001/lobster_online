"""搜索页"等不到视频条目"的取证/重试，以及日志中文不再变成 '?'（2026-09-22）。"""

import asyncio
import io
import sys

import backend.app.api.h5_chat_channel as h5_chat_channel

h5_chat_channel._install_douyin_origin_import_path()
import douyin_comment_scraper as scraper_module  # type: ignore  # noqa: E402
from douyin_comment_scraper import DouyinCommentScraper  # type: ignore  # noqa: E402


class _FakePage:
    def __init__(self):
        self.url = "https://www.douyin.com/search/AI?type=video"
        self.selector_waits = 0
        self.gotos = 0
        self.reloads = 0

    async def reload(self, **_kwargs):
        self.reloads += 1
        return None

    async def wait_for_selector(self, *_args, **_kwargs):
        self.selector_waits += 1
        raise TimeoutError("Page.wait_for_selector: Timeout 30000ms exceeded.")

    async def goto(self, url, **_kwargs):
        self.gotos += 1
        self.url = url
        return None

    async def wait_for_timeout(self, _ms):
        return None

    async def title(self):
        return "抖音搜索"

    async def evaluate(self, *_args, **_kwargs):
        return "请完成安全验证"

    async def screenshot(self):
        return b"\x89PNG\r\n"


def test_search_wait_captures_evidence_and_reports_chinese(monkeypatch):
    scraper = DouyinCommentScraper(account_id=1, cdp_port=9332)
    page = _FakePage()
    captured = []
    logs = []

    async def fake_capture(target_page, **kwargs):
        captured.append(kwargs)
        return {
            "login_wall": True,
            "captcha": {"type": "slider"},
            "screenshot": "D:/runtime/logs/douyin_login_shots/login_wall_1.png",
            "text_excerpt": "请完成安全验证",
        }

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(scraper_module.session_health, "capture_login_wall", fake_capture)
    monkeypatch.setattr(scraper, "_raise_if_login_intercept", noop)
    monkeypatch.setattr(scraper_module, "DOUYIN_SEARCH_RESULT_WAIT_ATTEMPTS", 2)
    monkeypatch.setattr(scraper_module, "DOUYIN_SEARCH_RESULT_WAIT_TIMEOUT_MS", 50)

    try:
        asyncio.run(
            scraper._wait_for_search_results_ready(
                page,
                keyword="AI直播获客案例",
                url=page.url,
                logger=lambda message, level="info": logs.append(message),
            )
        )
    except RuntimeError as exc:
        message = str(exc)
        assert "AI直播获客案例" in message
        assert "连续 2 次" in message
        assert "登录/验证拦截=True" in message
        assert "验证类型=slider" in message
        assert "login_wall_" in message
    else:  # pragma: no cover
        raise AssertionError("两次都等不到视频条目时必须抛错")

    assert page.selector_waits == 2, page.selector_waits
    assert page.reloads == 1, "第二次尝试前应刷新页面，而不是对同一个 URL 再 goto"
    assert page.gotos == 0, page.gotos
    assert len(captured) == 2, captured
    assert any("等不到视频条目" in line for line in logs)


def test_search_wait_shell_page_says_result_area_empty(monkeypatch):
    scraper = DouyinCommentScraper(account_id=1, cdp_port=9332)
    page = _FakePage()

    async def fake_capture(target_page, **kwargs):
        return {
            "login_wall": False,
            "captcha": {"type": "none", "signal": ""},
            "screenshot": "D:/runtime/logs/douyin_login_shots/login_wall_1.png",
            "text_excerpt": "精选\n推荐\nAI抖音\n关注\n朋友\n我的\n直播\n2026 © 抖音\n京ICP备16016397号",
        }

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(scraper_module.session_health, "capture_login_wall", fake_capture)
    monkeypatch.setattr(scraper, "_raise_if_login_intercept", noop)
    monkeypatch.setattr(scraper_module, "DOUYIN_SEARCH_RESULT_WAIT_ATTEMPTS", 2)
    monkeypatch.setattr(scraper_module, "DOUYIN_SEARCH_RESULT_WAIT_TIMEOUT_MS", 50)

    try:
        asyncio.run(
            scraper._wait_for_search_results_ready(
                page,
                keyword="同城商家AI获客",
                url=page.url,
                logger=lambda message, level="info": None,
            )
        )
    except RuntimeError as exc:
        message = str(exc)
        assert "同城商家AI获客" in message
        assert "结果区是空的" in message
        assert "不是登录墙" in message
        assert "验证码" in message
        assert "TimeoutError" not in message
    else:  # pragma: no cover
        raise AssertionError("空壳页重试后仍必须失败")

    assert page.reloads == 1


def test_self_comment_partial_is_not_a_node_failure_unless_replies_all_fail():
    code = h5_chat_channel.self_comment_monitor_workflow_code
    assert code("partial", {"auto_reply_enabled": True, "last_auto_reply_success": 6, "last_auto_reply_failed": 2}) == 200
    assert code("partial", {"auto_reply_enabled": False, "last_auto_reply_success": 0, "last_auto_reply_failed": 0}) == 200
    assert code("completed", {"auto_reply_enabled": True, "last_auto_reply_success": 1, "last_auto_reply_failed": 0}) == 200
    assert code(
        "partial",
        {"auto_reply_enabled": True, "last_auto_reply_success": 0, "last_auto_reply_failed": 8},
    ) == 500
    assert code(
        "completed",
        {"auto_reply_enabled": True, "last_auto_reply_success": 0, "last_auto_reply_failed": 8},
    ) == 500
    assert code("failed", {"auto_reply_enabled": False}) == 500


def test_search_wait_returns_when_results_appear(monkeypatch):
    scraper = DouyinCommentScraper(account_id=1, cdp_port=9332)
    page = _FakePage()

    async def ok_selector(*_args, **_kwargs):
        return None

    monkeypatch.setattr(page, "wait_for_selector", ok_selector)
    monkeypatch.setattr(scraper_module, "DOUYIN_SEARCH_RESULT_WAIT_ATTEMPTS", 2)

    asyncio.run(
        scraper._wait_for_search_results_ready(
            page, keyword="AI", url=page.url, logger=lambda message, level="info": None
        )
    )
    assert page.selector_waits == 0


class _RedirectedStdout:
    """模拟客户端：stdout 被重定向到文件、locale 编码放不下中文里的特殊字符。"""

    encoding = "cp936"

    def __init__(self):
        self.buffer = io.BytesIO()

    def isatty(self):
        return False

    def write(self, _text):
        raise UnicodeEncodeError("cp936", "x", 0, 1, "illegal multibyte sequence")

    def flush(self):
        return None


def test_safe_print_uses_utf8_when_redirected(monkeypatch):
    from console_safe import safe_print

    fake = _RedirectedStdout()
    monkeypatch.setattr(sys, "stdout", fake)

    safe_print("[抖音私信] 跳过（不可私信）：主页不可达 ✅")

    written = fake.buffer.getvalue()
    assert written, "应该往 buffer 写字节"
    assert written.decode("utf-8") == "[抖音私信] 跳过（不可私信）：主页不可达 ✅\n"
