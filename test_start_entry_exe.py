import json
from pathlib import Path

from desktop import build_desktop_exe, oem_configurator


def _clear_brand_environment(monkeypatch):
    for name in (
        "LOBSTER_BRAND_MARK",
        "LOBSTER_OEM_CODE",
        "LOBSTER_OEM_BOOTSTRAP_BASE",
        "LOBSTER_DESKTOP_TITLE",
        "AUTH_SERVER_BASE",
        "LOBSTER_BRANDING_UNAVAILABLE",
    ):
        monkeypatch.delenv(name, raising=False)


def _write_build_root(root: Path) -> None:
    registry = {
        "default_mark": "bihuo",
        "marks": {"bihuo": {"install": {"desktop_ico": "static/bihu_box.ico"}}},
    }
    path = root / "static" / "branding" / "brands.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry), encoding="utf-8")
    (root / "static" / "bihu_box.ico").write_bytes(b"ico")
    stub = root / "desktop" / "launcher_stub.cs"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text("// stub", encoding="utf-8")


def _write_oem_profile(root: Path, mark: str, filename: str, payload: bytes | None = None) -> dict:
    cached = root / "static" / "branding" / "cache" / mark / "v1" / "client_launcher.exe"
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(payload if payload is not None else f"brand-launcher-{mark}".encode())
    return {
        "mark": mark,
        "display_name": f"{mark} AI",
        "install": {
            "launcher_exe": f"/static/branding/cache/{mark}/v1/client_launcher.exe",
            "launcher_filename": filename,
        },
    }


def _write_oem_cache_record(root: Path, code: str, mark: str, version: str, start_entry_bytes: bytes | None) -> dict:
    """Fake the cache record oem_branding writes after a bootstrap download."""
    cache_dir = root / "static" / "branding" / "cache" / mark / version
    cache_dir.mkdir(parents=True, exist_ok=True)
    launcher = cache_dir / "client_launcher.exe"
    launcher.write_bytes(b"brand-launcher-" + mark.encode())
    assets = [
        {
            "key": "launcher_exe",
            "relative_path": f"static/branding/cache/{mark}/{version}/client_launcher.exe",
            "size": launcher.stat().st_size,
            "sha256": "0" * 64,
        }
    ]
    if start_entry_bytes is not None:
        entry = cache_dir / "start.exe"
        entry.write_bytes(start_entry_bytes)
        assets.append(
            {
                "key": "start_entry",
                "relative_path": f"static/branding/cache/{mark}/{version}/start.exe",
                "size": entry.stat().st_size,
                "sha256": "0" * 64,
            }
        )
    record = {
        "schema_version": 1,
        "oem_code": code,
        "brand_mark": mark,
        "version": version,
        "checked_at": 0,
        "profile": {"mark": mark},
        "assets": assets,
    }
    record_path = root / "static" / "branding" / "cache" / "profiles" / f"{code}.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record), encoding="utf-8")
    return {
        "mark": mark,
        "install": {
            "launcher_exe": f"/static/branding/cache/{mark}/{version}/client_launcher.exe",
            "launcher_filename": "HikongAI.exe",
        },
        "_oem_code": code,
        "_cache_profile_path": str(record_path),
    }


def test_build_emits_branded_exe_and_unified_start_entry(tmp_path, monkeypatch):
    _clear_brand_environment(monkeypatch)
    _write_build_root(tmp_path)
    payload = b"launcher-shell-bytes"

    def fake_check_call(command, cwd=None):
        target = next(str(item) for item in command if str(item).startswith("/out:"))
        Path(target.split(":", 1)[1]).write_bytes(payload)

    monkeypatch.setattr(build_desktop_exe, "__file__", str(tmp_path / "desktop" / "build_desktop_exe.py"))
    monkeypatch.setattr(build_desktop_exe, "find_csc", lambda: "csc.exe")
    monkeypatch.setattr(build_desktop_exe.subprocess, "check_call", fake_check_call)
    monkeypatch.setenv("LOBSTER_BRAND_MARK", "bihuo")

    assert build_desktop_exe.main() == 0

    branded = tmp_path / f"{build_desktop_exe.APP_NAME}.exe"
    entry = tmp_path / "start.exe"
    assert branded.read_bytes() == payload
    assert entry.read_bytes() == payload
    assert (tmp_path / "dist" / "start.exe").read_bytes() == payload
    assert (tmp_path / "dist" / f"{build_desktop_exe.APP_NAME}.exe").read_bytes() == payload


