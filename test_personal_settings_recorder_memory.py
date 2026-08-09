from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_online_memory_generation_can_select_completed_recorder_transcripts():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")
    view = (ROOT / "static" / "views" / "personal-settings.html").read_text(encoding="utf-8")

    assert 'id="psMemoryRecorderSourceList"' in view
    assert "'/api/h5/recorder/files?page=1&page_size=50'" in script
    assert "row && row.status === 'completed'" in script
    assert "state.memorySourceRecordings" in script
    assert "'recording'" in script
    assert "loadRecorderSources().catch" in script


def test_online_memory_generation_submits_same_source_ids_as_h5():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")

    assert "fd.append('source_doc_ids'" in script
    assert "fd.append('recorder_record_ids'" in script
    assert "!competitorText && !recorderRows.length" in script
    assert "kind === 'recording' ? state.memorySourceRecordings" in script
