"""index.html 模板替换位（2026-10-13）。

线上事故：模板里把「JS 全局名」和「服务端替换位」写成了同一个 token，
服务端 naive replace 之后变成 `window.true = true;` / `window.https://bhzn.top = ...`，
整段内联脚本直接 SyntaxError —— window.__LOBSTER_SERVER_PUBLIC__ /
__LOBSTER_IS_OVERSEAS_USER / __LOBSTER_BRANDING_AVAILABLE__ 全都没被赋值。
（靠新加的界面取证日志在真机上一个页面加载就抓到了 Uncaught SyntaxError: Unexpected token ':'）

这里守住：渲染出来的内联脚本必须是合法 JS，不能出现 window.true / window.https。
"""
import json
import re
import subprocess
import shutil
from pathlib import Path

from backend.app.api import branding

TEMPLATE_PATH = Path(__file__).resolve().parent / "static" / "index.html"


def _render(monkeypatch, *, available: bool, server_public: str, overseas: bool) -> str:
    monkeypatch.setattr(
        branding,
        "_index_branding",
        lambda _mark="": {"available": available, "mark": ""},
    )
    monkeypatch.setattr(branding.settings, "auth_server_base", server_public, raising=False)
    monkeypatch.setattr(branding.settings, "lobster_is_overseas_user", overseas, raising=False)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return branding.render_index_html(template, "")


def _inline_globals_script(html: str) -> str:
    match = re.search(
        r"<script>(?:(?!</script>).)*?__LOBSTER_CONFIGURED_OVERSEAS(?:(?!</script>).)*?</script>",
        html,
        re.S,
    )
    assert match, "找不到内联全局变量脚本"
    return match.group(0)[len("<script>"):-len("</script>")]


def test_rendered_index_globals_are_valid_js(monkeypatch):
    html = _render(monkeypatch, available=False, server_public="https://bhzn.top", overseas=False)

    assert "window.true = true;" not in html
    assert "window.false = false;" not in html
    assert "window.https" not in html
    assert "window.__LOBSTER_BRANDING_AVAILABLE__ = false;" in html
    assert "window.__LOBSTER_SERVER_PUBLIC__ = 'https://bhzn.top';" in html
    assert "window.__LOBSTER_CONFIGURED_OVERSEAS = false;" in html

    script = _inline_globals_script(html)
    node = shutil.which("node")
    if not node:
        return
    path = Path(__file__).resolve().parent / "_tmp_inline_globals_check.js"
    path.write_text(script, encoding="utf-8")
    try:
        proc = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
    finally:
        path.unlink(missing_ok=True)


def test_rendered_index_globals_with_overseas_value(monkeypatch):
    html = _render(monkeypatch, available=True, server_public="https://lobster-server.icu/", overseas=True)

    assert "window.__LOBSTER_CONFIGURED_OVERSEAS = true;" in html
    assert "window.__LOBSTER_SERVER_PUBLIC__ = 'https://lobster-server.icu';" in html
    assert "window.__LOBSTER_BRANDING_AVAILABLE__ = true;" in html
    # 模板里不能再残留未替换的占位符
    assert "__LOBSTER_BRANDING_AVAILABLE_VALUE__" not in html
    assert "__LOBSTER_SERVER_PUBLIC_VALUE__" not in html
    assert "__LOBSTER_IS_OVERSEAS_VALUE__" not in html

