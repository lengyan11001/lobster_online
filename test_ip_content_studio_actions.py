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

    # The 选中出图 checkbox sits on every 文案 record row (not on the batch node and
    # not in the expanded detail), so the five 朋友圈文案 are picked one by one.
    assert "ip-record-select" in script
    assert 'data-moment-select="' in script
    assert "closest('.ip-record-select')" in script
    assert "data-moment-select-group" not in script
    assert (
        "renderDraftCards(draftDetailTargetId(item.index), [item.rec], { selectable: false, hideTitle: true, hideTaskBadge: true })"
        in script
    )


def test_ip_daily_generate_buttons_moved_to_the_header_without_a_setting_panel():
    view = (ROOT / "static" / "views" / "ip-content-studio.html").read_text(encoding="utf-8")

    header = view[view.index('<header class="ip-content-head">'): view.index("</header>")]
    for element in ('id="ipGenerateIndustryBtn"', 'id="ipGenerateIpBtn"', 'id="ipGenerateMomentsBtn"'):
        assert element in header
    assert 'id="ipContentRefreshBtn"' in header
    # 生成设置 panel, the profile summary box and its standalone select are gone.
    assert "生成设置" not in view
    assert 'id="ipCurrentProfileTemplateBox"' not in view
    assert 'id="ipGenerateTemplateSelect"' not in view
    assert 'id="ipOpenPersonalSettingsBtn"' not in view


def test_ip_daily_reinit_keeps_the_expanded_tree_and_only_entry_resets_it():
    script = (ROOT / "static" / "js" / "ip-content-studio.js").read_text(encoding="utf-8")
    skill = (ROOT / "static" / "js" / "skill.js").read_text(encoding="utf-8")

    # A plain re-init (the shell re-showing the view) keeps what the user expanded.
    assert "window.initIpContentStudioView = function(mode, options) {" in script
    assert "if (options.enter) resetRecordTreeState();" in script
    assert "window.initIpContentStudioView(mode, { enter: true });" in skill


def test_ip_daily_record_list_is_painted_from_the_server_list_only():
    script = (ROOT / "static" / "js" / "ip-content-studio.js").read_text(encoding="utf-8")

    # The cached batch jobs must not add rows of their own: that is what made the list
    # show yesterday's 20 条 and then swap/flash when the server answer arrived.
    restore_start = script.index("function restoreMomentBatchJobs")
    restore_end = script.index("function resetRecordTreeState", restore_start)
    assert "renderDraftRecords" not in script[restore_start:restore_end]
    assert "if (showMoments && state.draftRecordsLoaded) {" in script
    assert "if (job.status !== 'failed' && job.status !== 'running') return;" in script

    # One shared in-flight load, and a loading placeholder instead of an empty list.
    assert "if (state.draftRecordsInFlight) return state.draftRecordsInFlight;" in script
    assert "state.draftRecordsLoaded = true;" in script
    assert "正在加载生成记录…" in script


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


def test_record_rows_do_not_repeat_the_type_and_count():
    script = (ROOT / "static" / "js" / "ip-content-studio.js").read_text(encoding="utf-8")

    # 记录行：类型和条数只在徽标出现一次，标题行只留批次信息
    assert "var groupTitle = job ? (job.label + (Number(job.batch_count) > 1 ? ' / 共 ' + job.batch_count + ' 批' : '')) : '';" in script
    assert "(groupTitle ? '<strong>' + esc(groupTitle) + '</strong>' : '') +" in script
    assert "'<strong>' + esc(taskLabel(group.task)) + ' \u00b7 '" not in script

    # 下级每条文案：不再重复类型徽标（选择框 / 图片数 / 箭头保留）
    assert script.count("'<span class=\"ip-badge\">' + esc(taskLabel(rec.task)) + '</span>' +") == 1
    assert "hideTaskBadge: true" in script
    assert "(hideTaskBadge ? '' : '<span class=\"ip-badge\">' + esc(taskLabel(rec.task)) + '</span>'" in script
