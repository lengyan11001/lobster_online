"""Regression guard for the optional BHZN ToDesk remote-support agent.

System Config reports the remote switch as "未安装" whenever
``desktop/BHZN-ToDesk-Agent.exe`` is missing on the client, so every website
OTA bundle has to carry that executable.
"""
from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACK_SCRIPT = ROOT / "scripts" / "pack_client_code_ota.py"
REMOTE_AGENT_REL_PATH = "desktop/BHZN-ToDesk-Agent.exe"


def _load_pack_module():
    spec = importlib.util.spec_from_file_location("pack_client_code_ota", PACK_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_website_ota_ships_remote_support_agent():
    module = _load_pack_module()

    assert REMOTE_AGENT_REL_PATH in module.WEBSITE_OTA_PATHS


def test_website_ota_never_lists_the_whole_desktop_directory():
    module = _load_pack_module()

    # Listing "desktop" makes the client updater reconcile (and prune) the whole
    # directory, which would delete the branded EXE shells and local data.
    assert "desktop" not in module.WEBSITE_OTA_PATHS


def test_pack_keeps_the_remote_agent_binary_intact(tmp_path):
    module = _load_pack_module()
    source = ROOT / REMOTE_AGENT_REL_PATH

    assert source.is_file(), f"remote support agent is missing: {source}"
    assert module._skip_file(REMOTE_AGENT_REL_PATH) is False

    plain_zip = tmp_path / "plain-ota.zip"
    with zipfile.ZipFile(plain_zip, "w") as archive:
        module._add_tree(archive, ROOT, REMOTE_AGENT_REL_PATH)
    with zipfile.ZipFile(plain_zip) as archive:
        assert archive.read(REMOTE_AGENT_REL_PATH) == source.read_bytes()

    # Encrypted OTAs only compile Python sources; the agent is copied as-is.
    staged = tmp_path / "staged-agent.exe"
    module._copy_tree_for_encrypted_ota(source, staged, REMOTE_AGENT_REL_PATH)
    assert staged.read_bytes() == source.read_bytes()
