from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_profile_photo_question_uses_upload_and_asset_picker():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")
    view = (ROOT / "static" / "views" / "personal-settings.html").read_text(encoding="utf-8")

    assert "field: 'psProfilePhoto', label: '\u4eba\u7269\u7167\u7247', type: 'photo'" in script
    assert "id=\"psProfilePhotoUploadBtn\"" in script
    assert "id=\"psProfilePhotoLibraryBtn\"" in script
    assert "'/api/assets/upload'" in script
    assert "'/api/assets?media_type=image&limit=200'" in script
    assert 'id="psProfilePhotoPicker"' in view
    assert 'id="psProfilePhotoPickerGrid"' in view


def test_profile_photo_keeps_existing_profile_storage_contract():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")

    assert "profile_photo_asset_id: /^https?:\\/\\//i.test(profilePhoto) ? '' : profilePhoto" in script
    assert "profile_photo_url: /^https?:\\/\\//i.test(profilePhoto) ? profilePhoto : ''" in script


def test_profile_photo_prefers_public_url_and_refreshes_after_local_upload():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")

    assert "function profilePhotoPublicUrl(row)" in script
    assert "row.source_url" in script
    assert "return profilePhotoPublicUrl(row);" in script
    assert "row.preview_url || row.local_preview_url || row.open_url || row.url" not in script
    assert "profilePhotoUploadPreview" not in script
    assert "blob:" not in script
    assert "data:image/" not in script
    assert "waitForProfilePhotoPublicUrl(item.asset_id, 0)" in script
    assert "var PROFILE_PHOTO_PUBLIC_URL_RETRIES = 20" in script


def test_personal_settings_loading_is_progressive_and_has_timeout():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")

    assert "var PERSONAL_SETTINGS_LOAD_TIMEOUT_MS = 10000" in script
    assert "Promise.race([request, timeout])" in script
    assert "timeoutMs: PERSONAL_SETTINGS_LOAD_TIMEOUT_MS" in script
