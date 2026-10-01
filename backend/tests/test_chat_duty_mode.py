"""AI 调度助手：工作 / 客服 下拉（Online）。

用户口径（2026-10-01）：调度助手加一个下拉，选「工作」跟现在一样；选「客服」只把客服问题交给 AI。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_chat_duty_mode_select_and_wiring():
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "static/js/chat.js").read_text(encoding="utf-8")
    css = (ROOT / "static/css/index.css").read_text(encoding="utf-8")

    # 下拉框：工作 / 客服
    assert 'id="chatDutyModeSelect"' in html
    assert '<option value="work" selected>工作</option>' in html
    assert '<option value="service">客服</option>' in html
    assert ".chat-duty-mode select" in css

    # 逻辑：默认工作；客服模式把指令包一层，非客服问题不执行
    assert "function chatDutyMode(" in js
    assert "function applyChatDutyModeToRequest(" in js
    assert "function initChatDutyMode(" in js
    assert "【客服模式】" in js
    assert "只处理客服问题" in js
    assert "要安排工作请把输入框左边的下拉切回「工作」" in js

    # 请求：message 用包装后的文本，并带上 duty_mode
    assert "var requestMessage = applyChatDutyModeToRequest(message);" in js
    assert "message: requestMessage," in js
    assert "duty_mode: dutyMode," in js

    # 初始化 + 记住选择
    assert "initChatDutyMode();" in js
    assert "lobster_chat_duty_mode" in js


def test_customer_service_faq_is_shipped():
    faq = (ROOT / "static/data/customer-service-faq.md").read_text(encoding="utf-8")
    assert "客服百问百答" in faq
    assert faq.count("**Q") >= 100
    for keyword in ("同城爆款", "积分", "方案记录", "客服模式", "H5", "OTA"):
        assert keyword in faq
    assert (ROOT / "docs/客服百问百答.md").read_text(encoding="utf-8") == faq
