"""发布文案长度统一 + 标签格式归一。"""
from backend.app.services.publish_copy_llm import _platform_copy_limits
from backend.app.api.h5_chat_channel import _clean_publish_tags
from backend.app.api.publish import _sanitize_internal_publish_tags


def test_douyin_copy_uses_short_body():
    for media in ("video", "image"):
        text = _platform_copy_limits("douyin", media)
        assert "40-90" in text
        assert "400 字" not in text
        assert "500 字" not in text


def test_h5_publish_tags_are_comma_separated_without_hash():
    assert _clean_publish_tags("#热点, 干货 #AI") == "热点,干货,AI"
    assert _clean_publish_tags(["a", "b", "a"]) == "a,b"
    assert "#" not in _clean_publish_tags("x,y")


def test_publish_api_normalizes_legacy_hash_tags():
    assert _sanitize_internal_publish_tags("#热点 #干货, AI") == "热点,干货,AI"
    assert _sanitize_internal_publish_tags("sutui.transfer_url, 正常") == "正常"
    assert _sanitize_internal_publish_tags("a，b a") == "a,b"
