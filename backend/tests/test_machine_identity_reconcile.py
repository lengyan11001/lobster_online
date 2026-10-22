# -*- coding: utf-8 -*-
"""machine-identity is the only identity endpoint the client calls at boot."""
from backend.app.api import settings_api
from backend.app.services import installation_identity as mod


def _seed(tmp_path, monkeypatch, machine="m1a0474c3a3dcc8b"):
    path = tmp_path / "installation_identity.json"
    monkeypatch.setattr(mod, "INSTALLATION_IDENTITY_FILE", path)
    monkeypatch.setattr(mod, "_fallback_file", lambda: tmp_path / "fallback.json")
    mod.write_installation_identity("u260-" + "a" * 32, "a" * 32, machine_instance_id=machine)
    return path


def test_machine_identity_reconciles_identity_file(tmp_path, monkeypatch):
    path = _seed(tmp_path, monkeypatch)
    hardware = "mw" + "b" * 48
    monkeypatch.setattr(settings_api, "_get_or_create_machine_instance_id", lambda: hardware)

    resp = settings_api.get_machine_identity()

    assert resp.machine_instance_id == hardware
    loaded = mod.read_installation_identity()
    assert loaded["installation_id"] == "u260-" + "a" * 32
    assert loaded["machine_instance_id"] == hardware
    assert loaded["legacy_machine_instance_id"] == "m1a0474c3a3dcc8b"
    assert path.is_file()


def test_machine_identity_is_noop_when_already_aligned(tmp_path, monkeypatch):
    hardware = "mw" + "c" * 48
    path = _seed(tmp_path, monkeypatch, machine=hardware)
    monkeypatch.setattr(settings_api, "_get_or_create_machine_instance_id", lambda: hardware)

    settings_api.get_machine_identity()

    loaded = mod.read_installation_identity()
    assert loaded["machine_instance_id"] == hardware
    assert loaded["legacy_machine_instance_id"] == ""
