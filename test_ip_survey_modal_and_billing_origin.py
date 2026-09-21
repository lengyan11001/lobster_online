"""个人 IP 资料调查弹窗关闭方式 + 消费记录消耗位置列。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_survey_editor_modal_closes_only_by_button():
    """资料调查新增/编辑弹窗：点遮罩不关、Esc 不关，只能点「关闭」/「×」。"""
    js = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")

    assert "if (modal.id === 'psSurveyEditorModal') return;" in js
    # Esc：资料调查打开时其余弹窗照关，它自己留着
    assert "surveyOpen" in js
    assert "var surveyModal = $('psSurveyEditorModal');" in js
    # 「关闭」按钮路径必须仍然有效
    assert "data-close-ps-editor" in js


def test_billing_credit_history_has_consumption_origin_column():
    """消费记录列表新增「消耗位置」列（origin 由服务端 credit-history 提供）。"""
    js = (ROOT / "static" / "js" / "views" / "billing.js").read_text(encoding="utf-8")
    registry = (ROOT / "static" / "js" / "view-registry.js").read_text(encoding="utf-8")

    assert ">消耗位置</th>" in js
    assert "var origin = (h.origin != null" in js
    assert "escapeHtml(origin)" in js
    # 旧接口没有 origin 时要有兜底，不能出现空列
    assert "(typeText || '-')" in js
    assert "20260921-credit-history-origin-v1" in registry
    assert "20260921-survey-modal-close-only-v1" in registry
