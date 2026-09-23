import pytest

from backend.app.services.asset_ai_tags import parse_ai_tag_text


def test_parse_json_tags():
    assert parse_ai_tag_text('{"tags":["\u6d77\u8fb9","\u9632\u6652","\u590f\u65e5"]}') == "\u6d77\u8fb9,\u9632\u6652,\u590f\u65e5"


def test_parse_markdown_and_chinese_commas():
    text = '```json\n{"tags":"\u6d77\u8fb9\uff0c\u9632\u6652\uff0c\u590f\u65e5"}\n```'
    assert parse_ai_tag_text(text) == "\u6d77\u8fb9,\u9632\u6652,\u590f\u65e5"


def test_parse_dedupes_and_keeps_three():
    assert parse_ai_tag_text('{"tags":["\u6d77\u8fb9","\u6d77\u8fb9","\u9632\u6652","\u590f\u65e5","\u591a\u7684"]}') == "\u6d77\u8fb9,\u9632\u6652,\u590f\u65e5"


def test_parse_rejects_fewer_than_two_tags():
    with pytest.raises(ValueError):
        parse_ai_tag_text('{"tags":["\u53ea\u6709\u4e00\u4e2a"]}')
