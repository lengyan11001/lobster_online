# -*- coding: utf-8 -*-
"""Reading the identity file must not keep a stale image-baked machine id."""
from backend.app.api import settings_api
from backend.app.services import installation_identity as mod


def test_read_reconciles_stale_machine_id(tmp_path, monkeypatch):
    path = tmp_path / "installation_identity.json"
    monkeypatch.setattr(mod, "INSTALLATION_IDENTITY_FILE", path)
    monkeypatch.setattr(mod, "_fallback_file", lambda: tmp_path / "fallback.json")
    mod.write_installation_identity(
        "u260-" + "a" * 32, "a" * 32, machine_instance_id="m1a0474c3a3dcc8b"
    )

    hardware = "mw" + "b" * 48
    monkeypatch.setattr(settings_api, "_get_or_create_machine_instance_id", lambda: hardware)

    data = settings_api.get_installation_identity()

    assert data["machine_instance_id"] == hardware
    assert data["legacy_machine_instance_id"] == "m1a0474c3a3dcc8b"
    loaded = mod.read_installation_identity()
    assert loaded["machine_instance_id"] == hardware
    assert loaded["legacy_machine_instance_id"] == "m1a0474c3a3dcc8b"


def test_read_keeps_file_when_machine_matches(tmp_path, monkeypatch):
    path = tmp_path / "installation_identity.json"
    monkeypatch.setattr(mod, "INSTALLATION_IDENTITY_FILE", path)
    monkeypatch.setattr(mod, "_fallback_file", lambda: tmp_path / "fallback.json")
    hardware = "mw" + "c" * 48
    mod.write_installation_identity(
        "u260-" + "d" * 32, "d" * 32, machine_instance_id=hardware
    )
    monkeypatch.setattr(settings_api, "_get_or_create_machine_instance_id", lambda: hardware)

    data = settings_api.get_installation_identity()

    assert data["machine_instance_id"] == hardware
    assert data["legacy_machine_instance_id"] == ""
