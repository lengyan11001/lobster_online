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
