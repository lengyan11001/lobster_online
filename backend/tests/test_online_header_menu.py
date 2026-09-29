"""online 顶部导航 / 右上角个人下拉菜单的结构契约。

需求（2026-09-29）：
- 顶部导航不再显示「定时任务」「教程」
- 这两项改到右上角个人下拉菜单里
- 「灵感画布」(canvas_web) 入口先放在个人下拉菜单里，用功能开关控制（默认关）
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "static" / "index.html"
INIT_JS = ROOT / "static" / "js" / "init.js"


def _index_html() -> str:
    return INDEX.read_text(encoding="utf-8")


def _dropdown_block(html: str) -> str:
    start = html.index('<div class="header-dropdown-menu">')
    end = html.index('</header>', start)
    return html[start:end]


def _top_nav_block(html: str) -> str:
    start = html.index('<nav class="app-top-nav"')
    end = html.index("</nav>", start)
    return html[start:end]


def test_top_nav_no_longer_shows_scheduled_tasks_and_tutorial():
    nav = _top_nav_block(_index_html())
    for view in ("scheduled-tasks", "tutorial"):
        match = re.search(r'<button[^>]*data-view="%s"[^>]*>' % re.escape(view), nav)
        assert match, "隐藏路由锚点应保留，避免下拉项点击链路失效"
        tag = match.group(0)
        assert "hidden" in tag and "nav-hidden-route" in tag, "顶部导航不应再直接显示该入口"


def test_personal_dropdown_has_scheduled_tasks_tutorial_and_canvas():
    dropdown = _dropdown_block(_index_html())
    for view in ("scheduled-tasks", "tutorial", "canvas-studio"):
        assert 'class="header-menu-view"' in dropdown
        assert 'data-view="%s"' % view in dropdown, "个人下拉菜单缺少 %s" % view
    assert 'data-view="scheduled-tasks" data-feature-gate="scheduled_tasks_entry"' in dropdown
    assert 'data-view="tutorial" data-feature-gate="tutorial_entry"' in dropdown
    assert 'data-view="canvas-studio" data-feature-gate="canvas_studio_entry"' in dropdown


def test_dropdown_items_are_wired_by_init_js():
    js = INIT_JS.read_text(encoding="utf-8")
    assert ".header-menu-view[data-view]" in js, "下拉菜单项需要 init.js 的点击转发"
    assert "showAppView(view, item)" in js
