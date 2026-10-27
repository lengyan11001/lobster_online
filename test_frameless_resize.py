from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_frameless_resize_handles_are_large_and_watcher_owns_native_resize():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    chrome = (ROOT / "desktop" / "frameless_chrome.py").read_text(encoding="utf-8")

    assert "height:8px;cursor:ns-resize" in html
    assert "width:8px;cursor:ew-resize" in html
    assert "width:16px;height:16px;cursor:nwse-resize" in html
    assert "width:16px;height:16px;cursor:nesw-resize" in html
    assert "window_start_resize(item[0])" not in html
    assert "def _resize_watch_loop" in chrome
    assert "GetAsyncKeyState(VK_LBUTTON)" in chrome
    assert "GetAncestor(under, GA_ROOT)" in chrome
    assert "def _drag_resize" in chrome
    assert "_resize_rect_for_drag" in chrome
    assert "SetWindowPos(hwnd, 0, x, y, width, height" in chrome


def test_resize_code_maps_all_edges_and_corners():
    from desktop.frameless_chrome import (
        HTBOTTOM,
        HTBOTTOMLEFT,
        HTBOTTOMRIGHT,
        HTLEFT,
        HTRIGHT,
        HTTOP,
        HTTOPLEFT,
        HTTOPRIGHT,
        _resize_code,
        _resize_rect_for_drag,
    )

    w, h, edge = 1000, 800, 8
    assert _resize_code(2, 2, w, h, edge) == HTTOPLEFT
    assert _resize_code(w - 2, 2, w, h, edge) == HTTOPRIGHT
    assert _resize_code(2, h - 2, w, h, edge) == HTBOTTOMLEFT
    assert _resize_code(w - 2, h - 2, w, h, edge) == HTBOTTOMRIGHT
    assert _resize_code(2, 400, w, h, edge) == HTLEFT
    assert _resize_code(w - 2, 400, w, h, edge) == HTRIGHT
    assert _resize_code(500, 2, w, h, edge) == HTTOP
    assert _resize_code(500, h - 2, w, h, edge) == HTBOTTOM
    assert _resize_code(500, 400, w, h, edge) == 0

    class R:
        left, top, right, bottom = 100, 100, 1100, 900

    assert _resize_rect_for_drag(R, -40, 0, HTLEFT, 900, 640) == (60, 100, 1040, 800)
    assert _resize_rect_for_drag(R, 40, 0, HTRIGHT, 900, 640) == (100, 100, 1040, 800)
    assert _resize_rect_for_drag(R, 0, -40, HTTOP, 900, 640) == (100, 60, 1000, 840)
    assert _resize_rect_for_drag(R, 0, 40, HTBOTTOM, 900, 640) == (100, 100, 1000, 840)
    assert _resize_rect_for_drag(R, 200, 0, HTLEFT, 900, 640)[0] == 200

def test_website_ota_ships_frameless_chrome_module():
    pack = (ROOT / "scripts" / "pack_client_code_ota.py").read_text(encoding="utf-8")
    assert '"desktop/frameless_chrome.py"' in pack

def test_frameless_chrome_has_shown_event_fallback_and_is_idempotent():
    launcher = (ROOT / "desktop" / "launcher.py").read_text(encoding="utf-8")
    chrome = (ROOT / "desktop" / "frameless_chrome.py").read_text(encoding="utf-8")
    assert "def _apply_frameless_chrome_when_ready" in launcher
    assert "applying via fallback" in launcher
    assert "lobster-frameless-fallback" in launcher
    assert "if hwnd in _state:" in chrome
    assert "mode=manual-drag" in launcher
