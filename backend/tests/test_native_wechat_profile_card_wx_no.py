"""资料卡取号：只认"微信号"标签的取值，纯字母昵称不能被当成微信号。"""
from __future__ import annotations

from backend.app.services import native_wechat_engine as engine


class _FakeNode:
    def __init__(self, class_name: str, name: str) -> None:
        self.ClassName = class_name
        self.Name = name


def _nodes(*entries: tuple[str, str]) -> list[_FakeNode]:
    return [_FakeNode(class_name, name) for class_name, name in entries]


def _extract(monkeypatch, entries: list[tuple[str, str]]) -> str:
    walk = _nodes(*entries)
    monkeypatch.setattr(
        engine, "_uia_walk", lambda root, *, max_depth=16, max_nodes=600: list(walk)
    )
    return engine._extract_contact_profile_wx_no(object())


def _three_field_card(nickname: str, wx_no: str = "AIZhang7891") -> list[tuple[str, str]]:
    """昵称 / 微信号 / 地区 三个字段的资料卡（张深根那种版式）。"""
    return [
        ("mmui::ProfileUniquePop", "Weixin"),
        ("mmui::ContactHeadView", "张深根-AI三域营销运营1"),
        ("mmui::ContactProfileBottomButton", "发消息"),
        ("mmui::XTextView", "张深根-AI三域营销运营1"),
        ("mmui::XTextView", "发消息"),
        ("mmui::XButton", "更多"),
        ("mmui::XTextView", "昵称："),
        ("mmui::ContactProfileTextView", nickname),
        ("mmui::XTextView", "微信号："),
        ("mmui::ContactProfileTextView", wx_no),
        ("mmui::XTextView", "地区："),
        ("mmui::ContactProfileTextView", "广东 深圳"),
        ("mmui::XTextView", "朋友圈"),
        ("mmui::XMouseEventView", "朋友圈"),
        ("mmui::XTextView", "备注"),
        ("mmui::XMouseEventView", "张深根-AI三域营销运营1"),
    ]


def test_all_letter_nickname_is_not_returned_as_wechat_id(monkeypatch):
    # 回归：以前"头像区之后第一个不含中文的资料文本"被当成微信号，
    # 纯字母昵称（Awareness 这种）会把真正的微信号顶掉。
    assert _extract(monkeypatch, _three_field_card("Awareness")) == "AIZhang7891"


def test_short_all_letter_nickname_is_not_returned_as_wechat_id(monkeypatch):
    # Jason 这种短昵称会被格式校验拒掉，以前表现为"资料卡上有微信号却读不到"。
    assert _extract(monkeypatch, _three_field_card("Jason")) == "AIZhang7891"


def test_chinese_nickname_still_returns_wechat_id(monkeypatch):
    assert _extract(monkeypatch, _three_field_card("张深根-AI三域营销运营")) == "AIZhang7891"


def test_card_without_nickname_field_returns_wechat_id(monkeypatch):
    """澳洲专线物流那种版式：没有昵称行。"""
    entries = [
        ("mmui::ProfileUniquePop", "Weixin"),
        ("mmui::ContactHeadView", "澳洲专线物流"),
        ("mmui::XTextView", "澳洲专线物流"),
        ("mmui::XTextView", "微信号："),
        ("mmui::ContactProfileTextView", "ID20010218"),
        ("mmui::XTextView", "地区："),
        ("mmui::ContactProfileTextView", "广东 深圳"),
        ("mmui::XTextView", "备注"),
        ("mmui::XLineField", "添加备注名"),
        ("mmui::XTextView", "来源"),
        ("mmui::ContactProfileTextView", "通过扫一扫添加"),
    ]
    assert _extract(monkeypatch, entries) == "ID20010218"


def test_missing_wechat_id_value_does_not_fall_back_to_region(monkeypatch):
    entries = [
        ("mmui::ContactHeadView", "某人"),
        ("mmui::XTextView", "微信号："),
        ("mmui::XTextView", "地区："),
        ("mmui::ContactProfileTextView", "广东 深圳"),
    ]
    assert _extract(monkeypatch, entries) == ""


def test_label_and_value_in_one_node(monkeypatch):
    entries = [
        ("mmui::ContactHeadView", "某人"),
        ("mmui::XTextView", "微信号：ID20010218"),
        ("mmui::XTextView", "地区："),
    ]
    assert _extract(monkeypatch, entries) == "ID20010218"


def test_explicit_wxid_wins(monkeypatch):
    entries = [
        ("mmui::XTextView", "昵称："),
        ("mmui::ContactProfileTextView", "安雅"),
        ("mmui::XTextView", "微信号："),
        ("mmui::ContactProfileTextView", "wxid_f4q5yr3t6af021"),
    ]
    assert _extract(monkeypatch, entries) == "wxid_f4q5yr3t6af021"


def test_all_letter_name_without_wechat_id_field_returns_nothing(monkeypatch):
    entries = [
        ("mmui::ContactHeadView", "Awareness"),
        ("mmui::XTextView", "Awareness"),
        ("mmui::ContactProfileTextView", "Awareness"),
        ("mmui::XTextView", "地区："),
        ("mmui::ContactProfileTextView", "广东 深圳"),
    ]
    assert _extract(monkeypatch, entries) == ""
