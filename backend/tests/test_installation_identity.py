# -*- coding: utf-8 -*-
from backend.app.services import installation_identity as mod


def test_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "installation_identity.json"
    monkeypatch.setattr(mod, "INSTALLATION_IDENTITY_FILE", path)
    assert mod.read_installation_identity()["present"] is False
    written = mod.write_installation_identity(
        "u260-971d45b5a53a1db5e1a25effeee21911",
        "971d45b5a53a1db5e1a25effeee21911",
    )
    assert written["present"] is True
    loaded = mod.read_installation_identity()
    assert loaded["installation_id"] == "u260-971d45b5a53a1db5e1a25effeee21911"
    assert loaded["device_seed"] == "971d45b5a53a1db5e1a25effeee21911"


def test_unsigned_second_write_is_ignored(tmp_path, monkeypatch):
    path = tmp_path / "installation_identity.json"
    monkeypatch.setattr(mod, "INSTALLATION_IDENTITY_FILE", path)
    first = mod.write_installation_identity("a" * 32, "a" * 32)
    second = mod.write_installation_identity("b" * 32, "b" * 32)
    assert second["ignored"] is True
    assert second["installation_id"] == first["installation_id"]
    assert mod.read_installation_identity()["installation_id"] == "a" * 32


def test_signed_slot_can_replace_unsigned(tmp_path, monkeypatch):
    path = tmp_path / "installation_identity.json"
    monkeypatch.setattr(mod, "INSTALLATION_IDENTITY_FILE", path)
    mod.write_installation_identity("a" * 32, "a" * 32)
    signed = mod.write_installation_identity("u260-" + "c" * 32, "c" * 32)
    assert signed["installation_id"] == "u260-" + "c" * 32
    assert mod.read_installation_identity()["installation_id"] == "u260-" + "c" * 32
