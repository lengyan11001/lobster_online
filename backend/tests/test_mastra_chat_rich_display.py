"""Online AI 调度助手的富内容显示层。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_online_renders_rich_content_per_reply_type():
    script = (ROOT / "static" / "js" / "mastra-chat.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "rich-content.css").read_text(encoding="utf-8")

    for token in (
        "function renderRichBody",
        "function richUrlKind",
        "function richMediaGroup",
        "function richLinkCard",
        "function richImageGrid",
        "ensureRichStyles",
    ):
        assert token in script, token

    # 气泡文本改走富渲染，不再直接 textContent 赋值
    assert "renderRichBody(bubble.body, bubble.text);" in script
    assert "bubble.body.textContent = bubble.text" not in script

    for token in (".rich-media-grid", ".rich-link-card", ".rich-file-row", ".rich-lightbox"):
        assert token in css, token
