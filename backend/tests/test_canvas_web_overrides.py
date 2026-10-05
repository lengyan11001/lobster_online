"""画布入口页（static/canvas-web/index.html）上的定制：隐藏/修复第三方画布的按钮。

2026-10-05 用户要求 + 反馈：
1) 顶部「文档教程」按钮去掉（它本身是个空 onClick={} 的死按钮）；
2) 新手引导可以留着，但「关闭」点了没反应，只能点「跳过」。
   原因：画布用的是受控 stepIndex 的 react-joyride，close() 不会结束引导，
   而「跳过」走的是 skip() 会触发 onFinish。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

INDEX = ROOT / "static" / "canvas-web" / "index.html"


def test_canvas_index_hides_dead_doc_tutorial_button():
    html = INDEX.read_text(encoding="utf-8")
    assert 'button[title="查看文档教程"]' in html
    assert "display:none !important" in html
    # 新手引导按钮（title=查看新手引导）必须保留
    assert 'button[title="查看新手引导"]' not in html, "只隐藏文档教程，别把新手引导也干掉"


def test_canvas_index_forward_tour_close_to_skip():
    html = INDEX.read_text(encoding="utf-8")
    assert "lobster-canvas-tour-close" in html
    assert '[data-action="close"]' in html
    assert '[data-action="skip"]' in html


def test_canvas_app_serves_patched_index_html():
    """独立 origin（canvas_app）也必须带上这些定制块。"""
    from backend import canvas_app

    body = canvas_app._canvas_html().body.decode("utf-8")
    assert 'button[title="查看文档教程"]' in body
    assert "lobster-canvas-tour-close" in body
