import pytest
import httpx

from backend.app.api import h5_chat_channel as channel
from backend.app.api.h5_chat_channel import _extract_parent_publish_context
from publisher.platform_publish_limits import normalize_publish_texts


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


def test_extracts_local_bestseller_subtitle_as_publish_source():
    context = channel._extract_parent_publish_context(
        {
            "task_kind": "client_workflow",
            "action": "local_bestseller_daily_video",
            "local_result": {
                "mode": "daily_video",
                "item": {
                    "title": "年龄层共鸣",
                    "subtitle_text": "国内最发达省份十强\n1. 江苏省\n2. 福建省",
                },
            },
        }
    )

    assert context["source_title"] == "年龄层共鸣"
    assert context["source_script"] == "国内最发达省份十强 1. 江苏省 2. 福建省"
    assert context["source_capability_id"] == "local_bestseller_daily_video"


def test_video_parent_material_never_falls_back_to_image(monkeypatch):
    monkeypatch.setattr(channel, "_local_asset_media_type_map", lambda asset_ids: {"scene-image": "image"})

    material = channel._extract_parent_material(
        {
            "local_result": {
                "item": {
                    "image_asset_id": "scene-image",
                    "image_url": "https://example.com/scene.png",
                }
            }
        },
        preferred_media_type="video",
    )

    assert material == {}


@pytest.mark.asyncio
async def test_local_bestseller_waits_for_final_video(monkeypatch):
    responses = iter(
        [
            {"ok": True, "status": "running"},
            {
                "ok": True,
                "status": "completed",
                "result": {
                    "final_video": {
                        "asset_id": "final-video",
                        "url": "https://example.com/final.mp4",
                        "kind": "local_bestseller_bgm_final",
                    }
                },
            },
        ]
    )

    async def get_local(path, **kwargs):
        assert path.startswith("/api/comfly-seedance-tvc/pipeline/jobs/job-1")
        return next(responses)

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(channel, "_get_local_api_json", get_local)
    monkeypatch.setattr(channel.asyncio, "sleep", no_sleep)

    result = await channel._wait_local_bestseller_video(
        {
            "item": {
                "title": "同城榜单",
                "video_job_id": "job-1",
                "video_poll_path": "/api/comfly-seedance-tvc/pipeline/jobs/job-1",
            }
        },
        headers={},
        poll_interval_seconds=0.1,
    )

    assert result["item"]["video_status"] == "completed"
    assert result["item"]["video_asset_id"] == "final-video"
    assert result["item"]["video_url"] == "https://example.com/final.mp4"


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
async def test_publish_content_preserves_douyin_origin_slot_account_id(monkeypatch):
    calls = []

    async def post_local(path, body, **kwargs):
        calls.append((path, body))
        return {"ok": True}

    monkeypatch.setattr(channel, "_post_local_api_json", post_local)

    await channel._run_client_workflow_action(
        "publish_content",
        {
            "platform": "douyin",
            "asset_id": "1a8bc876a535",
            "account_id": "-1002",
            "account_nickname": "抖音账号2",
            "media_type": "video",
            "ai_publish_copy": False,
        },
        headers={},
        run_id="publish-origin-slot",
    )

    assert calls[0][0] == "/api/publish"
    assert calls[0][1]["account_id"] == -1002
    assert calls[0][1]["account_nickname"] == "抖音账号2"


