from __future__ import annotations

import asyncio
import sys
import threading
import types

from backend.app.services import native_wechat_engine as engine


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
    monkeypatch.setattr(engine, "_send_hotkey", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine, "_send_hotkey_quick", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine, "_paste_text_quick", lambda text: None)
    monkeypatch.setattr(
        engine,
        "_submit_local_wechat_typed_message",
        lambda wx, hwnd, text: {"ok": True, "verified": True, "send_method": "test", "attempts": 1},
    )

    result = engine._send_text_local_slow_once(
        "local-pc",
        "customer-a",
        "reply",
        use_current_chat=True,
    )

    assert result["ok"] is True
    assert wx.chat_with_calls == []


def test_session_time_over_24h_is_conservative():
    now = engine.datetime(2026, 8, 19, 15, 0)
    assert engine._session_time_over_24h("24小时前", now=now)
    assert engine._session_time_over_24h("昨天14:59", now=now)
    assert engine._session_time_over_24h("前天", now=now)
    assert not engine._session_time_over_24h("昨天", now=now)
    assert not engine._session_time_over_24h("14:00", now=now)
    assert engine._session_time_over_24h("昨天下午2:59", now=now)
    assert not engine._session_time_over_24h("下午2:00", now=now)
