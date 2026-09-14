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
    assert 'data-record-action="publish-moments"' in script
    assert 'function openDraftMomentsPublish' in script
    assert "/api/ip-content/draft-records/" in script
    assert "/api/ip-content/draft-record-groups/" in script
    assert "id=\"ipRecordSelectAll\"" in view
    assert "id=\"ipCopySelectedRecordsBtn\"" in view


def test_ip_daily_actions_fill_the_corresponding_workbench_fields():
    script = (ROOT / "static" / "js" / "ip-content-studio.js").read_text(encoding="utf-8")

    assert "imglabPromptInput" in script
    assert "seedanceTaskPromptInput" in script
    assert "shanjianScriptInput" in script
    assert "shanjianTitleInput" in script
    assert "_openShanjianDigitalHumanView" in script
    assert "prefillNativeWechatMoments" in script


def test_ip_daily_action_handlers_are_bound_when_cards_render():
    script = (ROOT / "static" / "js" / "ip-content-studio.js").read_text(encoding="utf-8")
    render_start = script.index("function renderDraftCards")
    render_end = script.index("function renderDraftRecords", render_start)
    render_body = script[render_start:render_end]
    payload_start = script.index("function generationPayload")
    payload_end = script.index("function clonePayload", payload_start)

    assert "box.querySelectorAll('[data-record-action]')" in render_body
    assert "openDraftContentAction(rec, action" in render_body
    assert "box.querySelectorAll('[data-record-action]')" not in script[payload_start:payload_end]


def test_moment_batches_show_copy_preview_and_open_a_dedicated_result_modal():
    script = (ROOT / "static" / "js" / "ip-content-studio.js").read_text(encoding="utf-8")
    view = (ROOT / "static" / "views" / "ip-content-studio.html").read_text(encoding="utf-8")

    assert "ip-moment-batch-preview" in script
    assert "function openMomentBatchResult" in script
    assert "renderDraftCards('ipMomentBatchResultList'" in script
    assert 'id="ipMomentBatchResultModal"' in view
    assert 'id="ipMomentBatchResultList"' in view


def test_moment_batch_detail_keeps_direct_and_bulk_image_generation():
    script = (ROOT / "static" / "js" / "ip-content-studio.js").read_text(encoding="utf-8")
    view = (ROOT / "static" / "views" / "ip-content-studio.html").read_text(encoding="utf-8")

    assert 'id="ipMomentBatchGenerateImagesBtn"' in view
    assert "renderDraftCards('ipMomentBatchResultList', records, { selectable: true })" in script
    assert "rec.task === 'moments_candidate' && action === 'image'" in script
    assert "confirmMomentsImages([rec], btn" in script
    assert "confirmMomentsImages(momentBatchRecords(job)" in script


def test_moment_image_failures_are_rendered_on_the_matching_record():
    script = (ROOT / "static" / "js" / "ip-content-studio.js").read_text(encoding="utf-8")
    view = (ROOT / "static" / "views" / "ip-content-studio.html").read_text(encoding="utf-8")

    assert "function momentRecordError" in script
    assert "function momentRecordFailedIndex" in script
    assert "if (recordImages(rec).length >= 3) return false" in script
    assert "张图片生成失败" in script
    assert 'data-retry-moment-image-record' in script
    assert 'data-publish-moment-image-record' in script
    assert "失败原因已标在对应文案" in script
    assert "setMsg(err.message || '朋友圈出图失败', true)" not in script
    assert ".ip-moment-image-error" in view


def test_ip_daily_records_are_a_single_expandable_tree_without_a_detail_column():
    script = (ROOT / "static" / "js" / "ip-content-studio.js").read_text(encoding="utf-8")
    view = (ROOT / "static" / "views" / "ip-content-studio.html").read_text(encoding="utf-8")

    # Opening the entry shows the record list only: the right hand detail column
    # and the always-on batch queue cards are gone for good.
    assert 'id="ipLatestDraftList"' not in view
    assert 'id="ipMomentBatchQueue"' not in view
    assert 'id="ipMomentImageDetail"' not in view
    assert 'id="ipRecordDetailTitle"' not in view
    assert "resetRecordTreeState();" in script
    assert "grid-template-columns: minmax(0, 1fr);" in view

    # A batch expands to its own records, a record expands to its own detail.
    assert "function recordLeafHtml" in script
    assert "function recordTreeEntries" in script
    assert "state.expandedRecordGroups" in script
    assert "state.expandedRecordLeaves" in script
    assert 'data-record-group="' in script
    assert 'data-record-leaf="' in script
    assert "draftDetailTargetId" in script
    assert "renderDraftCards(draftDetailTargetId(item.index), [item.rec]" in script

    # Several batches stay open side by side; collapsing one keeps the others.
    assert "function toggleDraftGroupNode" in script
    assert "toggleDictFlag(state.expandedRecordGroups, groupId)" in script


def test_moment_image_records_expand_in_the_same_left_hand_tree():
    script = (ROOT / "static" / "js" / "ip-content-studio.js").read_text(encoding="utf-8")

    assert "function momentImageLeafHtml" in script
    assert "function momentImageDetailHtml" in script
    assert "function bindMomentImageRecordActions" in script
    assert "state.expandedMomentImageGroups" in script
    assert "state.expandedMomentImageLeaves" in script
    assert 'data-moment-image-batch="' in script
    assert 'data-moment-image-record="' in script
    assert "momentImageLeafTargetId" in script
    assert "toggleDictFlag(state.expandedMomentImageGroups, batchId)" in script
