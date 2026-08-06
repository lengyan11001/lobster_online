import hashlib

from desktop import oem_branding


def _payload(content: bytes) -> dict:
    return {
        "schema_version": 1,
        "oem_code": "0400",
        "brand_mark": "hikong",
        "version": "test-v1",
        "profile": {
            "mark": "hikong",
            "document_title": "Hikong AI Agent",
            "icons": {"logo_mark": "/client/oem/hikong/icon_32.png"},
            "install": {"desktop_ico": "/client/oem/hikong/desktop.ico"},
        },
        "assets": [
            {
                "key": "icon_32",
                "url": "/client/oem/hikong/icon_32.png",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            },
            {
                "key": "desktop_ico",
                "url": "/client/oem/hikong/desktop.ico",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            },
        ],
    }


def test_materialized_oem_profile_is_verified_and_reused_offline(tmp_path, monkeypatch):
    content = b"verified-oem-asset"
    payload = _payload(content)
    downloads = []

    def _download(url, target, expected_size, expected_sha256):
        downloads.append(url)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    monkeypatch.setattr(oem_branding, "_download_asset", _download)
    profile = oem_branding.materialize_oem_profile(tmp_path, "https://brand.example", "0400", payload)
    cached, _ = oem_branding.load_cached_oem_profile(tmp_path, "0400")

    assert profile["mark"] == "hikong"
    assert profile["icons"]["logo_mark"].startswith("/static/branding/cache/hikong/test-v1/")
    assert not profile["install"]["desktop_ico"].startswith("/")
    assert len(downloads) == 2
    assert cached is not None
    assert cached["document_title"] == "Hikong AI Agent"


def test_corrupt_cached_oem_asset_is_rejected(tmp_path, monkeypatch):
    content = b"verified-oem-asset"
    payload = _payload(content)

    def _download(url, target, expected_size, expected_sha256):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    monkeypatch.setattr(oem_branding, "_download_asset", _download)
    profile = oem_branding.materialize_oem_profile(tmp_path, "https://brand.example", "0400", payload)
    icon = tmp_path / profile["icons"]["logo_mark"].lstrip("/")
    icon.write_bytes(b"corrupt")

    cached, _ = oem_branding.load_cached_oem_profile(tmp_path, "0400")

    assert cached is None
