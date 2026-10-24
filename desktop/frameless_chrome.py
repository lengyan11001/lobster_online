"""Frameless window chrome.

去掉原生标题栏（图标 + 文案），但保留：
- 任务栏按钮与应用图标（WS_EX_APPWINDOW + WinForms Icon）
- 边缘缩放（WS_THICKFRAME）
- 顶部 36px 标题栏拖动（WM_NCHITTEST → HTCAPTION，右侧按钮区除外）
"""
from __future__ import annotations

import ctypes
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


def apply(hwnd: int, *, drag_height: int = 14, border: int = 6) -> bool:
    """给已创建的窗口加无边框样式 + 拖动/缩放支持。失败返回 False。"""
    if not hwnd:
        return False
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
    _state[hwnd] = (cb, old_proc)
    _user32.SetWindowLongPtrW(hwnd, GWL_WNDPROC, ctypes.cast(cb, ctypes.c_void_p).value)
    # The first SWP_FRAMECHANGED above happened before our WndProc was
    # installed, so the WM_NCCALCSIZE handler had not removed the frame yet.
    _user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
    return True
