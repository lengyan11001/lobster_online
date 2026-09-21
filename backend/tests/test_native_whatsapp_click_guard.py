"""点击坐标守卫：输入法浮层不算"别的程序"，其它程序一律不点。"""
from __future__ import annotations

import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import native_whatsapp_engine as engine  # noqa: E402


def test_point_clickable_matrix(monkeypatch):
    monkeypatch.setattr(engine, "_point_window_hwnd", lambda x, y: 111)
    monkeypatch.setattr(engine, "_same_process_window", lambda a, b: a == b)
    monkeypatch.setattr(engine, "_is_ime_like_window", lambda hwnd: hwnd == 111)
    assert engine._point_clickable(1, 1, 111) is True, "WhatsApp 自己的窗口可以点"
    assert engine._point_clickable(1, 1, 555) is True, "输入法浮层可以点（点了最多无效）"
    monkeypatch.setattr(engine, "_is_ime_like_window", lambda hwnd: False)
    assert engine._point_clickable(1, 1, 555) is False, "其它程序绝对不点"
    monkeypatch.setattr(engine, "_point_window_hwnd", lambda x, y: 0)
    assert engine._point_clickable(1, 1, 555) is True, "坐标处没有窗口（桌面）视为可点"


def test_is_ime_like_window_detects_qq_pinyin(monkeypatch):
    fake_gui = types.SimpleNamespace(
        GetClassName=lambda hwnd: "QQPinyinImageCandWndTSF",
        GetWindowText=lambda hwnd: "",
    )
    fake_process = types.SimpleNamespace(GetWindowThreadProcessId=lambda hwnd: (0, 0))
    monkeypatch.setitem(sys.modules, "win32gui", fake_gui)
    monkeypatch.setitem(sys.modules, "win32process", fake_process)
    assert engine._is_ime_like_window(1234) is True

    fake_gui2 = types.SimpleNamespace(
        GetClassName=lambda hwnd: "Chrome_WidgetWin_1",
        GetWindowText=lambda hwnd: "微信",
    )
    monkeypatch.setitem(sys.modules, "win32gui", fake_gui2)
    assert engine._is_ime_like_window(1234) is False


def test_click_with_mouse_waits_then_fails_clearly(monkeypatch):
    """被其它程序挡住时：重试到超时后给明确提示，而不是静默乱点。"""
    node = types.SimpleNamespace(kind="ButtonControl", rect=(100, 100, 300, 140))
    monkeypatch.setattr(engine, "_rect", lambda n: (100, 100, 300, 140))
    monkeypatch.setattr(engine, "_node_hwnd", lambda n: 111)
    monkeypatch.setattr(engine, "_primary_window_hwnd", lambda: 111)
    monkeypatch.setattr(engine, "_activate_window", lambda hwnd, **kw: None)
    monkeypatch.setattr(engine, "_force_foreground", lambda hwnd, **kw: True)
    monkeypatch.setattr(engine, "_point_clickable", lambda x, y, target: False)
    monkeypatch.setitem(sys.modules, "win32gui", types.SimpleNamespace(SetWindowPos=lambda *a, **k: None))
    import pytest

    with pytest.raises(RuntimeError) as exc:
        engine._click_with_mouse(node)
    assert "已自动尝试 6 次" in str(exc.value)


def test_remote_control_window_detected(monkeypatch):
    monkeypatch.setattr(engine, "_window_haystack", lambda hwnd: "orayui awesun.exe 161 874 568 3")
    assert engine._is_remote_control_window(123) is True
    monkeypatch.setattr(engine, "_window_haystack", lambda hwnd: "chrome_widgetwin_1 chrome.exe")
    assert engine._is_remote_control_window(123) is False


def test_point_clickable_accepts_whatsapp_shell_pid(monkeypatch):
    """WhatsApp = WinUI3 壳(pid A) + WebView2(pid B)：点两者都算"自己的窗口"。"""
    monkeypatch.setattr(engine, "_point_window_hwnd", lambda x, y: 500)
    monkeypatch.setattr(engine, "_primary_window_hwnd", lambda: 700)
    monkeypatch.setattr(engine, "_same_process_window", lambda a, b: (int(a), int(b)) == (500, 700))
    monkeypatch.setattr(engine, "_is_ime_like_window", lambda hwnd: False)
    assert engine._point_clickable(1, 1, 999) is True


def test_click_reports_remote_control_blocker(monkeypatch):
    node = types.SimpleNamespace(kind="ButtonControl", rect=(100, 100, 300, 140))
    monkeypatch.setattr(engine, "_rect", lambda n: (100, 100, 300, 140))
    monkeypatch.setattr(engine, "_node_hwnd", lambda n: 111)
    monkeypatch.setattr(engine, "_primary_window_hwnd", lambda: 111)
    monkeypatch.setattr(engine, "_activate_window", lambda hwnd, **kw: None)
    monkeypatch.setattr(engine, "_force_foreground", lambda hwnd, **kw: True)
    monkeypatch.setattr(engine, "_point_clickable", lambda x, y, target: False)
    monkeypatch.setattr(engine, "_point_window_hwnd", lambda x, y: 777)
    monkeypatch.setattr(engine, "_is_remote_control_window", lambda hwnd: True)
    monkeypatch.setattr(engine, "_window_haystack", lambda hwnd: "orayui awesun.exe")
    monkeypatch.setitem(sys.modules, "win32gui", types.SimpleNamespace(SetWindowPos=lambda *a, **k: None))
    import pytest

    with pytest.raises(RuntimeError) as exc:
        engine._click_with_mouse(node)
    assert "远程控制软件" in str(exc.value)


def test_window_activation_is_fully_automatic():
    """窗口该由程序自己拉到最前，不许让用户手动移动（用户明确要求）。"""
    source = (ROOT / "backend" / "app" / "services" / "native_whatsapp_engine.py").read_text(encoding="utf-8")
    # 取窗口 / 点击 / 输入聚焦 三条路径都必须走 _force_foreground
    assert "    _force_foreground(hwnd)\n    return hwnd, window" in source, "取窗口时要抢前台"
    assert "_force_foreground(target, wait_seconds=0.8)" in source, "点击前每轮都要抢前台"
    assert source.count("_force_foreground(") >= 4
    # 报错文案不该再让用户"自己移到前面"
    assert "请把它移到前面后重试" not in source
    assert "已自动尝试 6 次" in source


def test_force_foreground_uses_attach_thread_input():
    source = (ROOT / "backend" / "app" / "services" / "native_whatsapp_engine.py").read_text(encoding="utf-8")
    start = source.index("def _force_foreground(")
    body = source[start:start + 2000]
    assert "AttachThreadInput" in body and "SetForegroundWindow" in body
