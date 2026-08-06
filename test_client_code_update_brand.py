import json
from pathlib import Path

from backend.app.api import h5_chat_channel
from scripts import check_client_code_update as updater
from scripts import pack_client_code_ota as packer
from scripts import pack_code_only_zip, pack_full_project_zip, pack_slim_zip


def test_default_ota_paths_do_not_replace_runtime_env():
    assert ".env" not in updater.DEFAULT_PATHS
    assert ".env" not in packer.OTA_PATHS
    assert ".env.example" in updater.DEFAULT_PATHS
    assert ".env.example" in packer.OTA_PATHS


def test_default_ota_paths_do_not_replace_installed_brand_exe():
    def _root_exes(paths):
        return {
            path
            for path in paths
            if path.lower().endswith(".exe") and "/" not in path and "\\" not in path
        }

    assert _root_exes(updater.DEFAULT_PATHS) == set()
    assert _root_exes(packer.OTA_PATHS) == set()


def test_packaging_bumps_and_synchronizes_local_version_files(tmp_path):
    version = {
        "version": "1.0.131",
        "build": 175,
        "applied_at": "2026-08-01T18:16:45+08:00",
        "note": "previous",
    }
    static = tmp_path / "static"
    static.mkdir()
    for rel in packer.VERSION_FILE_RELS:
        path = tmp_path / rel
        path.write_text(json.dumps(version), encoding="utf-8")

    bumped = packer._bump_local_client_version(tmp_path, note="regression test")

    assert bumped["version"] == "1.0.132"
    assert bumped["build"] == 176
    for rel in packer.VERSION_FILE_RELS:
        saved = json.loads((tmp_path / rel).read_text(encoding="utf-8"))
        assert saved == bumped
        assert saved["note"] == "regression test"


def test_packaging_refuses_unsynchronized_local_version_files(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (tmp_path / "CLIENT_CODE_VERSION.json").write_text(
        json.dumps({"version": "1.0.131", "build": 175}),
        encoding="utf-8",
    )
    (static / "client_version.json").write_text(
        json.dumps({"version": "1.0.130", "build": 174}),
        encoding="utf-8",
    )

    try:
        packer._bump_local_client_version(tmp_path)
    except ValueError as exc:
        assert "out of sync" in str(exc)
    else:
        raise AssertionError("unsynchronized version files must block OTA packing")


def test_packaging_records_bundle_identity_for_same_build_update_check(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    version = {"version": "1.0.132", "build": 176}
    for rel in packer.VERSION_FILE_RELS:
        (tmp_path / rel).write_text(json.dumps(version), encoding="utf-8")
    digest = "a" * 64

    packer._record_local_bundle_sha256(
        tmp_path,
        version="1.0.132",
        build=176,
        bundle_sha256=digest,
    )

    for rel in packer.VERSION_FILE_RELS:
        saved = json.loads((tmp_path / rel).read_text(encoding="utf-8"))
        assert saved["bundle_sha256"] == digest
    assert (
        updater._update_reason(
            local_build=176,
            local_version="1.0.132",
            local_bundle_sha256=digest,
            remote_build=176,
            remote_version="1.0.132",
            remote_bundle_sha256=digest,
        )
        == ""
    )


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


def test_stage_env_preserves_numeric_oem_code(tmp_path):
    source = tmp_path / "bundle.env"
    source.write_text("LOBSTER_BRAND_MARK=bihuo\n", encoding="utf-8")

    staged = updater._stage_env_with_local_brand(source, tmp_path, "0400")

    assert staged != source
    assert "LOBSTER_BRAND_MARK=0400" in staged.read_text(encoding="utf-8")


def test_ota_preserves_and_packers_exclude_downloaded_oem_cache():
    cache_path = "static/branding/cache/hikong/v1/icon_32.png"

    assert "static/branding/cache" in updater._PRESERVED_STATIC_REL_PATHS
    assert packer._skip_file(cache_path)
    assert pack_code_only_zip._is_excluded(cache_path)
    assert pack_full_project_zip.should_exclude("lobster_online", cache_path)
    assert pack_slim_zip.is_excluded(Path("lobster_online") / cache_path)


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
