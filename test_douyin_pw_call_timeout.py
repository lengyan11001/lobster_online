"""抖音评论采集：Playwright 无超时调用不能挂死整条任务（2026-09-21）。

现场现象：单条视频挂 11~14 分钟后报 `CancelledError`（栈位置一次在 page.evaluate，
一次在 browser_context.new_page），runtime 日志显示这是 900 秒看门狗主动 cancel。
根因是 page.evaluate() / new_page() 在 Playwright 里没有超时参数，CDP 半死时会一直挂。
"""

import asyncio

import backend.app.api.h5_chat_channel as h5_chat_channel

h5_chat_channel._install_douyin_origin_import_path()
import douyin_api  # type: ignore  # noqa: E402
import douyin_comment_scraper as scraper_module  # type: ignore  # noqa: E402
from douyin_comment_scraper import DouyinCommentScraper  # type: ignore  # noqa: E402


class _HangingContext:
    """new_page() 永远不返回，模拟 CDP 连接卡死。"""

    def __init__(self):
        self.new_page_calls = 0

    async def new_page(self):
        self.new_page_calls += 1
        await asyncio.sleep(3600)


class _RecoveringContext(_HangingContext):
    """重连后的健康 context：立刻返回页面。"""

    async def new_page(self):
        self.new_page_calls += 1
        return "RECOVERED_PAGE"


class _HangingEvaluatePage:
    def __init__(self):
        self.evaluate_calls = 0

    async def evaluate(self, *_args, **_kwargs):
        self.evaluate_calls += 1
        await asyncio.sleep(3600)


def _scraper(monkeypatch):
    scraper = DouyinCommentScraper(account_id=1, cdp_port=9332)
    monkeypatch.setattr(scraper_module, "DOUYIN_PW_NEW_PAGE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(scraper_module, "DOUYIN_PW_EVALUATE_TIMEOUT_SECONDS", 0.05)
    return scraper


def test_new_page_hard_timeout_then_reconnects(monkeypatch):
    scraper = _scraper(monkeypatch)
    contexts = [_HangingContext(), _RecoveringContext()]
    ensure_calls = []
    disposed = []

    async def fake_ensure(_self=None, logger=None):
        ensure_calls.append(len(ensure_calls) + 1)
        scraper._context = contexts[min(len(ensure_calls), len(contexts)) - 1]

    async def fake_dispose(_self=None):
        disposed.append(True)

    monkeypatch.setattr(type(scraper), "_ensure_browser", fake_ensure, raising=False)
    monkeypatch.setattr(type(scraper), "_dispose_browser_runtime", fake_dispose, raising=False)

    logs = []
    page = asyncio.run(scraper._new_page(logger=lambda message, level="info": logs.append((level, message))))

    assert page == "RECOVERED_PAGE"
    assert len(disposed) == 1, "硬超时后必须丢弃旧 runtime 再重连 CDP"
    assert any("准备重连 CDP 后重试" in line for _level, line in logs)


def test_new_page_hard_timeout_surfaces_when_reconnect_also_hangs(monkeypatch):
    scraper = _scraper(monkeypatch)

    async def fake_ensure(_self=None, logger=None):
        scraper._context = _HangingContext()

    async def fake_dispose(_self=None):
        return None

    monkeypatch.setattr(type(scraper), "_ensure_browser", fake_ensure, raising=False)
    monkeypatch.setattr(type(scraper), "_dispose_browser_runtime", fake_dispose, raising=False)

    try:
        asyncio.run(scraper._new_page())
    except TimeoutError as exc:
        assert "无响应" in str(exc)
    else:  # pragma: no cover - 挂死的话这里会超时失败
        raise AssertionError("重连后仍然卡死时必须抛错，而不是一直等")


def test_reply_button_evaluate_has_hard_timeout(monkeypatch):
    scraper = _scraper(monkeypatch)
    page = _HangingEvaluatePage()

    async def run():
        await scraper._find_and_click_comment_reply_button(
            page,
            {"username": "用户A", "comment": "怎么收费", "comment_index": 1},
            max_scroll_rounds=1,
            allow_scroll=False,
        )

    try:
        asyncio.run(run())
    except TimeoutError as exc:
        assert "无响应" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("page.evaluate 挂住时必须抛硬超时")
    assert page.evaluate_calls == 1


def test_interrupt_message_separates_timeout_from_cancel():
    timeout_message = douyin_api._douyin_collection_interrupt_message(
        asyncio.CancelledError(), True
    )
    cancel_message = douyin_api._douyin_collection_interrupt_message(
        asyncio.CancelledError(), False
    )

    assert str(int(douyin_api.DOUYIN_COMMENT_TASK_TIMEOUT_SECONDS)) in timeout_message
    assert "CancelledError" not in timeout_message
    assert "CancelledError" in cancel_message


def test_watchdog_timeout_is_not_hardcoded_twice():
    source = (
        __import__("pathlib").Path(douyin_api.__file__).read_text(encoding="utf-8", errors="replace")
    )

    assert "asyncio.sleep(900.0)" not in source
    assert source.count("asyncio.sleep(DOUYIN_COMMENT_TASK_TIMEOUT_SECONDS)") == 2
