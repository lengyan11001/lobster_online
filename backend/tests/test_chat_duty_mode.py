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
    # 客服模式不再在前端做判断/隔离：只把 duty_mode 交给服务端（服务端注入客服百问百答）
    assert "【客服模式】" not in js
    assert "只处理客服问题" not in js
    assert "return String(message || '');" in js

    # 请求：message 用包装后的文本，并带上 duty_mode
    assert "var requestMessage = applyChatDutyModeToRequest(message);" in js
    assert "message: requestMessage," in js
    # 工作模式请求体保持原样：只有客服模式才带 duty_mode
    assert "if (dutyMode === 'service') body.duty_mode = 'service';" in js
    assert "CHAT_DUTY_PLACEHOLDER_BACKUP" in js

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

def test_mastra_chat_also_has_duty_mode_and_shows_view_on_session_click():
    """点输入框后进入的 Mastra 对话也必须有「范围」下拉；左侧点会话要先显示对话页。"""
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "static/js/mastra-chat.js").read_text(encoding="utf-8")

    assert 'id="onlineMastraDutyModeSelect"' in html
    assert "function dutyMode(" in js
    assert "function applyDutyModeToContent(" in js
    assert "function initDutyMode(" in js
    assert "if (dutyModeValue === 'service') payload.duty_mode = 'service';" in js
    assert "DUTY_PLACEHOLDER_BACKUP" in js
    assert "initDutyMode();" in js

    # 两个下拉共用同一个选择（首页 composer 与 Mastra composer）
    assert "el('chatDutyModeSelect')" in js
    assert "lobster_chat_duty_mode" in js

    # 不在首页时点左侧会话：先把对话页显示出来，再切会话/加载历史
    assert "function ensureChatPageVisible(" in js
    assert "return ensureChatPageVisible().then(apply);" in js
    assert "showAppView('chat')" in js


def test_home_and_mastra_duty_selects_stay_in_sync():
    js_chat = (ROOT / "static/js/chat.js").read_text(encoding="utf-8")
    js_mastra = (ROOT / "static/js/mastra-chat.js").read_text(encoding="utf-8")

    assert "var mastraSelect = document.getElementById('onlineMastraDutyModeSelect');" in js_chat
    assert "dutyModeSelects().forEach(function (other) { other.value = value; });" in js_mastra
