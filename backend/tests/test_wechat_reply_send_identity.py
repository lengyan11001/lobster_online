"""发送前身份核对：只在“显示名空间”比较，微信号不能和昵称互比。

回归两个线上场景：

① 2026-09-01 九变（eiaiyuangong）：会话本来就是对的，旧逻辑拿 wxid 去比昵称，
   于是把正确的会话判成“不一致”并整条不发；
② 2026-09-15 小亮（gxl5186）：当前窗口其实是涛哥的会话，旧逻辑直接放行，
   结果把回给小亮的话发进了涛哥的会话。
"""
from __future__ import annotations

import pytest

from backend.app.services import native_wechat_engine as engine


class _FakeChatBox:
    def __init__(self, name: str, chat_type: str) -> None:
        self._info = {"chat_name": name, "chat_type": chat_type}

    def get_info(self) -> dict:
        return dict(self._info)


class _FakeWx:
    """Minimal stand-in for the wxauto4 client used by the chat readers."""

    def __init__(self, name: str, chat_type: str = "friend") -> None:
        self.ChatBox = _FakeChatBox(name, chat_type)

    def ChatInfo(self) -> dict:
        return dict(self.ChatBox._info)


def test_anchor_accepts_matching_display_name():
    anchor = engine._local_send_chat_anchor(_FakeWx("九变"), "九变", use_current_chat=True)
    assert anchor["anchored"] is True
    assert anchor["reanchor"] is False
    assert anchor["current_chat"] == "九变"


def test_anchor_requires_reanchor_when_another_contact_is_open():
    anchor = engine._local_send_chat_anchor(_FakeWx("涛哥"), "小亮", use_current_chat=True)
    assert anchor["anchored"] is False
    assert anchor["reanchor"] is True
    assert anchor["current_chat"] == "涛哥"
    assert anchor["expected_display_name"] == "小亮"


def test_anchor_is_neutral_when_display_name_unknown():
    anchor = engine._local_send_chat_anchor(_FakeWx("涛哥"), "", use_current_chat=True)
    assert anchor["anchored"] is True
    assert anchor["reanchor"] is False
    assert anchor["reason"] == "name_unavailable"


def test_anchor_off_for_freshly_opened_chat():
    anchor = engine._local_send_chat_anchor(_FakeWx("涛哥"), "小亮", use_current_chat=False)
    assert anchor["reanchor"] is False
    assert anchor["reason"] == "chat_opened_by_id"


def test_wechat_id_target_with_matching_nickname_is_not_blocked():
    """① 九变那次：目标给的是微信号，当前会话是本人昵称，必须放行。"""
    result = engine._verify_local_send_chat(
        _FakeWx("九变"),
        "eiaiyuangong",
        strict_private=True,
        nickname_identity=True,
        expected_display_name="九变",
    )
    assert result["chat_name"] == "九变"


def test_another_contact_open_for_target_is_blocked():
    """② 小亮那次：目标是小亮，当前窗口是涛哥，必须判不一致且禁止发送。"""
    with pytest.raises(RuntimeError, match="chat_identity_mismatch"):
        engine._verify_local_send_chat(
            _FakeWx("涛哥"),
            "gxl5186",
            strict_private=True,
            expected_display_name="小亮",
        )


def test_verify_without_display_name_keeps_legacy_leniency_for_wxid_targets():
    """调用方拿不到昵称时保持旧行为，避免重新引入“总是判不对”。"""
    result = engine._verify_local_send_chat(
        _FakeWx("九变"),
        "eiaiyuangong",
        strict_private=True,
    )
    assert result["chat_name"] == "九变"

def test_anchor_reanchors_when_current_chat_title_unreadable():
    """2026-09-17 福永十亩地小管家/小洛神那次：窗口标题读不出来时，
    旧逻辑默认"就是目标"并复用窗口，结果把回复发给了别人。"""
    anchor = engine._local_send_chat_anchor(_FakeWx(""), "福永十亩地小管家", use_current_chat=True)
    assert anchor["anchored"] is False
    assert anchor["reanchor"] is True
    assert anchor["reason"] == "current_chat_unreadable"
    assert anchor["expected_display_name"] == "福永十亩地小管家"


def test_verify_blocks_when_private_chat_title_is_unreadable():
    """收件人无法确认时宁可整条不发，也不能发错人。"""
    with pytest.raises(RuntimeError, match="chat_identity_unreadable"):
        engine._verify_local_send_chat(
            _FakeWx("", "friend"),
            "xkcmxu",
            strict_private=True,
            expected_display_name="福永十亩地小管家",
        )


def test_auto_reply_never_reuses_the_execute_stage_window():
    """发送阶段固定按已校验的微信号重新打开会话，不再复用 execute 阶段留下的窗口。"""
    assert engine._auto_reply_send_uses_current_chat() is False
