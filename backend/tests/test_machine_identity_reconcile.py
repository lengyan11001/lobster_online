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
# -*- coding: utf-8 -*-
"""POST without a hardware machine id must let the server fill it, not write the stale one."""
from backend.app.api import settings_api
from backend.app.services import installation_identity as mod


def test_save_without_machine_writes_hardware_id(tmp_path, monkeypatch):
    path = tmp_path / "installation_identity.json"
    monkeypatch.setattr(mod, "INSTALLATION_IDENTITY_FILE", path)
    monkeypatch.setattr(mod, "_fallback_file", lambda: tmp_path / "fallback.json")
    mod.write_installation_identity(
        "u260-" + "a" * 32, "a" * 32, machine_instance_id="m1a0474c3a3dcc8b"
    )
    hardware = "mw" + "e" * 48
    monkeypatch.setattr(settings_api, "_get_or_create_machine_instance_id", lambda: hardware)

    body = settings_api.InstallationIdentityRequest(
        installation_id="u260-" + "a" * 32,
        device_seed="a" * 32,
        machine_instance_id="",
        source="boot_persist",
    )
    result = settings_api.save_installation_identity(body)

    assert result.get("ok") is True
    loaded = mod.read_installation_identity()
    assert loaded["machine_instance_id"] == hardware
    assert loaded["legacy_machine_instance_id"] == "m1a0474c3a3dcc8b"
    assert loaded["installation_id"] == "u260-" + "a" * 32

def test_machine_change_accumulates_legacy_ids(tmp_path, monkeypatch):
    path = tmp_path / "installation_identity.json"
    monkeypatch.setattr(mod, "INSTALLATION_IDENTITY_FILE", path)
    monkeypatch.setattr(mod, "_fallback_file", lambda: tmp_path / "fallback.json")
    iid = "u260-" + "a" * 32
    first = "mw" + "a" * 48
    second = "mw" + "b" * 48
    mod.write_installation_identity(iid, "a" * 32, machine_instance_id=first)
    mod.write_installation_identity(iid, "a" * 32, machine_instance_id=second)

    loaded = mod.read_installation_identity()
    assert loaded["machine_instance_id"] == second
    assert set(loaded["legacy_machine_instance_id"].split(",")) == {first}

    third = "mw" + "c" * 48
    mod.write_installation_identity(iid, "a" * 32, machine_instance_id=third)
    loaded = mod.read_installation_identity()
    assert loaded["machine_instance_id"] == third
    assert set(loaded["legacy_machine_instance_id"].split(",")) == {first, second}
