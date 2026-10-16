"""通讯录/新的朋友「展不开」的三个根因（2026-10-16，对照鹰牛 pyweixin 改）。

现场（本机日志 13:00:07-13:00:12）：
  contacts_list_lookup found=False 而 recyclers=1   ← 列表查找只认 Name 里有「通讯录」，窗口挪了就不认了
  click_refused node_text=联系人 fence=(100,100,300,400)  ← 围栏还是旧的冻结矩形，整片点击被白拒
  contacts_sync_failed reason=contact_list_not_found
所以三处都改了：① 列表查找放宽但绝不认会话列表；② 围栏改按"节点在不在通讯录列表里"判定 + 自动刷新；
③ 「新的朋友」改成键盘展开（抄鹰牛），鼠标点击只做兜底。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import native_wechat_engine as engine  # noqa: E402

ENGINE = ROOT / "backend" / "app" / "services" / "native_wechat_engine.py"


class _Node:
    def __init__(self, cls, text="", rect=None, children=(), parent=None):
        self.cls, self.text, self.rect = cls, text, rect
        self.children, self.parent = list(children), parent
        for child in self.children:
            child.parent = self

    def GetChildren(self):
        return list(self.children)

    def GetParentControl(self):
        return self.parent

    def SetFocus(self):
        return None


@pytest.fixture
def fake_uia(monkeypatch):
    monkeypatch.setattr(engine, "_uia_control_class", lambda n: getattr(n, "cls", ""), raising=False)
    monkeypatch.setattr(engine, "_uia_control_text", lambda n: getattr(n, "text", ""), raising=False)
    monkeypatch.setattr(engine, "_uia_rect", lambda n: getattr(n, "rect", None), raising=False)
    monkeypatch.setattr(engine, "_uia_rect_tuple", lambda n: getattr(n, "rect", None), raising=False)
    monkeypatch.setattr(engine, "_contact_sync_debug", lambda *a, **k: None, raising=False)
    return monkeypatch


def _list_node(text="", rect=(809.0, 69.0, 1009.0, 992.0), rows=(), extra=()):
    children = list(rows) + list(extra)
    return _Node("mmui::StickyHeaderRecyclerListView", text, rect, children)


def test_contact_list_accepts_group_named_recycler(fake_uia):
    """13:00 那次就是这种：类名对、但 Name 不叫「通讯录」→ 原来直接判 None，整条同步失败。"""
    row = _Node("mmui::ContactsCellItemView", "张三", (830.0, 100.0, 990.0, 140.0))
    node = _list_node("联系人", rows=[row])
    fake_uia.setattr(engine, "_uia_contact_recycler_lists", lambda root: [node], raising=False)
    fake_uia.setattr(engine, "_uia_visible_contact_cell_rows", lambda n: [{"node": row}], raising=False)
    assert engine._uia_strict_contact_list(object()) is node


def test_contact_list_never_accepts_a_session_list(fake_uia):
    """会话列表（含 mmui::ChatSessionCell）永远不算通讯录 —— 2026-10-14 那个事故的硬规矩。"""
    session = _Node("mmui::ChatSessionCell", "某个聊天", (830.0, 100.0, 990.0, 140.0))
    node = _list_node("微信", rows=[session])
    fake_uia.setattr(engine, "_uia_contact_recycler_lists", lambda root: [node], raising=False)
    fake_uia.setattr(engine, "_uia_visible_contact_cell_rows", lambda n: [], raising=False)
    assert engine._uia_strict_contact_list(object()) is None


def test_click_fence_allows_nodes_inside_the_contacts_list_even_if_frozen_rect_is_stale(fake_uia):
    """窗口挪了以后老的冻结矩形会把整片点击拒掉（refused=2 那两次）。"""
    row = _Node("mmui::ContactsCellItemView", "联系人", (830.0, 100.0, 990.0, 140.0))
    node = _list_node("联系人", rect=(809.0, 69.0, 1009.0, 992.0), rows=[row])
    fake_uia.setattr(engine, "_uia_visible_contact_cell_rows", lambda n: [{"node": row}], raising=False)

    engine._contacts_click_fence_open((100.0, 100.0, 300.0, 400.0), "acct")  # 旧矩形（窗口在左边时）
    try:
        assert engine._contacts_click_fence_node_ok(row) is True, "节点确实在通讯录列表里，不该拒"
        assert engine._contacts_click_fence_refresh_for_node(row) is True
        assert tuple(engine._CONTACTS_CLICK_FENCE["rect"]) == (809.0, 69.0, 1009.0, 992.0), "围栏应刷成当前列表矩形"
        assert int(engine._CONTACTS_CLICK_FENCE["refreshed"]) >= 1
    finally:
        engine._contacts_click_fence_close()


def test_click_fence_still_refuses_outside_nodes(fake_uia):
    stray = _Node("mmui::ChatSessionCell", "别人的会话", (1500.0, 300.0, 1700.0, 340.0))
    fake_uia.setattr(engine, "_uia_visible_contact_cell_rows", lambda n: [], raising=False)
    engine._contacts_click_fence_open((100.0, 100.0, 300.0, 400.0), "acct")
    try:
        assert engine._contacts_click_fence_node_ok(stray) is False
        assert engine._contacts_click_fence_refresh_for_node(stray) is False
    finally:
        engine._contacts_click_fence_close()


def test_keyboard_expand_sends_home_down_right_and_verifies(fake_uia):
    """抄鹰牛：HOME 回顶 → {DOWN}×序号 → {RIGHT}，然后校验出现 XTableCell。"""
    calls: list = []
    contact_list = _Node("mmui::StickyHeaderRecyclerListView", "联系人", (10.0, 10.0, 300.0, 900.0))
    fake_uia.setattr(engine, "_focus_local_wechat", lambda hwnd: calls.append("focus"), raising=False)
    fake_uia.setattr(engine, "_send_hotkey_fast", lambda key: calls.append(key), raising=False)

    class _Auto:
        @staticmethod
        def ControlFromHandle(_hwnd):
            return object()

    fake_uia.setitem(sys.modules, "uiautomation", _Auto)
    verified = {"n": 0}

    def _expanded(_root):
        verified["n"] += 1
        return object()

    fake_uia.setattr(engine, "_uia_friend_requests_expanded", _expanded, raising=False)
    assert engine._kb_expand_friend_requests(1234, contact_list, 1) is True
    assert calls[0] == "focus"
    assert calls[1:4] == ["home", "down", "right"], calls
    assert verified["n"] >= 1


def test_open_friend_requests_falls_back_to_click(fake_uia, monkeypatch):
    """键盘没成功时必须回退老的点击路径（不能因为新路径挂了就整条链失败）。"""
    contact_list = _Node("mmui::StickyHeaderRecyclerListView", "联系人", (10.0, 10.0, 300.0, 900.0))
    entry = _Node("mmui::ContactsCellGroupView", "新的朋友", (10.0, 10.0, 300.0, 40.0))
    contact_list.children = [entry]

    class _Auto:
        @staticmethod
        def ControlFromHandle(_hwnd):
            return "root"

    monkeypatch.setitem(sys.modules, "uiautomation", _Auto)
    monkeypatch.setattr(engine, "_uia_primary_contact_list", lambda root: contact_list, raising=False)
    monkeypatch.setattr(engine, "_kb_expand_friend_requests", lambda *a, **k: False, raising=False)
    used: list = []
    monkeypatch.setattr(
        engine, "_open_friend_requests_list_by_click", lambda hwnd, e: used.append(e) or "clicked", raising=False
    )
    monkeypatch.setattr(engine, "_contact_scan_mode", lambda: "keyboard", raising=False)
    assert engine._open_friend_requests_list(99) == "clicked"
    assert used == [entry]
