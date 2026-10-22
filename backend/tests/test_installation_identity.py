# -*- coding: utf-8 -*-
from backend.app.services import installation_identity as mod


def test_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "installation_identity.json"
    monkeypatch.setattr(mod, "INSTALLATION_IDENTITY_FILE", path)
    monkeypatch.setattr(mod, "_fallback_file", lambda: tmp_path / "fallback.json")
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
    monkeypatch.setattr(mod, "_fallback_file", lambda: tmp_path / "fallback.json")
    first = mod.write_installation_identity("a" * 32, "a" * 32)
    second = mod.write_installation_identity("b" * 32, "b" * 32)
    assert second["ignored"] is True
    assert second["installation_id"] == first["installation_id"]
    assert mod.read_installation_identity()["installation_id"] == "a" * 32


def test_signed_slot_can_replace_unsigned(tmp_path, monkeypatch):
    path = tmp_path / "installation_identity.json"
    monkeypatch.setattr(mod, "INSTALLATION_IDENTITY_FILE", path)
    monkeypatch.setattr(mod, "_fallback_file", lambda: tmp_path / "fallback.json")
    mod.write_installation_identity("a" * 32, "a" * 32)
    signed = mod.write_installation_identity("u260-" + "c" * 32, "c" * 32)
    assert signed["installation_id"] == "u260-" + "c" * 32
    assert mod.read_installation_identity()["installation_id"] == "u260-" + "c" * 32

def test_fallback_file_restores_slot_when_install_dir_lost(tmp_path, monkeypatch):
    path = tmp_path / "installation_identity.json"
    fallback = tmp_path / "fallback.json"
    monkeypatch.setattr(mod, "INSTALLATION_IDENTITY_FILE", path)
    monkeypatch.setattr(mod, "_fallback_file", lambda: fallback)

    mod.write_installation_identity(
        "u260-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "a" * 32,
        machine_instance_id="m-machine",
    )
    assert fallback.is_file()

    # OTA 把安装目录的 data/ 覆盖掉：主文件没了，备份还在
    path.unlink()
    loaded = mod.read_installation_identity()

    assert loaded["present"] is True
    assert loaded["installation_id"] == "u260-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert loaded["machine_instance_id"] == "m-machine"

def test_ensure_creates_and_reuses_file_identity(tmp_path, monkeypatch):
    path = tmp_path / "installation_identity.json"
    monkeypatch.setattr(mod, "INSTALLATION_IDENTITY_FILE", path)
    monkeypatch.setattr(mod, "_fallback_file", lambda: tmp_path / "fallback.json")

    first = mod.ensure_installation_identity("hikong")
    assert first["present"] is True
    assert first["installation_id"]
    assert first["machine_instance_id"] == ""
    assert first["brand_mark"] == "hikong"
    assert path.is_file()

    second = mod.ensure_installation_identity("hikong")
    assert second["installation_id"] == first["installation_id"]


def test_ensure_uses_legacy_candidate_once(tmp_path, monkeypatch):
    path = tmp_path / "installation_identity.json"
    monkeypatch.setattr(mod, "INSTALLATION_IDENTITY_FILE", path)
    monkeypatch.setattr(mod, "_fallback_file", lambda: tmp_path / "fallback.json")

    created = mod.ensure_installation_identity("bihuo", candidate="u260-" + "d" * 32)

    assert created["installation_id"] == "u260-" + "d" * 32
    assert created["device_seed"] == "d" * 32

def test_force_write_overrides_frozen_slot(tmp_path, monkeypatch):
    path = tmp_path / "installation_identity.json"
    monkeypatch.setattr(mod, "INSTALLATION_IDENTITY_FILE", path)
    monkeypatch.setattr(mod, "_fallback_file", lambda: tmp_path / "fallback.json")

    mod.write_installation_identity("u260-" + "a" * 32, "a" * 32)
    forced = mod.write_installation_identity("u260-" + "b" * 32, "b" * 32, force=True)

    assert forced["installation_id"] == "u260-" + "b" * 32
    assert mod.read_installation_identity()["installation_id"] == "u260-" + "b" * 32

def test_ignored_write_still_backfills_machine_and_brand(tmp_path, monkeypatch):
    path = tmp_path / "installation_identity.json"
    monkeypatch.setattr(mod, "INSTALLATION_IDENTITY_FILE", path)
    monkeypatch.setattr(mod, "_fallback_file", lambda: tmp_path / "fallback.json")

    mod.write_installation_identity("u260-" + "a" * 32, "a" * 32)
    result = mod.write_installation_identity(
        "u260-" + "a" * 32,
        "a" * 32,
        machine_instance_id="machine-a",
        brand_mark="hikong",
    )

    assert result["ignored"] is True
    loaded = mod.read_installation_identity()
    assert loaded["machine_instance_id"] == "machine-a"
    assert loaded["brand_mark"] == "hikong"
