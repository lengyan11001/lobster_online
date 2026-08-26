import json
import sys
import zipfile
from pathlib import Path

from backend.app.api import h5_chat_channel
from scripts import check_client_code_update as updater
from scripts import pack_client_code_ota as packer
from scripts import pack_code_only_zip, pack_full_project_zip, pack_slim_zip


def _read_update_status_output(output: str) -> dict:
    prefix = "__LOBSTER_UPDATE_STATUS__="
    line = next(row for row in output.splitlines() if row.startswith(prefix))
    return json.loads(line[len(prefix) :])


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


def test_bundle_apply_succeeds_when_target_did_not_exist(monkeypatch, tmp_path):
    root = tmp_path / "client"
    root.mkdir()
    update_state = root / ".updates"
    bundle = tmp_path / "bundle.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("new-runtime-file.txt", "installed")

    monkeypatch.setattr(updater, "ROOT", root)
    monkeypatch.setattr(updater, "_PENDING_UPDATE_DIR", update_state)
    monkeypatch.setattr(
        updater,
        "_PENDING_PATH_REPLACE_MARKER",
        update_state / "pending_path_replace.json",
    )
    monkeypatch.setattr(
        updater,
        "_PENDING_BUNDLE_ROLLBACK_MARKER",
        update_state / "pending_bundle_rollback.json",
    )

    applied = updater._apply_bundle_zip(
        bundle,
        ["new-runtime-file.txt"],
        tmp_path / "work",
    )

    assert applied == ["new-runtime-file.txt"]
    assert (root / "new-runtime-file.txt").read_text(encoding="utf-8") == "installed"
    assert not updater._PENDING_BUNDLE_ROLLBACK_MARKER.exists()


def test_check_only_reports_new_build_without_applying_update(monkeypatch, capsys):
    monkeypatch.setattr(
        updater,
        "_load_dotenv_simple",
        lambda _path: {"CLIENT_CODE_MANIFEST_URL": "https://example.test/manifest.json"},
    )
    monkeypatch.setattr(updater, "_local_build", lambda: 199)
    monkeypatch.setattr(updater, "_local_semver", lambda: "1.0.155")
    monkeypatch.setattr(updater, "_local_bundle_sha256", lambda: "a" * 64)
    monkeypatch.setattr(
        updater,
        "_fetch_manifest_with_retry",
        lambda _url: {"build": 200, "version": "1.0.156", "sha256": "b" * 64},
    )

    assert updater._check_only_locked() == 0

    status = _read_update_status_output(capsys.readouterr().out)
    assert status == {
        "ok": True,
        "configured": True,
        "available": True,
        "restart_required": True,
        "local_build": 199,
        "local_version": "1.0.155",
        "remote_build": 200,
        "remote_version": "1.0.156",
        "reason": "build",
    }


def test_check_only_detects_same_version_replaced_bundle(monkeypatch, capsys):
    monkeypatch.setattr(
        updater,
        "_load_dotenv_simple",
        lambda _path: {"CLIENT_CODE_MANIFEST_URL": "https://example.test/manifest.json"},
    )
    monkeypatch.setattr(updater, "_local_build", lambda: 199)
    monkeypatch.setattr(updater, "_local_semver", lambda: "1.0.155")
    monkeypatch.setattr(updater, "_local_bundle_sha256", lambda: "a" * 64)
    monkeypatch.setattr(
        updater,
        "_fetch_manifest_with_retry",
        lambda _url: {"build": 199, "version": "1.0.155", "sha256": "b" * 64},
    )

    updater._check_only_locked()

    status = _read_update_status_output(capsys.readouterr().out)
    assert status["available"] is True
    assert status["restart_required"] is True
    assert status["reason"] == "bundle_sha256"


