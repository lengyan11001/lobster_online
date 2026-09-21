"""WhatsApp 按键不再依赖 pywinauto（客户机缺包会直接报错）。"""
from __future__ import annotations

import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import native_whatsapp_engine as engine  # noqa: E402


def _fake_win32api(events):
    return types.SimpleNamespace(
        keybd_event=lambda vk, scan, flags, extra: events.append((int(vk), int(flags)))
    )


def test_send_keys_simple_ctrl_combo(monkeypatch):
    events = []
    monkeypatch.setitem(sys.modules, "win32api", _fake_win32api(events))
    engine.send_keys_simple("^a")
    presses = [vk for vk, flags in events if flags == 0]
    releases = [vk for vk, flags in events if flags != 0]
    assert 0x11 in presses and 0x41 in presses, "要按下 Ctrl+A"
    assert sorted(presses) == sorted(releases), "每个按下的键都要抬起"


def test_send_keys_simple_enter(monkeypatch):
    events = []
    monkeypatch.setitem(sys.modules, "win32api", _fake_win32api(events))
    engine.send_keys_simple("{ENTER}")
    assert [vk for vk, flags in events if flags == 0] == [0x0D]


def test_send_keys_simple_rejects_unknown(monkeypatch):
    monkeypatch.setitem(sys.modules, "win32api", _fake_win32api([]))
    with pytest.raises(RuntimeError):
        engine.send_keys_simple("{F13}")


def test_engine_no_longer_imports_pywinauto():
    source = (ROOT / "backend" / "app" / "services" / "native_whatsapp_engine.py").read_text(encoding="utf-8")
    code_lines = [line.strip() for line in source.splitlines() if not line.strip().startswith("#")]
    assert not any(line.startswith("from pywinauto") or line.startswith("import pywinauto") for line in code_lines), \
        "WhatsApp 引擎不该再 import pywinauto（客户机可能没装）"
    assert "send_keys_simple" in source
