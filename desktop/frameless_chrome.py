"""Frameless window chrome.

去掉原生标题栏（图标 + 文案），但保留：
- 任务栏按钮与应用图标（WS_EX_APPWINDOW + WinForms Icon）
- 边缘缩放（WS_THICKFRAME）
- 顶部 36px 标题栏拖动（WM_NCHITTEST → HTCAPTION，右侧按钮区除外）
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes

GWL_STYLE = -16
GWL_EXSTYLE = -20
GWL_WNDPROC = -4

WS_THICKFRAME = 0x00040000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000

WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

WM_GETMINMAXINFO = 0x0024

MONITOR_DEFAULTTONEAREST = 2


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class _MINMAXINFO(ctypes.Structure):
    _fields_ = [
        ("ptReserved", _POINT),
        ("ptMaxSize", _POINT),
        ("ptMaxPosition", _POINT),
        ("ptMinTrackSize", _POINT),
        ("ptMaxTrackSize", _POINT),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
    ]


WM_NCHITTEST = 0x0084
HTCLIENT = 1
HTCAPTION = 2
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_LRESULT = ctypes.c_ssize_t
_WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
_user32.CallWindowProcW.restype = ctypes.c_ssize_t
_user32.CallWindowProcW.argtypes = [ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.MonitorFromWindow.restype = ctypes.c_void_p
_user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.GetMonitorInfoW.restype = wintypes.BOOL
_user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MONITORINFO)]

WM_NCCALCSIZE = 0x0083

_state: dict[int, tuple] = {}

VK_LBUTTON = 0x01
GA_ROOT = 2
GW_OWNER = 4

_user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.WindowFromPoint.restype = wintypes.HWND
_user32.WindowFromPoint.argtypes = [wintypes.POINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
_user32.SetWindowPos.restype = wintypes.BOOL
_user32.IsZoomed.argtypes = [wintypes.HWND]
_user32.IsZoomed.restype = wintypes.BOOL

_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.restype = ctypes.c_int
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.IsIconic.restype = wintypes.BOOL
try:
    _user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    _user32.GetDpiForWindow.restype = wintypes.UINT
except Exception:  # pragma: no cover - very old Windows
    pass


def _win_pid(hwnd: int) -> int:
    if not hwnd:
        return 0
    try:
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def describe_window(hwnd: int) -> str:
    """One-line window identity, used by every chrome/resize log line."""
    if not hwnd:
        return "hwnd=0"
    try:
        cls = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, cls, 256)
        n = _user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(n + 1)
        if n:
            _user32.GetWindowTextW(hwnd, title, n + 1)
        rc = wintypes.RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(rc))
        dpi = 0
        try:
            dpi = int(_user32.GetDpiForWindow(hwnd))
        except Exception:
            pass
        return (
            f"hwnd={hwnd} class={cls.value!r} title={title.value!r} "
            f"rect=({rc.left},{rc.top},{rc.right - rc.left}x{rc.bottom - rc.top}) "
            f"dpi={dpi} zoomed={int(bool(_user32.IsZoomed(hwnd)))} "
            f"iconic={int(bool(_user32.IsIconic(hwnd)))} pid={_win_pid(hwnd)}"
        )
    except Exception as exc:
        return f"hwnd={hwnd} describe-failed={exc}"


def _enum_best_window(title_hint: str, pid: int, strict_owner: bool) -> int:
    target_pid = int(pid or os.getpid())
    hint = (title_hint or "").strip()
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        try:
            if not _user32.IsWindowVisible(hwnd):
                return True
            wpid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            if target_pid and wpid.value != target_pid:
                return True
            if strict_owner and _user32.GetWindow(hwnd, GW_OWNER):
                return True
            if hint:
                n = _user32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    _user32.GetWindowTextW(hwnd, buf, n + 1)
                    if hint not in buf.value:
                        return True
            found.append(int(hwnd))
        except Exception:
            pass
        return True

    _user32.EnumWindows(_cb, 0)
    best, best_area = 0, 0
    for hwnd in found:
        rect = wintypes.RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            continue
        area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        if area > best_area:
            best, best_area = hwnd, area
    return best


def find_app_window(title_hint: str = "", pid: int = 0) -> int:
    """兜底找窗口：先严格（本进程 + 无 owner），再放宽（本进程任意可见窗口）。

    有些机器 pywebview 的 window.native.Handle 一直是 0，无边框/边缘缩放就整块
    失效；这里直接用 Win32 枚举兜底。
    """
    for strict in (True, False):
        try:
            hwnd = _enum_best_window(title_hint, pid, strict)
        except Exception:
            hwnd = 0
        if hwnd:
            return hwnd
    return 0


def _resize_code(cx: int, cy: int, cw: int, ch: int, edge: int) -> int:
    """Return the HT* code for a point in client coordinates, or 0."""
    edge = max(1, int(edge))
    left = cx < edge
    right = cx >= cw - edge
    top = cy < edge
    bottom = cy >= ch - edge
    if top and left:
        return HTTOPLEFT
    if top and right:
        return HTTOPRIGHT
    if bottom and left:
        return HTBOTTOMLEFT
    if bottom and right:
        return HTBOTTOMRIGHT
    if left:
        return HTLEFT
    if right:
        return HTRIGHT
    if top:
        return HTTOP
    if bottom:
        return HTBOTTOM
    return 0


def _resize_rect_for_drag(
    rect: wintypes.RECT,
    dx: int,
    dy: int,
    code: int,
    min_width: int,
    min_height: int,
) -> tuple[int, int, int, int] | None:
    """Return (x, y, width, height) for a manual edge drag."""
    left_edge = code in (HTLEFT, HTTOPLEFT, HTBOTTOMLEFT)
    right_edge = code in (HTRIGHT, HTTOPRIGHT, HTBOTTOMRIGHT)
    top_edge = code in (HTTOP, HTTOPLEFT, HTTOPRIGHT)
    bottom_edge = code in (HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT)
    if not (left_edge or right_edge or top_edge or bottom_edge):
        return None

    left = rect.left + (dx if left_edge else 0)
    right = rect.right + (dx if right_edge else 0)
    top = rect.top + (dy if top_edge else 0)
    bottom = rect.bottom + (dy if bottom_edge else 0)

    width = right - left
    height = bottom - top
    min_width = max(320, int(min_width))
    min_height = max(240, int(min_height))
    if width < min_width:
        if left_edge:
            left = right - min_width
        else:
            right = left + min_width
    if height < min_height:
        if top_edge:
            top = bottom - min_height
        else:
            bottom = top + min_height
    return left, top, right - left, bottom - top


def _drag_resize(hwnd: int, point: wintypes.POINT, code: int, min_width: int, min_height: int) -> bool:
    if _user32.IsZoomed(hwnd):
        return False
    rect = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    start_x, start_y = point.x, point.y
    while _user32.IsWindow(hwnd) and (_user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000):
        current = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(current))
        dx, dy = current.x - start_x, current.y - start_y
        target = _resize_rect_for_drag(rect, dx, dy, code, min_width, min_height)
        if target:
            x, y, width, height = target
            _user32.SetWindowPos(hwnd, 0, x, y, width, height, SWP_NOZORDER | SWP_NOACTIVATE)
        time.sleep(0.012)
    return True


def _resize_watch_loop(
    hwnd: int,
    edge: int,
    stop: threading.Event,
    min_width: int,
    min_height: int,
    log=None,
) -> None:
    """WebView2 consumes WM_NCHITTEST, so resize the top-level window directly
    from the global mouse state instead of relying on the native resize loop."""
    last = {}

    def note(kind: str, text: str, min_interval: float = 2.0) -> None:
        if log is None:
            return
        now = time.time()
        if now - last.get(kind, 0.0) < min_interval:
            return
        last[kind] = now
        try:
            log(f"resize: {text}")
        except Exception:
            pass

    my_pid = _win_pid(hwnd)
    note("start", f"watch start {describe_window(hwnd)} edge={edge}", 0.0)
    was_down = False
    while not stop.is_set() and _user32.IsWindow(hwnd):
        down = bool(_user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
        if down and not was_down:
            try:
                point = wintypes.POINT()
                _user32.GetCursorPos(ctypes.byref(point))
                win = wintypes.RECT()
                _user32.GetWindowRect(hwnd, ctypes.byref(win))
                inside = win.left <= point.x < win.right and win.top <= point.y < win.bottom
                if not inside:
                    was_down = down
                    time.sleep(0.012)
                    continue
                if _user32.IsIconic(hwnd):
                    note("iconic", f"skip minimized {describe_window(hwnd)}", 5.0)
                    was_down = down
                    time.sleep(0.012)
                    continue
                cw, ch = win.right - win.left, win.bottom - win.top
                cx, cy = point.x - win.left, point.y - win.top
                # 前台判断放宽：本窗口、它的 owner/popup、或同进程任意窗口在前台都算。
                # 老逻辑只认 GetForegroundWindow()==hwnd，只要 owner/popup 抢了前台，
                # 边缘拖动就永远不命中。
                fg = _user32.GetForegroundWindow()
                fg_ok = bool(fg) and (
                    fg == hwnd
                    or _user32.GetAncestor(fg, GA_ROOT) == hwnd
                    or (_win_pid(fg) == my_pid and my_pid != 0)
                )
                code = _resize_code(cx, cy, cw, ch, edge)
                near = cx < edge * 3 or cx >= cw - edge * 3 or cy < edge * 3 or cy >= ch - edge * 3
                if not fg_ok:
                    if near:
                        note(
                            "nofore",
                            f"skip not-foreground fg={fg} fg_pid={_win_pid(fg)} "
                            f"point=({point.x},{point.y}) {describe_window(hwnd)}",
                            3.0,
                        )
                    was_down = down
                    time.sleep(0.012)
                    continue
                if code:
                    note(
                        "begin",
                        f"begin code={code} at=({cx},{cy}) size={cw}x{ch} edge={edge}",
                        0.0,
                    )
                    _drag_resize(hwnd, point, code, min_width, min_height)
                    note("end", f"end code={code} {describe_window(hwnd)}", 0.0)
                    was_down = True
                    continue
                if near:
                    note(
                        "miss",
                        f"near-edge but code=0 at=({cx},{cy}) size={cw}x{ch} edge={edge} "
                        f"fg={fg} fg_pid={_win_pid(fg)}",
                        3.0,
                    )
            except Exception as exc:
                note("error", f"loop error: {type(exc).__name__}: {exc}", 5.0)
        was_down = down
        time.sleep(0.012)


def apply(hwnd: int, *, drag_height: int = 26, border: int = 8, min_width: int = 900, min_height: int = 640, log=None) -> bool:
    """给已创建的窗口加无边框样式 + 拖动/缩放支持。失败返回 False。"""
    if not hwnd:
        return False
    if hwnd in _state:
        if log is not None:
            try:
                log(f"frameless chrome: already applied {describe_window(hwnd)}")
            except Exception:
                pass
        return True
    if log is not None:
        try:
            log(f"frameless chrome: applying drag={drag_height} border={border} {describe_window(hwnd)}")
        except Exception:
            pass
    style = _user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
    exstyle = _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    _user32.SetWindowLongPtrW(
        hwnd,
        GWL_STYLE,
        style | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX,
    )
    # 任务栏必须保留：APPWINDOW 强制显示任务栏按钮，TOOLWINDOW 会让它消失。
    _user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, (exstyle | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW)
    _user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)

    old_proc = _user32.GetWindowLongPtrW(hwnd, GWL_WNDPROC)
    drag = max(1, int(drag_height))
    edge = max(1, int(border))

    def _proc(h, msg, wparam, lparam):
        try:
            # WS_THICKFRAME keeps native resize hit-testing, but it also adds a
            # white non-client frame. Make the client area cover the whole
            # window; WM_NCHITTEST below still returns the resize edges.
            if msg == WM_GETMINMAXINFO:
                try:
                    info = ctypes.cast(lparam, ctypes.POINTER(_MINMAXINFO)).contents
                    monitor = _user32.MonitorFromWindow(h, MONITOR_DEFAULTTONEAREST)
                    if monitor:
                        mi = _MONITORINFO()
                        mi.cbSize = ctypes.sizeof(_MONITORINFO)
                        if _user32.GetMonitorInfoW(monitor, ctypes.byref(mi)):
                            work = mi.rcWork
                            mon = mi.rcMonitor
                            info.ptMaxPosition.x = work.left - mon.left
                            info.ptMaxPosition.y = work.top - mon.top
                            info.ptMaxSize.x = work.right - work.left
                            info.ptMaxSize.y = work.bottom - work.top
                            info.ptMaxTrackSize.x = max(info.ptMaxTrackSize.x, info.ptMaxSize.x)
                            info.ptMaxTrackSize.y = max(info.ptMaxTrackSize.y, info.ptMaxSize.y)
                            return 0
                except Exception:
                    pass
            if msg == WM_NCCALCSIZE and wparam:
                return 0
            if msg == WM_NCHITTEST:
                sx = ctypes.c_short(lparam & 0xFFFF).value
                sy = ctypes.c_short((lparam >> 16) & 0xFFFF).value
                pt = wintypes.POINT(sx, sy)
                _user32.ScreenToClient(h, ctypes.byref(pt))
                rc = wintypes.RECT()
                _user32.GetClientRect(h, ctypes.byref(rc))
                cx, cy, cw, ch = pt.x, pt.y, rc.right, rc.bottom
                if cx < edge and cy < edge:
                    return HTTOPLEFT
                if cx >= cw - edge and cy < edge:
                    return HTTOPRIGHT
                if cx < edge and cy >= ch - edge:
                    return HTBOTTOMLEFT
                if cx >= cw - edge and cy >= ch - edge:
                    return HTBOTTOMRIGHT
                if cx < edge:
                    return HTLEFT
                if cx >= cw - edge:
                    return HTRIGHT
                if cy < edge:
                    return HTTOP
                if cy >= ch - edge:
                    return HTBOTTOM
                if edge <= cy < max(drag, edge + 1) and cx < cw - 150:
                    return HTCAPTION
                return HTCLIENT
        except Exception:
            pass
        return _user32.CallWindowProcW(old_proc, h, msg, wparam, lparam)

    cb = _WNDPROC(_proc)
    stop = threading.Event()
    watcher = threading.Thread(
        target=_resize_watch_loop,
        args=(hwnd, edge, stop, min_width, min_height, log),
        name=f"lobster-resize-{hwnd}",
        daemon=True,
    )
    watcher.start()
    _state[hwnd] = (cb, old_proc, stop, watcher)
    _user32.SetWindowLongPtrW(hwnd, GWL_WNDPROC, ctypes.cast(cb, ctypes.c_void_p).value)
    # The first SWP_FRAMECHANGED above happened before our WndProc was
    # installed, so the WM_NCCALCSIZE handler had not removed the frame yet.
    _user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
    return True
