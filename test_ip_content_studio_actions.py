from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_ip_daily_records_have_delete_bulk_copy_and_creation_actions():
    script = (ROOT / "static" / "js" / "ip-content-studio.js").read_text(encoding="utf-8")
    view = (ROOT / "static" / "views" / "ip-content-studio.html").read_text(encoding="utf-8")

    assert "function deleteDraftRecord" in script
    assert "function deleteDraftGroup" in script
    assert "function copySelectedDraftRecords" in script
    assert "data-copy-record" in script
    assert 'data-record-action="image"' in script
    assert 'data-record-action="video"' in script
    assert 'data-record-action="digital-human"' in script
    assert "/api/ip-content/draft-records/" in script
    assert "/api/ip-content/draft-record-groups/" in script
    assert "id=\"ipRecordSelectAll\"" in view
    assert "id=\"ipCopySelectedRecordsBtn\"" in view


def test_ip_daily_actions_fill_the_corresponding_workbench_fields():
    script = (ROOT / "static" / "js" / "ip-content-studio.js").read_text(encoding="utf-8")

    assert "imglabPromptInput" in script
    assert "seedanceTaskPromptInput" in script
    assert "hiflyScriptInput" in script
    assert "hiflyTitleInput" in script
