"""全局悬浮「暂停任务」键（pywebview 置顶小窗，2026-10-16）。

问题：微信/抖音这些自动化跑起来之后，界面上没有统一的"停"，只能去结束进程。
这个脚本由后端拉起（task_control.ensure_overlay），是一个**置顶、无边框、可拖动**的小窗：
  - 大字按钮「暂停任务」→ 写 data/task_control/stop.flag（各引擎循环立刻能看见）
  - 暂停后按钮变「继续」→ 清掉标记
  - 上面一行显示"谁在跑 + 跑了多久"（读 data/task_control/status.json）
  - 空闲超过 --idle-seconds（默认 90 秒）自动退出，不常驻打扰

用文件通信而不是 HTTP：不需要登录态，后端正忙（自动化占着线程）时也能立刻生效。

⚠️ 摆位注意：窗口默认贴右下角。自动化点击有"鼠标下必须是目标窗口"的保护
（_uia_click_screen_point 的 expect_hwnd 检查），所以别把它拖到微信窗口上，
否则那一片区域的点击会被守卫生效地拒绝。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path


def _load_control(root: Path):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from backend.app.services import task_control  # type: ignore

    return task_control


class OverlayApi:
    """暴露给页面 JS：status / pause / resume / close。"""

    def __init__(self, control, *, idle_seconds: int = 90):
        self._control = control
        self._idle_seconds = int(idle_seconds)
        self._window = None
        self._hotkey_note = ""

    def attach(self, window) -> None:
        self._window = window

    def status(self):
        # 每次被页面调用就落一次心跳：后台/支持侧据此判断"悬浮键活着且桥是通的"。
        try:
            self._control.overlay_heartbeat()
        except Exception:
            pass
        state = self._control.task_status()
        overlay = self._control.overlay_status()
        return {
            "running": bool(state.get("running")),
            "label": str(state.get("label") or state.get("kind") or ""),
            "elapsed": float(state.get("elapsed_seconds") or 0.0),
            "stopped": bool(state.get("stopped")),
            "overlay_pid": int(overlay.get("pid") or 0),
            "idle_seconds": self._idle_seconds,
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

    # ── 全局热键回调（抄竞品 InputGuard 的 Alt+R / Alt+Q）──────────────────
    def set_hotkey_note(self, note: str) -> None:
        self._hotkey_note = str(note or "")[:80]

    def toggle_pause(self, reason: str = "hotkey"):
        """Alt+R：暂停 ↔ 恢复（和按钮同义）。"""
        try:
            if self._control.stop_requested():
                return self.resume()
        except Exception:
            pass
        return self.pause()

    def stop_and_close(self, reason: str = "hotkey"):
        """Alt+Q：有序停机 —— 先请求暂停/停止，再关掉悬浮窗（竞品的 Alt+Q 退出）。"""
        try:
            self._control.request_stop(reason)
            if self._control.task_status().get("running"):
                self._control.stop_all(reason)
        except Exception:
            pass
        self.close()
        return {"ok": True, "stopped": True}

    def close(self):
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception:
            pass
        return {"ok": True}


PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<style>
  /* 2026-10-16 换成产品自己的色调：深空底 + 品牌蓝（与客户端设计令牌一致） */
  :root{--brand:#5b7cff;--brand-hi:#7c93ff;--brand-deep:#3f5cf0;--bg0:rgba(18,18,30,.97);--bg1:rgba(10,10,18,.97);
        --text:#e4e4e7;--muted:#a1a1aa;--line:rgba(91,124,255,.28)}
  html,body{margin:0;padding:0;background:transparent;font-family:"Microsoft YaHei",system-ui,sans-serif}
  .wrap{display:flex;flex-direction:column;gap:4px;padding:8px;border-radius:14px;
        background:linear-gradient(180deg,var(--bg0),var(--bg1));
        border:1px solid var(--line);box-shadow:0 10px 26px rgba(4,8,24,.5)}
  .row{display:flex;gap:6px;align-items:center}
  #btn{flex:1;border:0;border-radius:11px;padding:12px 10px;font-size:15px;font-weight:800;color:#fff;cursor:pointer;
       background:linear-gradient(135deg,var(--brand),var(--brand-deep));box-shadow:0 4px 12px rgba(91,124,255,.34)}
  #btn:hover{background:linear-gradient(135deg,var(--brand-hi),var(--brand))}
  #btn.paused{background:linear-gradient(135deg,var(--brand-hi),#6b83f5);box-shadow:0 4px 12px rgba(124,147,255,.3)}
  #close{border:0;border-radius:10px;width:30px;padding:12px 0;background:rgba(91,124,255,.16);color:#aeb9e8;
         font-size:14px;cursor:pointer}
  #close:hover{background:rgba(239,68,68,.24);color:#ffd7d7}
  #state{color:var(--muted);font-size:11px;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  #hint{color:#7883b8;font-size:10px;text-align:center;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
</style></head><body>
<div class="wrap">
  <div class="row"><button id="btn">暂停任务</button><button id="close">×</button></div>
  <div id="state">检查中…</div>
  <div id="hint">Alt+R 暂停/继续 · Alt+Q 停止并关闭</div>
</div>
<script>
  var paused = false;
  function render(s) {
    paused = !!s.stopped;
    var b = document.getElementById('btn');
    b.textContent = paused ? '继续' : '暂停任务';
    b.className = paused ? 'paused' : '';
    var t = s.running ? (s.label || '任务') + ' · ' + Math.round(s.elapsed || 0) + 's' : '当前没有在跑的任务';
    var note = String(s.hotkey_note || '');
    document.getElementById('state').textContent = (note && note.indexOf('Alt') < 0)
      ? note : (paused ? ('已请求暂停 · ' + t) : t);
  }
  function tick() {
    if (!window.pywebview || !window.pywebview.api) { document.getElementById('state').textContent = '悬浮键已启动（等待桥接…）'; return; }
    window.pywebview.api.status().then(render).catch(function(){});
  }
  document.getElementById('btn').addEventListener('click', function () {
    if (!window.pywebview || !window.pywebview.api) return;
    var call = paused ? window.pywebview.api.resume() : window.pywebview.api.pause();
    call.then(render).catch(function(){});
  });
  document.getElementById('close').addEventListener('click', function () {
    if (window.pywebview && window.pywebview.api) window.pywebview.api.close();
  });
  setInterval(tick, 1000);
  setTimeout(tick, 300);
</script></body></html>"""


