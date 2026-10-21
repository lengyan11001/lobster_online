# -*- coding: utf-8 -*-
"""无边框窗口：只能拖顶部拖动条，不能整页拖动（2026-10-21）。

pywebview 的 easy_drag 在 frameless 下会把 window mousedown 绑成拖窗口，
导致点正文/选文字都会把窗口拖走。这里锁住修复：easy_drag=False + 独立的
pywebview-drag-region 拖动条。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_launcher_disables_easy_drag():
    src = (ROOT / "desktop" / "launcher.py").read_text(encoding="utf-8")
    assert "easy_drag=False" in src


def test_header_has_drag_strip():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "windowDragStrip" in html
    assert "pywebview-drag-region" in html


def test_drag_strip_style():
    css = (ROOT / "static" / "css" / "common.css").read_text(encoding="utf-8")
    assert ".window-drag-strip" in css
    assert "right: 150px" in css