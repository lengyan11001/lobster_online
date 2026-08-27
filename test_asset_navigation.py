from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_generated_content_and_user_assets_have_separate_entries():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    view = (ROOT / "static" / "views" / "assets.html").read_text(encoding="utf-8")
    publish = (ROOT / "static" / "js" / "publish.js").read_text(encoding="utf-8")
    init = (ROOT / "static" / "js" / "init.js").read_text(encoding="utf-8")

    assert 'data-view="assets" data-asset-origin-target="generated"' in html
    assert 'data-view="assets" data-asset-origin-target="user_upload"' in html
    assert 'id="assetViewTitle">内容记录' in view
    assert 'data-asset-origin="generated"' in view
    assert 'class="asset-upload-control"' in view
    assert "function setAssetLibraryOrigin" in publish
    assert "assetViewDescription" in publish
    assert "asset_origin: 'user_upload'" in publish
    assert "assets:" in init


def test_online_content_record_categories_match_h5_and_use_shared_records_api():
    view = (ROOT / "static" / "views" / "assets.html").read_text(encoding="utf-8")
    publish = (ROOT / "static" / "js" / "publish.js").read_text(encoding="utf-8")

    expected = [
        '<option value="image">图片</option>',
        '<option value="video">视频</option>',
        '<option value="article">文案</option>',
        '<option value="wechat_article">公众号文章</option>',
        '<option value="ppt">PPT</option>',
    ]
    positions = [view.index(option) for option in expected]

    assert positions == sorted(positions)
    assert "_CONTENT_RECORD_TYPE_OPTIONS" in publish
    assert "'/api/content-records?kind='" in publish
    assert "_normalizeSharedContentRecord" in publish


def test_online_content_records_match_h5_actions_and_hide_missing_article_images():
    publish = (ROOT / "static" / "js" / "publish.js").read_text(encoding="utf-8")
    view = (ROOT / "static" / "views" / "assets.html").read_text(encoding="utf-8")

    for action in ["重新生成", "生成图片", "生成视频", "数字人口播", "生成数字人", "发布"]:
        assert action in publish
    assert "function _contentRecordImageUrls" in publish
    assert 'data-hide-on-error="1"' in publish
    assert "image.addEventListener('error', function() { image.remove(); }" in publish
    assert "function _resolveAssetContentActionDetail" in publish
    assert "var item = data && data.item && typeof data.item === 'object' ? data.item : data;" in publish
    assert "Object.assign({}, asset, item, { _compact: false })" in publish
    assert "(!!_assetContentText(asset) || !!asset._compact)" in publish
    assert "function _assetCreativePromptFromObject" in publish
    assert "'image_prompts', 'video_prompt', 'video_prompts'" in publish
    assert "function _assetContentTags" in publish
    assert "function _performAssetContentAction" in publish
    assert "prefillImageComposerContent" in publish
    assert "prefillSeedanceTvcContent" in publish
    assert ".asset-content-action-menu" in view


def test_content_record_digital_human_actions_open_version_2_workbench():
    publish = (ROOT / "static" / "js" / "publish.js").read_text(encoding="utf-8")
    action_start = publish.index("function _assetOpenTalkingVideo")
    action_end = publish.index("function _assetOpenPublish", action_start)
    action_code = publish[action_start:action_end]

    assert "_assetOpenWorkspace('shanjian-digital-human')" in action_code
    assert "shanjianScriptInput" in action_code
    assert "shanjianOpenAvatarCreateBtn" in action_code
    assert "shanjianAvatarImageFile" in action_code
    assert "hifly-digital-human" not in action_code


def test_skill_store_copy_is_oem_neutral():
    skill = (ROOT / "static" / "js" / "skill.js").read_text(encoding="utf-8")

    assert "数字人口播" in skill
    assert "智能视频 2.5" in skill
    assert "数字人 &middot; 必火" not in skill
    assert "视频包装 &middot; 必火" not in skill
