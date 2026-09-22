"""抖音页面坏链接与两类时序优化（私信探测预算 / 评论@回滚）。"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GUIDE = ROOT / "static" / "douyin-origin" / "douyin-guide.html"
SCRAPER = ROOT / "backend" / "douyin_origin" / "douyin_comment_scraper.py"


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
