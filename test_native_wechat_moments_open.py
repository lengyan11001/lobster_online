# -*- coding: utf-8 -*-
"""Moments left-nav open retries when the entry is past the old UIA cap."""
from __future__ import annotations

import pytest

from backend.app.services import native_wechat_engine as engine


class _Node:
    def __init__(self, name="", class_name="", children=None):
        self.Name = name
        self.ClassName = class_name
        self._children = list(children or [])

    def GetChildren(self):
        return list(self._children)


def _silence_open(monkeypatch, scan):
    monkeypatch.setattr(engine, "_focus_local_wechat", lambda _hwnd: None)
    monkeypatch.setattr(engine, "_find_visible_local_moments_hwnd", lambda: 0)
    monkeypatch.setattr(engine, "_uia_foreground_or_main_root", lambda hwnd: {"which": "fg", "hwnd": hwnd})
    monkeypatch.setattr(engine, "_uia_main_root", lambda hwnd: {"which": "main", "hwnd": hwnd})
    monkeypatch.setattr(
        engine,
        "_root_native_handle",
        lambda root: 11 if isinstance(root, dict) and root.get("which") == "fg" else 22,
    )
    monkeypatch.setattr(engine, "_scan_moments_surface", scan)
    monkeypatch.setattr(engine, "_refresh_local_moments", lambda _root, steps: steps.append({"step": "refresh_moments", "ok": True}))
    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)


def test_scan_finds_moments_nav_beyond_old_600_node_cap():
    fillers = [_Node("row-%s" % index) for index in range(700)]
    target = _Node("朋友圈", "mmui::XTabBarItem")
    root = _Node(
        "微信",
        "mmui::MainWindow",
        [_Node("聊天、朋友圈、微信运动等"), *fillers, target],
    )

    surface = engine._scan_moments_surface(root)

    assert surface["kind"] == "nav"
    assert surface["nav"] is target
    assert surface["seen"] > 600


def test_scan_ignores_permission_sentence_and_contact_album():
    sentence = engine._scan_moments_surface(_Node("", "", [_Node("聊天、朋友圈、微信运动等")]))
    album = engine._scan_moments_surface(
        _Node("", "", [_Node("朋友圈"), _Node("", "mmui::AlbumContentCell")])
    )
    timeline = engine._scan_moments_surface(
        _Node("", "", [_Node("朋友圈"), _Node("", "mmui::TimeLineListView")])
    )

    assert sentence["kind"] == "none"
    assert album == {"kind": "contact_album", "nav": None, "seen": album["seen"]}
    assert album["nav"] is None
    assert timeline["kind"] == "timeline"


def test_open_moments_checks_main_window_before_giving_up(monkeypatch):
    nav = _Node("朋友圈")
    seen = {"scan": 0}
    clicks = []
    recovers = []

    def scan(_root, **_kwargs):
        seen["scan"] += 1
        if seen["scan"] == 1:
            return {"kind": "none", "nav": None, "seen": 3200}
        return {"kind": "nav", "nav": nav, "seen": 900}

    _silence_open(monkeypatch, scan)
    monkeypatch.setattr(engine, "_uia_click", lambda node: clicks.append(node))
    monkeypatch.setattr(
        engine,
        "_recover_local_moments_entry",
        lambda _hwnd, _steps, attempt: recovers.append(attempt),
    )
    steps = []

    engine._open_local_moments(100, steps)

    assert clicks == [nav]
    assert recovers == []
    assert seen["scan"] == 2
    assert steps[0]["step"] == "open_moments"
    assert steps[0]["attempt"] == 1
    assert steps[0]["entry"] == "朋友圈"
    assert not any(item["step"] == "open_moments_retry" for item in steps)


def test_open_moments_recovers_when_both_windows_miss_then_clicks_once(monkeypatch):
    nav = _Node("朋友圈")
    seen = {"scan": 0}
    clicks = []
    recovers = []

    def scan(_root, **_kwargs):
        seen["scan"] += 1
        # Attempt 1 scans foreground and the main window. Both miss.
        if seen["scan"] < 3:
            return {"kind": "none", "nav": None, "seen": 3200}
        return {"kind": "nav", "nav": nav, "seen": 1100}

    _silence_open(monkeypatch, scan)
    monkeypatch.setattr(engine, "_uia_click", lambda node: clicks.append(node))
    monkeypatch.setattr(
        engine,
        "_recover_local_moments_entry",
        lambda _hwnd, _steps, attempt: recovers.append(attempt),
    )
    steps = []

    engine._open_local_moments(100, steps)

    assert clicks == [nav]
    assert recovers == [1]
    retry = [item for item in steps if item["step"] == "open_moments_retry"]
    assert [item["attempt"] for item in retry] == [1]
    assert retry[0]["seen"] == 3200
    opened = [item for item in steps if item["step"] == "open_moments"]
    assert opened[0]["attempt"] == 2
    assert opened[0]["entry"] == "朋友圈"


