from __future__ import annotations

import asyncio
import sys
import threading
import types

import pytest

from backend.app.services import native_wechat_engine as engine
from backend.app.api import native_wechat as native_wechat_api


def _use_temp_native_wechat_db(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native_wechat_engine.db")
    engine.init_db()


def test_partial_contact_sync_merges_without_dropping_existing(monkeypatch, tmp_path):
    _use_temp_native_wechat_db(monkeypatch, tmp_path)

    account_id = "wechat-account-1"
    existing = [
        {
            "contact_key": f"wxid_{index:04d}",
            "display_name": f"联系人{index:04d}",
            "source": "pc_wechat_uia_contacts",
        }
        for index in range(1000)
    ]
    engine._merge_contacts_snapshot(account_id, existing)

    partial = [
        {
            "contact_key": f"wxid_{index:04d}",
            "display_name": f"联系人{index:04d} updated",
            "source": "pc_wechat_uia_contacts",
        }
        for index in range(400)
    ]
    saved = engine._merge_contacts_snapshot(account_id, partial)

    page = engine.list_contacts(account_id, limit=1, offset=0)
    updated = engine.list_contacts(account_id, limit=5, keyword="updated")
    assert len(saved) == 400
    assert page["count"] == 1000
    assert updated["count"] == 400


def test_contact_sync_round_budget_covers_large_address_books():
    assert engine._contact_sync_max_rounds(3000) >= 600
    assert engine._contact_sync_max_rounds(10000) >= 900


def test_existing_contact_wx_no_index_reuses_saved_profile_id(monkeypatch, tmp_path):
    _use_temp_native_wechat_db(monkeypatch, tmp_path)

    account_id = "wechat-account-wx-no"
    engine._merge_contacts_snapshot(account_id, [{
        "contact_key": "张深根-AI三域营销运营",
        "display_name": "张深根-AI三域营销运营",
        "remark": "张老师",
        "wxNo": "AIZhang7891",
    }])

    index = engine._existing_contact_wx_no_index(account_id)

    assert index["张深根-ai三域营销运营"] == "AIZhang7891"
    assert index["张老师"] == "AIZhang7891"
    assert index["aizhang7891"] == "AIZhang7891"


def test_existing_contact_wx_no_index_drops_ambiguous_display_name(monkeypatch, tmp_path):
    _use_temp_native_wechat_db(monkeypatch, tmp_path)

    account_id = "wechat-account-duplicate-name"
    engine._merge_contacts_snapshot(account_id, [
        {
            "contact_key": "wxid_first_contact",
            "display_name": "徐",
            "wxNo": "wxid_first_contact",
        },
        {
            "contact_key": "wxid_second_contact",
            "display_name": "徐",
            "wxNo": "wxid_second_contact",
        },
    ])

    index = engine._existing_contact_wx_no_index(account_id)

    assert "徐" not in index
    assert index["wxid_first_contact"] == "wxid_first_contact"
    assert index["wxid_second_contact"] == "wxid_second_contact"


def test_recent_session_scan_only_resets_to_top_and_reads_first_page(monkeypatch):
    root = object()
    cells = [object(), object()]
    calls = []

    class FakeScrollTarget:
        def SetFocus(self):
            calls.append(("focus",))

        def WheelUp(self, *, wheelTimes):
            calls.append(("up", wheelTimes))

        def WheelDown(self, *, wheelTimes):
            calls.append(("down", wheelTimes))

    fake_auto = types.ModuleType("uiautomation")
    fake_auto.ControlFromHandle = lambda hwnd: root
    monkeypatch.setitem(sys.modules, "uiautomation", fake_auto)
    monkeypatch.setattr(engine, "_uia_session_cells", lambda value: cells)
    monkeypatch.setattr(engine, "_uia_scroll_target_from_cells", lambda visible, fallback: FakeScrollTarget())
    monkeypatch.setattr(
        engine,
        "_session_from_uia_cell",
        lambda cell: {"peer_id": "客户A" if cell is cells[0] else "客户B"},
    )
    monkeypatch.setattr(engine.time, "sleep", lambda seconds: None)

    result = engine._uia_collect_recent_sessions(1001)

    assert [item["peer_id"] for item in result["items"]] == ["客户A", "客户B"]
    assert result["rounds"] == 1
    assert calls == [("focus",), ("up", 60)]


def test_open_visible_session_does_not_scroll_down_when_target_is_missing(monkeypatch):
    root = object()
    cells = [object()]
    clicked = []

    fake_auto = types.ModuleType("uiautomation")
    fake_auto.ControlFromHandle = lambda hwnd: root
    monkeypatch.setitem(sys.modules, "uiautomation", fake_auto)
    monkeypatch.setattr(engine, "_module_available", lambda name: name == "uiautomation")
    monkeypatch.setattr(engine, "_local_wechat_hwnd", lambda account_id: 1001)
    monkeypatch.setattr(engine, "_uia_session_cells", lambda value: cells)
    monkeypatch.setattr(engine, "_find_uia_session_cell", lambda value, peer_id: None)
    monkeypatch.setattr(engine, "_uia_click", lambda control: clicked.append(control))

    try:
        engine._open_local_session_by_uia("local-pc", "不存在的会话")
    except RuntimeError as exc:
        assert "未在微信会话列表找到联系人" in str(exc)
    else:
        raise AssertionError("missing session should not be silently selected")

    assert clicked == []


def test_next_visible_session_uses_list_order_and_skips_processed_without_scroll(monkeypatch):
    root = object()
    cells = [object(), object(), object()]
    clicked = []
    labels = {cells[0]: "群聊", cells[1]: "客户A", cells[2]: "客户B"}

    fake_auto = types.ModuleType("uiautomation")
    fake_auto.ControlFromHandle = lambda hwnd: root
    monkeypatch.setitem(sys.modules, "uiautomation", fake_auto)
    monkeypatch.setattr(engine, "_module_available", lambda name: name == "uiautomation")
    monkeypatch.setattr(engine, "_local_wechat_hwnd", lambda account_id: 1001)
    monkeypatch.setattr(engine, "_uia_session_cells", lambda value: cells)
    monkeypatch.setattr(
        engine,
        "_session_from_uia_cell",
        lambda cell: {"peer_id": labels[cell], "display_name": labels[cell]},
    )
    monkeypatch.setattr(engine, "_uia_click", lambda cell: clicked.append(cell))
    monkeypatch.setattr(engine.time, "sleep", lambda seconds: None)

    first = engine._open_next_visible_session("local-pc", {"群聊"})
    second = engine._open_next_visible_session("local-pc", {"群聊", "客户A"})

    assert first["peer_id"] == "客户A"
    assert second["peer_id"] == "客户B"
    assert clicked == [cells[1], cells[2]]


def test_next_visible_session_processes_duplicate_display_names_separately(monkeypatch):
    root = object()
    cells = [object(), object()]
    clicked = []

    fake_auto = types.ModuleType("uiautomation")
    fake_auto.ControlFromHandle = lambda hwnd: root
    monkeypatch.setitem(sys.modules, "uiautomation", fake_auto)
    monkeypatch.setattr(engine, "_module_available", lambda name: name == "uiautomation")
    monkeypatch.setattr(engine, "_local_wechat_hwnd", lambda account_id: 1001)
    monkeypatch.setattr(engine, "_uia_session_cells", lambda value: cells)
    monkeypatch.setattr(
        engine,
        "_session_from_uia_cell",
        lambda cell: {
            "peer_id": "徐",
            "display_name": "徐",
            "session_key": "row-a" if cell is cells[0] else "row-b",
        },
    )
    monkeypatch.setattr(engine, "_uia_click", lambda cell: clicked.append(cell))
    monkeypatch.setattr(engine.time, "sleep", lambda seconds: None)

    processed = set()
    first = engine._open_next_visible_session("local-pc", processed, {})
    processed.add(first["peer_id"])
    second = engine._open_next_visible_session("local-pc", processed, {})

    assert first["peer_id"] != second["peer_id"]
    assert first["display_name"] == second["display_name"] == "徐"
    assert clicked == cells


def test_next_visible_session_scrolls_one_page_after_current_page(monkeypatch):
    root = object()
    first_page = [object(), object()]
    second_page = [object(), object()]
    pages = [first_page, first_page, first_page, second_page, second_page]
    clicked = []
    scrolls = []
    labels = {
        first_page[0]: "客户A",
        first_page[1]: "客户B",
        second_page[0]: "客户C",
        second_page[1]: "客户D",
    }

    class FakeScrollTarget:
        def SetFocus(self):
            pass

        def WheelDown(self, *, wheelTimes):
            scrolls.append(wheelTimes)

    fake_auto = types.ModuleType("uiautomation")

    def control_from_handle(hwnd):
        return root

    fake_auto.ControlFromHandle = control_from_handle
    monkeypatch.setitem(sys.modules, "uiautomation", fake_auto)
    monkeypatch.setattr(engine, "_module_available", lambda name: name == "uiautomation")
    monkeypatch.setattr(engine, "_local_wechat_hwnd", lambda account_id: 1001)
    monkeypatch.setattr(engine, "_uia_session_cells", lambda value: pages.pop(0))
    monkeypatch.setattr(engine, "_uia_scroll_target_from_cells", lambda visible, fallback: FakeScrollTarget())
    monkeypatch.setattr(
        engine,
        "_session_from_uia_cell",
        lambda cell: {"peer_id": labels[cell], "display_name": labels[cell]},
    )
    monkeypatch.setattr(engine, "_uia_click", lambda cell: clicked.append(cell))
    monkeypatch.setattr(engine.time, "sleep", lambda seconds: None)

    processed = set()
    state = {}
    assert engine._open_next_visible_session("local-pc", processed, state)["peer_id"] == "客户A"
    processed.add("客户A")
    assert engine._open_next_visible_session("local-pc", processed, state)["peer_id"] == "客户B"
    processed.add("客户B")
    assert engine._open_next_visible_session("local-pc", processed, state)["peer_id"] == "客户C"
    assert scrolls == [4]
    assert clicked == [first_page[0], first_page[1], second_page[0]]


def test_next_visible_session_skips_processed_overlap_and_keeps_scrolling(monkeypatch):
    root = object()
    page_one = [object(), object()]
    page_overlap = [object(), object()]
    page_three = [object()]
    pages = [page_one, page_overlap, page_three]
    clicked = []
    scrolls = []
    labels = {
        page_one[0]: "客户A",
        page_one[1]: "客户B",
        page_overlap[0]: "客户B",
        page_overlap[1]: "客户A",
        page_three[0]: "客户C",
    }

    class FakeScrollTarget:
        def SetFocus(self):
            pass

        def WheelDown(self, *, wheelTimes):
            scrolls.append(wheelTimes)

    fake_auto = types.ModuleType("uiautomation")
    fake_auto.ControlFromHandle = lambda hwnd: root
    monkeypatch.setitem(sys.modules, "uiautomation", fake_auto)
    monkeypatch.setattr(engine, "_module_available", lambda name: name == "uiautomation")
    monkeypatch.setattr(engine, "_local_wechat_hwnd", lambda account_id: 1001)
    monkeypatch.setattr(engine, "_uia_session_cells", lambda value: pages.pop(0))
    monkeypatch.setattr(engine, "_uia_scroll_target_from_cells", lambda visible, fallback: FakeScrollTarget())
    monkeypatch.setattr(
        engine,
        "_session_from_uia_cell",
        lambda cell: {"peer_id": labels[cell], "display_name": labels[cell]},
    )
    monkeypatch.setattr(engine, "_uia_click", lambda cell: clicked.append(cell))
    monkeypatch.setattr(engine.time, "sleep", lambda seconds: None)

    processed = {"客户A", "客户B"}
    state = {}
    item = engine._open_next_visible_session("local-pc", processed, state)

    assert item["peer_id"] == "客户C"
    assert scrolls == [4, 4]
    assert clicked == [page_three[0]]


def test_wxauto4_client_is_reused_per_account_and_thread(monkeypatch):
    created = []
    first = object()
    second = object()

    monkeypatch.setattr(
        engine,
        "_new_wxauto4_client",
        lambda account_id, ensure_chat_tab=True: created.append(account_id)
        or (first if len(created) == 1 else second),
    )
    monkeypatch.setattr(engine, "_prepare_local_automation_thread", lambda: {})
    monkeypatch.setattr(engine, "_recover_local_wechat_driver", lambda *args, **kwargs: {"ok": False})
    engine._LOCAL_WXAUTO4_CLIENTS.clear()
    engine._LOCAL_WECHAT_THREAD_STATE.operation_handles_recovery = False

    assert engine._get_wxauto4_client("local-pc") is first
    assert engine._get_wxauto4_client("local-pc") is first
    assert engine._get_wxauto4_client("another-pc") is second
    assert created == ["local-pc", "another-pc"]
    engine._LOCAL_WXAUTO4_CLIENTS.clear()


def test_local_wechat_async_operations_stay_on_one_thread():
    seen = []

    def record_thread():
        seen.append(threading.get_ident())
        return seen[-1]

    async def run():
        first = await engine._run_local_wechat_async(record_thread)
        second = await engine._run_local_wechat_async(record_thread)
        return first, second

    first, second = asyncio.run(run())

    assert first == second
    assert seen == [first, second]


def test_auto_reply_send_keeps_the_already_open_session(monkeypatch, tmp_path):
    _use_temp_native_wechat_db(monkeypatch, tmp_path)

    class FakeWechat:
        def __init__(self):
            self.chat_with_calls = []

        def ChatWith(self, *args, **kwargs):
            self.chat_with_calls.append((args, kwargs))
            raise AssertionError("auto reply must not reselect the chat with ChatWith")

    wx = FakeWechat()
    monkeypatch.setattr(engine, "_find_local_account", lambda account_id: {"hwnd": 1001})
    monkeypatch.setattr(engine, "_get_wxauto4_client", lambda account_id: wx)
    monkeypatch.setattr(engine, "_enforce_local_send_rate", lambda account_id: None)
    monkeypatch.setattr(engine, "_focus_local_wechat", lambda hwnd: None)
    monkeypatch.setattr(
        engine,
        "_open_local_contact_profile_via_search",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("the execute-stage verified chat must not be searched again during send")
        ),
    )
    monkeypatch.setattr(engine, "_send_hotkey", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine, "_send_hotkey_quick", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine, "_paste_text_quick", lambda text: None)
    monkeypatch.setattr(
        engine,
        "_prepare_local_wechat_input_text",
        lambda hwnd, text: {
            "ok": True,
            "method": "test",
            "attempts": [{"attempt": 1, "after": text}],
            "input": {"found": True, "draft": text},
        },
    )
    monkeypatch.setattr(
        engine,
        "_submit_local_wechat_typed_message",
        lambda wx, hwnd, text, **kwargs: {"ok": True, "verified": True, "send_method": "test", "attempts": 1},
    )

    result = engine._send_text_local_slow_once(
        "local-pc",
        "wxid_customer_a",
        "reply",
        {"driver": "native_wechat_auto_reply"},
        use_current_chat=True,
    )

    assert result["ok"] is True
    assert wx.chat_with_calls == []