def test_online_header_has_hourly_update_reminder_and_desktop_restart_action():
    html = (updater.ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = (updater.ROOT / "static" / "js" / "init.js").read_text(encoding="utf-8")
    launcher_source = (updater.ROOT / "desktop" / "launcher.py").read_text(encoding="utf-8")

    assert 'id="clientUpdateHeaderDot"' in html
    assert 'id="clientUpdateAction"' in html
    assert "60 * 60 * 1000" in script
    assert "/api/client-update/status" in script
    assert "window.pywebview.api.install_client_update()" in script
    assert "def install_client_update(self)" in launcher_source
    assert "apply_client_update_and_restart.py" in launcher_source


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


def test_memory_document_runtime_is_packable_and_installed_by_updater():
    assert packer.MEMORY_DOCUMENT_RUNTIME_WHEEL_PATTERNS == (
        "pypdf-*.whl",
        "xlrd-*.whl",
    )
    group = next(
        item for item in updater.RUNTIME_DEPENDENCY_GROUPS
        if item.get("name") == "memory_document_runtime"
    )
    assert group["requirements"] == ("pypdf>=4.0.0", "xlrd>=2.0.1")
    assert group["verify_imports"] == ("pypdf", "xlrd")
    assert updater._should_install_runtime_group(
        group,
        ["backend/app/services/document_text_extractor.py"],
    )


def test_memory_document_runtime_packer_collects_both_wheels(tmp_path):
    wheel_dir = tmp_path / "deps" / "wheels"
    wheel_dir.mkdir(parents=True)
    (wheel_dir / "pypdf-6.14.2-py3-none-any.whl").write_bytes(b"pypdf")
    (wheel_dir / "xlrd-2.0.2-py2.py3-none-any.whl").write_bytes(b"xlrd")

    copied = packer._prepare_memory_document_runtime_wheels(tmp_path)

    assert copied == [
        "scripts/memory_document_runtime_wheels/pypdf-6.14.2-py3-none-any.whl",
        "scripts/memory_document_runtime_wheels/xlrd-2.0.2-py2.py3-none-any.whl",
    ]


def test_no_dependency_ota_excludes_nested_node_modules(monkeypatch):
    monkeypatch.setattr(
        packer,
        "_PACK_SKIP_REL_PREFIXES",
        {
            "nodejs/node_modules",
            "backend/douyin_origin/douyin_protocol/node_modules",
        },
    )

    assert packer._skip_file("nodejs/node_modules/openclaw/index.js")
    assert packer._skip_file(
        "backend/douyin_origin/douyin_protocol/node_modules/jsrsasign/lib/jsrsasign.js"
    )
    assert not packer._skip_file("backend/douyin_origin/douyin_protocol/index.js")


def test_default_ota_is_encrypted_website_only(monkeypatch, tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "static").mkdir()
    (tmp_path / "skills" / "should_not_ship").mkdir(parents=True)
    (tmp_path / "backend" / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "scripts" / "check_client_code_update.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )
    (tmp_path / "static" / "index.html").write_text("<html></html>\n", encoding="utf-8")
    (tmp_path / "skills" / "should_not_ship" / "SKILL.md").write_text("secret\n", encoding="utf-8")
    version = {"version": "1.0.200", "build": 244}
    for rel in packer.VERSION_FILE_RELS:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(version), encoding="utf-8")
    out = tmp_path / "website-ota.zip"
    monkeypatch.setattr(
        sys,
        "argv",
        ["pack_client_code_ota.py", "--root", str(tmp_path), "--out", str(out)],
    )

    assert packer.main() == 0

    with zipfile.ZipFile(out) as archive:
        names = set(archive.namelist())
        assert "backend/sample.pyc" in names
        assert "backend/sample.py" in names
        assert "scripts/check_client_code_update.py" in names
        assert "scripts/check_client_code_update.pyc" in names
        assert "static/index.html" in names
        assert not any(name.startswith("skills/") for name in names)
        loader = archive.read("backend/sample.py").decode("utf-8")
        assert "Auto-generated by scripts/pack_client_code_ota.py --encrypted" in loader


def test_plain_full_project_zip_is_blocked_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("LOBSTER_ALLOW_PLAIN_PACK", raising=False)
    assert pack_full_project_zip.main() == 2
