import pytest

from backend.app.api import chat
from backend.app.services import publish_copy_llm


def test_wechat_channels_publish_copy_prompt_uses_16_character_limit():
    limits = publish_copy_llm._platform_copy_limits("wechat_channels", "video")

    assert "最多 16 个字" in limits
    assert "不要标点" in limits


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested_model", "expected_model"),
    [
        ("openai/gpt-5.6-sol", "openai/gpt-5.6-sol"),
        ("sutui", "server-default-model"),
        ("", "server-default-model"),
    ],
)
async def test_online_publish_copy_always_routes_to_server(
    monkeypatch,
    requested_model,
    expected_model,
):
    monkeypatch.setattr(publish_copy_llm.settings, "lobster_edition", "online")
    monkeypatch.setattr(publish_copy_llm.settings, "auth_server_base", "https://server.example")
    monkeypatch.setattr(
        publish_copy_llm.settings,
        "lobster_orchestration_sutui_chat_model",
        "server-default-model",
    )

    calls = []

    async def fake_chat_openai(messages, cfg, tools, raw_token, sutui_token=None, **kwargs):
        calls.append(
            {
                "cfg": cfg,
                "raw_token": raw_token,
                "override_url": kwargs.get("override_url"),
                "override_headers": kwargs.get("override_headers"),
            }
        )
        return '{"title":"Title","description":"Description","tags":"tag"}'

    monkeypatch.setattr(chat, "_chat_openai", fake_chat_openai)

    result = await publish_copy_llm.generate_publish_copy(
        platform="douyin",
        media_type="video",
        asset_prompt="Source script",
        filename="video.mp4",
        raw_token="server-token",
        chat_model=requested_model,
    )

    assert result == ("Title", "Description", "tag")
    assert calls[0]["cfg"]["provider"] == "sutui"
    assert calls[0]["cfg"]["model_name"] == expected_model
    assert calls[0]["raw_token"] == "server-token"
    assert calls[0]["override_url"] == "https://server.example/api/sutui-chat/completions"
    assert calls[0]["override_headers"]["Authorization"] == "Bearer server-token"


@pytest.mark.asyncio
async def test_online_publish_copy_never_falls_back_to_local_key(monkeypatch):
    monkeypatch.setattr(publish_copy_llm.settings, "lobster_edition", "online")
    monkeypatch.setattr(publish_copy_llm.settings, "auth_server_base", "https://server.example")

    def fail_local_model_lookup():
        raise AssertionError("local model lookup must not run in online edition")

    monkeypatch.setattr(chat, "_pick_default_model", fail_local_model_lookup)

    with pytest.raises(publish_copy_llm.PublishCopyLLMError, match="登录状态无效"):
        await publish_copy_llm.generate_publish_copy(
            platform="douyin",
            media_type="video",
            asset_prompt="Source script",
            filename="video.mp4",
            raw_token="",
        )


@pytest.mark.asyncio
async def test_publish_copy_prompt_defaults_to_chinese_tags_when_language_missing(monkeypatch):
    """没传模板语种时也要锁中文标签（模板选了中文却出英文 tags 的兜底）。"""
    monkeypatch.setattr(publish_copy_llm.settings, "lobster_edition", "online")
    monkeypatch.setattr(publish_copy_llm.settings, "auth_server_base", "https://server.example")
    monkeypatch.setattr(
        publish_copy_llm.settings,
        "lobster_orchestration_sutui_chat_model",
        "server-default-model",
    )
    captured = {}

    async def fake_chat_openai(messages, cfg, tools, raw_token, sutui_token=None, **kwargs):
        captured["system"] = messages[0]["content"]
        return '{"title":"客服场景","description":"正文","tags":"同城,客服"}'

    monkeypatch.setattr(chat, "_chat_openai", fake_chat_openai)

    result = await publish_copy_llm.generate_publish_copy(
        platform="douyin",
        media_type="video",
        asset_prompt="客服把客户地址发到群里，信息全曝光。",
        filename="video.mp4",
        raw_token="server-token",
        chat_model="sutui",
    )

    assert "简体中文" in captured["system"]
    assert "话题标签" in captured["system"]
    assert result[2] == "同城,客服"