def test_oem_switch_installs_branded_exe_and_unified_start_entry(tmp_path):
    profile = _write_oem_profile(tmp_path, "hikong", "HikongAI.exe")

    installed = oem_configurator.install_brand_launcher(tmp_path, profile)

    assert installed == tmp_path / "HikongAI.exe"
    assert installed.read_bytes() == b"brand-launcher-hikong"
    assert (tmp_path / "start.exe").read_bytes() == b"brand-launcher-hikong"


def test_oem_switch_refreshes_existing_start_entry(tmp_path):
    profile = _write_oem_profile(tmp_path, "hikong", "HikongAI.exe")
    (tmp_path / "start.exe").write_bytes(b"previous-unified-shell")

    oem_configurator.install_brand_launcher(tmp_path, profile)

    assert (tmp_path / "start.exe").read_bytes() == b"brand-launcher-hikong"
    assert (tmp_path / "HikongAI.exe").read_bytes() == b"brand-launcher-hikong"


def test_oem_switch_prefers_the_delivered_start_entry_asset(tmp_path):
    payload = b"brand-launcher-hikong"
    profile = _write_oem_cache_record(tmp_path, "0400", "hikong", "v1", payload)

    cached = oem_configurator.cached_start_entry_asset(tmp_path, profile)

    assert cached is not None
    assert cached.name == "start.exe"
    assert cached.read_bytes() == payload
    installed = oem_configurator.install_brand_launcher(tmp_path, profile)
    assert installed.read_bytes() == payload
    assert (tmp_path / "start.exe").read_bytes() == payload


def test_oem_switch_ignores_a_stale_delivered_start_entry(tmp_path):
    profile = _write_oem_cache_record(tmp_path, "0400", "hikong", "v1", b"stale-shell-from-server")

    oem_configurator.install_brand_launcher(tmp_path, profile)

    # The branded EXE wins, so the autostart shell can never show another brand's icon.
    assert (tmp_path / "start.exe").read_bytes() == b"brand-launcher-hikong"
    assert (tmp_path / "HikongAI.exe").read_bytes() == b"brand-launcher-hikong"


def test_cached_start_entry_asset_is_optional(tmp_path):
    profile = _write_oem_cache_record(tmp_path, "0400", "hikong", "v1", None)

    assert oem_configurator.cached_start_entry_asset(tmp_path, profile) is None
    oem_configurator.install_brand_launcher(tmp_path, profile)
    assert (tmp_path / "start.exe").read_bytes() == b"brand-launcher-hikong"


def test_oem_switch_keeps_unified_entry_and_drops_previous_brand_shell(tmp_path, monkeypatch):
    profiles = tmp_path / "static" / "branding" / "cache" / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    (profiles / "0100.json").write_text(
        json.dumps(
            {
                "oem_code": "0100",
                "brand_mark": "oldmark",
                "profile": {"mark": "oldmark", "install": {"launcher_filename": "OldBrand.exe"}},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "OldBrand.exe").write_bytes(b"old-brand")
    (tmp_path / "start.exe").write_bytes(b"unified-shell")
    monkeypatch.setattr(oem_configurator, "_desktop_directories", lambda: [tmp_path / "desktop"])
    (tmp_path / "desktop").mkdir()

    current = {"mark": "newmark", "install": {"launcher_filename": "NewBrand.exe"}}
    oem_configurator.cleanup_previous_oem(tmp_path, "0100", "0200", current)

    assert not (tmp_path / "OldBrand.exe").exists()
    assert (tmp_path / "start.exe").read_bytes() == b"unified-shell"


def test_oem_switch_never_deletes_start_entry_filename(tmp_path, monkeypatch):
    profiles = tmp_path / "static" / "branding" / "cache" / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    (profiles / "0100.json").write_text(
        json.dumps(
            {
                "oem_code": "0100",
                "brand_mark": "oldmark",
                "profile": {"mark": "oldmark", "install": {"launcher_filename": "start.exe"}},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "start.exe").write_bytes(b"unified-shell")
    monkeypatch.setattr(oem_configurator, "_desktop_directories", lambda: [tmp_path / "desktop"])
    (tmp_path / "desktop").mkdir()

    current = {"mark": "newmark", "install": {"launcher_filename": "NewBrand.exe"}}
    oem_configurator.cleanup_previous_oem(tmp_path, "0100", "0200", current)

    assert (tmp_path / "start.exe").read_bytes() == b"unified-shell"
