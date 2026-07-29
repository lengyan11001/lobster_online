from scripts import check_client_code_update as updater
from scripts import pack_client_code_ota as packer
from backend.app.api import h5_chat_channel


def test_default_ota_paths_do_not_replace_runtime_env():
    assert ".env" not in updater.DEFAULT_PATHS
    assert ".env" not in packer.OTA_PATHS
    assert ".env.example" in updater.DEFAULT_PATHS
    assert ".env.example" in packer.OTA_PATHS


def test_stage_env_preserves_existing_brand(tmp_path):
    source = tmp_path / "bundle.env"
    source.write_text(
        "LOBSTER_BRAND_MARK=bihuo\nCLIENT_CODE_MANIFEST_URL=https://example.test/manifest.json\n",
        encoding="utf-8",
    )

    staged = updater._stage_env_with_local_brand(source, tmp_path, "daka")

    staged_text = staged.read_text(encoding="utf-8")
    assert staged != source
    assert "LOBSTER_BRAND_MARK=daka" in staged_text
    assert "CLIENT_CODE_MANIFEST_URL=https://example.test/manifest.json" in staged_text
    assert "LOBSTER_BRAND_MARK=bihuo" in source.read_text(encoding="utf-8")


def test_stage_env_uses_bundle_when_local_brand_is_invalid(tmp_path):
    source = tmp_path / "bundle.env"
    source.write_text("LOBSTER_BRAND_MARK=bihuo\n", encoding="utf-8")

    assert updater._stage_env_with_local_brand(source, tmp_path, "invalid brand") == source


def test_background_channel_requests_include_oem_brand(monkeypatch):
    monkeypatch.setattr(h5_chat_channel.settings, "lobster_brand_mark", "daka")

    headers = h5_chat_channel._headers("token", "daka--installation")

    assert headers["X-Lobster-Brand"] == "daka"
    assert headers["X-Installation-Id"] == "daka--installation"


def test_online_cloud_header_builder_uses_installed_brand(monkeypatch):
    from backend.app.services import oem_brand_context

    monkeypatch.setattr(oem_brand_context.settings, "lobster_brand_mark", "daka")

    headers = oem_brand_context.with_oem_brand_header({"Authorization": "Bearer token"})

    assert headers == {
        "Authorization": "Bearer token",
        "X-Lobster-Brand": "daka",
    }


def test_browser_brand_interceptor_covers_chat_and_api_requests():
    script = (updater.ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "installBrandRequestContext" in script
    assert "(?:api|auth|chat|skills|capabilities)" in script
    assert "headers.set('X-Lobster-Brand', getLobsterBrandMark())" in script
