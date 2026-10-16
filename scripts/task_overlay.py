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

    def close(self):
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception:
            pass
        return {"ok": True}


PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<style>
  html,body{margin:0;padding:0;background:transparent;font-family:"Microsoft YaHei",system-ui,sans-serif}
  .wrap{display:flex;flex-direction:column;gap:4px;background:rgba(18,26,32,.92);border-radius:14px;
        padding:8px;box-shadow:0 10px 26px rgba(0,0,0,.35)}
  .row{display:flex;gap:6px;align-items:center}
  #btn{flex:1;border:0;border-radius:11px;padding:12px 10px;font-size:15px;font-weight:800;color:#fff;
       background:linear-gradient(135deg,#e5484d,#c62a2f);cursor:pointer}
  #btn.paused{background:linear-gradient(135deg,#2f9e5f,#1f7a46)}
  #close{border:0;border-radius:10px;width:30px;padding:12px 0;background:rgba(255,255,255,.14);color:#cfd8dd;
         font-size:14px;cursor:pointer}
  #state{color:#9fb3bf;font-size:11px;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
</style></head><body>
<div class="wrap">
  <div class="row"><button id="btn">暂停任务</button><button id="close">×</button></div>
  <div id="state">检查中…</div>
</div>
<script>
  var paused = false;
  function render(s) {
    paused = !!s.stopped;
    var b = document.getElementById('btn');
    b.textContent = paused ? '继续' : '暂停任务';
    b.className = paused ? 'paused' : '';
    var t = s.running ? (s.label || '任务') + ' · ' + Math.round(s.elapsed || 0) + 's' : '当前没有在跑的任务';
    document.getElementById('state').textContent = paused ? ('已请求暂停 · ' + t) : t;
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--idle-seconds", type=int, default=90)
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
    )
    api.attach(window)

    idle_limit = int(args.idle_seconds or 0)
    last_busy = time.monotonic()
    last_status = {"running": False}

    def _is_closed() -> bool:
        try:
            return bool(getattr(window, "events", None) and window.events.closed.is_set())
        except Exception:
            return False

    def _watch_idle() -> None:
        nonlocal last_busy, last_status
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
