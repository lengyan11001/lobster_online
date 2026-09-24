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
