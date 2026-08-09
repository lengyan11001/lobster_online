from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_ai_secretary_appears_below_ip_persona_with_permission_gate():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    top_nav = html[html.index('<nav class="app-top-nav"'):html.index("</nav>", html.index('<nav class="app-top-nav"'))]
    side_start = html.index('<div class="chat-sidebar-nav chat-sidebar-tree"')
    side_nav = html[side_start:html.index("<details", side_start)]

    expected = 'data-view="audio-transcription" data-feature-gate="personal_settings_entry"'
    assert expected not in top_nav
    assert html.count(expected) == 1
    assert f'class="chat-sidebar-entry app-side-primary-entry" {expected}' in side_nav
    assert side_nav.index('data-view="personal-settings"') < side_nav.index(expected)
    assert '<span class="chat-sidebar-entry-copy">AI秘书</span>' in side_nav
    assert f'class="header-menu-view" {expected}' not in html


def test_audio_transcription_registered_as_dynamic_view():
    registry = (ROOT / "static" / "js" / "view-registry.js").read_text(encoding="utf-8")
    runtime = (ROOT / "static" / "js" / "init.js").read_text(encoding="utf-8")

    assert "registerLobsterView('audio-transcription'" in registry
    assert "/static/views/audio-transcription.html" in registry
    assert "/static/css/audio-transcription.css" in registry
    assert "/static/js/audio-transcription.js" in registry
    assert "'audio-transcription': true" in runtime
    assert "'audio-transcription': 'personal_settings_entry'" in runtime


def test_online_audio_view_has_local_mobile_records_and_secondary_detail():
    view = (ROOT / "static" / "views" / "audio-transcription.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "js" / "audio-transcription.js").read_text(encoding="utf-8")

    assert 'data-at-tab="local"' in view
    assert 'data-at-tab="mobile"' in view
    assert 'data-at-tab="records"' in view
    assert view.index('data-at-tab="records"') < view.index('data-at-tab="local"') < view.index('data-at-tab="mobile"')
    assert 'class="is-active" data-at-tab="records"' in view
    assert "tab: 'records'" in script
    assert "? tab : 'records'" in script
    assert 'data-at-screen="main"' in view
    assert 'data-at-screen="detail" hidden' in view
    assert 'source_type=device' in script
    assert "startRecorderScan" not in script
    assert "syncNewRecorderFiles" not in script
    assert 'data-at-new' in view
    assert 'AI秘书' in view
    assert "function newestFirst" in script
    assert ".slice().sort(newestFirst)" in script


def test_local_audio_upload_returns_immediately_to_background_record_list():
    script = (ROOT / "static" / "js" / "audio-transcription.js").read_text(encoding="utf-8")
    upload = script[script.index("function uploadLocalFile"):script.index("function recordRow")]

    assert "音频已保存到本机，正在后台上传并转写，可以继续操作其他功能" in upload
    assert ": '已上传，正在后台转写'" in upload
    assert "setTab('records')" in upload
    assert "showDetail(" not in upload


def test_cloud_record_replaces_stale_local_upload_snapshot():
    script = (ROOT / "static" / "js" / "audio-transcription.js").read_text(encoding="utf-8")
    assert "cloudRecordIds" in script
    assert "job && job.record && job.record.id" in script
    assert "Do not render" in script


def test_online_audio_progress_shows_long_audio_chunk_position():
    script = (ROOT / "static" / "js" / "audio-transcription.js").read_text(encoding="utf-8")
    assert "^transcribing:(\\d+)\\/(\\d+)$" in script
    assert "正在识别第 " in script


def test_online_audio_detail_supports_speaker_rename_copy_and_export():
    view = (ROOT / "static" / "views" / "audio-transcription.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "js" / "audio-transcription.js").read_text(encoding="utf-8")

    assert 'data-at-copy="summary"' in view
    assert 'data-at-export="transcript"' in view
    assert "/speakers" in script
    assert "renameSpeaker" in script
    assert "data-at-speaker-id" in script
    assert "speaker_id" in script
    assert "navigator.clipboard" in script
    assert "anchor.download" in script
