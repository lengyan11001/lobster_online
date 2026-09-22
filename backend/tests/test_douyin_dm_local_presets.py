"""抖音主动私信：H5 不配话术时回落「私信互动」保存的本地话术预设。"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend" / "douyin_origin") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend" / "douyin_origin"))

douyin_api = pytest.importorskip("douyin_api")


def test_payload_messages_win_over_local(monkeypatch):
    monkeypatch.setattr(douyin_api, "load_douyin_local_interaction_messages", lambda: ["本地A"])
    payload = {"messages": ["payload A", "payload B"]}
    assert douyin_api.normalize_douyin_interaction_fixed_messages(payload) == ["payload A", "payload B"]


def test_single_message_field_still_works(monkeypatch):
    monkeypatch.setattr(douyin_api, "load_douyin_local_interaction_messages", lambda: ["本地A"])
    assert douyin_api.normalize_douyin_interaction_fixed_messages({"message": "单条"}) == ["单条"]


def test_falls_back_to_local_presets_when_payload_empty(monkeypatch):
    """这是用户反馈的场景：H5 节点不带话术 → 必须取本地「私信互动」的预设。"""
    monkeypatch.setattr(douyin_api, "load_douyin_local_interaction_messages", lambda: ["本地A", "本地B"])
    assert douyin_api.normalize_douyin_interaction_fixed_messages({}) == ["本地A", "本地B"]


def test_local_presets_are_deduped_and_trimmed(monkeypatch):
    monkeypatch.setattr(
        douyin_api,
        "load_douyin_local_settings",
        lambda: {douyin_api.DOUYIN_INTERACTION_PRESET_KEY: {"activeIndex": 0, "presets": [" 一 ", "", "二", "二", "三"]}},
    )
    assert douyin_api.load_douyin_local_interaction_messages() == ["一", "二", "三"]


def test_empty_settings_yield_no_messages(monkeypatch):
    monkeypatch.setattr(douyin_api, "load_douyin_local_settings", lambda: {})
    assert douyin_api.load_douyin_local_interaction_messages() == []


def test_h5_no_longer_ships_hardcoded_dm_text():
    source = (ROOT / "backend" / "app" / "api" / "h5_chat_channel.py").read_text(encoding="utf-8")
    assert "看到你的内容挺有启发" not in source, "H5 不该再写死私信默认文案"
    assert 'source.get("direct_message") or ""' in source


def test_rotation_uses_all_presets(monkeypatch):
    users = [{"id": index} for index in range(5)]
    douyin_api.assign_douyin_interaction_fixed_messages(users, ["A", "B"])
    assert [user["_interaction_fixed_message"] for user in users] == ["A", "B", "A", "B", "A"]
