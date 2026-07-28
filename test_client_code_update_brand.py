from scripts import check_client_code_update as updater
from scripts import pack_client_code_ota as packer


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
