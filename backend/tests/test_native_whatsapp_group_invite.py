"""WhatsApp 拉群：配置、关键词触发、建群流程（对齐个微）。"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import native_whatsapp_engine as engine  # noqa: E402


def test_group_invite_hit_matches_keywords():
    assert engine.group_invite_hit("可以拉群吗", "建群,拉群")
    assert engine.group_invite_hit("please create a GROUP", "group")
    assert engine.group_invite_hit("拉 群 吧", "拉群, 建群") is False
    assert engine.group_invite_hit("", "建群") is False
    assert engine.group_invite_hit("你好", "") is False


def test_group_config_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "STATE_DIR", tmp_path)
    monkeypatch.setattr(engine, "CONFIG_PATH", tmp_path / "config.json")
    saved = engine.save_config(
        group_invite_enabled=True,
        group_invite_keywords="建群,拉群",
        group_invite_contacts=["hehao", " liuxin "],
        group_invite_group_name="客户群",
        group_invite_welcome_message="欢迎",
        group_invite_memory_doc_id="doc_x",
    )
    assert saved["group_invite_enabled"] is True
    assert saved["group_invite_contacts"] == ["hehao", "liuxin"]
    again = engine.get_config()
    assert again["group_invite_keywords"] == "建群,拉群"
    assert again["group_invite_group_name"] == "客户群"
    engine.save_config(group_invite_enabled=False, group_invite_contacts=[])


def test_create_group_requires_name_and_members():
    try:
        engine.create_group(name="", members=["a"], dry_run=True)
        raise AssertionError("空群名应该报错")
    except RuntimeError as exc:
        assert "群名" in str(exc)
    try:
        engine.create_group(name="群", members=[], dry_run=True)
        raise AssertionError("空成员应该报错")
    except RuntimeError as exc:
        assert "群成员" in str(exc)


def test_group_flow_uses_mouse_click_for_member_rows():
    source = (ROOT / "backend" / "app" / "services" / "native_whatsapp_engine.py").read_text(encoding="utf-8")
    start = source.index("def create_group(")
    body = source[start:start + 5000]
    assert "_click(row[\"node\"], force_mouse=True)" in body, "成员行必须鼠标点击（UIA Invoke 无效）"
    assert '"下一步"' in body and '"创建群组"' in body
    assert "dry_run" in body


def test_frontend_has_group_settings():
    html = (ROOT / "static" / "views" / "personal-whatsapp.html").read_text(encoding="utf-8")
    for control in ("personalWhatsappGroupInviteEnabled", "personalWhatsappGroupInviteMemoryDoc",
                    "personalWhatsappGroupInviteKeywords", "personalWhatsappGroupInviteContacts",
                    "personalWhatsappGroupInviteGroupName", "personalWhatsappGroupInviteWelcome"):
        assert control in html, control
    js = (ROOT / "static" / "js" / "personal-whatsapp.js").read_text(encoding="utf-8")
    assert "group_invite_contacts" in js
    assert "group_invite_keywords" in js
