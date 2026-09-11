"""回复语种判定：中文消息里夹英文产品词不能被判成英文。"""
from __future__ import annotations

import pytest

from backend.app.services import native_wechat_engine as engine


@pytest.mark.parametrize(
    "text",
    [
        "你好要1000件有logo的polo衫",
        "要1000件有logo的polo衫",
        "polo衫要1000件",
        "帮我看看这个logo",
        "你好",
        "OK，谢谢",
        "1000件",
    ],
)
def test_chinese_message_with_english_product_words_stays_chinese(text):
    assert engine._detect_auto_reply_language(text) == "zh-CN"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("logo", "en"),
        ("Hello", "en"),
        ("1000 pcs polo shirt", "en"),
        ("Can you quote 1000 polo shirts with logo?", "en"),
        ("Ok", "en"),
    ],
)
def test_plain_english_stays_english(text, expected):
    assert engine._detect_auto_reply_language(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("안녕하세요", "ko"),
        ("こんにちは", "ja"),
        ("สวัสดี", "th"),
        ("مرحبا", "ar"),
        ("привет", "ru"),
    ],
)
def test_other_scripts_unchanged(text, expected):
    assert engine._detect_auto_reply_language(text) == expected


def test_english_reply_is_blocked_for_a_chinese_customer():
    detected = engine._detect_auto_reply_language("你好要1000件有logo的polo衫")
    assert detected == "zh-CN"
    assert engine._auto_reply_language_matches(
        "Got it - 1,000 polo shirts with your logo. To quote it properly I need a few things:", detected
    ) is False
    assert engine._auto_reply_language_matches("好的，我这边帮您确认一下细节。", detected) is True


def test_english_message_still_accepts_english_reply():
    detected = engine._detect_auto_reply_language("Can you quote 1000 polo shirts with logo?")
    assert detected == "en"
    assert engine._auto_reply_language_matches("Sure, I can quote that for you.", detected) is True