def test_auto_reply_send_uses_verified_contact_search_for_captured_wxid(monkeypatch, tmp_path):
    _use_temp_native_wechat_db(monkeypatch, tmp_path)

    class FakeWechat:
        def __init__(self):
            self.chat_with_calls = []

        def ChatWith(self, *args, **kwargs):
            self.chat_with_calls.append((args, kwargs))

        def ChatInfo(self):
            return {"chat_name": "Customer nickname", "chat_type": "direct"}

    wx = FakeWechat()
    search_calls = []
    monkeypatch.setattr(engine, "_find_local_account", lambda account_id: {"hwnd": 1001})
    monkeypatch.setattr(engine, "_get_wxauto4_client", lambda account_id: wx)
    monkeypatch.setattr(engine, "_enforce_local_send_rate", lambda account_id: None)
    monkeypatch.setattr(engine, "_focus_local_wechat", lambda hwnd: None)
    monkeypatch.setattr(
        engine,
        "_open_local_contact_profile_via_search",
        lambda hwnd, account_id, target, steps, *, open_moments=False: search_calls.append(
            (hwnd, account_id, target, open_moments)
        ) or target,
    )
    monkeypatch.setattr(engine, "_prepare_local_wechat_input_text", lambda hwnd, text: {
        "ok": True,
        "method": "test",
        "attempts": [{"attempt": 1, "after": text}],
        "input": {"found": True, "draft": text},
    })
    monkeypatch.setattr(
        engine,
        "_submit_local_wechat_typed_message",
        lambda wx, hwnd, text, **kwargs: {"ok": True, "verified": True, "send_method": "test", "attempts": 1},
    )

    result = engine._send_text_local_slow_once(
        "local-pc",
        "wxid_customer_a",
        "reply",
        {"driver": "native_wechat_auto_reply"},
        use_current_chat=False,
    )

    assert result["ok"] is True
    assert search_calls == [(1001, "local-pc", "wxid_customer_a", False)]
    assert wx.chat_with_calls == []


