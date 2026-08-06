from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_personal_profile_survey_renders_all_questions_on_one_page():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")
    view = (ROOT / "static" / "views" / "personal-settings.html").read_text(encoding="utf-8")

    questions_start = script.index("function profileQuestions()")
    questions_end = script.index("function profilePhotoAssetPreview", questions_start)
    questions_source = script[questions_start:questions_end]

    assert questions_source.count("{ field: 'ps") == 14
    assert 'id="psProfileQuestionList"' in view
    assert 'id="psProfileCompletionText"' in view
    assert "questions.map(function(question, idx)" in script
    assert "data-ps-profile-answer" in script


def test_personal_profile_survey_has_no_step_navigation():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")
    view = (ROOT / "static" / "views" / "personal-settings.html").read_text(encoding="utf-8")

    for legacy_id in (
        "psProfileAnswerHost",
        "psProfileQuestionTitle",
        "psProfileStepText",
        "psProfileProgress",
        "psProfilePrevBtn",
        "psProfileNextBtn",
    ):
        assert legacy_id not in script
        assert legacy_id not in view
    assert "profileIndex" not in script
    assert "moveProfile" not in script


def test_personal_profile_survey_keeps_existing_storage_fields():
    view = (ROOT / "static" / "views" / "personal-settings.html").read_text(encoding="utf-8")

    for field_id in (
        "psProfileName",
        "psGender",
        "psProfilePhoto",
        "psBirthEra",
        "psCurrentProvince",
        "psCurrentCity",
        "psHometown",
        "psRole",
        "psShareTopic",
        "psVideoStyle",
        "psAfterViewAction",
        "psBusinessProduct",
        "psTargetCustomer",
        "psAdvantages",
    ):
        assert f'id="{field_id}"' in view