@pytest.mark.asyncio
async def test_douyin_child_without_script_still_generates_copy_on_server(monkeypatch):
    async def resolve_parent(*args, **kwargs):
        return {
            "asset_id": "bestseller-video",
            "url": "https://example.com/bestseller.mp4",
            "media_type": "video",
            "source_title": "同城榜单",
            "source_caption": "同城热门内容",
            "source_capability_id": "local_bestseller_daily_video",
            "source_run_id": "parent-run",
        }

    generated_calls = []

    async def generate_copy(**kwargs):
        generated_calls.append(kwargs)
        return {"title": "同城榜单发布标题", "description": "同城榜单发布正文", "tags": "#同城 #热门"}

    publish_calls = []

    async def post_local(path, body, **kwargs):
        publish_calls.append((path, body))
        return {"ok": True}

    monkeypatch.setattr(channel, "_resolve_parent_workflow_material", resolve_parent)
    monkeypatch.setattr(channel, "_generate_scheduled_publish_copy", generate_copy)
    monkeypatch.setattr(channel, "_post_local_api_json", post_local)

    await channel._run_client_workflow_action(
        "publish_content",
        {
            "platform": "douyin",
            "source_mode": "parent_latest_run",
            "source_workflow_node_id": "bestseller-node",
            "source_workflow_node_label": "创作同城爆款视频",
            "media_type": "video",
            "account_nickname": "默认抖音",
            "ai_publish_copy": True,
        },
        headers={},
        run_id="child-run",
        cloud=object(),
        base="https://example.com",
    )

    assert len(generated_calls) == 1
    assert generated_calls[0]["source_script"] == ""
    assert publish_calls[0][0] == "/api/publish"
    assert publish_calls[0][1]["ai_publish_copy"] is False
    assert publish_calls[0][1]["title"] == "同城榜单发布标题"
    assert publish_calls[0][1]["description"] == "同城榜单发布正文"


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


@pytest.mark.asyncio
async def test_moments_child_drops_explicit_source_title(monkeypatch):
    drafts = []

    async def submit_draft(*, draft, headers):
        drafts.append(draft)
        return {"ok": True}

    monkeypatch.setattr(channel, "_submit_local_publish_draft", submit_draft)

    await channel._run_client_workflow_action(
        "publish_content",
        {
            "platform": "wechat_moments",
            "url": "https://example.com/digital-human.mp4",
            "media_type": "video",
            "title": "AI增长｜实战！",
            "description": "朋友圈发布正文。",
            "tags": "#AI增长",
            "ai_publish_copy": False,
        },
        headers={},
        run_id="child-run",
        cloud=None,
        base="https://example.com",
    )

    assert drafts[0]["title"] == ""
    assert drafts[0]["description"] == "朋友圈发布正文。"


@pytest.mark.asyncio
async def test_wechat_moments_publish_waits_for_local_queue_final_status(monkeypatch):
    calls = []

    async def post_local(path, body, **kwargs):
        calls.append(("post", path, body))
        return {
            "ok": True,
            "queued": True,
            "task": {"id": "moments-task", "status": "pending"},
            "message": "queued",
        }

    poll_responses = iter(
        [
            {"ok": True, "task": {"id": "moments-task", "status": "running"}},
            {"ok": True, "task": {"id": "moments-task", "status": "success", "success": 1}},
        ]
    )

    async def get_local(path, **kwargs):
        calls.append(("get", path, None))
        return next(poll_responses)

    monkeypatch.setattr(channel, "_post_local_api_json", post_local)
    monkeypatch.setattr(channel, "_get_local_api_json", get_local)

    result = await channel._submit_local_publish_draft(
        draft={
            "platform": "wechat_moments",
            "description": "朋友圈正文",
            "account_id": "pc-wechat-default",
        },
        headers={},
    )

    assert result["status"] == "success"
    assert result["queued"] is False
    assert [call[1] for call in calls] == [
        "/api/native-wechat/moments/publish",
        "/api/native-wechat/tasks/moments-task",
        "/api/native-wechat/tasks/moments-task",
    ]


@pytest.mark.asyncio
async def test_wechat_channels_child_strips_symbols_from_short_title(monkeypatch):
    calls = []

    async def post_local(path, body, **kwargs):
        calls.append((path, body))
        return {"ok": True}

    monkeypatch.setattr(channel, "_post_local_api_json", post_local)

    result = await channel._run_client_workflow_action(
        "publish_content",
        {
            "platform": "wechat_channels",
            "asset_id": "video-asset",
            "media_type": "video",
            "account_nickname": "默认视频号",
            "title": "每天2000万人在豆包看病AI大健康要变天！",
            "description": "视频号发布正文。",
            "tags": "#AI增长",
            "ai_publish_copy": False,
        },
        headers={},
        run_id="child-run",
        cloud=None,
        base="https://example.com",
    )

    assert calls[0][0] == "/api/publish"
    assert calls[0][1]["title"] == "每天2000万人在豆包看病AI大"
    assert result["publish_copy"]["title"] == "每天2000万人在豆包看病AI大"


