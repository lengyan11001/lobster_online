from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent


def test_desktop_home_uses_the_designer_workspace_structure():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "pc-workbench.css").read_text(encoding="utf-8")

    assert "/static/css/pc-workbench.css" in html
    assert 'class="chat-panel card chat-workspace is-empty" id="chatWorkspace"' in html
    assert 'class="chat-home-hero"' in html
    assert 'data-view="production" data-feature-gate="production_records_entry">内容记录' not in html
    assert 'id="chatHomeTools"' not in html
    assert "最近使用" not in html
    assert 'id="chatInput"' in html
    assert 'id="chatFileInput"' in html
    assert "chat-shortcut-row" not in html
    assert "width: 240px" in css


def test_desktop_home_preserves_feature_gates_and_action_contracts():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    home = html[html.index('id="chatWorkspace"'): html.index('id="content-openclaw-skill-chat"')]

    for required_id in ("chatAttachments", "chatDirectLlmCheck", "chatCancelBtn", "chatSendBtn"):
        assert f'id="{required_id}"' in home

    assert 'id="chatHomeTools"' not in home


def test_ip_profile_uses_one_active_designer_stylesheet_and_keeps_all_panels():
    view = (ROOT / "static" / "views" / "personal-settings.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "pc-workbench.css").read_text(encoding="utf-8")

    assert view.count('media="not all" data-superseded-by="pc-workbench.css"') == 2
    assert "ps-heading-copy" in view
    assert "ps-form-action-row" in view
    assert "ps-competitor-form" in view
    assert 'id="psUploadDropzone"' in view
    assert "ps-upload-surface" in view
    assert "ps-upload-primary" in view
    assert "ps-upload-zone.is-dragover" in css
    survey_styles = css[css.index(".personal-settings-view .ps-survey-list"):]
    assert "grid-template-columns: 1fr" in survey_styles[:240]

    for panel in ("keywords", "competitors", "profile", "upload", "memory", "template"):
        assert f'data-ps-panel="{panel}"' in view
        assert f'data-ps-tab="{panel}"' in view

    assert '<input id="psKeywordDisplayName" type="hidden">' in view


def test_ip_profile_preserves_unique_runtime_ids():
    view = (ROOT / "static" / "views" / "personal-settings.html").read_text(encoding="utf-8")
    ids = re.findall(r'\bid="([^"]+)"', view)
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    assert duplicates == []

    for required_id in (
        "psRefreshBtn",
        "psBackBtn",
        "psMsg",
        "psKeywordInput",
        "psAddKeywordBtn",
        "psCompetitorPlatform",
        "psCompetitorSearchInput",
        "psProfileQuestionList",
        "psSaveProfileBtn",
        "psMemoryFiles",
        "psSaveRawMemoryBtn",
        "psGenerateMemoryBtn",
        "psMemoryList",
        "psSaveTemplateBtn",
        "psSavedTemplateList",
    ):
        assert required_id in ids
