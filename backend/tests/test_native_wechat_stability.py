from __future__ import annotations

from backend.app.services import native_wechat_engine as nw


def test_popup_classifier_closes_screenshot_tool_window():
    assert nw._wechat_popup_should_close("Qt51514QWindowToolSaveBits", "Weixin", 22100, 863406) is True


def test_popup_classifier_does_not_close_main_or_large_chat_window():
    assert nw._wechat_popup_should_close("Qt51514QWindowIcon", "微信", 863406, 863406) is False
    assert nw._wechat_popup_should_close("Qt51514QWindowIcon", "某个联系人", 500000, 863406) is False


def test_popup_classifier_does_not_close_benign_system_window():
    assert nw._wechat_popup_should_close("Base_PowerMessageWindow", "", 100, 863406) is False


def test_popup_classifier_matches_search_title_even_with_main_class():
    assert nw._wechat_popup_should_close("Qt51514QWindowIcon", "搜一搜", 300000, 863406) is True


def test_recover_circuit_breaker_opens_after_limit_and_clears():
    account = "acct-test"
    peer = "peer-test"
    nw._clear_auto_reply_recover_failure(account, peer)
    assert nw._auto_reply_recover_circuit_open(account, peer) is False
    for index in range(nw._AUTO_REPLY_RECOVER_FAIL_LIMIT):
        assert nw._note_auto_reply_recover_failure(account, peer, "test") == index + 1
    assert nw._auto_reply_recover_circuit_open(account, peer) is True
    nw._clear_auto_reply_recover_failure(account, peer)
    assert nw._auto_reply_recover_failure_count(account, peer) == 0
    assert nw._auto_reply_recover_circuit_open(account, peer) is False


def test_ui_executor_busy_marker_roundtrip():
    nw._clear_ui_executor_busy()
    nw._set_ui_executor_busy("读取微信会话", "acct-test")
    info = nw._ui_executor_busy_info()
    assert info["operation"] == "读取微信会话"
    assert info["account_id"] == "acct-test"
    assert info["age_seconds"] >= 0
    nw._clear_ui_executor_busy()
    assert nw._ui_executor_busy_info() == {}


def test_hung_exception_is_runtime_error():
    assert issubclass(nw._LocalWeChatWindowHung, RuntimeError)