# ── 全局热键：抄竞品（ai-bot-pc 的 InputGuard）──────────────────────────────
# 竞品跑自动化时会挂键盘钩子 + 屏幕状态条，文案「微信自动化运行中，Alt+Q退出，Alt+R临时解锁」：
#   Alt+R 暂停/恢复、Alt+Q 有序停机。
# 我们用 win32 RegisterHotKey 挂在自己的消息窗口上（不装钩子、不锁用户键鼠），
# 好处是不管前台是微信还是别的窗口，按了都生效。
HOTKEY_ID_PAUSE = 0xB001
HOTKEY_ID_STOP = 0xB002
_MOD_ALT = 0x0001
_MOD_NOREPEAT = 0x4000
_VK_R = 0x52
_VK_Q = 0x51


def _start_hotkey_listener(api) -> None:
    def run() -> None:
        try:
            import win32api  # type: ignore
            import win32con  # type: ignore
            import win32gui  # type: ignore
        except Exception as exc:
            api.set_hotkey_note("热键不可用：%s" % exc)
            return

        def on_message(hwnd, msg, wparam, lparam):
            try:
                if msg == win32con.WM_HOTKEY:
                    if wparam == HOTKEY_ID_PAUSE:
                        api.toggle_pause("hotkey")
                    elif wparam == HOTKEY_ID_STOP:
                        api.stop_and_close("hotkey")
                    return 0
            except Exception:
                pass
            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

        try:
            wc = win32gui.WNDCLASS()
            wc.hInstance = win32api.GetModuleHandle(None)
            wc.lpszClassName = "LobsterTaskOverlayHotkeys"
            wc.lpfnWndProc = on_message
            try:
                win32gui.RegisterClass(wc)
            except Exception:
                pass
            hwnd = win32gui.CreateWindow(
                wc.lpszClassName, "LobsterTaskOverlayHotkeys", 0, 0, 0, 0, 0,
                win32con.HWND_MESSAGE, 0, wc.hInstance, None,
            )
            ok = []
            try:
                win32gui.RegisterHotKey(hwnd, HOTKEY_ID_PAUSE, _MOD_ALT | _MOD_NOREPEAT, _VK_R)
                ok.append("Alt+R")
            except Exception:
                pass
            try:
                win32gui.RegisterHotKey(hwnd, HOTKEY_ID_STOP, _MOD_ALT | _MOD_NOREPEAT, _VK_Q)
                ok.append("Alt+Q")
            except Exception:
                pass
            api.set_hotkey_note("热键 " + " / ".join(ok) if ok else "热键注册失败（被别的程序占用？）")
            win32gui.PumpMessages()
        except Exception as exc:
            api.set_hotkey_note("热键注册失败：%s" % exc)

    threading.Thread(target=run, daemon=True, name="lobster-overlay-hotkeys").start()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--idle-seconds", type=int, default=8)  # 任务结束 8 秒后就自动消失（用户反馈 90 秒太久）
    parser.add_argument("--width", type=int, default=196)
    parser.add_argument("--height", type=int, default=86)
    parser.add_argument("--margin", type=int, default=24)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    control = _load_control(root)
    api = OverlayApi(control, idle_seconds=args.idle_seconds)

    try:
        import webview  # type: ignore
    except Exception as exc:
        sys.stderr.write("pywebview 不可用：%s\n" % exc)
        return 2

    x = y = None
    try:
        screen = webview.screens[0]
        x = max(0, int(screen.width) - int(args.width) - int(args.margin))
        y = max(0, int(screen.height) - int(args.height) - int(args.margin) - 80)
    except Exception:
        pass

    window = webview.create_window(
        "任务暂停",
        html=PAGE,
        width=int(args.width),
        height=int(args.height),
        x=x,
        y=y,
        frameless=True,
        easy_drag=True,
        on_top=True,
        resizable=False,
        background_color="#121A20",
        js_api=api,
        # 先"隐藏创建"：样式要在显示之前打上，否则创建那一下就已经把它激活成前台了。
        hidden=True,
    )
    api.attach(window)
    _start_hotkey_listener(api)

    idle_limit = int(args.idle_seconds or 0)
    last_busy = time.monotonic()
    last_status = {"running": False}

    def _is_closed() -> bool:
        try:
            return bool(getattr(window, "events", None) and window.events.closed.is_set())
        except Exception:
            return False

    def _keep_non_activating(window_title: str, *, seconds: float = 10.0) -> None:
        """把悬浮窗设成"置顶但永不激活"，并且**反复重打**：

        实测（2026-10-16）：只打一次没用 —— WinForms 的 Show() 会把扩展样式盖回去，
        窗口照样抢走键盘焦点，正在跑的自动化发的 {DOWN}/{HOME} 全打到它身上
        （同步只走 1 步、只认了一屏 11 个人）。所以前 10 秒每 0.5 秒重打一次，
        并且一旦发现前台变成了悬浮窗，就把焦点还给上一个窗口。
        """
        try:
            import win32con  # type: ignore
            import win32gui  # type: ignore
        except Exception:
            return
        deadline = time.monotonic() + max(2.0, float(seconds))
        previous = 0
        while time.monotonic() < deadline:
            try:
                hwnd = win32gui.FindWindow(None, window_title)
                if hwnd:
                    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                    want = ex | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOPMOST | win32con.WS_EX_TOOLWINDOW
                    if ex != want:
                        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, want)
                    win32gui.SetWindowPos(
                        hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
                    )
                    if win32gui.GetForegroundWindow() == hwnd:
                        # 它把自己弄到前台了：还回去（还给上一个非悬浮窗）。
                        if previous and previous != hwnd and win32gui.IsWindow(previous):
                            try:
                                win32gui.SetForegroundWindow(previous)
                            except Exception:
                                pass
                    else:
                        previous = win32gui.GetForegroundWindow()
            except Exception:
                pass
            time.sleep(0.5)

    def _watch_idle() -> None:
        nonlocal last_busy, last_status
        # 顺序很重要：窗口此时还是隐藏的 —— 先打 NOACTIVATE/TOPMOST，再 show。
        try:
            import win32gui as _wg0

            previous = int(_wg0.GetForegroundWindow() or 0)
        except Exception:
            previous = 0
        _keep_non_activating("任务暂停", seconds=1.0)
        try:
            window.show()
        except Exception:
            pass
        # 实测：WinForms 的 Show() 还是会把自己激活成前台（一次）。
        # 但此刻"它"就是前台进程，所以它说话管用 —— 立刻把前台还给刚才那个窗口。
        try:
            import win32con as _wc
            import win32gui as _wg
            hwnd_self = _wg.FindWindow(None, "任务暂停")
            if hwnd_self and _wg.GetForegroundWindow() == hwnd_self and previous and previous != hwnd_self and _wg.IsWindow(previous):
                _wg.SetForegroundWindow(previous)
                _wg.ShowWindow(previous, _wc.SW_SHOW)
        except Exception:
            pass
        _keep_non_activating("任务暂停", seconds=10.0)
        while True:
            time.sleep(2.0)
            if _is_closed():
                return
            try:
                last_status = control.task_status()
            except Exception:
                last_status = {"running": False}
            if last_status.get("running") or last_status.get("stopped"):
                last_busy = time.monotonic()
                continue
            if idle_limit > 0 and (time.monotonic() - last_busy) > idle_limit:
                try:
                    window.destroy()
                except Exception:
                    pass
                return

    try:
        webview.start(_watch_idle, gui=None)
    except TypeError:
        webview.start(_watch_idle)
    finally:
        # 退出时**不动**暂停标记：用户是按了暂停才关的，标记该由用户点「继续」清。
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
