from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from starlette.requests import Request

from backend.app.api import local_bestseller

ROOT = Path(__file__).resolve().parents[2]
NO_PERSON_DAYS = {1, 3, 4, 5, 8}


def _profile() -> dict:
    return local_bestseller._clean_profile(
        local_bestseller.LocalBestsellerProfile(
            name="张想",
            nickname="张想",
            gender="female",
            identity="女老板",
            industry="大健康",
            city="深圳",
            province="广东",
        )
    )


def _cards() -> list:
    profile = _profile()
    return [local_bestseller._build_card(row, profile) for row in local_bestseller._load_templates()[:30]]


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/local-bestseller/scene/generate",
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
        }
    )


def test_no_person_days_are_unchanged():
    assert local_bestseller._city_scene_only_days() == NO_PERSON_DAYS


def test_scene_only_days_never_ask_for_a_person():
    for card in _cards():
        if int(card["day"]) not in NO_PERSON_DAYS:
            continue
        prompt = card["video_prompt"]
        assert "第4-6秒" not in prompt, card["day"]
        assert "人物要自然抬头看向镜头" not in prompt, card["day"]
        assert "人物正常走动" not in prompt, card["day"]
        assert "无声自然动作" not in prompt, card["day"]
        assert "全片不要出现人物主体" in prompt, card["day"]
        assert "远景或虚化的背景点缀" in prompt, card["day"]
        assert "不要生成人物主体" in card["scene_prompt"], card["day"]
        assert "人形像必须仍以用户人物照片为准" not in card["scene_prompt"], card["day"]


def test_person_days_keep_identity_and_mid_look():
    for card in _cards():
        if int(card["day"]) in NO_PERSON_DAYS:
            continue
        prompt = card["video_prompt"]
        assert "第4-6秒" in prompt, card["day"]
        assert "人物身份保持不变" in prompt, card["day"]
        assert "全片不要出现人物主体" not in prompt, card["day"]
        assert "必须以用户上传的人物照片为唯一身份参考" in card["scene_prompt"], card["day"]


def test_scene_only_sanitizer_is_idempotent():
    profile = _profile()
    card = local_bestseller._build_card(local_bestseller._load_templates()[0], profile)
    again = local_bestseller._sanitize_video_prompt_no_speech(card["video_prompt"], scene_only=True)
    assert again.count("全片不要出现人物主体") == 1
    assert "第4-6秒" not in again


def test_scene_only_day_does_not_attach_person_photo(monkeypatch):
    monkeypatch.setattr(
        local_bestseller,
        "get_asset_public_url",
        lambda *_args, **_kwargs: "https://example.com/scene.jpg",
    )
    urls = local_bestseller._resolve_reference_urls(
        profile={"photo_url": "https://example.com/person.jpg", "photo_asset_id": "photo-1"},
        card={"day": 1, "scene_url": "https://example.com/scene.jpg"},
        current_user=SimpleNamespace(id=1),
        request=_request(),
        db=None,
    )
    assert urls == ["https://example.com/scene.jpg"]


def test_frontend_sanitizer_matches_scene_only_days():
    script = (ROOT / "static" / "js" / "local-bestseller.js").read_text(encoding="utf-8")
    assert "var CITY_SCENE_ONLY_DAYS = [1, 3, 4, 5, 8];" in script
    assert "function isCitySceneOnlyDay(day)" in script
    assert "if (sceneOnly) {" in script
    assert script.count("sanitizeVideoPromptNoSpeech(item.video_prompt || '', isCitySceneOnlyDay(item.day))") == 2
    assert "全片不要出现人物主体" in script
