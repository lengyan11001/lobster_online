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
    assert "assets:" in init


def test_skill_store_copy_is_oem_neutral():
    skill = (ROOT / "static" / "js" / "skill.js").read_text(encoding="utf-8")

    assert "数字人口播" in skill
    assert "智能视频 2.5" in skill
    assert "数字人 &middot; 必火" not in skill
    assert "视频包装 &middot; 必火" not in skill