def test_open_moments_raises_same_error_after_three_misses(monkeypatch):
    recovers = []

    def scan(_root, **_kwargs):
        return {"kind": "none", "nav": None, "seen": 3200}

    _silence_open(monkeypatch, scan)
    monkeypatch.setattr(engine, "_uia_click", lambda _node: (_ for _ in ()).throw(AssertionError("clicked")))
    monkeypatch.setattr(
        engine,
        "_recover_local_moments_entry",
        lambda _hwnd, _steps, attempt: recovers.append(attempt),
    )
    steps = []

    with pytest.raises(RuntimeError, match="未找到朋友圈入口"):
        engine._open_local_moments(100, steps)

    assert recovers == [1, 2]
    assert [item["attempt"] for item in steps if item["step"] == "open_moments_retry"] == [1, 2, 3]


def test_open_moments_uses_existing_global_window_without_clicking_nav(monkeypatch):
    clicks = []
    focused = []

    def scan(root, **_kwargs):
        assert root == "moments-root"
        return {"kind": "timeline", "nav": None, "seen": 30}

    _silence_open(monkeypatch, scan)
    monkeypatch.setattr(engine, "_find_visible_local_moments_hwnd", lambda: 55)
    monkeypatch.setattr(engine, "_focus_local_wechat", lambda hwnd: focused.append(hwnd))
    monkeypatch.setattr(
        engine,
        "_uia_main_root",
        lambda hwnd: "moments-root" if int(hwnd) == 55 else {"which": "main", "hwnd": hwnd},
    )
    monkeypatch.setattr(engine, "_uia_click", lambda node: clicks.append(node))
    steps = []

    engine._open_local_moments(100, steps)

    assert clicks == []
    assert focused[0] == 100
    assert 55 in focused
    assert steps[0]["entry"] == "existing_window"
    assert steps[0]["ok"] is True


def test_find_nav_more_button_prefers_left_rail():
    left_more = _Node("…", "mmui::XButton")
    right_more = _Node("…", "mmui::XButton")
    left_more.BoundingRectangle = type("R", (), {"left": 40, "top": 300, "right": 60, "bottom": 320})
    right_more.BoundingRectangle = type("R", (), {"left": 900, "top": 300, "right": 920, "bottom": 320})
    root = _Node("微信", "mmui::MainWindow", [right_more, left_more])

    found = engine._find_nav_more_button(root, (0, 0, 1200, 800))

    assert found is left_more


def test_collapsed_nav_expands_more_then_clicks_moments(monkeypatch):
    moments_entry = _Node("朋友圈", "mmui::XButton")
    more = _Node("…", "mmui::XButton")
    main_root = _Node("微信", "mmui::MainWindow", [more])
    popup_root = _Node("", "mmui::Popup", [moments_entry])
    clicks = []

    monkeypatch.setattr(engine, "_uia_main_root", lambda hwnd: main_root)
    monkeypatch.setattr(engine, "_wechat_popup_roots", lambda hwnd: [popup_root])
    monkeypatch.setattr(engine, "_uia_click", lambda node, require_bounds=True: clicks.append(node))
    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)

    steps = []
    ok = engine._expand_collapsed_nav(123, steps)

    assert ok is True
    assert clicks == [more, moments_entry]
    assert [s["step"] for s in steps] == ["expand_nav", "open_moments_from_more"]


def test_pwa_node_adapter_exposes_uiautomation_shape():
    class _Info:
        class_name = "mmui::XTabBarItem"
        control_type = "Button"
        automation_id = "aid-1"
        handle = 0

    class _Rect:
        left, top, right, bottom = 1.0, 2.0, 3.0, 4.0

    calls = []

    class _Wrapper:
        element_info = _Info()

        def window_text(self):
            return "朋友圈"

        def rectangle(self):
            return _Rect()

        def children(self):
            return []

        def click_input(self):
            calls.append("click")

    node = engine._PwaNode(_Wrapper())

    assert node.Name == "朋友圈"
    assert node.ClassName == "mmui::XTabBarItem"
    assert node.ControlTypeName == "Button"
    assert node.AutomationId == "aid-1"
    rect = node.BoundingRectangle
    assert (rect.left, rect.top, rect.right, rect.bottom) == (1.0, 2.0, 3.0, 4.0)
    assert node.GetChildren() == []
    node.Click()
    assert calls == ["click"]


def test_qt_tool_window_is_not_treated_as_main_window():
    meta = {"name": "Weixin.exe", "exe": r"C:\Weixin\Weixin.exe", "version": "4.1.7.59"}
    # 用户 142 的机器上就是名字也叫 Weixin 的工具窗顶替了主窗口
    assert engine._looks_like_wechat_window("Weixin", "Qt51514QWindowToolSaveBits", meta) is False
    assert engine._looks_like_wechat_window("Weixin", "QWindowToolTip", meta) is False
    assert engine._looks_like_wechat_window("微信", "Qt51514QWindowIcon", meta) is True
    assert engine._is_wechat_main_window_class("Qt51514QWindowIcon") is True
    assert engine._is_wechat_main_window_class("Qt51514QWindowToolSaveBits") is False


def test_wechat_window_rank_prefers_main_over_tool():
    main = engine._wechat_window_rank(
        {"class_name": "Qt51514QWindowIcon", "area": 500000, "full_driver_ready": True}
    )
    tool = engine._wechat_window_rank(
        {"class_name": "Qt51514QWindowToolSaveBits", "area": 270000, "full_driver_ready": False}
    )
    assert main > tool
