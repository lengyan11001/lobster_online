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


def test_online_raw_memory_upload_uses_h5_online_parse_contract():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")

    assert "function requireOnlineMemoryParser(files)" in script
    assert "'/api/h5-chat/devices/status'" in script
    assert "'memory_document_parse_v1'" in script
    assert "result.processing === 'online' && result.message_id" in script
    assert "monitorOnlineMemoryParse(item.messageId, item.filename)" in script
    assert "'/api/h5-chat/messages/' + encodeURIComponent(messageId)" in script
    assert "loadMemories().then(saveConfigSilently)" in script


def test_online_raw_memory_upload_does_not_report_queued_or_failed_files_as_saved():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")
    save_raw = script.split("function saveRawMemory()", 1)[1].split("function saveMemory()", 1)[0]
    save_upload = script.split("function saveUploadedMemory(btn, formData, options)", 1)[1].split(
        "function previewMemory", 1
    )[0]

    assert "savedKeys[uploadFileKey(file)] = true" in save_raw
    assert "failed.push" in save_raw
    assert "state.uploadFiles = files.filter" in save_raw
    assert "完成后自动存入记忆" in save_raw
    assert "return fetch(cloudBase() + '/api/personal-settings/memory-documents/save-upload'" in save_upload
    assert "throw err" in save_upload


def test_online_memory_generation_waits_for_online_review_payload():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")
    wait_online = script.split("function waitForOnlineMemoryGeneration(messageId)", 1)[1].split(
        "function monitorOnlineMemoryParse", 1
    )[0]
    generate = script.split("function generateMemoryDocs()", 1)[1].split("function saveRawMemory()", 1)[0]

    assert "payload.documents" in wait_online
    assert "message.status === 'failed' || message.status === 'cancelled'" in wait_online
    assert "message.status === 'completed'" in wait_online
    assert "waitForOnlineMemoryGeneration(data.message_id)" in generate
    assert "state.generatedDocuments = data.documents || {}" in generate


def test_online_upload_page_lists_shared_uploaded_memory_history():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")
    view = (ROOT / "static" / "views" / "personal-settings.html").read_text(encoding="utf-8")

    assert 'id="psUploadDocList"' in view
    assert 'id="psUploadHistoryCount"' in view
    assert "function uploadedMemoryDocRows()" in script
    assert "isUploadedMemoryDoc(doc)" in script
    assert "renderUploadedDocuments();" in script
    assert "data-preview-upload-memory" in script
    assert "data-delete-upload-memory" in script
    assert "switchTab('memory')" in script