def test_submit_does_not_click_when_input_text_is_missing(monkeypatch):
    clicked = []
    monkeypatch.setattr(engine, "_local_wechat_draft_text", lambda hwnd: "")
    monkeypatch.setattr(engine, "_click_local_wechat_send_button", lambda hwnd: clicked.append(hwnd))

    with pytest.raises(RuntimeError, match="已阻止点击发送"):
        engine._submit_local_wechat_typed_message(object(), 1001, "reply")

    assert clicked == []


def test_auto_reply_history_terminal_outcome_does_not_block_a_new_round(monkeypatch, tmp_path):
    _use_temp_native_wechat_db(monkeypatch, tmp_path)
    account_id = "wechat-account-history"
    peer_id = "contact-1"
    inbound = {
        "provider_message_id": "wx-message-1",
        "content": "hello",
        "direction": "in",
    }

    assert engine._record_auto_reply_history(
        account_id,
        peer_id,
        inbound,
        reply="hi",
        status="sending",
    )
    assert engine._record_auto_reply_history(
        account_id,
        peer_id,
        inbound,
        reply="hi",
        status="sent",
    )
    assert engine._record_auto_reply_history(
        account_id,
        peer_id,
        inbound,
        reply="hi again",
        status="sending",
    )
    assert engine._auto_reply_history_state(account_id, peer_id, inbound)["status"] == "sending"


