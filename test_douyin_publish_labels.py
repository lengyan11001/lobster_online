from __future__ import annotations

from skills.douyin_publish import driver as d


def test_all_label_types_have_code_and_display_name():
    assert set(d._DOUYIN_LABEL_CODES) == set(d._DOUYIN_LABEL_DISPLAY)
    assert set(d._DOUYIN_LABEL_CODES) == {
        "location",
        "groupbuy",
        "cart",
        "movie",
        "miniapp",
        "gamepad",
        "soda_playlist",
        "mark_all",
    }


def test_label_type_aliases():
    assert d._douyin_normalize_label_type("小黄车") == "cart"
    assert d._douyin_normalize_label_type("shopping-cart") == "cart"
    assert d._douyin_normalize_label_type("影视") == "movie"
    assert d._douyin_normalize_label_type("mini_app") == "miniapp"
    assert d._douyin_normalize_label_type("汽水单曲") == "soda_playlist"
    assert d._douyin_normalize_label_type("标记万物") == "mark_all"
    assert d._douyin_normalize_label_type("unknown") == ""


def test_label_options_prefers_explicit_douyin_label():
    opts = {
        "douyin": {"douyin_label": {"type": "movie", "keyword": "少年的你"}},
        "douyin_label": {"type": "cart", "link": "https://example.com/item/1"},
    }
    assert d._douyin_label_options(opts) == {
        "type": "cart",
        "link": "https://example.com/item/1",
    }


def test_label_options_accepts_list_and_douyin_anchor():
    opts = {"douyin_anchor": [{"type": "mark_all", "keyword": "手机"}]}
    assert d._douyin_label_options(opts) == {"type": "mark_all", "keyword": "手机"}


def test_goods_alias_maps_link_to_cart_and_keyword_to_mark_all():
    assert d._douyin_label_options({"goods": {"link": "https://x/1"}}) == {
        "type": "cart",
        "link": "https://x/1",
    }
    assert d._douyin_label_options({"goods": {"keyword": "手机"}}) == {
        "type": "mark_all",
        "keyword": "手机",
    }


def test_location_string_alias():
    assert d._douyin_label_options({"location": "深圳市南山区"}) == {
        "type": "location",
        "keyword": "深圳市南山区",
    }


def test_enabled_false_is_preserved_for_caller_to_skip():
    assert d._douyin_label_options({"douyin_label": {"enabled": False}}) == {
        "enabled": False
    }


def test_short_label_title_truncates_to_ten_chars():
    assert d._douyin_short_label_title("美食火锅潮汕牛肉火锅2人餐") == "美食火锅潮汕牛肉火锅"
    assert d._douyin_short_label_title("  双人火锅  \n￥59.6") == "双人火锅"
    assert len(d._douyin_short_label_title("a" * 20)) == 10


def test_cart_label_keeps_required_short_title():
    opts = {
        "douyin_label": {
            "type": "cart",
            "link": "https://haohuo.jinritemai.com/ecommerce/trade/detail/index.html?id=1",
            "short_title": "去油抹布",
        }
    }
    label = d._douyin_label_options(opts)
    assert label["type"] == "cart"
    assert label["short_title"] == "去油抹布"
