"""残留系统弹窗清扫：一旦「找不到文件」之类的框卡住，后面所有微信节点都会点不动。"""
from __future__ import annotations

from backend.app.services import native_wechat_engine as engine


def _allow_sweep(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)


def test_stray_error_dialog_is_closed(monkeypatch):
    _allow_sweep(monkeypatch)
    steps = []
    forced = []
    monkeypatch.setattr(
        engine,
        "_top_level_windows",
        lambda: [
            {"hwnd": 501, "class": "#32770", "title": "找不到文件", "pid": 1},
            {"hwnd": 502, "class": "WeChatMainWndForPC", "title": "微信", "pid": 1},
        ],
    )
    monkeypatch.setattr(engine, "_window_looks_like_moments_picker", lambda window: False)
    monkeypatch.setattr(engine, "_dismiss_moments_error_box", lambda hwnd, steps=None: True)
    monkeypatch.setattr(engine, "_activate_window", lambda hwnd: True)
    monkeypatch.setattr(engine, "_send_hotkey", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine, "_wait_window_closed", lambda hwnd, timeout=0: True)
    monkeypatch.setattr(engine, "_close_window_force", lambda hwnd: forced.append(hwnd) or True)

    count = engine._clear_stray_wechat_dialogs(steps)

    assert count == 1
    assert [item["hwnd"] for item in steps[-1]["closed"]] == [501]
    assert steps[-1]["step"] == "clear_stray_wechat_dialogs"
    assert forced == []  # esc 关掉了就不用强关


def test_stuck_dialog_is_force_closed(monkeypatch):
    _allow_sweep(monkeypatch)
    steps = []
    forced = []
    monkeypatch.setattr(
        engine,
        "_top_level_windows",
        lambda: [{"hwnd": 601, "class": "#32770", "title": "找不到文件", "pid": 1}],
    )
    monkeypatch.setattr(engine, "_window_looks_like_moments_picker", lambda window: False)
    monkeypatch.setattr(engine, "_dismiss_moments_error_box", lambda hwnd, steps=None: False)
    monkeypatch.setattr(engine, "_activate_window", lambda hwnd: True)
    monkeypatch.setattr(engine, "_send_hotkey", lambda *args, **kwargs: None)
    closed_once = {"done": False}

    def fake_wait(hwnd, timeout=0):
        if not closed_once["done"]:
            closed_once["done"] = True
            return False
        return True

    monkeypatch.setattr(engine, "_wait_window_closed", fake_wait)
    monkeypatch.setattr(engine, "_close_window_force", lambda hwnd: forced.append(hwnd) or True)

    count = engine._clear_stray_wechat_dialogs(steps)

    assert count == 1
    assert forced == [601]


def test_wechat_main_window_is_never_touched(monkeypatch):
    _allow_sweep(monkeypatch)
    steps = []
    monkeypatch.setattr(
        engine,
        "_top_level_windows",
        lambda: [{"hwnd": 700, "class": "WeChatMainWndForPC", "title": "微信", "pid": 1}],
    )
    monkeypatch.setattr(engine, "_window_looks_like_moments_picker", lambda window: False)
    monkeypatch.setattr(engine, "_close_window_force", lambda hwnd: (_ for _ in ()).throw(AssertionError("不该动微信主窗")))

    assert engine._clear_stray_wechat_dialogs(steps) == 0
    assert steps == []


def test_sweep_is_throttled(monkeypatch):
    _allow_sweep(monkeypatch)
    calls = []
    monkeypatch.setattr(engine, "_clear_stray_wechat_dialogs", lambda *args, **kwargs: calls.append(1) or 0)
    engine._STRAY_SWEEP_STATE["at"] = 0.0
    engine._sweep_stray_dialogs_throttled(throttle=60.0)
    engine._sweep_stray_dialogs_throttled(throttle=60.0)
    assert len(calls) == 1
