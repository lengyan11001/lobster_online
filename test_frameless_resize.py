from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_frameless_resize_handles_are_large_and_trigger_native_resize_with_cursor_position():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    launcher = (ROOT / "desktop" / "launcher.py").read_text(encoding="utf-8")

    assert "height:8px;cursor:ns-resize" in html
    assert "width:8px;cursor:ew-resize" in html
    assert "width:16px;height:16px;cursor:nwse-resize" in html
    assert "width:16px;height:16px;cursor:nesw-resize" in html
    assert "window_start_resize(item[0])" in html
    assert "user32.GetCursorPos(ctypes.byref(point))" in launcher
    assert "lparam = ((point.y & 0xFFFF) << 16) | (point.x & 0xFFFF)" in launcher
    assert "user32.SetForegroundWindow(hwnd)" in launcher
    assert "user32.SendMessageW(hwnd, 0x00A1, code, lparam)" in launcher