def test_wechat_channels_driver_limits_short_title_to_16_characters():
    title, description, tags, warnings = normalize_publish_texts(
        "wechat_channels",
        "video.mp4",
        "每天2000万人在豆包看病AI大健康要变天！",
        "视频号发布正文。",
        "AI健康",
    )

    assert title == "每天2000万人在豆包看病AI大"
    assert len(title) == 16
    assert description == "视频号发布正文。"
    assert tags == "AI健康"
    assert warnings


@pytest.mark.asyncio
async def test_local_bestseller_moments_child_publishes_final_video_not_scene_image(monkeypatch):
    class Cloud:
        async def get(self, url, **kwargs):
            if url.endswith("/api/scheduled-tasks/runs"):
                return httpx.Response(
                    200,
                    json={
                        "runs": [
                            {
                                "id": "bestseller-parent",
                                "status": "completed",
                                "created_at": "2026-07-29T14:42:00",
                                "finished_at": "2026-07-29T15:05:59",
                                "payload": {
                                    "h5_context": {
                                        "workflow_template_id": 7,
                                        "workflow_node_id": "bestseller-node",
                                    }
                                },
                            }
                        ]
                    },
                )
            assert url.endswith("/api/scheduled-tasks/runs/bestseller-parent")
            return httpx.Response(
                200,
                json={
                    "run": {
                        "id": "bestseller-parent",
                        "result_payload": {
                            "local_result": {
                                "mode": "daily_video",
                                "item": {
                                    "title": "Local bestseller",
                                    "subtitle_text": "Final video narration",
                                    "image_asset_id": "scene-image",
                                    "image_url": "https://example.com/scene.png",
                                    "video_asset_id": "final-video",
                                    "video_url": "https://example.com/final.mp4",
                                },
                                "final_video": {
                                    "asset_id": "final-video",
                                    "url": "https://example.com/final.mp4",
                                    "kind": "local_bestseller_bgm_final",
                                },
                            }
                        },
                    }
                },
            )

    monkeypatch.setattr(
        channel,
        "_local_asset_media_type_map",
        lambda asset_ids: {"scene-image": "image", "final-video": "video"},
    )

    async def generate_copy(**kwargs):
        assert kwargs["source_script"] == "Final video narration"
        return {"title": "", "description": "Moments copy", "tags": "#local"}

    drafts = []

    async def submit_draft(*, draft, headers):
        drafts.append(draft)
        return {"ok": True}

    monkeypatch.setattr(channel, "_generate_scheduled_publish_copy", generate_copy)
    monkeypatch.setattr(channel, "_submit_local_publish_draft", submit_draft)

    await channel._run_client_workflow_action(
        "publish_content",
        {
            "platform": "wechat_moments",
            "source_mode": "parent_latest_run",
            "source_workflow_node_id": "bestseller-node",
            "media_type": "image_text",
            "ai_publish_copy": True,
            "h5_context": {"workflow_template_id": 7},
            "schedule_config": {"timezone_offset_minutes": 480},
        },
        headers={},
        run_id="moments-child",
        current_item={
            "id": "moments-child",
            "created_at": "2026-07-29T14:50:00",
            "started_at": "2026-07-29T15:06:30",
        },
        cloud=Cloud(),
        base="https://example.com",
    )

    assert drafts[0]["asset_id"] == "final-video"
    assert drafts[0]["source_url"] == "https://example.com/final.mp4"
    assert drafts[0]["media_type"] == "video"
    assert drafts[0]["description"] == "Moments copy"


