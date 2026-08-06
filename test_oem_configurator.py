import base64
import json
from pathlib import Path
import subprocess

import pytest

from desktop import oem_configurator
from scripts import pack_full_project_zip, pack_slim_zip


def _profile(root: Path) -> dict:
    cached = root / "static" / "branding" / "cache" / "hikong" / "v1" / "client_launcher.exe"
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"brand-launcher")
    return {
        "mark": "hikong",
        "display_name": "Hikong AI Agent",
        "install": {
            "launcher_exe": "/static/branding/cache/hikong/v1/client_launcher.exe",
            "launcher_filename": "HikongAI.exe",
        },
    }


def test_factory_config_installs_launcher_writes_code_and_runs_install(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    (tmp_path / ".env").write_text("AUTH_SERVER_BASE=https://brand.example\nLOBSTER_BRAND_MARK=bihuo\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(oem_configurator, "resolve_oem_branding", lambda *args, **kwargs: profile)
    monkeypatch.setattr(oem_configurator, "run_install", lambda root, code: calls.append((root, code)))

    name, launcher = oem_configurator.configure(tmp_path, "0400")

    assert name == "Hikong AI Agent"
    assert launcher == tmp_path / "HikongAI.exe"
    assert launcher.read_bytes() == b"brand-launcher"
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "LOBSTER_BRAND_MARK=0400" in env_text
    assert "LOBSTER_OEM_CODE=0400" in env_text
    assert "AUTH_SERVER_BASE=https://brand.example" in env_text
    assert calls == [(tmp_path, "0400")]


def test_factory_config_rejects_invalid_code_before_download(tmp_path, monkeypatch):
    called = False

    def _resolve(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(oem_configurator, "resolve_oem_branding", _resolve)

    with pytest.raises(RuntimeError, match="4 到 12 位数字"):
        oem_configurator.configure(tmp_path, "hikong")

    assert called is False


def test_factory_switch_removes_previous_brand_shell_only(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    (tmp_path / ".env").write_text(
        "AUTH_SERVER_BASE=https://brand.example\nLOBSTER_BRAND_MARK=0200\nLOBSTER_OEM_CODE=0200\n",
        encoding="utf-8",
    )
    old_cache = tmp_path / "static" / "branding" / "cache" / "daka" / "v1"
    old_cache.mkdir(parents=True)
    (old_cache / "icon.png").write_bytes(b"old-brand")
    old_record = tmp_path / "static" / "branding" / "cache" / "profiles" / "0200.json"
    old_record.parent.mkdir(parents=True, exist_ok=True)
    old_record.write_text(
        json.dumps(
            {
                "oem_code": "0200",
                "brand_mark": "daka",
                "profile": {
                    "mark": "daka",
                    "install": {
                        "launcher_filename": "DakaAI.exe",
                        "shortcut_lnk_name": "DakaAI.lnk",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    old_launcher = tmp_path / "DakaAI.exe"
    old_launcher.write_bytes(b"old-launcher")
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    old_shortcut = desktop / "DakaAI.lnk"
    old_shortcut.write_bytes(b"old-shortcut")
    stale_cache = tmp_path / "static" / "branding" / "cache" / "bihuo" / "v1"
    stale_cache.mkdir(parents=True)
    (stale_cache / "icon.png").write_bytes(b"stale-brand")
    stale_record = tmp_path / "static" / "branding" / "cache" / "profiles" / "0100.json"
    stale_record.write_text(
        json.dumps(
            {
                "oem_code": "0100",
                "brand_mark": "bihuo",
                "profile": {
                    "mark": "bihuo",
                    "install": {"launcher_filename": "BihuoAI.exe"},
                },
            }
        ),
        encoding="utf-8",
    )
    stale_launcher = tmp_path / "BihuoAI.exe"
    stale_launcher.write_bytes(b"stale-launcher")
    business_data = tmp_path / "browser_data" / "session.db"
    business_data.parent.mkdir()
    business_data.write_bytes(b"keep-user-data")

    monkeypatch.setattr(oem_configurator, "resolve_oem_branding", lambda *args, **kwargs: profile)
    monkeypatch.setattr(oem_configurator, "run_install", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(oem_configurator, "_desktop_directories", lambda: [desktop])

    _, launcher = oem_configurator.configure(tmp_path, "0400")

    assert launcher.is_file()
    assert not old_launcher.exists()
    assert not old_shortcut.exists()
    assert not old_cache.parent.exists()
    assert not old_record.exists()
    assert not stale_cache.parent.exists()
    assert not stale_record.exists()
    assert not stale_launcher.exists()
    assert business_data.read_bytes() == b"keep-user-data"


def test_factory_switch_failure_restores_previous_brand(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    original_env = b"LOBSTER_BRAND_MARK=0200\r\nLOBSTER_OEM_CODE=0200\r\nCUSTOM=value\r\n"
    (tmp_path / ".env").write_bytes(original_env)
    cleanup_called = False

    monkeypatch.setattr(oem_configurator, "resolve_oem_branding", lambda *args, **kwargs: profile)

    def _fail_install(*_args, **_kwargs):
        raise RuntimeError("install failed")

    def _cleanup(*_args, **_kwargs):
        nonlocal cleanup_called
        cleanup_called = True

    monkeypatch.setattr(oem_configurator, "run_install", _fail_install)
    monkeypatch.setattr(oem_configurator, "cleanup_previous_oem", _cleanup)

    with pytest.raises(RuntimeError, match="install failed"):
        oem_configurator.configure(tmp_path, "0400")

    assert (tmp_path / ".env").read_bytes() == original_env
    assert cleanup_called is False


def test_factory_install_uses_cached_oem_profile_for_shortcut():
    root = oem_configurator.resolve_root()
    install = (root / "install.bat").read_text(encoding="utf-8")
    shortcut = (root / "scripts" / "create_desktop_shortcut.ps1").read_text(encoding="utf-8")

    assert "static\\branding\\cache\\profiles\\%LOBSTER_BRAND_MARK%.json" in install
    assert '-BrandProfilePath "%LOBSTER_BRAND_PROFILE_PATH%"' in install
    assert "$runtimeCode -eq $m" in shortcut
    assert "$inst.launcher_filename" in shortcut
    assert "if not defined LOBSTER_SKIP_INSTALL_PAUSE pause" in install
    assert "\npause\n" not in install.replace("\r\n", "\n")


def test_factory_configurator_is_included_in_full_factory_packages():
    filename = "OEM配置启动器.exe"

    assert (oem_configurator.resolve_root() / filename).is_file()
    assert not pack_full_project_zip.should_exclude("lobster_online", filename)
    assert not pack_slim_zip.is_excluded(Path("lobster_online") / filename)


def test_factory_full_package_excludes_local_brand_and_runtime_data():
    configurator = pack_full_project_zip.FACTORY_CONFIGURATOR_EXE

    assert not pack_full_project_zip.should_exclude(
        "lobster_online", configurator, factory_oem=True
    )
    assert pack_full_project_zip.should_exclude(
        "lobster_online", "brand-launcher.exe", factory_oem=True
    )
    assert pack_full_project_zip.should_exclude(
        "lobster_online", ".env", factory_oem=True
    )
    assert not pack_full_project_zip.should_exclude(
        "lobster_online", ".env.example", factory_oem=True
    )
    assert pack_full_project_zip.should_exclude(
        "lobster_online", "data/app_state.db", factory_oem=True
    )
    assert pack_full_project_zip.should_exclude(
        "lobster_online", "_probe_apiz_sdk_src/README.md", factory_oem=True
    )
    assert pack_full_project_zip.should_exclude(
        "lobster_online", ".installed", factory_oem=True
    )
    assert pack_full_project_zip.should_exclude(
        "lobster_online", "lobster.db.bak-20260806", factory_oem=True
    )
    assert pack_full_project_zip.should_exclude(
        "lobster_online", "brand-launcher.spec", factory_oem=True
    )
    assert not pack_full_project_zip.should_exclude(
        "lobster_online", "models/sam/sam_vit_b.pth", factory_oem=True
    )
    assert not pack_full_project_zip.should_exclude(
        "lobster_online", "deps/vc_redist.x64.exe", factory_oem=True
    )


def test_factory_oem_code_seeds_env_from_example(tmp_path):
    (tmp_path / ".env.example").write_text(
        "AUTH_SERVER_BASE=https://example.test\nLOBSTER_BRAND_MARK=bihuo\n",
        encoding="utf-8",
    )

    oem_configurator.write_oem_code(tmp_path, "0400")

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "AUTH_SERVER_BASE=https://example.test" in env_text
    assert "LOBSTER_BRAND_MARK=0400" in env_text
    assert "LOBSTER_OEM_CODE=0400" in env_text


def test_factory_scripts_load_with_bundled_python():
    root = oem_configurator.resolve_root()
    runtime = root / "python" / "python.exe"
    if not runtime.is_file():
        pytest.skip("bundled Python is not available")

    configurator = subprocess.run(
        [str(runtime), str(root / "desktop" / "oem_configurator.py"), "--help"],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert configurator.returncode == 0, configurator.stderr

    launcher_probe = (
        "import pathlib, runpy; "
        f"ns=runpy.run_path({str(root / 'desktop' / 'launcher.py')!r}); "
        f"result=ns['resolve_factory_oem_branding'](pathlib.Path({str(root)!r}), 'invalid', 'https://bhzn.top'); "
        "assert result is None"
    )
    launcher = subprocess.run(
        [str(runtime), "-c", launcher_probe],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert launcher.returncode == 0, launcher.stderr


def test_factory_process_protocol_is_ascii_and_utf8_safe():
    root = oem_configurator.resolve_root()
    runtime = root / "python" / "python.exe"
    if not runtime.is_file():
        pytest.skip("bundled Python is not available")

    result = subprocess.run(
        [str(runtime), str(root / "desktop" / "oem_configurator.py"), "--code", "invalid", "--root", str(root)],
        cwd=str(root),
        capture_output=True,
        timeout=20,
    )
    line = result.stdout.strip().splitlines()[-1]
    kind, encoded = line.split(b"\t", 1)

    assert result.returncode == 1
    assert kind == b"ERROR64"
    assert all(byte < 128 for byte in line)
    assert "请输入 4 到 12 位数字 OEM 编号" in base64.b64decode(encoded).decode("utf-8")

    stub = (root / "desktop" / "oem_configurator_stub.cs").read_text(encoding="utf-8")
    assert 'psi.EnvironmentVariables["PYTHONUTF8"] = "1"' in stub
    assert 'fields[0] == "OK64"' in stub