def test_auto_reply_unknown_send_can_be_retried_in_a_later_round(monkeypatch, tmp_path):
    _use_temp_native_wechat_db(monkeypatch, tmp_path)
    account_id = "wechat-account-unknown"
    peer_id = "contact-unknown"
    inbound = {
        "provider_message_id": "wx-message-unknown",
        "content": "hello",
        "direction": "in",
    }

    assert engine._record_auto_reply_history(
        account_id,
        peer_id,
        inbound,
        reply="hi",
        status="unknown",
    )
    assert engine._record_auto_reply_history(
        account_id,
        peer_id,
        inbound,
        reply="hi again",
        status="sending",
    )
    assert engine._auto_reply_history_state(account_id, peer_id, inbound)["status"] == "sending"


def test_stale_session_preview_cannot_override_newer_outbound(monkeypatch, tmp_path):
    _use_temp_native_wechat_db(monkeypatch, tmp_path)
    account_id = "wechat-account-preview"
    peer_id = "contact-preview"
    with engine._connect() as conn:
        conn.execute(
            """
            insert into wechat_messages(
                id, account_id, peer_id, direction, msg_type, content,
                provider_message_id, status, raw_json, created_at
            ) values(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "inbound-old",
                account_id,
                peer_id,
                "in",
                "text",
                "old customer text",
                "wx-inbound-old",
                "received",
                "{}",
                "2026-09-01T03:00:00",
            ),
        )
        conn.execute(
            """
            insert into wechat_messages(
                id, account_id, peer_id, direction, msg_type, content,
                provider_message_id, status, raw_json, created_at
            ) values(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "outbound-new",
                account_id,
                peer_id,
                "out",
                "text",
                "new reply",
                None,
                "sent",
                "{}",
                "2026-09-01T03:01:00",
            ),
        )

    promoted = engine._promote_session_preview_latest(account_id, peer_id, "old customer text")

    assert promoted is not None
    assert promoted["direction"] == "out"
    assert promoted["content"] == "new reply"


