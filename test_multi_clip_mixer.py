import subprocess
from pathlib import Path

import pytest

from backend.app.api import multi_clip_mixer as mixer
from backend.app.api import cutcli_templates_local as cutcli_templates
from backend.app.api import shanjian_smart_clip as shanjian


class _FakeSession:
    def close(self):
        return None


def test_upload_headers_allow_browser_to_set_multipart_boundary():
    script = (Path(__file__).parent / "static" / "js" / "multi-clip-mixer.js").read_text(
        encoding="utf-8"
    )

    assert "delete result['Content-Type']" in script
    assert "delete result['content-type']" in script
    assert "data.append('file', file)" in script


def test_template_customization_uses_the_existing_local_cutcli_pipeline():
    script = (Path(__file__).parent / "static" / "js" / "multi-clip-mixer.js").read_text(
        encoding="utf-8"
    )

    assert "function cloudBaseUrl()" in script
    assert "'/api/cutcli/local/templates'" in script
    assert "'/api/cutcli/local/tasks/start'" in script
    assert "'/api/cutcli/local/templates/jobs/'" in script
    assert "'/api/shanjian-smart-clip/submit'" in script
    assert "'/api/shanjian-smart-clip/task'" in script
    assert "function normalizeShanjianTitle" in script
    assert "scene: 'newsMixCutting'" in script
    assert "materials: [{ type: 'video', fileUrl: videoUrl, soundSwitch: true }]" in script
    assert "material_composition: 'order'" in script
    assert "struct_layers: structLayers" in script
    assert "introduce_name: supportsIpLayer && description ? title : ''" in script
    assert "loadShanjianTemplateDetail" in script
    assert "overlay_texts: readTemplateOverlayTexts()" in script
    assert "templateOverlayFields(state.selectedTemplate)" in script
    assert "overlayFieldMaxLength(field)" in script


def test_shanjian_submit_params_match_provider_contract():
    assert shanjian._normalize_submit_title("Hi") == "智能剪辑"
    assert shanjian._normalize_submit_title("闪剪模板") == "闪剪模板"
    assert shanjian._normalize_material_composition("sequential") == "order"
    assert shanjian._normalize_material_composition("order") == "order"
    assert shanjian._normalize_material_composition("bad-value") == "random"


def test_ui_keeps_timeline_and_template_task_resumable():
    root = Path(__file__).parent
    script = (root / "static" / "js" / "multi-clip-mixer.js").read_text(encoding="utf-8")
    styles = (root / "static" / "css" / "multi-clip-mixer.css").read_text(encoding="utf-8")

    assert "var timelineCursor = 0" not in script
    assert "随机抽段时会重新计算" in script
    assert "已标记为整片原声音轨" in script
    assert "renderMode = modes.indexOf('ffmpeg') >= 0" in script
    assert "asset_id: baseResult.asset_id || ''" in script
    assert "window.addMultiClipMixerAsset" in script
    assert "addUploadedVideoClip(file, data, info)" in script
    assert "function openSegmentForFile" not in script
    assert "openSegmentForFile(state.pendingFiles.shift())" not in script
    assert "mcmKeepAudioSwitch" not in script
    assert "keep_original_audio: false" in script
    assert "PENDING_TEMPLATE_STORAGE_KEY" in script
    assert "resumePendingTemplateTask()" in script
    assert "网络暂时中断，5 秒后继续查询任务" in script
    assert "encodeURIComponent(taskId)" in script
    assert "openVideoPicker" in script
    assert "mcmClipEmpty" in script
    assert 'button[data-action="edit"]' in styles
    assert "white-space: nowrap" in styles


def test_template_job_result_is_used_without_saving_or_duration_rejection_again():
    script = (Path(__file__).parent / "static" / "js" / "multi-clip-mixer.js").read_text(
        encoding="utf-8"
    )

    finish_start = script.index("function finishTemplateTask")
    finish_end = script.index("\n  function applyTemplate", finish_start)
    finish_script = script[finish_start:finish_end]

    assert "模板成片时长异常" not in finish_script
    assert "saveTemplateResult" not in script
    assert "task.final_asset_id || task.preview_asset_id" in finish_script
    assert "基础成片和模板成片均已保存在素材库" in finish_script
    assert script.count("result && result.completion_message") == 3


