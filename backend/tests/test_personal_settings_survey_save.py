from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _settings_script() -> str:
    return (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")


def test_survey_save_renders_before_personal_default_sync():
    script = _settings_script()
    start = script.index("function saveProfile()")
    end = script.index("function applyTemplate(", start)
    block = script[start:end]

    assert "var item = data.item || {};" in block
    assert "state.editingSurveyId = item.id ? String(item.id) : '';" in block
    assert block.index("renderSurveyRecords();") < block.index("return saveCurrentDefault(")
    assert "资料调查已保存，但同步到个人默认配置失败" in block


def test_survey_load_failure_keeps_existing_rows():
    script = _settings_script()
    start = script.index("function loadSurveys()")
    end = script.index("function addCompetitorByChannelId(", start)
    block = script[start:end]

    assert "state.surveys = []; });" not in block
    assert "资料调查加载失败" in block
    assert "if (!Array.isArray(state.surveys)) state.surveys = [];" in block