def test_prepare_input_switches_to_clipboard_when_uia_draft_never_appears(monkeypatch):
    expected = "reply from clipboard"
    pasted = []
    set_values = []

    class FakeInput:
        def SetFocus(self):
            return None

    clock = {"value": 0.0}

    def monotonic():
        clock["value"] += 0.5
        return clock["value"]

    def set_value(_control, value):
        set_values.append(value)
        return True

    monkeypatch.setattr(engine, "_focus_local_wechat_input", lambda hwnd: (FakeInput(), {"found": True}))
    monkeypatch.setattr(engine, "_uia_value_text", lambda control: "")
    monkeypatch.setattr(engine, "_uia_try_set_value", set_value)
    monkeypatch.setattr(engine, "_paste_text_quick", lambda value: pasted.append(value))
    monkeypatch.setattr(engine, "_local_wechat_draft_text", lambda hwnd: expected if pasted else "")
    monkeypatch.setattr(engine, "_local_wechat_input_state", lambda hwnd: {"found": True, "draft": expected})
    monkeypatch.setattr(engine.time, "monotonic", monotonic)
    monkeypatch.setattr(engine.time, "sleep", lambda seconds: None)

    result = engine._prepare_local_wechat_input_text(1001, expected)

    assert result["method"] == "clipboard_paste"
    assert pasted == [expected]
    assert set_values.count(expected) == 1


def test_send_button_waits_for_verified_uia_button(monkeypatch):
    clicked = []
    scans = {"count": 0}

    class FakeButton:
        ControlTypeName = "ButtonControl"

    button = FakeButton()

    def walk(root, **kwargs):
        scans["count"] += 1
        return [] if scans["count"] == 1 else [button]

    monkeypatch.setattr(engine, "_focus_local_wechat", lambda hwnd: None)
    monkeypatch.setattr(engine, "_uia_foreground_or_main_root", lambda hwnd: object())
    monkeypatch.setattr(engine, "_uia_walk", walk)
    monkeypatch.setattr(engine, "_uia_control_text", lambda node: "发送(S)")
    monkeypatch.setattr(engine, "_uia_control_class", lambda node: "mmui::XOutlineButton")
    monkeypatch.setattr(engine, "_uia_rect_tuple", lambda node: (100, 200, 180, 240))
    monkeypatch.setattr(engine, "_uia_click", lambda node: clicked.append(node))
    monkeypatch.setattr(engine.time, "sleep", lambda seconds: None)

    method = engine._click_local_wechat_send_button(1001)

    assert scans["count"] == 2
    assert clicked == [button]
    assert method == "uia_send_button:100,200,180,240"


def test_unverified_send_does_not_persist_a_false_outbound(monkeypatch, tmp_path):
    _use_temp_native_wechat_db(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine,
        "_find_local_account",
        lambda _account_id: {"hwnd": 1001, "account_id": "wechat-account"},
    )
    monkeypatch.setattr(engine, "_focus_local_wechat", lambda _hwnd: None)
    monkeypatch.setattr(engine, "_enforce_local_send_rate", lambda _account_id: None)
    monkeypatch.setattr(engine, "_get_wxauto4_client", lambda _account_id: object())
    monkeypatch.setattr(
        engine,
        "_verify_local_send_chat",
        lambda *_args, **_kwargs: {"chat_type": "direct", "chat_name": "contact-a"},
    )
    monkeypatch.setattr(
        engine,
        "_prepare_local_wechat_input_text",
        lambda _hwnd, text: {
            "ok": True,
            "method": "test",
            "attempts": [],
            "input": {"found": True, "draft": text},
        },
    )
    monkeypatch.setattr(
        engine,
        "_submit_local_wechat_typed_message",
        lambda *_args, **_kwargs: {
            "ok": True,
            "verified": False,
            "verification": "draft_cleared_snapshot_pending",
            "send_method": "uia_send_button",
            "attempts": 1,
        },
    )

    with pytest.raises(engine._LocalWeChatSendUncertain):
        engine._send_text_local_slow_once(
            "wechat-account",
            "contact-a",
            "reply",
            use_current_chat=True,
        )

    with engine._connect() as conn:
        count = conn.execute(
            "select count(*) from wechat_messages where account_id=? and direction='out'",
            ("wechat-account",),
        ).fetchone()[0]
    assert count == 0


