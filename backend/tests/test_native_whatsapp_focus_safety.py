"""WhatsApp 自动化不再误操作其它程序（键盘/剪贴板/鼠标都要有守卫）。"""
from __future__ import annotations

import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import native_whatsapp_engine as engine  # noqa: E402


def test_set_edit_text_aborts_when_whatsapp_not_foreground(monkeypatch):
    """拿不到 WhatsApp 前台焦点时不能碰剪贴板，否则 ^v 会打到微信之类的窗口上。"""
    writes = []
    monkeypatch.setattr(engine, "_rect", lambda node: (0, 0, 10, 10))
    monkeypatch.setattr(engine, "_ensure_foreground", lambda hwnd, **kwargs: False)
    monkeypatch.setattr(engine, "set_clipboard_text", lambda text: writes.append(text) or "ctypes")
    with pytest.raises(RuntimeError) as exc:
        engine._set_edit_text(types.SimpleNamespace(kind="EditControl"), "hello", hwnd=123)
    assert "前台" in str(exc.value)
    assert writes == [], "没有前台焦点时不该写剪贴板"


def test_set_edit_text_restores_user_clipboard(monkeypatch):
    writes = []
    monkeypatch.setattr(engine, "_rect", lambda node: (0, 0, 10, 10))
    monkeypatch.setattr(engine, "_ensure_foreground", lambda hwnd, **kwargs: True)
    monkeypatch.setattr(engine, "_focus_by_mouse", lambda node, hwnd=0: None)
    monkeypatch.setattr(engine, "_read_clipboard_text", lambda: "USER-CLIP")
    monkeypatch.setattr(engine, "set_clipboard_text", lambda text: writes.append(text) or "pyperclip")

    import pywinauto.keyboard as keyboard

    monkeypatch.setattr(keyboard, "send_keys", lambda *args, **kwargs: None)

    engine._set_edit_text(types.SimpleNamespace(kind="EditControl"), "hello", hwnd=123)
    assert writes == ["hello", "USER-CLIP"], "写完之后必须把用户剪贴板还原"


def test_click_prefers_uia_pattern(monkeypatch):
    called = []

    class Pattern:
        def Invoke(self):
            called.append("invoke")

    node = types.SimpleNamespace(kind="ButtonControl", GetInvokePattern=lambda: Pattern())
    monkeypatch.setattr(engine, "_rect", lambda item: (0, 0, 10, 10))
    monkeypatch.setattr(engine, "_node_type", lambda item: getattr(item, "kind", ""))
    engine._click(node)
    assert called == ["invoke"], "能用 UIA 原生调用时不该动鼠标"


def test_click_aborts_when_point_is_not_whatsapp(monkeypatch):
    monkeypatch.setattr(engine, "_rect", lambda node: (0, 0, 10, 10))
    monkeypatch.setattr(engine, "_node_hwnd", lambda node: 0)
    monkeypatch.setattr(engine, "_primary_window_hwnd", lambda: 999)
    monkeypatch.setattr(engine, "_point_window_hwnd", lambda x, y: 111)
    monkeypatch.setattr(engine, "_same_process_window", lambda a, b: False)
    monkeypatch.setattr(engine, "_ensure_foreground", lambda hwnd, **kwargs: False)
    with pytest.raises(RuntimeError) as exc:
        engine._click(object())
    assert "不在 WhatsApp 窗口上" in str(exc.value)


def test_focus_by_mouse_aborts_when_point_moved_away(monkeypatch):
    monkeypatch.setattr(engine, "_rect", lambda node: (0, 0, 10, 10))
    monkeypatch.setattr(engine, "_primary_window_hwnd", lambda: 999)
    monkeypatch.setattr(engine, "_activate_window", lambda hwnd: None)
    monkeypatch.setattr(engine, "_point_window_hwnd", lambda x, y: 111)
    monkeypatch.setattr(engine, "_same_process_window", lambda a, b: False)
    with pytest.raises(RuntimeError) as exc:
        engine._focus_by_mouse(types.SimpleNamespace(kind="EditControl"))
    assert "不在 WhatsApp 窗口上" in str(exc.value)


def test_send_message_guards_foreground(monkeypatch):
    """发送消息也会按 Enter，必须先确认前台是 WhatsApp。"""
    source = pathlib.Path(str(ROOT / "backend" / "app" / "services" / "native_whatsapp_engine.py")).read_text(encoding="utf-8")
    start = source.index("def _send_current_message(")
    body = source[start:start + 1400]
    assert "_ensure_foreground" in body
