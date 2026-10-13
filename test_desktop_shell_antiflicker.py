"""桌面壳抗闪（2026-10-13）。

现象：部分机器上鼠标移到右上角、或滚动界面时，WebView2 整屏闪一下。
根因：WebView2 硬件合成/DirectComposition 重绘会短暂露出默认底板色，带
      backdrop-filter 的浮层每次重绘又要回读底图，把抖动放大成可见白闪。
修复：① 窗口底板色 = 界面底色 ② 关掉 GPU 合成/DirectComposition ③ 桌面壳里禁 backdrop-filter

这里验证壳侧两件事：窗口底板色、WebView2 启动参数注入。
"""
import sys
import types
from types import SimpleNamespace

from desktop import launcher


class _ClosingEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class _FakeCreationProperties:
    def __init__(self):
        self.UserDataFolder = None
        self.BrowserExecutableFolder = None
        self.AdditionalBrowserArguments = ""


def _install_fake_edgechromium(monkeypatch):
    pkg = types.ModuleType("webview")
    pkg.__path__ = []
    platforms = types.ModuleType("webview.platforms")
    platforms.__path__ = []
    edge = types.ModuleType("webview.platforms.edgechromium")
    edge.CoreWebView2CreationProperties = _FakeCreationProperties
    pkg.settings = {}
    pkg.platforms = platforms
    platforms.edgechromium = edge
    monkeypatch.setitem(sys.modules, "webview", pkg)
    monkeypatch.setitem(sys.modules, "webview.platforms", platforms)
    monkeypatch.setitem(sys.modules, "webview.platforms.edgechromium", edge)
    return edge


def test_apply_desktop_webview_args_keeps_pywebview_args(monkeypatch):
    monkeypatch.delenv("LOBSTER_DESKTOP_WEBVIEW_ARGS", raising=False)
    edge = _install_fake_edgechromium(monkeypatch)

    assert launcher.apply_desktop_webview_args() is True

    props = edge.CoreWebView2CreationProperties()
    # pywebview 在 EdgeChrome.__init__ 里就是这么赋值的（直接覆盖，不看环境变量）
    props.AdditionalBrowserArguments = "--disable-features=ElasticOverscroll"

    assert props.AdditionalBrowserArguments.startswith("--disable-features=ElasticOverscroll")
    assert "--disable-gpu-compositing" in props.AdditionalBrowserArguments
    assert "--disable-direct-composition" in props.AdditionalBrowserArguments
    # 代理对象要能正常透传 pywebview 用到的其它属性
    props.UserDataFolder = r"C:\\tmp\\ud"
    assert props.UserDataFolder == r"C:\\tmp\\ud"


def test_desktop_webview_args_can_be_disabled(monkeypatch):
    monkeypatch.setenv("LOBSTER_DESKTOP_WEBVIEW_ARGS", "")
    assert launcher.desktop_webview_args() == ""
    assert launcher.apply_desktop_webview_args() is False


def test_run_window_uses_shell_background_color(monkeypatch):
    monkeypatch.delenv("LOBSTER_DESKTOP_WEBVIEW_ARGS", raising=False)
    monkeypatch.delenv("LOBSTER_DESKTOP_BG_COLOR", raising=False)
    edge = _install_fake_edgechromium(monkeypatch)

    recorded = {}
    window = SimpleNamespace(
        events=SimpleNamespace(closing=_ClosingEvent()),
        load_url=lambda _url: None,
        load_html=lambda *_args, **_kwargs: None,
    )

    def create_window(*args, **kwargs):
        recorded["kwargs"] = kwargs
        return window

    def start(callback, args, **_kwargs):
        recorded["started"] = True
        return callback(*args)

    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(
        settings={}, create_window=create_window, start=start,
    ))
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
    assert recorded["started"] is True
    assert recorded["kwargs"]["background_color"] == launcher.SHELL_BACKGROUND_COLOR
    assert launcher.SHELL_BACKGROUND_COLOR.lower() == "#fbfaf8"
    # 启动参数也注入了（否则 create_window 之后 create_window 里的 pywebview 会覆盖掉）
    assert getattr(edge.CoreWebView2CreationProperties, "_lobster_webview_args_wrapped", False) is True
