"""加好友支持任意国家码（2026-09-21：客户机报「仅确认支持中国 +86」）。"""
from __future__ import annotations

import os
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import native_whatsapp_engine as engine  # noqa: E402


@pytest.mark.parametrize(
    "text,digits,expected",
    [
        ("\U0001F1EE\U0001F1F9 意大利 Italia +39", "39", True),
        ("\U0001F1FA\U0001F1F8 美国 United States +1", "1", True),
        ("\U0001F1ED\U0001F1F0 中国香港特别行政区 +852", "852", True),
        ("\U0001F1E8\U0001F1F3 中国", "86", True),
        ("国家/地区：中国 +86", "86", True),
        ("国家/地区：Italia +39", "39", True),
        ("\U0001F1FA\U0001F1F8 美国 +1", "12", False),
        ("\U0001F1FA\U0001F1F8 美国 +12", "12", True),
        ("", "86", False),
        ("\U0001F1E8\U0001F1F3 中国", "39", False),
    ],
)
def test_country_item_matches(text, digits, expected):
    assert engine._country_item_matches(text, digits) is expected


@pytest.mark.parametrize(
    "line,country,phone",
    [
        ("liuxin,+8618124655127", "+86", "18124655127"),
        ("liuxin,+393311234567", "+39", "3311234567"),
        ("liuxin,+14155552671", "+1", "4155552671"),
        ("liuxin,18124655127", "+86", "18124655127"),
    ],
)
def test_parse_target_line_keeps_country_and_local_number(line, country, phone):
    target = engine._parse_target_line(line)
    assert target["country_code"] == country
    assert target["phone"] == phone


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+86", "86"),
        ("86", "86"),
        ("+1", "1"),
        ("+39", "39"),
        ("+852", "852"),
        ("+8618124655127", "86"),
        ("8618124655127", "86"),
        ("", "86"),
        ("abc", "86"),
    ],
)
def test_normalize_country_digits(raw, expected):
    assert engine._normalize_country_digits(raw) == expected


def _fake_iter_nodes(add_button):
    def stub(root, *, max_depth=24, max_nodes=2400):
        if max_nodes == 10000:
            return [(add_button, 1)]
        return []

    return stub


def test_add_contact_switches_country_instead_of_rejecting(monkeypatch):
    """+39 这类国家码不该再被拒绝，而是先把表单国家切过去。"""
    button = types.SimpleNamespace(kind="ButtonControl", text="添加联系人", rect=(0, 0, 10, 10))
    first_field = types.SimpleNamespace(kind="EditControl", text="名字", rect=(0, 0, 10, 10))
    phone_field = types.SimpleNamespace(kind="EditControl", text="电话号码", rect=(0, 0, 10, 10))
    calls = {"country": [], "edits": []}

    monkeypatch.setattr(engine, "_claim_action", lambda action: None)
    monkeypatch.setattr(engine, "_release_action", lambda: None)
    monkeypatch.setattr(engine, "_window_or_raise", lambda: (123, {"title": "WhatsApp"}))
    monkeypatch.setattr(engine, "_open_new_chat_page", lambda hwnd: None)
    monkeypatch.setattr(engine, "_root_for_hwnd", lambda hwnd: "root")
    monkeypatch.setattr(engine, "_iter_nodes", _fake_iter_nodes(button))
    monkeypatch.setattr(engine, "_node_type", lambda node: getattr(node, "kind", ""))
    monkeypatch.setattr(engine, "_node_text", lambda node: getattr(node, "text", ""))
    monkeypatch.setattr(engine, "_rect", lambda node: getattr(node, "rect", None))
    monkeypatch.setattr(engine, "_click", lambda node: None)
    monkeypatch.setattr(
        engine,
        "_collect_contact_form_fields",
        lambda root: {"first_name": first_field, "phone": phone_field},
    )
    monkeypatch.setattr(
        engine,
        "select_contact_country",
        lambda hwnd, code: calls["country"].append(str(code)) or "国家/地区：Italia +39",
    )
    monkeypatch.setattr(engine, "_set_edit_text", lambda node, value: calls["edits"].append((node.text, value)))
    monkeypatch.setattr(engine, "_contact_form_hint", lambda root: "")
    monkeypatch.setattr(engine, "_click_named", lambda root, names, **kwargs: True)
    monkeypatch.setattr(engine, "_persist_contact", lambda payload: payload)
    monkeypatch.setattr(engine, "_record_operation", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine, "_dismiss_contact_form", lambda hwnd: None)

    result = engine.add_contact(first_name="Li", phone="3311234567", country_code="+39")

    assert result["ok"] is True
    assert calls["country"] == ["39"]
    assert ("名字", "Li") in calls["edits"]
    assert ("电话号码", "3311234567") in calls["edits"]
    assert result["contact"]["country_code"] == "+39"
    assert result["contact"]["phone"] == "+393311234567"


def test_add_contact_strips_duplicated_country_prefix(monkeypatch):
    button = types.SimpleNamespace(kind="ButtonControl", text="添加联系人", rect=(0, 0, 10, 10))
    first_field = types.SimpleNamespace(kind="EditControl", text="名字", rect=(0, 0, 10, 10))
    phone_field = types.SimpleNamespace(kind="EditControl", text="电话号码", rect=(0, 0, 10, 10))
    edits = []

    monkeypatch.setattr(engine, "_claim_action", lambda action: None)
    monkeypatch.setattr(engine, "_release_action", lambda: None)
    monkeypatch.setattr(engine, "_window_or_raise", lambda: (123, {"title": "WhatsApp"}))
    monkeypatch.setattr(engine, "_open_new_chat_page", lambda hwnd: None)
    monkeypatch.setattr(engine, "_root_for_hwnd", lambda hwnd: "root")
    monkeypatch.setattr(engine, "_iter_nodes", _fake_iter_nodes(button))
    monkeypatch.setattr(engine, "_node_type", lambda node: getattr(node, "kind", ""))
    monkeypatch.setattr(engine, "_node_text", lambda node: getattr(node, "text", ""))
    monkeypatch.setattr(engine, "_rect", lambda node: getattr(node, "rect", None))
    monkeypatch.setattr(engine, "_click", lambda node: None)
    monkeypatch.setattr(
        engine,
        "_collect_contact_form_fields",
        lambda root: {"first_name": first_field, "phone": phone_field},
    )
    monkeypatch.setattr(engine, "select_contact_country", lambda hwnd, code: "国家/地区：中国 +86")
    monkeypatch.setattr(engine, "_set_edit_text", lambda node, value: edits.append((node.text, value)))
    monkeypatch.setattr(engine, "_contact_form_hint", lambda root: "")
    monkeypatch.setattr(engine, "_click_named", lambda root, names, **kwargs: True)
    monkeypatch.setattr(engine, "_persist_contact", lambda payload: payload)
    monkeypatch.setattr(engine, "_record_operation", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine, "_dismiss_contact_form", lambda hwnd: None)

    engine.add_contact(first_name="张", phone="+8618124655127")

    assert ("电话号码", "18124655127") in edits


def test_frontend_no_longer_says_china_only():
    html = (ROOT / "static" / "views" / "personal-whatsapp.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "js" / "personal-whatsapp.js").read_text(encoding="utf-8")
    assert "只支持 +86" not in html
    assert "只支持中国大陆 +86" not in html
    assert "+86 / +1 / +39" in html
    assert "+86 / +1 / +39" in js
    registry = (ROOT / "static" / "js" / "view-registry.js").read_text(encoding="utf-8")
    assert "personal-whatsapp-country-v11" in registry