@pytest.mark.asyncio
async def test_parent_material_only_uses_current_workflow_day(monkeypatch):
    class Cloud:
        async def get(self, url, **kwargs):
            if url.endswith("/api/scheduled-tasks/runs"):
                return httpx.Response(
                    200,
                    json={
                        "runs": [
                            {
                                "id": "yesterday-parent",
                                "status": "completed",
                                "created_at": "2026-07-26T00:00:00",
                                "finished_at": "2026-07-26T00:30:00",
                                "payload": {
                                    "h5_context": {
                                        "workflow_template_id": 0,
                                        "workflow_node_id": "sales-video",
                                    }
                                },
                            },
                            {
                                "id": "today-parent",
                                "status": "completed",
                                "created_at": "2026-07-27T00:00:00",
                                "finished_at": "2026-07-27T00:30:00",
                                "payload": {
                                    "h5_context": {
                                        "workflow_template_id": 0,
                                        "workflow_node_id": "sales-video",
                                    }
                                },
                            },
                        ]
                    },
                )
            run_id = url.rsplit("/", 1)[-1]
            asset_id = "old-video" if run_id == "yesterday-parent" else "today-video"
            return httpx.Response(
                200,
                json={
                    "run": {
                        "id": run_id,
                        "result_payload": {
                            "saved_assets": [{"asset_id": asset_id, "media_type": "video"}]
                        },
                    }
                },
            )

    result = await channel._resolve_parent_workflow_material(
        Cloud(),
        "https://example.com",
        {},
        params={
            "source_workflow_node_id": "sales-video",
            "media_type": "video",
            "h5_context": {"workflow_template_id": 0},
            "schedule_config": {"timezone_offset_minutes": 480},
        },
        current_item={"id": "child", "created_at": "2026-07-27T00:45:00"},
    )

    assert result["asset_id"] == "today-video"
    assert result["source_run_id"] == "today-parent"


@pytest.mark.asyncio
async def test_parent_material_accepts_final_video_that_finished_after_child_schedule():
    class Cloud:
        async def get(self, url, **kwargs):
            if url.endswith("/api/scheduled-tasks/runs"):
                return httpx.Response(
                    200,
                    json={
                        "runs": [
                            {
                                "id": "delayed-parent",
                                "status": "completed",
                                "created_at": "2026-07-29T14:42:00",
                                "finished_at": "2026-07-29T15:05:59",
                                "payload": {
                                    "h5_context": {
                                        "workflow_template_id": 0,
                                        "workflow_node_id": "sales-video",
                                    }
                                },
                            }
                        ]
                    },
                )
            assert url.endswith("/api/scheduled-tasks/runs/delayed-parent")
            return httpx.Response(
                200,
                json={
                    "run": {
                        "id": "delayed-parent",
                        "result_payload": {
                            "final_video": {
                                "asset_id": "final-video",
                                "url": "https://example.com/final.mp4",
                            }
                        },
                    }
                },
            )

    result = await channel._resolve_parent_workflow_material(
        Cloud(),
        "https://example.com",
        {},
        params={
            "source_workflow_node_id": "sales-video",
            "media_type": "video",
            "h5_context": {"workflow_template_id": 0},
            "schedule_config": {"timezone_offset_minutes": 480},
        },
        current_item={
            "id": "child",
            "created_at": "2026-07-29T14:50:00",
            "started_at": "2026-07-29T15:06:30",
        },
    )

    assert result["asset_id"] == "final-video"
    assert result["source_run_id"] == "delayed-parent"


@pytest.mark.asyncio
async def test_parent_material_rejects_previous_day_when_today_has_none():
    class Cloud:
        async def get(self, url, **kwargs):
            if url.endswith("/api/scheduled-tasks/runs"):
                return httpx.Response(
                    200,
                    json={
                        "runs": [
                            {
                                "id": "yesterday-parent",
                                "status": "completed",
                                "finished_at": "2026-07-26T00:30:00",
                                "payload": {
                                    "h5_context": {
                                        "workflow_template_id": 0,
                                        "workflow_node_id": "sales-video",
                                    }
                                },
                            }
                        ]
                    },
                )
            raise AssertionError("previous-day parent detail must not be fetched")

    with pytest.raises(RuntimeError, match="上级节点还没有可发布的素材"):
        await channel._resolve_parent_workflow_material(
            Cloud(),
            "https://example.com",
            {},
            params={
                "source_workflow_node_id": "sales-video",
                "media_type": "video",
                "h5_context": {"workflow_template_id": 0},
                "schedule_config": {"timezone_offset_minutes": 480},
            },
            current_item={"id": "child", "created_at": "2026-07-27T00:45:00"},
        )
