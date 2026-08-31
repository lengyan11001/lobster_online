from pathlib import Path


ROOT = Path(__file__).resolve().parent


def _script() -> str:
    return (ROOT / "static" / "js" / "views" / "h5-employees.js").read_text(encoding="utf-8")


def test_local_bestseller_node_does_not_turn_a_custom_employee_into_system_sales():
    script = _script()
    start = script.index("function isSalesTemplate(template)")
    end = script.index("function templateNeedsPlanDay(template)", start)
    identity_check = script[start:end]

    assert "activeTemplateKey(template) === 'system_sales'" in identity_check
    assert "local_bestseller" not in identity_check
    assert "templateNeedsPlanDay(template)" in script
    assert "local_bestseller_daily_video" in script


def test_employee_mutations_are_single_flight():
    script = _script()

    assert "submitting: ''" in script
    assert "function runSubmission(kind, task)" in script
    assert "if (state.submitting)" in script
    assert "runSubmission('save'" in script
    assert "runSubmission('activate'" in script
    assert "runSubmission('stop'" in script
    assert "runSubmission('delete'" in script


def test_douyin_private_message_editor_preserves_add_friend_switch():
    script = _script()
    start = script.index("if (row.key === 'douyin_leads')")
    end = script.index("throw new Error", start)
    douyin_plan = script[start:end]

    assert "Object.assign({}, row.params || {}" in douyin_plan
    assert "baseScheduleParams(row,douyinParams)" in douyin_plan
    assert "delete douyinParams.wechat_add_friend_enabled" in douyin_plan
    assert "if (action !== 'stranger_message')" in douyin_plan
    assert "wechat_add_friend_targets_source='douyin_private_message_phone'" in script
    assert "action === 'stranger_message'" in script
    assert "selectedSalesAction === 'stranger_message'" in script


def test_employee_editor_uses_server_as_the_only_persisted_source():
    script = _script()

    assert "drafts: {}" not in script
    assert "state.drafts" not in script
    assert "function restoreDraft" not in script
    assert "function selectTemplate(id)" in script
    assert "return loadTemplates().then(function(){return applyServerTemplate(selected);});" in script
    assert "state.selectedTemplate=base" in script


def test_node_modal_saves_to_server_and_uses_strict_false_default():
    script = _script()
    last_save = script.rindex("function saveNodeFromModal()")
    save_source = script[last_save:script.index("function saveChildFromModal()", last_save)]

    assert "boolParam(params.wechat_add_friend_enabled,false)" in script
    assert "return saveTemplate().then" in save_source
    assert "节点参数已保存到服务器" in save_source
    assert "saveNodeFromModal().catch" in script


def test_online_employee_editor_deletes_saved_custom_employee_from_server():
    script = _script()
    html = (ROOT / "static" / "views" / "h5-employees.html").read_text(encoding="utf-8")

    assert 'data-oe-action="delete-template"' in html
    assert "function deleteTemplate()" in script
    assert "/api/h5-workflows/templates/" in script
    assert "method:'DELETE'" in script
    assert "只能删除自己创建的员工" in script
    assert "删除后服务器模板会移除" in script


def test_online_employee_editor_copies_granted_template_into_owned_template():
    script = _script()
    html = (ROOT / "static" / "views" / "h5-employees.html").read_text(encoding="utf-8")
    registry = (ROOT / "static" / "js" / "view-registry.js").read_text(encoding="utf-8")

    assert 'id="oeCopyTemplateBtn"' in html
    assert 'data-oe-action="copy-template"' in html
    assert "function copiedTemplateName(template)" in script
    assert "function copyTemplate()" in script
    assert "source.source !== 'granted'" in script
    assert "meta:{copied_from:sourceId,copied_source:String(source.source || '')}" in script
    assert "method:'POST'" in script
    assert "state.templatesLoadedInstallationId=''" in script
    assert "applyServerTemplate(copiedId)" in script
    assert "h5-employees.js?v=20260831-employee-copy-v1" in registry


def test_online_employee_editor_manages_supported_child_actions():
    script = _script()
    html = (ROOT / "static" / "views" / "h5-employees.html").read_text(encoding="utf-8")
    registry = (ROOT / "static" / "js" / "view-registry.js").read_text(encoding="utf-8")

    assert "var CHILD_ACTION_OPTIONS" in script
    assert "function buildWorkflowChild(parent, form, existing)" in script
    assert "function syncParentChildRules(parent)" in script
    assert "function openChildModal(parentIndex,childId)" in script
    assert "function removeChild(parentIndex,childId)" in script
    assert "data-oe-child-add" in script
    assert "data-oe-child-edit" in script
    assert "data-oe-child-delete" in script
    assert "data-oe-demo" in script
    assert "function demoNode(index)" in script
    assert "演示任务已下发" in script
    assert "salesAction(note)" in script
    assert "NODE_OPTION_GROUP_ORDER = ['抖音','个微','AI营销']" in script
    assert "nodeOptionGroupForKey(row.key)" in script
    assert "AI营销" in script
    assert "function nodeOptionFeatureGate(key)" in script
    assert "private_domain_entry" in script
    assert "overseas_platform_entry" in script
    assert "'linkedin_leads':'linkedin_leads'" in script
    assert "'reddit_leads':'reddit_leads'" in script
    assert "'x_leads':'x_leads'" in script
    assert "'tiktok_leads':'tiktok_leads'" in script
    assert "function nodeOptionPackageVisible(packageId)" in script
    assert "function loadNodePermissions()" in script
    assert "nodeOptionIsAllowed(findOption('native_wechat_moments_engage'))" in script
    assert "native_wechat_group_invite" in script
    assert "native_wechat_moments_engage" in script
    assert 'id="oeChildModal"' in html
    assert 'id="oeChildPlatform"' in html
    assert "function currentTemplateIsEditable()" in script
    assert "function requireEditableTemplate()" in script
    assert "childHtml(child,parentIndex,editable)" in script
    assert "nodeHtml(node,index,editable)" in script
    assert "state.editingId || template && template.id" in script
    assert ".oe-form-label[hidden] { display:none; }" in html
    assert "100dvh" in html
    assert "20260827-touch-action-echo-v1" in registry


def test_moments_nodes_save_paginated_contact_selection_as_wechat_ids():
    script = _script()
    html = (ROOT / "static" / "views" / "h5-employees.html").read_text(encoding="utf-8")
    channel = (ROOT / "backend" / "app" / "api" / "h5_chat_channel.py").read_text(encoding="utf-8")

    assert 'id="oeNodeMomentField"' in html
    assert 'id="oeChildMomentField"' in html
    assert 'id="oeNodeMomentPrev"' in html
    assert 'id="oeNodeMomentNext"' in html
    assert "var pageSize=20" in script
    assert "params.contact_wx_nos" in script
    assert "row.params.targets=row.params.contact_wx_nos.slice()" in script
    assert "contact_wx_nos:momentSelectionValues('child')" in script
    assert '_workflow_target_list(source, "contact_wx_nos", "targets", "contacts", "names")' in channel
    assert "function loadLocalWechatContacts()" in script
    assert "/api/native-wechat/contacts?account_id=pc-wechat-default" in script
    assert "loadDevices()" in script and "loadLocalWechatContacts()" in script
    assert '"/api/h5-chat/devices/status"' in channel
    assert "proxy_h5_chat_devices_status" in channel
