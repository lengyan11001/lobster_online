"""桌面壳抗闪（2026-10-13 引入，2026-10-14 修正）。

背景：
- 2026-10-13 那版为了关 WebView2 的 GPU 合成，把 pywebview 的
  CoreWebView2CreationProperties 换成了 Python 代理对象；pywebview 里
  `self.webview.CreationProperties = props` 拿到 Python 对象、不是 .NET 类型，
  WebView2 初始化直接崩（真机 exit=0xE0434352 CLR 异常 / 窗口永远不出来），
  升级到 build 412 的机器全部启动不了。
- 现在只保留零风险的两件事：窗口底板色 = 界面底色；桌面壳里禁用 backdrop-filter（纯 CSS）。

本文件守住这两点，并且**禁止再 monkeypatch pywebview 内部对象**。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

from desktop import launcher

LAUNCHER_SRC = (Path(__file__).resolve().parent / "desktop" / "launcher.py").read_text(encoding="utf-8")


class _ClosingEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


def test_launcher_never_patches_pywebview_internals():
    """build 412 就是踩了这个坑：把 .NET 的 CreationProperties 换成 Python 代理。"""
    code = "\n".join(
        line for line in LAUNCHER_SRC.splitlines() if not line.strip().startswith("#")
    )
    assert "CoreWebView2CreationProperties" not in code
    assert "webview.platforms" not in code
    assert "AdditionalBrowserArguments" not in code
    assert "disable-gpu-compositing" not in code
    assert "apply_desktop_webview_args" not in code


def test_run_window_uses_shell_background_color(monkeypatch):
    recorded = {}
    window = SimpleNamespace(
        events=SimpleNamespace(closing=_ClosingEvent()),
        load_url=lambda _url: None,
        load_html=lambda *_args, **_kwargs: None,
    )

    def create_window(*args, **kwargs):
        recorded["kwargs"] = kwargs
        return window

    fake = SimpleNamespace(
        settings={},
        create_window=create_window,
        start=lambda callback, args, **_kwargs: callback(*args),
    )
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(launcher, "webview2_runtime_available", lambda: True)
    monkeypatch.setattr(launcher, "ensure_desktop_runtime", lambda _env: True)
    monkeypatch.setattr(launcher, "find_fixed_webview2_runtime", lambda: None)
    monkeypatch.setattr(
        launcher,
        "start_services_blocking",
        lambda *_args, **_kwargs: (True, "http://127.0.0.1:8000", None, None, ""),
    )
    monkeypatch.setattr(launcher, "cleanup_owned_services", lambda *_a, **_k: None)

    ok, _backend, _mcp = launcher.run_window(
        "http://127.0.0.1:8000", "Lobster", 1200, 800, 8000, 8001, {}, 5,
    )

    assert ok is True
    assert recorded["kwargs"]["background_color"] == launcher.SHELL_BACKGROUND_COLOR
    assert launcher.SHELL_BACKGROUND_COLOR.lower() == "#fbfaf8"


def test_desktop_shell_css_disables_backdrop_filter():
    css = (Path(__file__).resolve().parent / "static" / "css" / "common.css").read_text(encoding="utf-8")
    assert "html.is-desktop-shell" in css
    assert "backdrop-filter: none !important" in css
    html = (Path(__file__).resolve().parent / "static" / "index.html").read_text(encoding="utf-8")
    assert "is-desktop-shell" in html
