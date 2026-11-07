"""运行期字体补拉：清单完整、校验严格、已存在则不重复下载。"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.app.services import runtime_assets as ra  # noqa: E402


def test_font_manifest_covers_all_five_weights():
    rels = {a["rel"] for a in ra.RUNTIME_ASSETS}
    assert len(rels) == 5
    for rel in rels:
        assert rel.startswith("static/fonts/AlibabaPuHuiTi-3-")
        assert rel.endswith(".otf")


def test_manifest_urls_are_tos_https_and_sha_is_hex():
    for asset in ra.RUNTIME_ASSETS:
        assert asset["url"].startswith("https://lobster-online-assets-")
        assert len(asset["sha256"]) == 64
        int(asset["sha256"], 16)


def test_missing_assets_detects_absent_and_corrupt(tmp_path):
    assert len(ra.missing_assets(tmp_path)) == len(ra.RUNTIME_ASSETS)
    one = ra.RUNTIME_ASSETS[0]
    target = tmp_path / one["rel"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"not-a-font")
    assert one in ra.missing_assets(tmp_path)


def test_valid_asset_is_not_missing(tmp_path):
    payload = b"x" * 4096
    asset = {
        "rel": "static/fonts/demo.otf",
        "url": "https://example.invalid/demo.otf",
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    target = tmp_path / asset["rel"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    assert ra.missing_assets(tmp_path, (asset,)) == []


def test_ensure_assets_skips_when_present(tmp_path):
    payload = b"y" * 4096
    asset = {
        "rel": "static/fonts/demo.otf",
        "url": "https://example.invalid/demo.otf",
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    target = tmp_path / asset["rel"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    assert ra.ensure_assets(tmp_path, (asset,)) == []