def test_task_targets_preserve_spaces_inside_contact_names():
    targets = engine._normalize_task_targets([
        "FIPILOCK-Joy 温副总",
        "erbiandeqixi",
        "wxid_one, wxid_two\nwxid_three，wxid_four、wxid_five；wxid_six",
    ])

    assert targets == [
        "FIPILOCK-Joy 温副总",
        "erbiandeqixi",
        "wxid_one",
        "wxid_two",
        "wxid_three",
        "wxid_four",
        "wxid_five",
        "wxid_six",
    ]


def test_native_wechat_api_targets_preserve_spaces_inside_contact_names():
    assert native_wechat_api._merge_targets(
        ["FIPILOCK-Joy 温副总", "erbiandeqixi"],
        "wxid_one,wxid_two\nwxid_three",
    ) == [
        "FIPILOCK-Joy 温副总",
        "erbiandeqixi",
        "wxid_one",
        "wxid_two",
        "wxid_three",
    ]


def test_current_chat_group_only_searches_configured_wechat_id(monkeypatch):
    calls = []

    async def fake_run(func, *args):
        calls.append((func, args))
        return {
            "ok": True,
            "selected": 1,
            "group_verified": True,
            "verified_member_count": 3,
            "group": {"group_key": "test-group"},
        }

    monkeypatch.setattr(engine, "_run_local_wechat_async", fake_run)
    monkeypatch.setattr(engine, "_update_task_payload", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine, "_finish_task", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine, "_observe_wechat_intelligence", lambda *args, **kwargs: None)

    asyncio.run(engine._process_create_group_task({
        "id": "task-1",
        "account_id": "pc-wechat-default",
        "targets": ["FIPILOCK-Joy 温副总", "erbiandeqixi"],
        "payload": {"use_current_chat": True},
    }))

    assert calls == [(
        engine.create_local_group_from_current,
        (
            "pc-wechat-default",
            ["FIPILOCK-Joy 温副总", "erbiandeqixi"],
            ["erbiandeqixi"],
        ),
    )]


def test_session_time_old_calendar_boundary_is_conservative():
    now = engine.datetime(2026, 8, 19, 15, 0)
    assert engine._session_time_over_24h("24小时前", now=now)
    assert engine._session_time_over_24h("昨天14:59", now=now)
    assert engine._session_time_over_24h("前天", now=now)
    assert engine._session_time_over_24h("昨天", now=now)
    assert not engine._session_time_over_24h("14:00", now=now)
    assert engine._session_time_over_24h("昨天下午2:59", now=now)
    assert not engine._session_time_over_24h("下午2:00", now=now)
    assert engine._session_time_over_24h("8月18日23:59", now=now)
    assert not engine._session_time_over_24h("8月19日00:01", now=now)
    assert engine._session_time_over_24h("\u661f\u671f\u4e00", now=now)
    assert engine._session_time_over_24h("\u5468\u4e00", now=now)
    assert engine._session_time_over_24h(
        "12\u670831\u65e523:59",
        now=engine.datetime(2027, 1, 1, 0, 5),
    )


def test_auto_reply_defaults_to_100_private_sessions_and_respects_old_boundary():
    assert engine.DEFAULT_STRATEGY["auto_reply_private_sessions_per_round"] == 100
    assert engine._auto_reply_default_config("pc-wechat-default")["private_sessions_per_round"] == 100
    assert engine._normalize_auto_reply_private_session_limit(None) == 100
    assert engine._normalize_auto_reply_private_session_limit(10) == 100
    assert engine._normalize_auto_reply_private_session_limit(36) == 36
    assert engine._session_stops_recent_scan({"session_time": "24小时前"})
    assert engine._session_stops_recent_scan({"session_time": "昨天"})
    assert not engine._session_stops_recent_scan({"session_time": "昨天14:59", "pinned": True})


