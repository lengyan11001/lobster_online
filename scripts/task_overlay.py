# -*- coding: utf-8 -*-
"""全局悬浮「暂停任务」键（原生 Win32 自绘，2026-10-17 重写）。

为什么重写：原 pywebview 版本在部分机器上 WebView2 初始化会卡住 → 窗口全黑；
而且 WinForms 的 Show() 会在任务栏留下一个没有图标的空白条目，关掉窗口也不消失。
现在直接用 WS_POPUP + WS_EX_TOOLWINDOW 自绘（创建即不进任务栏），不依赖 WebView2：
  · 大字按钮「暂停任务 / 继续」，右侧 × 停止并关闭
  · 一行状态：谁在跑 + 跑了多久（读 data/task_control/status.json）
  · Alt+R 暂停/继续 · Alt+Q 停止并关闭（与按钮同义）
  · 空闲超过 --idle-seconds 自动退出
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import win32api
import win32con
import win32gui

gdi32 = ctypes.windll.gdi32
user32 = ctypes.windll.user32

WIDTH = 196
HEIGHT = 92
COL_BG = (18, 18, 30)
COL_BORDER = (43, 58, 122)
COL_BTN = (91, 124, 255)
COL_BTN_HOVER = (124, 147, 255)
COL_CLOSE = (27, 36, 64)
COL_CLOSE_HOVER = (74, 32, 40)
COL_TEXT = (228, 228, 231)
COL_MUTED = (161, 161, 170)
COL_HINT = (120, 131, 184)

WM_APP_TICK = win32con.WM_APP + 1


def _colorref(rgb):
    return int(rgb[0]) | (int(rgb[1]) << 8) | (int(rgb[2]) << 16)


def _load_control(root: Path):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from backend.app.services import task_control  # type: ignore

    return task_control


class OverlayApi:
    """给热键回调/关闭用的小门面（保持和老版本同名方法）。"""

    def __init__(self, control, *, idle_seconds: int = 90, overlay=None):
        self._control = control
        self._idle_seconds = int(idle_seconds)
        self._overlay = overlay
        self._hotkey_note = ""

    def attach(self, overlay) -> None:
        self._overlay = overlay

    def set_hotkey_note(self, note: str) -> None:
        self._hotkey_note = str(note or "")[:80]

    def status(self):
        state = self._control.task_status()
        return {
            "running": bool(state.get("running")),
            "label": str(state.get("label") or state.get("kind") or ""),
            "elapsed": float(state.get("elapsed_seconds") or 0.0),
            "stopped": bool(state.get("stopped")),
            "hotkey_note": self._hotkey_note,
        }

    def pause(self):
        self._control.request_stop("overlay")
        try:
            if self._control.task_status().get("running"):
                self._control.stop_all("overlay")
        except Exception:
            pass
        return self.status()

    def resume(self):
        self._control.clear_stop()
        return self.status()

    def toggle_pause(self, reason: str = "hotkey"):
        try:
            if self._control.stop_requested():
                return self.resume()
        except Exception:
            pass
        return self.pause()

    def stop_and_close(self, reason: str = "hotkey"):
        try:
            self._control.request_stop(reason)
            if self._control.task_status().get("running"):
                self._control.stop_all(reason)
        except Exception:
            pass
        self.close()
        return {"ok": True, "stopped": True}

    def close(self):
        if self._overlay is not None:
            self._overlay.close()


class Overlay:
    PADDING = 8
    BTN_H = 42
    CLOSE_W = 24

    def __init__(self, control, api, *, width=WIDTH, height=HEIGHT, margin=24, idle_seconds=8):
        self.control = control
        self.api = api
        self.width = int(width)
        self.height = int(height)
        self.idle_seconds = int(idle_seconds)
        self.hwnd = 0
        self._hover = ""
        self._drag = None
        self._moved = 0
        self._last_busy = time.monotonic()
        self._state = {"running": False, "stopped": False, "label": "", "elapsed": 0.0}
        self._hint = "Alt+R 暂停/继续 · Alt+Q 停止并关闭"
        self._fonts = {}
        self._register_class()
        sw = win32api.GetSystemMetrics(0)
        sh = win32api.GetSystemMetrics(1)
        x = max(0, sw - self.width - int(margin))
        y = max(0, sh - self.height - int(margin) - 80)
        self.hwnd = win32gui.CreateWindowEx(
            win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_TOPMOST | win32con.WS_EX_NOACTIVATE,
            "LobsterTaskOverlayWnd", "任务暂停", win32con.WS_POPUP,
            x, y, self.width, self.height, 0, 0, win32api.GetModuleHandle(None), None,
        )
        rgn = gdi32.CreateRoundRectRgn(0, 0, self.width + 1, self.height + 1, 18, 18)
        user32.SetWindowRgn(self.hwnd, rgn, True)
        self._start_status_thread()
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNOACTIVATE)
        win32gui.SetWindowPos(
            self.hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
        )

    # ── win32 plumbing ────────────────────────────────────────────────
    def _register_class(self) -> None:
        wc = win32gui.WNDCLASS()
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = "LobsterTaskOverlayWnd"
        wc.lpfnWndProc = self._wnd_proc
        wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
        try:
            win32gui.RegisterClass(wc)
        except Exception:
            pass

    def _font(self, size: int, bold: bool):
        key = (size, bold)
        if key not in self._fonts:
            weight = win32con.FW_BOLD if bold else win32con.FW_NORMAL
            self._fonts[key] = gdi32.CreateFontW(
                -int(size), 0, 0, 0, weight, 0, 0, 0, win32con.DEFAULT_CHARSET,
                win32con.OUT_DEFAULT_PRECIS, win32con.CLIP_DEFAULT_PRECIS,
                win32con.CLEARTYPE_QUALITY, win32con.DEFAULT_PITCH | win32con.FF_SWISS,
                "Microsoft YaHei",
            )
        return self._fonts[key]

    def _text(self, hdc, text, rect, size, color, bold=False):
        old = gdi32.SelectObject(hdc, self._font(size, bold))
        gdi32.SetTextColor(hdc, _colorref(color))
        gdi32.SetBkMode(hdc, 1)  # TRANSPARENT
        r = wintypes.RECT(*rect)
        user32.DrawTextW(hdc, str(text), -1, ctypes.byref(r), 0x00000001 | 0x00000004 | 0x00080000 | 0x00000020)  # CENTER|VCENTER|SINGLELINE|TABSTOP
        gdi32.SelectObject(hdc, old)

    def _fill(self, hdc, rect, color, radius=0):
        brush = gdi32.CreateSolidBrush(_colorref(color))
        pen = gdi32.CreatePen(win32con.PS_SOLID, 0, _colorref(color))
        ob = gdi32.SelectObject(hdc, brush)
        op = gdi32.SelectObject(hdc, pen)
        if radius:
            gdi32.RoundRect(hdc, rect[0], rect[1], rect[2], rect[3], radius, radius)
        else:
            gdi32.Rectangle(hdc, rect[0], rect[1], rect[2], rect[3])
        gdi32.SelectObject(hdc, op)
        gdi32.SelectObject(hdc, ob)
        gdi32.DeleteObject(brush)
        gdi32.DeleteObject(pen)

    def _pause_rect(self):
        return (self.PADDING, self.PADDING, self.width - self.PADDING - self.CLOSE_W - 6, self.PADDING + self.BTN_H)

    def _close_rect(self):
        left = self.width - self.PADDING - self.CLOSE_W
        return (left, self.PADDING, self.width - self.PADDING, self.PADDING + self.BTN_H)

    def _hit(self, x, y):
        px1, py1, px2, py2 = self._pause_rect()
        if px1 <= x <= px2 and py1 <= y <= py2:
            return "pause"
        cx1, cy1, cx2, cy2 = self._close_rect()
        if cx1 <= x <= cx2 and cy1 <= y <= cy2:
            return "close"
        return ""

    def _paint(self):
        hdc, ps = win32gui.BeginPaint(self.hwnd)
        try:
            self._fill(hdc, (0, 0, self.width, self.height), COL_BG, radius=18)
            # 1px 品牌蓝描边
            pen = gdi32.CreatePen(win32con.PS_SOLID, 1, _colorref(COL_BORDER))
            brush = gdi32.GetStockObject(win32con.NULL_BRUSH)
            op = gdi32.SelectObject(hdc, pen)
            ob = gdi32.SelectObject(hdc, brush)
            gdi32.RoundRect(hdc, 0, 0, self.width, self.height, 18, 18)
            gdi32.SelectObject(hdc, ob)
            gdi32.SelectObject(hdc, op)
            gdi32.DeleteObject(pen)

            paused = bool(self._state.get("stopped"))
            btn_color = COL_BTN_HOVER if (self._hover == "pause" or paused) else COL_BTN
            self._fill(hdc, self._pause_rect(), btn_color, radius=10)
            self._text(hdc, "继续" if paused else "暂停任务",
                       (self._pause_rect()[0], self._pause_rect()[1], self._pause_rect()[2], self._pause_rect()[3]),
                       15, (255, 255, 255), bold=True)
            close_color = COL_CLOSE_HOVER if self._hover == "close" else COL_CLOSE
            self._fill(hdc, self._close_rect(), close_color, radius=8)
            self._text(hdc, "×", self._close_rect(), 15, (174, 185, 232) if self._hover != "close" else (255, 215, 215))

            running = bool(self._state.get("running"))
            label = str(self._state.get("label") or "任务")
            elapsed = int(float(self._state.get("elapsed") or 0))
            if paused:
                state_text = "已请求暂停 · " + (label + " · %ds" % elapsed if running else "当前没有在跑的任务")
            elif running:
                state_text = "%s · %ds" % (label, elapsed)
            else:
                state_text = "当前没有在跑的任务"
            self._text(hdc, state_text, (6, self.PADDING + self.BTN_H + 4, self.width - 6, self.PADDING + self.BTN_H + 20),
                       10, COL_MUTED)
            self._text(hdc, self._hint, (6, self.PADDING + self.BTN_H + 20, self.width - 6, self.height - 2),
                       8, COL_HINT)
        finally:
            win32gui.EndPaint(self.hwnd, ps)

    def _on_down(self, x, y):
        hit = self._hit(x, y)

        if hit:
            self._hover = hit
            win32gui.InvalidateRect(self.hwnd, None, True)
            return
        rect = win32gui.GetWindowRect(self.hwnd)
        self._drag = (x, y, rect[0], rect[1])
        self._moved = 0

    def _on_move(self, x, y):
        if self._drag:
            dx = abs(x - self._drag[0])
            dy = abs(y - self._drag[1])
            self._moved = max(self._moved, dx + dy)
            nx = self._drag[2] + (x - self._drag[0])
            ny = self._drag[3] + (y - self._drag[1])
            win32gui.SetWindowPos(
                self.hwnd, win32con.HWND_TOPMOST, nx, ny, 0, 0,
                win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
            )
            return
        hit = self._hit(x, y)
        if hit != self._hover:
            self._hover = hit
            win32gui.InvalidateRect(self.hwnd, None, True)

    def _on_up(self, x, y):
        if self._drag:
            moved = self._moved
            self._drag = None
            if moved <= 4:
                hit = self._hit(x, y)
                if hit == "pause":
                    self._toggle()
                elif hit == "close":
                    self.api.stop_and_close("overlay")
            return
        # 2026-10-17：按在按钮上时没有拖拽状态，up 事件要按落点命中处理（否则按钮点不动）
        hit = self._hit(x, y)

        if hit == "pause":
            self._toggle()
        elif hit == "close":
            self.api.stop_and_close("overlay")

    def _toggle(self):
        try:
            if self.control.stop_requested():
                self.api.resume()
            else:
                self.api.pause()
        except Exception:
            pass
        self._refresh()

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == win32con.WM_PAINT:
            self._paint()
            return 0
        if msg == win32con.WM_ERASEBKGND:
            return 1
        if msg == win32con.WM_LBUTTONDOWN:
            self._on_down(win32api.LOWORD(lparam), win32api.HIWORD(lparam))
            return 0
        if msg == win32con.WM_MOUSEMOVE:
            self._on_move(win32api.LOWORD(lparam), win32api.HIWORD(lparam))
            return 0
        if msg == win32con.WM_LBUTTONUP:
            self._on_up(win32api.LOWORD(lparam), win32api.HIWORD(lparam))
            return 0
        if msg == WM_APP_TICK:
            self._refresh()
            return 0
        if msg == win32con.WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
            return 0
        if msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _refresh(self):
        try:
            state = self.control.task_status()
        except Exception:
            state = {}
        self._state = {
            "running": bool(state.get("running")),
            "stopped": bool(state.get("stopped")),
            "label": str(state.get("label") or state.get("kind") or "任务"),
            "elapsed": float(state.get("elapsed_seconds") or 0.0),
        }
        if self._state["running"] or self._state["stopped"]:
            self._last_busy = time.monotonic()
        try:
            win32gui.InvalidateRect(self.hwnd, None, True)
        except Exception:
            pass

    def _start_status_thread(self):
        def loop():
            while True:
                time.sleep(1.0)
                try:
                    win32gui.PostMessage(self.hwnd, WM_APP_TICK, 0, 0)
                except Exception:
                    return
                if self.idle_seconds > 0 and not self._state["running"] and not self._state["stopped"]:
                    if time.monotonic() - self._last_busy > self.idle_seconds:
                        try:
                            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
                        except Exception:
                            pass
                        return

        threading.Thread(target=loop, daemon=True, name="lobster-overlay-status").start()

    def close(self):
        try:
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass

    def pump(self):
        win32gui.PumpMessages()


# ── 全局热键（不装钩子，RegisterHotKey 挂在消息窗口上）──────────────────
HOTKEY_ID_PAUSE = 0xB001
HOTKEY_ID_STOP = 0xB002
_MOD_ALT = 0x0001
_MOD_NOREPEAT = 0x4000
_VK_R = 0x52
_VK_Q = 0x51


def _start_hotkey_listener(api) -> None:
    def run() -> None:
        try:
            import win32api as _wa
            import win32con as _wc
            import win32gui as _wg
        except Exception as exc:
            api.set_hotkey_note("热键不可用：%s" % exc)
            return

        def on_message(hwnd, msg, wparam, lparam):
            try:
                if msg == _wc.WM_HOTKEY:
                    if wparam == HOTKEY_ID_PAUSE:
                        api.toggle_pause("hotkey")
                    elif wparam == HOTKEY_ID_STOP:
                        api.stop_and_close("hotkey")
                    return 0
            except Exception:
                pass
            return _wg.DefWindowProc(hwnd, msg, wparam, lparam)

        try:
            wc = _wg.WNDCLASS()
            wc.hInstance = _wa.GetModuleHandle(None)
            wc.lpszClassName = "LobsterTaskOverlayHotkeys"
            wc.lpfnWndProc = on_message
            try:
                _wg.RegisterClass(wc)
            except Exception:
                pass
            hwnd = _wg.CreateWindow(
                wc.lpszClassName, "LobsterTaskOverlayHotkeys", 0, 0, 0, 0, 0,
                _wc.HWND_MESSAGE, 0, wc.hInstance, None,
            )
            ok = []
            try:
                _wg.RegisterHotKey(hwnd, HOTKEY_ID_PAUSE, _MOD_ALT | _MOD_NOREPEAT, _VK_R)
                ok.append("Alt+R")
            except Exception:
                pass
            try:
                _wg.RegisterHotKey(hwnd, HOTKEY_ID_STOP, _MOD_ALT | _MOD_NOREPEAT, _VK_Q)
                ok.append("Alt+Q")
            except Exception:
                pass
            api.set_hotkey_note("热键 " + " / ".join(ok) if ok else "热键注册失败（被别的程序占用？）")
            _wg.PumpMessages()
        except Exception as exc:
            api.set_hotkey_note("热键注册失败：%s" % exc)

    threading.Thread(target=run, daemon=True, name="lobster-overlay-hotkeys").start()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--idle-seconds", type=int, default=8)
    parser.add_argument("--width", type=int, default=WIDTH)
    parser.add_argument("--height", type=int, default=HEIGHT)
    parser.add_argument("--margin", type=int, default=24)
    parser.add_argument("--port", type=int, default=0)  # 兼容旧参数，未使用
    args = parser.parse_args()

    root = Path(args.root).resolve()
    control = _load_control(root)
    api = OverlayApi(control, idle_seconds=args.idle_seconds)
    overlay = Overlay(
        control, api,
        width=args.width, height=args.height,
        margin=args.margin, idle_seconds=args.idle_seconds,
    )
    api.attach(overlay)
    _start_hotkey_listener(api)
    overlay.pump()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
