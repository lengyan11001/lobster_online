import pytest

from backend.app.api import h5_chat_channel as channel
from backend.app.api.h5_chat_channel import _extract_parent_publish_context


def test_extracts_ip_daily_oral_script_for_child_publish():
    context = _extract_parent_publish_context(
        {
            "capability_id": "hifly.video.create_by_tts",
            "generated": {
                "title": "fallback title",
                "script": "This is the actual oral script.",
                "language": "en-US",
                "ip_daily_record": {
                    "title": "IP daily title",
                    "body": "This is the actual oral script.",
                    "tags": ["AI", "sales"],
                },
            },
            "caption": "parent caption",
            "skill_prompt": "stale task prompt",
        }
    )

    assert context["source_script"] == "This is the actual oral script."
    assert context["source_title"] == "IP daily title"
    assert context["source_caption"] == "parent caption"
    assert context["source_tags"] == "#AI #sales"
    assert context["source_language"] == "en-US"
    assert context["source_capability_id"] == "hifly.video.create_by_tts"


def test_actual_generated_script_wins_over_stale_skill_prompt():
    context = _extract_parent_publish_context(
        {
            "generated": {"script": "new generated script"},
            "skill_prompt": "old node title or prompt",
        }
    )

    assert context["source_script"] == "new generated script"


def test_extracts_script_from_digital_human_2_local_result():
    context = _extract_parent_publish_context(
        {
            "task_kind": "client_workflow",
            "action": "shanjian_digital_human_video",
            "local_result": {
                "action": "shanjian_digital_human_video",
                "title": "数字人口播标题",
                "script": "数字人二点零实际使用的口播文案。",
                "language": "zh-CN",
                "caption_hint": "行业热点",
                "ip_daily_record": {"title": "IP日更标题"},
            },
        }
    )

    assert context["source_script"] == "数字人二点零实际使用的口播文案。"
    assert context["source_title"] == "IP日更标题"
    assert context["source_caption"] == "行业热点"
    assert context["source_language"] == "zh-CN"
    assert context["source_capability_id"] == "hifly.video.create_by_tts"


@pytest.mark.asyncio
async def test_douyin_child_uses_oral_script_copy_without_second_rewrite(monkeypatch):
    async def resolve_parent(*args, **kwargs):
        return {
            "asset_id": "video-asset",
            "media_type": "video",
            "source_script": "The real oral script controls the publishing copy.",
            "source_title": "Oral script title",
            "source_capability_id": "hifly.video.create_by_tts",
            "source_run_id": "parent-run",
        }

    async def generate_copy(**kwargs):
        assert kwargs["source_script"] == "The real oral script controls the publishing copy."
        assert kwargs["platform"] == "douyin"
        return {
            "title": "Generated title",
            "description": "Generated description",
            "tags": "#AI #sales",
        }

    calls = []

    async def post_local(path, body, **kwargs):
        calls.append((path, body))
        return {"ok": True}

    monkeypatch.setattr(channel, "_resolve_parent_workflow_material", resolve_parent)
    monkeypatch.setattr(channel, "_generate_scheduled_publish_copy", generate_copy)
    monkeypatch.setattr(channel, "_post_local_api_json", post_local)

    result = await channel._run_client_workflow_action(
        "publish_content",
        {
            "platform": "douyin",
            "source_mode": "parent_latest_run",
            "source_workflow_node_id": "digital-human-node",
            "account_nickname": "default douyin",
            "ai_publish_copy": True,
        },
        headers={},
        run_id="child-run",
        cloud=object(),
        base="https://example.com",
    )

    assert calls[0][0] == "/api/publish"
    body = calls[0][1]
    assert body["title"] == "Generated title"
    assert body["description"] == "Generated description"
    assert body["tags"] == "#AI #sales"
    assert body["ai_publish_copy"] is False
    assert body["options"]["_source_prompt"] == "The real oral script controls the publishing copy."
    assert result["publish_copy"]["description"] == "Generated description"


@pytest.mark.asyncio
async def test_moments_child_uses_oral_script_description_and_no_generated_title(monkeypatch):
    async def resolve_parent(*args, **kwargs):
        return {
            "source_url": "https://example.com/digital-human.mp4",
            "url": "https://example.com/digital-human.mp4",
            "media_type": "video",
            "source_script": "这是数字人实际使用的口播文案。",
            "source_title": "口播主题",
            "source_run_id": "parent-run",
        }

    async def generate_copy(**kwargs):
        return {"title": "不应显示的标题", "description": "根据口播生成的朋友圈正文", "tags": "#行业分享"}

    drafts = []

    async def submit_draft(*, draft, headers):
        drafts.append(draft)
        return {"ok": True}

    monkeypatch.setattr(channel, "_resolve_parent_workflow_material", resolve_parent)
    monkeypatch.setattr(channel, "_generate_scheduled_publish_copy", generate_copy)
    monkeypatch.setattr(channel, "_submit_local_publish_draft", submit_draft)

    await channel._run_client_workflow_action(
        "publish_content",
        {
            "platform": "wechat_moments",
            "source_mode": "parent_latest_run",
            "source_workflow_node_id": "digital-human-node",
            "ai_publish_copy": True,
        },
        headers={},
        run_id="child-run",
        cloud=object(),
        base="https://example.com",
    )

    assert drafts[0]["title"] == ""
    assert drafts[0]["description"] == "根据口播生成的朋友圈正文"
    assert drafts[0]["tags"] == "#行业分享"