def test_template_overlay_inputs_are_driven_by_template_metadata():
    root = Path(__file__).parent
    script = (root / "static" / "js" / "multi-clip-mixer.js").read_text(encoding="utf-8")
    view = (root / "static" / "views" / "multi-clip-mixer.html").read_text(encoding="utf-8")

    assert 'id="mcmTemplateOverlayPanel"' in view
    assert 'id="mcmTemplateOverlayFields"' in view
    assert 'id="mcmShanjianCopyPanel"' in view
    assert "introduce_description: description" in script
    assert "field.label || key" in script
    assert "field.multiline" in script
    assert "truncateChars" in script
    assert "Object.prototype.hasOwnProperty.call(state.overlayTexts, key)" in script
    assert 'id="mcmTemplateLayoutEditor"' in view
    assert 'id="mcmTemplateLayoutLayer"' in view
    assert "defaultPositionOverrides" in script
    assert "setTargetPosition" in script
    assert "position_overrides: templatePositionOverridesForSubmit()" in script


def test_template_title_is_entered_before_random_or_specific_template_selection():
    root = Path(__file__).parent
    script = (root / "static" / "js" / "multi-clip-mixer.js").read_text(encoding="utf-8")
    view = (root / "static" / "views" / "multi-clip-mixer.html").read_text(encoding="utf-8")
    registry = (root / "static" / "js" / "view-registry.js").read_text(encoding="utf-8")

    assert 'id="mcmTemplateTitle"' in view
    assert '<textarea id="mcmTemplateTitle"' in view
    assert view.index('id="mcmTemplateTitle"') < view.index('id="mcmRandomTemplateSwitch"')
    assert 'id="mcmTemplateRandomRow"' in view
    assert view.index('id="mcmRandomTemplateSwitch"') < view.index('name="mcmTemplateProvider"')
    assert "function templateTitleField(item)" in script
    assert "function templateTitleValue()" in script
    assert "function overlayTextsWithTemplateTitle(item, values)" in script
    assert "var title = normalizeShanjianTitle(templateTitleValue());" in script
    assert "title: templateTitleValue() ||" in script
    assert "multi-clip-mixer.js?v=20260831-mixer-history-v5" in registry


def test_random_music_and_template_mode_hides_specific_choices():
    root = Path(__file__).parent
    script = (root / "static" / "js" / "multi-clip-mixer.js").read_text(encoding="utf-8")
    view = (root / "static" / "views" / "multi-clip-mixer.html").read_text(encoding="utf-8")

    assert 'id="mcmMusicRandomRow"' in view
    assert 'id="mcmRandomMusicSwitch"' in view
    assert 'id="mcmTemplateChoiceArea"' in view
    assert "if (choiceArea) choiceArea.hidden = randomTemplate;" in script
    assert "if ($('mcmMusicGrid')) $('mcmMusicGrid').hidden = randomMusic;" in script
    assert "grid.innerHTML = randomMusic ? '' : state.musicOptions.map" in script
    assert "grid.innerHTML = randomTemplate ? '' : state.templates.map" in script


def test_multi_clip_history_persists_batches_and_supports_batch_actions():
    root = Path(__file__).parent
    script = (root / "static" / "js" / "multi-clip-mixer.js").read_text(encoding="utf-8")
    view = (root / "static" / "views" / "multi-clip-mixer.html").read_text(encoding="utf-8")
    styles = (root / "static" / "css" / "multi-clip-mixer.css").read_text(encoding="utf-8")

    assert "HISTORY_STORAGE_KEY = 'lobster_multi_clip_history_v1'" in script
    assert "HISTORY_LIMIT = 50" in script
    assert "function loadHistory()" in script
    assert "function historyStorageKey()" in script
    assert "getCurrentUserIdFromToken" in script
    assert "function saveActiveHistoryBatch(completed)" in script
    assert "function copyVideoUrls(urls)" in script
    assert "data-history-action=\"copy\"" in script
    assert "data-history-action=\"toggle\"" in script
    assert "state.historyExpanded[batch.id] === true" in script
    assert "(expanded ? '' : ' hidden')" in script
    assert "historySelectedUrls(batch)" in script
    assert "批量下载" not in script
    assert 'id="mcmHistoryToggleBtn"' in view
    assert 'id="mcmHistoryList"' in view
    assert ".mcm-history-item" in styles


def test_multi_clip_preview_reuses_template_studio_renderer():
    root = Path(__file__).parent
    mixer_script = (root / "static" / "js" / "multi-clip-mixer.js").read_text(encoding="utf-8")
    studio_script = (root / "static" / "js" / "cutcli-template-studio.js").read_text(encoding="utf-8")

    assert "window.CutcliTemplatePreview.captionStyle" in mixer_script
    assert "window.CutcliTemplatePreview.inlineStyle" in mixer_script
    assert "window.CutcliTemplatePreview.defaultPositions" in mixer_script
    assert "window.CutcliTemplatePreview =" in studio_script
    assert "textHtml: previewTextHtmlForValues" in studio_script