def test_wxauto4_recent_session_scan_continues_after_old_pinned_page(monkeypatch):
    now = engine.datetime.now().replace(microsecond=0)
    old_time = (now - engine.timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    recent_time = (now - engine.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    class FakeSessionBox:
        def __init__(self):
            self.page = 0

        def go_top(self):
            self.page = 0

        def roll_down(self):
            self.page = min(self.page + 1, 1)

    class FakeWx:
        def __init__(self):
            self.SessionBox = FakeSessionBox()

        def GetSession(self):
            pages = [
                [
                    {"name": "old-pinned", "content": "old", "time": old_time},
                    {"name": "recent-one", "content": "recent-1", "time": recent_time},
                ],
                [
                    {"name": "jun-zheng", "content": "recent-2", "time": recent_time},
                ],
            ]
            return pages[self.SessionBox.page]

    monkeypatch.setattr(engine, "_get_wxauto4_client", lambda _account_id: FakeWx())
    monkeypatch.setattr(engine, "_wxauto4_visible_pinned_names", lambda _account_id: {"old-pinned"})
    monkeypatch.setattr(
        engine,
        "_persist_session",
        lambda _account_id, session, *, chat_type="unknown": {
            **session,
            "changed": False,
            "chat_type": chat_type,
        },
    )

    result = engine._sync_recent_sessions_from_wxauto4("wechat-account", max_pages=10)
    names = {str(item.get("display_name") or "") for item in result["items"]}
    assert names == {"recent-one", "jun-zheng"}
    assert result["scroll_completed"] is True
    assert result["scroll_rounds"] >= 3


def test_wxauto4_recent_session_scan_stops_at_first_old_ordinary_row(monkeypatch):
    now = engine.datetime.now().replace(microsecond=0)
    old_time = (now - engine.timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    recent_time = (now - engine.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    class FakeSessionBox:
        def __init__(self):
            self.page = 0

        def go_top(self):
            self.page = 0

        def roll_down(self):
            self.page = min(self.page + 1, 1)

    class FakeWx:
        def __init__(self):
            self.SessionBox = FakeSessionBox()

        def GetSession(self):
            pages = [
                [
                    {"name": "recent-one", "content": "recent", "time": recent_time},
                    {"name": "old-boundary", "content": "old", "time": old_time},
                    {"name": "must-not-be-read", "content": "later row", "time": recent_time},
                ],
                [{"name": "must-not-scroll", "content": "later page", "time": recent_time}],
            ]
            return pages[self.SessionBox.page]

    monkeypatch.setattr(engine, "_get_wxauto4_client", lambda _account_id: FakeWx())
    monkeypatch.setattr(engine, "_wxauto4_visible_pinned_names", lambda _account_id: set())
    monkeypatch.setattr(
        engine,
        "_persist_session",
        lambda _account_id, session, *, chat_type="unknown": {
            **session,
            "changed": False,
            "chat_type": chat_type,
        },
    )

    result = engine._sync_recent_sessions_from_wxauto4("wechat-account", max_pages=10)

    assert [item["display_name"] for item in result["items"]] == ["recent-one"]
    assert result["time_scan_old_boundary"] is True
    assert result["scroll_rounds"] == 1


def test_wxauto4_auto_reply_scan_captures_each_live_page_before_scroll(monkeypatch):
    now = engine.datetime.now().replace(microsecond=0)
    recent_time = (now - engine.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    clicks = []
    sync_calls = []

    class LiveSession:
        name = "Customer"
        wxid = "wxid_customer"
        content = "hello"
        time = recent_time
        type = "friend"

        def click(self):
            clicks.append(self.name)

    live_session = LiveSession()

    class FakeSessionBox:
        def __init__(self):
            self.page = 0

        def go_top(self):
            self.page = 0

        def roll_down(self):
            self.page = min(self.page + 1, 1)

    class FakeWx:
        def __init__(self):
            self.SessionBox = FakeSessionBox()

        def GetSession(self):
            return [live_session]

    monkeypatch.setattr(engine, "_get_wxauto4_client", lambda _account_id: FakeWx())
    monkeypatch.setattr(engine, "_wxauto4_visible_pinned_names", lambda _account_id: set())
    monkeypatch.setattr(
        engine,
        "_sync_local_messages_once",
        lambda _account_id, peer_id, **kwargs: sync_calls.append((peer_id, kwargs)) or {
            "ok": True,
            "peer_id": "wxid_customer",
            "chat_info": {"chat_name": "Customer", "chat_type": "friend"},
            "fresh_latest_message": {"direction": "in", "content": "hello"},
        },
    )
    monkeypatch.setattr(
        engine,
        "_persist_session",
        lambda _account_id, session, *, chat_type="unknown": {**session, "changed": False, "chat_type": chat_type},
    )

    result = engine._sync_recent_sessions_from_wxauto4(
        "wechat-account",
        max_pages=3,
        capture_auto_reply=True,
    )

    assert clicks == ["Customer"]
    assert sync_calls[0][0] == ""
    assert sync_calls[0][1]["current_selected"] is True
    assert result["auto_reply_captures"]["wxid_customer"]["wechat_id"] == "wxid_customer"


def test_auto_reply_page_capture_accepts_normalized_session_snapshot(monkeypatch):
    recent_time = (engine.datetime.now() - engine.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    inbound = {"direction": "in", "content": "hello", "provider_message_id": "msg-1"}

    monkeypatch.setattr(
        engine,
        "_sync_local_messages_once",
        lambda *_args, **_kwargs: {
            "ok": True,
            "peer_id": "\u5f90",
            "chat_info": {"chat_type": "friend", "chat_name": "\u5f90"},
            "fresh_latest_message": inbound,
        },
    )
    monkeypatch.setattr(
        engine,
        "_read_current_private_chat_wx_no",
        lambda *_args, **_kwargs: {"ok": True, "wx_no": "wxid_friend"},
    )

    captures = engine._capture_auto_reply_scan_page(
        "wechat-account",
        [
            {
                "peer_id": "\u5f90",
                "display_name": "\u5f90",
                "last_content": "hello",
                "session_time": recent_time,
                "session_snapshot_fresh": True,
                "raw": {},
            }
        ],
    )

    assert captures["\u5f90"]["wechat_id"] == "wxid_friend"
    assert captures["\u5f90"]["inbound"] == inbound


def test_auto_reply_live_page_capture_clicks_session_element_without_nickname_search(monkeypatch):
    now = engine.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clicked = []
    calls = []

    class LiveSession:
        name = "Customer nickname"
        content = "hello"
        time = now

        def click(self):
            clicked.append(self.name)

    def sync_page(_account_id, peer_id, **kwargs):
        calls.append((peer_id, kwargs))
        return {
            "ok": True,
            "peer_id": "wxid_customer_a",
            "chat_info": {"chat_name": "Customer nickname", "chat_type": "direct"},
            "fresh_latest_message": {"direction": "in", "content": "hello"},
        }

    monkeypatch.setattr(engine, "_sync_local_messages_once", sync_page)
    monkeypatch.setattr(
        engine,
        "_read_current_private_chat_wx_no",
        lambda *_args, **_kwargs: {"ok": True, "wx_no": "wxid_customer_a"},
    )

    captures = engine._capture_auto_reply_scan_page(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        [LiveSession()],
    )

    assert clicked == ["Customer nickname"]
    assert calls[0][0] == ""
    assert calls[0][1]["current_selected"] is True
    assert captures["Customer nickname"]["wechat_id"] == "wxid_customer_a"


def test_auto_reply_page_capture_keeps_known_private_chat_when_chat_info_type_is_transiently_unknown(monkeypatch):
    now = engine.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setattr(
        engine,
        "_sync_local_messages_once",
        lambda *_args, **_kwargs: {
            "ok": True,
            "peer_id": "Jason",
            "chat_info": {"chat_name": "Jason", "chat_type": "unknown"},
            "message_chat_type": "",
            "fresh_latest_message": {"direction": "in", "content": "animated emoji"},
        },
    )
    monkeypatch.setattr(engine, "_known_local_peer_chat_type", lambda *_args: "friend")
    monkeypatch.setattr(
        engine,
        "_read_current_private_chat_wx_no",
        lambda *_args, **_kwargs: {"ok": True, "wx_no": "Jasonchen369258147"},
    )

    captures = engine._capture_auto_reply_scan_page(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        [{"name": "Jason", "content": "[animated emoji]", "time": now}],
    )

    assert captures["Jason"]["wechat_id"] == "Jasonchen369258147"
    assert captures["Jason"]["inbound"]["direction"] == "in"


def test_local_contact_search_uses_keyboard_events(monkeypatch):
    calls = []

    class FakeSearch:
        def SetFocus(self):
            calls.append(("focus",))

        def Click(self, **kwargs):
            calls.append(("click", kwargs))

        def SendKeys(self, value, **kwargs):
            calls.append(("send_keys", value, kwargs))

    monkeypatch.setattr(engine, "_send_hotkey", lambda *args, **kwargs: calls.append(("hotkey", args, kwargs)))
    monkeypatch.setattr(engine.time, "sleep", lambda seconds: None)

    result = engine._uia_set_local_search_query(FakeSearch(), "hddhdjjdjensb")

    assert result["method"] == "uia_send_keys"
    assert result["value"] == "hddhdjjdjensb"
    assert [item[0] for item in calls] == ["focus", "click", "hotkey", "hotkey", "send_keys"]
    assert calls[-1][2]["charMode"] is True


def test_local_contact_search_falls_back_to_clipboard(monkeypatch):
    calls = []

    class FakeSearch:
        def SetFocus(self):
            pass

        def Click(self, **kwargs):
            pass

        def SendKeys(self, value, **kwargs):
            raise RuntimeError("uia keyboard unavailable")

    monkeypatch.setattr(engine, "_send_hotkey", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine, "_paste_text", lambda value: calls.append(value))
    monkeypatch.setattr(engine.time, "sleep", lambda seconds: None)

    result = engine._uia_set_local_search_query(FakeSearch(), "hddhdjjdjensb")

    assert result["method"] == "clipboard_paste"
    assert result["fallback_error"] == "uia keyboard unavailable"
    assert calls == ["hddhdjjdjensb"]


def test_local_contact_search_can_force_clipboard_retry(monkeypatch):
    calls = []

    class FakeSearch:
        def SetFocus(self):
            pass

        def Click(self, **kwargs):
            pass

        def SendKeys(self, value, **kwargs):
            calls.append(("send_keys", value))

    monkeypatch.setattr(engine, "_send_hotkey", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine, "_paste_text", lambda value: calls.append(("paste", value)))
    monkeypatch.setattr(engine.time, "sleep", lambda seconds: None)

    result = engine._uia_set_local_search_query(
        FakeSearch(),
        "hddhdjjdjensb",
        force_paste=True,
    )

    assert result["method"] == "clipboard_paste"
    assert result["fallback_error"] == "forced clipboard retry"
    assert calls == [("paste", "hddhdjjdjensb")]
