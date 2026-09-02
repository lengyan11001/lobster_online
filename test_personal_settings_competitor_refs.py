from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_online_template_save_filters_deleted_competitor_ids():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")

    assert "function cleanExistingIntIds(values, rows)" in script
    assert "competitor_ids: cleanExistingIntIds" in script
    assert "pruneSelectedIntMap(state.selectedCompetitors" in script


def test_online_and_h5_use_account_search_contract():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")
    view = (ROOT / "static" / "views" / "personal-settings.html").read_text(encoding="utf-8")

    assert "/api/ip-content/wechat-channels/users/search?q=" in script
    assert "/api/ip-content/douyin/users/search?q=" in script
    assert "/api/ip-content/wechat-channels/competitors/by-channel-id" in script
    assert 'id="psAddCompetitorByChannelIdBtn"' in view
    assert "candidate.username || candidate.finder_username || candidate.id" in script