def test_segment_captions_are_offset_into_the_merged_timeline():
    captions = [
        {"text": "第二段字幕", "start": 100_000, "end": 1_400_000, "fontSize": 12},
        {"text": "第二句", "start": 1_500_000, "end": 2_900_000, "fontSize": 11},
    ]

    result = cutcli_templates._offset_segment_captions(
        captions,
        timeline_sec=3.0,
        segment_duration_sec=3.0,
        video_duration_sec=9.0,
    )

    assert [item["text"] for item in result] == ["第二段字幕", "第二句"]
    assert result[0]["start"] == 3_100_000
    assert result[0]["end"] == 4_400_000
    assert result[1]["start"] == 4_500_000
    assert result[1]["end"] == 5_900_000
    assert result[0]["fontSize"] == 12


def _make_video(ffmpeg: str, path: Path, color: str, *, with_audio: bool) -> None:
    command = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s=160x120:r=30:d=9",
    ]
    if with_audio:
        command.extend(["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=9"])
    command.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p"])
    if with_audio:
        command.extend(["-c:a", "aac", "-shortest"])
    command.append(str(path))
    subprocess.run(command, check=True, capture_output=True, timeout=120)


def test_multi_clip_default_segments_render_in_order(tmp_path, monkeypatch):
    try:
        ffmpeg = mixer.find_ffmpeg()
        mixer._find_ffprobe(ffmpeg)
    except RuntimeError as exc:
        pytest.skip(str(exc))

    paths = {}
    for index, color in enumerate(("red", "green", "blue"), start=1):
        path = tmp_path / f"source_{index}.mp4"
        _make_video(ffmpeg, path, color, with_audio=index == 1)
        paths[f"asset-{index}"] = path

    monkeypatch.setattr(mixer, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(
        mixer,
        "resolve_asset_path",
        lambda _db, _user_id, asset_id: (paths[asset_id], ".mp4", "video"),
    )

    body = mixer.MultiClipRenderBody(
        clips=[
            mixer.ClipSegment(asset_id="asset-1", start_sec=0, end_sec=3),
            mixer.ClipSegment(asset_id="asset-2", start_sec=3, end_sec=6),
            mixer.ClipSegment(asset_id="asset-3", start_sec=6, end_sec=9),
        ],
        keep_original_audio=True,
    )
    result = mixer._render_local_video(7, body.clips, body)

    assert 8.7 <= result["duration"] <= 9.3
    assert [item["start_sec"] for item in result["segments"]] == [0.0, 3.0, 6.0]
    assert [item["end_sec"] for item in result["segments"]] == [3.0, 6.0, 9.0]
    assert result["data"][:8].endswith(b"ftyp")


def test_output_size_preserves_orientation_and_caps_long_side():
    assert mixer._output_size(3840, 2160) == (1280, 720)
    assert mixer._output_size(1080, 1920) == (720, 1280)
    assert mixer._output_size(640, 640) == (640, 640)


def test_music_template_replaces_original_audio(tmp_path, monkeypatch):
    try:
        ffmpeg = mixer.find_ffmpeg()
        mixer._find_ffprobe(ffmpeg)
    except RuntimeError as exc:
        pytest.skip(str(exc))

    source = tmp_path / "source.mp4"
    music = tmp_path / "music.m4a"
    _make_video(ffmpeg, source, "yellow", with_audio=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000:duration=5",
            "-c:a",
            "aac",
            str(music),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )

    monkeypatch.setattr(mixer, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(mixer, "resolve_asset_path", lambda *_args: (source, ".mp4", "video"))
    monkeypatch.setattr(mixer, "_download_bgm", lambda _url, _work: music)
    body = mixer.MultiClipRenderBody(
        clips=[mixer.ClipSegment(asset_id="asset-1", start_sec=1, end_sec=4)],
        keep_original_audio=False,
        bgm_url="https://example.test/music.m4a",
        bgm_name="测试音乐",
        bgm_volume=0.3,
    )

    result = mixer._render_local_video(7, body.clips, body)

    assert 2.8 <= result["duration"] <= 3.2
    assert result["segments"][0]["start_sec"] == 1.0
    assert result["segments"][0]["end_sec"] == 4.0
