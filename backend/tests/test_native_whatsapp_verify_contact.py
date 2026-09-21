"""加好友后自动确认是否真的进了联系人（用搜索结果判定）。"""
from __future__ import annotations

import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import native_whatsapp_engine as engine  # noqa: E402


def _node(kind, text, rect):
    return types.SimpleNamespace(kind=kind, text=text, rect=rect)


SEARCH = _node("EditControl", "", (1139, 319, 1325, 346))


def _install(monkeypatch, rows):
    monkeypatch.setattr(engine, "_open_new_chat_page", lambda hwnd: SEARCH)
    monkeypatch.setattr(engine, "_set_edit_text", lambda node, value, **kwargs: None)
    monkeypatch.setattr(engine, "_root_for_hwnd", lambda hwnd: "root")
    monkeypatch.setattr(engine, "_iter_nodes", lambda root, **kwargs: [(node, 1) for node in rows])
    monkeypatch.setattr(engine, "_node_type", lambda node: getattr(node, "kind", ""))
    monkeypatch.setattr(engine, "_node_text", lambda node: getattr(node, "text", ""))
    monkeypatch.setattr(engine, "_rect", lambda node: getattr(node, "rect", None))


def test_reports_found_when_contact_card_shows_up(monkeypatch):
    """实测：已加成功的号码，搜索后先出现带姓名的卡片，然后才是「联系人」分组。"""
    _install(monkeypatch, [
        SEARCH,
        _node("ButtonControl", "hehao", (1144, 381, 1418, 405)),
        _node("DataItemControl", "联系人", (1144, 410, 1418, 430)),
        _node("DataItemControl", "hehao", (1144, 454, 1418, 480)),
    ])
    result = engine.verify_contact_added(123, first_name="hehao", phone="84867403591")
    assert result["checked"] is True
    assert result["found"] is True
    assert result["matched"]


def test_reports_not_found_without_contact_card(monkeypatch):
    _install(monkeypatch, [
        SEARCH,
        _node("DataItemControl", "联系人", (1144, 410, 1418, 430)),
        _node("DataItemControl", "hehao", (1144, 454, 1418, 480)),
    ])
    result = engine.verify_contact_added(123, first_name="nobody", phone="861312312312")
    assert result["checked"] is True
    assert result["found"] is False
    assert result["state"] == "not_found"


def test_username_probe_matches(monkeypatch):
    _install(monkeypatch, [
        SEARCH,
        _node("ButtonControl", "alice", (1144, 381, 1418, 405)),
        _node("DataItemControl", "联系人", (1144, 410, 1418, 430)),
    ])
    result = engine.verify_contact_added(123, first_name="Alice", username="alice")
    assert result["found"] is True


def test_skips_when_no_target(monkeypatch):
    _install(monkeypatch, [SEARCH])
    result = engine.verify_contact_added(123, first_name="who")
    assert result["checked"] is False
    assert "没有可搜索的目标" in result["reason"]


def test_add_contact_records_verify_result():
    source = pathlib.Path(str(ROOT / "backend" / "app" / "services" / "native_whatsapp_engine.py")).read_text(encoding="utf-8")
    start = source.index("def add_contact(")
    body = source[start:start + 6000]
    assert "verify_contact_added(" in body, "加完必须再搜一次确认"
    assert '"verify"' in body


def test_frontend_auto_refreshes_running_records():
    js = (ROOT / "static" / "js" / "personal-whatsapp.js").read_text(encoding="utf-8")
    assert "scheduleFriendRecordRefresh" in js
    assert "friendRecordTimer" in js
    registry = (ROOT / "static" / "js" / "view-registry.js").read_text(encoding="utf-8")
    assert "/static/js/personal-whatsapp.js?v=" in registry


def test_reports_not_in_contacts_state(monkeypatch):
    """用户给的截图：注册了但不是好友时，WhatsApp 显示「不在你的联系人中」。"""
    _install(monkeypatch, [
        SEARCH,
        _node("TextControl", "不在你的联系人中", (1090, 400, 1300, 425)),
        _node("DataItemControl", "+84 91 558 17 93", (1144, 454, 1418, 480)),
    ])
    result = engine.verify_contact_added(123, first_name="nobody", phone="84915581793")
    assert result["checked"] is True
    assert result["found"] is False
    assert result["state"] == "not_in_contacts"
    assert result["not_in_contacts"] is True
    assert "不在你的联系人中" in result["note"]


def test_reports_not_found_state(monkeypatch):
    _install(monkeypatch, [SEARCH])
    result = engine.verify_contact_added(123, first_name="nobody", phone="861312312312")
    assert result["state"] == "not_found"
    assert "没注册" in result["note"]


def test_in_contacts_state(monkeypatch):
    _install(monkeypatch, [
        SEARCH,
        _node("ButtonControl", "hehao", (1144, 381, 1418, 405)),
        _node("DataItemControl", "联系人", (1144, 410, 1418, 430)),
    ])
    result = engine.verify_contact_added(123, first_name="hehao", phone="84867403591")
    assert result["state"] == "in_contacts"
    assert result["note"] == ""
