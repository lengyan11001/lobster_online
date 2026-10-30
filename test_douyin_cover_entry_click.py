# -*- coding: utf-8 -*-
"""抖音封面入口点击回归：遮挡 / iframe 两种真实 DOM 场景，必须点开「设置封面」弹层。

不用抖音线上页面，用本地 HTML 复刻真实结构（隐藏重复文案 + 透明遮挡层 + iframe 内创作器）。
"""
from __future__ import annotations

import os
import sys

import pytest
from playwright.async_api import async_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from skills.douyin_publish.driver import (  # noqa: E402
    _douyin_click_horizontal_cover_entry,
    _douyin_cover_frame,
    _douyin_wait_setting_cover_modal_open,
)

MAIN_HTML = """<!doctype html><html><body>
  <div style="display:none">横封面4:3</div>
  <div id="cover-card" style="width:300px;height:180px;background:#eee">
    <div class="filter-box"><div class="title">选择封面</div></div>
  </div>
  <div id="blocker" style="position:fixed;left:0;top:0;width:100%;height:100%;z-index:9"></div>
  <div id="modal" role="dialog" style="display:none;position:fixed;z-index:99">
    <div>设置封面</div><div>上传封面</div><div>设置横封面</div><div>设置竖封面</div>
  </div>
  <script>
    document.getElementById('cover-card').addEventListener('click', () => {
      document.getElementById('modal').style.display = 'block';
    });
  </script>
</body></html>"""

FRAME_HTML = """<!doctype html><html><body>
  <div id="card" style="width:300px;height:180px;background:#eee">
    <div class="title">横封面4:3</div>
  </div>
  <div id="modal" role="dialog" style="display:none">
    <div>设置封面</div><div>上传封面</div><div>设置横封面</div><div>设置竖封面</div>
  </div>
  <script>
    document.getElementById('card').addEventListener('click', () => {
      document.getElementById('modal').style.display = 'block';
    });
  </script>
</body></html>"""


@pytest.mark.asyncio
async def test_cover_entry_clicked_when_playwright_blocked(tmp_path):
    """透明遮挡层让 Playwright 的 click 命不中卡片（真机常见「点了没反应」）。"""
    html = tmp_path / "cover_blocked.html"
    html.write_text(MAIN_HTML, encoding="utf-8")
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        await page.goto(html.as_uri())
        assert await _douyin_click_horizontal_cover_entry(page) is True
        assert await page.locator("#modal").is_visible()
        await browser.close()


@pytest.mark.asyncio
async def test_cover_entry_clicked_inside_iframe(tmp_path):
    """创作器在 iframe 里：主文档只有隐藏文案，弹层也在 iframe 内。"""
    frame_page = tmp_path / "frame_cover.html"
    frame_page.write_text(FRAME_HTML, encoding="utf-8")
    html = tmp_path / "cover_iframe.html"
    html.write_text(
        "<!doctype html><html><body>"
        "<div style='display:none'>选择封面</div>"
        f"<iframe src='{frame_page.as_uri()}' style='width:900px;height:600px;border:0'></iframe>"
        "</body></html>",
        encoding="utf-8",
    )
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        await page.goto(html.as_uri())
        await page.wait_for_timeout(400)

        assert await _douyin_click_horizontal_cover_entry(page) is True
        # 弹层在 iframe 内，跨 frame 检测必须能认出来
        assert await _douyin_wait_setting_cover_modal_open(page, 4000) is True
        fr = await _douyin_cover_frame(page)
        assert fr is not None and fr is not page
        await browser.close()