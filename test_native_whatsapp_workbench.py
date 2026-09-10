from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.services import native_whatsapp_engine as engine


@pytest.fixture()
def isolated_whatsapp_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(engine, "STATE_DIR", tmp_path)
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(engine, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(engine, "LOG_PATH", tmp_path / "native_whatsapp.jsonl")
    engine._release_action()
    yield
    engine._release_action()


def test_session_snapshot_persists_messages_and_filters(isolated_whatsapp_state):
    peer = engine._persist_session_snapshot(
        {
            "peer_name": "NplusTech",
            "is_group": False,
            "messages": [
                {"text": "Hello", "direction": "inbound"},
                {"text": "Hi, how can I help?", "direction": "outbound"},
            ],
            "last_message": {"text": "Hi, how can I help?", "direction": "outbound"},
        }
    )

    sessions = engine.list_sessions(limit=20, offset=0, keyword="Nplus")
    assert sessions["total"] == 1
    assert sessions["items"][0]["last_direction"] == "outbound"

    messages = engine.list_messages(peer["peer_key"], limit=20, offset=0)
    assert messages["total"] == 2
    assert [item["direction"] for item in messages["items"]] == ["inbound", "outbound"]


def test_contacts_and_operations_are_searchable(isolated_whatsapp_state):
    engine._persist_contact(
        {
            "display_name": "Alice Chen",
            "first_name": "Alice",
            "last_name": "Chen",
            "username": "alice_wa",
            "phone": "+8613800138000",
        }
    )
    engine._record_operation("add_contact", "alice_wa", "success", "saved")

    contacts = engine.list_contacts(limit=20, offset=0, keyword="alice_wa")
    records = engine.list_operations(limit=20, offset=0, keyword="add_contact")

    assert contacts["total"] == 1
    assert contacts["items"][0]["phone"] == "+8613800138000"
    assert records["total"] == 1
    assert records["items"][0]["status"] == "success"


def test_desktop_actions_share_one_lock(isolated_whatsapp_state):
    engine._claim_action("sync")
    with pytest.raises(RuntimeError, match="sync正在执行"):
        engine._claim_action("send")
    engine._release_action()
    engine._claim_action("send")
    engine._release_action()
