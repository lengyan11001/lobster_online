import json
import sys
from pathlib import Path

sys.path.insert(0, r"D:\lobster_online")
from backend.app.services import installation_identity as identity


def _seed(path: Path, iid: str, seed: str = "seed-0001"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "installation_id": iid, "device_seed": seed}), encoding="utf-8")


def _use(tmp_path, monkeypatch):
    path = tmp_path / "data" / "installation_identity.json"
    monkeypatch.setattr(identity, "INSTALLATION_IDENTITY_FILE", path)
    monkeypatch.setattr(identity, "_LAST_READ_SIGNATURE", None)
    return path


def test_same_user_signed_slot_is_immutable(tmp_path, monkeypatch):
    path = _use(tmp_path, monkeypatch)
    existing = "u260-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    _seed(path, existing)
    result = identity.write_installation_identity(
        "u260-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "seed-0001", source="server_sign"
    )
    assert result["ignored"] is True
    assert result["installation_id"] == existing
    assert json.loads(path.read_text(encoding="utf-8"))["installation_id"] == existing


def test_different_user_may_replace_slot(tmp_path, monkeypatch):
    path = _use(tmp_path, monkeypatch)
    _seed(path, "u260-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    result = identity.write_installation_identity(
        "u999-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "seed-0001", source="account_switch"
    )
    assert result.get("ignored") is not True
    assert result["installation_id"].startswith("u999-")
    assert json.loads(path.read_text(encoding="utf-8"))["installation_id"].startswith("u999-")


def test_unsigned_cannot_replace_signed_slot(tmp_path, monkeypatch):
    path = _use(tmp_path, monkeypatch)
    existing = "u260-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    _seed(path, existing)
    result = identity.write_installation_identity("seed-0001", "seed-0001", source="legacy")
    assert result["ignored"] is True
    assert result["installation_id"] == existing


def test_first_signed_write_wins_over_unsigned_file(tmp_path, monkeypatch):
    path = _use(tmp_path, monkeypatch)
    _seed(path, "seed-0001", "seed-0001")
    result = identity.write_installation_identity(
        "u260-cccccccccccccccccccccccccccccccc", "seed-0001", source="first_sign"
    )
    assert result.get("ignored") is not True
    assert result["installation_id"].startswith("u260-")


def test_missing_file_accepts_signed_slot(tmp_path, monkeypatch):
    path = _use(tmp_path, monkeypatch)
    result = identity.write_installation_identity(
        "u260-dddddddddddddddddddddddddddddddd", "seed-0001", source="first_launch"
    )
    assert result.get("ignored") is not True
    assert path.is_file()
