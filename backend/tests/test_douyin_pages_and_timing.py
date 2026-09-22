"""抖音页面坏链接与两类时序优化（私信探测预算 / 评论@回滚）。"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GUIDE = ROOT / "static" / "douyin-origin" / "douyin-guide.html"
SCRAPER = ROOT / "backend" / "douyin_origin" / "douyin_comment_scraper.py"
DOUYIN = ROOT / "static" / "douyin-origin"
SHARED_JS = DOUYIN / "douyin-workbench-shared.js"

# 轻页面：各自独立 HTML、共用 douyin-workbench-shared.js。
# douyin-search.html 是另一套外壳（自带 douyin-search-page.js），规则不同，不列在这里。
LIGHT_PAGES = [
    "douyin-collect.html",
    "douyin-follow.html",
    "douyin-self-comments.html",
    "douyin-stranger-leads.html",
    "douyin-mention.html",
]


def test_guide_has_no_broken_static_links():
    html = GUIDE.read_text(encoding="utf-8")
    assert "/static/static/" not in html, "使用说明页里不能有拼错的 /static/static/ 链接（点了 404）"
    assert "进入抖音工作台" not in html, "那个会 404 的工作台按钮要去掉"


def test_guide_uses_existing_css():
    html = GUIDE.read_text(encoding="utf-8")
    assert "/static/douyin-origin/douyin-workbench.css" in html
    assert (ROOT / "static" / "douyin-origin" / "douyin-workbench.css").is_file()


def test_private_message_dialog_budget_is_tighter():
    source = SCRAPER.read_text(encoding="utf-8")
    assert "DOUYIN_PM_DIALOG_OPEN_BUDGET_SECONDS = 20.0" in source, "对方关闭私信时不该白等 60 秒"
    assert "DOUYIN_PM_DIALOG_MISS_LIMIT = 3" in source


def test_mention_rollback_uses_backspace_fallback():
    source = SCRAPER.read_text(encoding="utf-8")
    start = source.index("async def _rollback_failed_mention_input(")
    body = source[start:start + 2200]
    assert "Control+Z" in body
    assert "Backspace" in body, "Ctrl+Z 无效时要按退格删掉残留的 @名字"


def test_light_pages_keep_console_page_visible():
    """#console-page 用 display:contents 会让轻页面整页空掉（用户报的「我的评论区白屏」）。"""
    for name in LIGHT_PAGES:
        html = (DOUYIN / name).read_text(encoding="utf-8")
        assert "display: contents" not in html, f"{name} 里不能再出现 display: contents"
        rule = re.search(r"#console-page\.page\.active\s*\{[^}]*\}", html)
        assert rule, f"{name} 缺少 #console-page.page.active 规则"
        assert "display: block" in rule.group(0), f"{name} 的 #console-page.page.active 必须是 display: block"


def test_light_pages_use_shared_js_without_search_page_script():
    """轻页面只加载 shared.js、不加载 douyin-search-page.js —— 所以 shared.js 里的调用必须带 typeof 守卫。"""
    for name in LIGHT_PAGES:
        html = (DOUYIN / name).read_text(encoding="utf-8")
        assert "douyin-workbench-shared.js" in html, f"{name} 预期加载共享脚本"
        assert "douyin-search-page.js" not in html, f"{name} 不应加载搜索页脚本"


def test_shared_js_guards_search_page_only_handlers():
    source = SHARED_JS.read_text(encoding="utf-8")
    assert 'typeof changeDouyinSearchMode==="function"' in source, (
        "轻页面里没有 changeDouyinSearchMode，不加守卫会在恢复本地话术时抛 ReferenceError"
        "（用户本机日志：读取本地数据库话术失败：changeDouyinSearchMode is not defined）"
    )
    assert 'typeof changeDouyinCollectionMode==="function"' in source
