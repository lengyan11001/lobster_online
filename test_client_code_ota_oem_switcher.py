from pathlib import Path

from scripts.pack_client_code_ota import WEBSITE_OTA_PATHS, _staged_manifest_paths


def test_website_ota_contains_oem_switcher_runtime():
    assert "desktop/oem_branding.py" in WEBSITE_OTA_PATHS
    assert "desktop/oem_configurator.py" in WEBSITE_OTA_PATHS
    assert "OEM配置启动器.exe" in WEBSITE_OTA_PATHS


def test_oem_switcher_runtime_files_exist():
    root = Path(__file__).resolve().parent
    assert (root / "desktop" / "oem_branding.py").is_file()
    assert (root / "desktop" / "oem_configurator.py").is_file()
    assert (root / "OEM配置启动器.exe").is_file()


def test_encrypted_manifest_declares_oem_switcher_bytecode(tmp_path):
    (tmp_path / "desktop").mkdir()
    (tmp_path / "desktop" / "oem_branding.pyc").write_bytes(b"pyc")
    (tmp_path / "desktop" / "oem_configurator.pyc").write_bytes(b"pyc")

    paths = _staged_manifest_paths(
        ("desktop/oem_branding.py", "desktop/oem_configurator.py", "OEM配置启动器.exe"),
        tmp_path,
        encrypted=True,
    )

    assert paths == (
        "desktop/oem_branding.py",
        "desktop/oem_branding.pyc",
        "desktop/oem_configurator.py",
        "desktop/oem_configurator.pyc",
        "OEM配置启动器.exe",
    )
