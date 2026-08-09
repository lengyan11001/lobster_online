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
    assert "20260808-workflow-children-v2" in registry
