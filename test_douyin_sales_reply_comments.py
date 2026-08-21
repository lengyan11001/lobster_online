import sys
import types

import pytest

from backend.app.api.h5_chat_channel import (
    _run_scheduled_douyin_batch_followups,
    _run_scheduled_douyin_sales_action,
    _scheduled_douyin_interaction_users,
)


def test_interaction_users_are_limited_to_current_collection_tasks(monkeypatch):
    fake = types.ModuleType("douyin_api")
    requested = []

    def collect(task_ids=None):
        requested.append(task_ids)
        rows = [
            {"username": "本次客户", "task_id": 10, "profile_url": "https://douyin.com/user/current"},
            {"username": "历史客户", "task_id": 9, "profile_url": "https://douyin.com/user/history"},
            {"username": "同行客户", "task_id": 0, "profile_url": "https://douyin.com/user/monitor"},
        ]
        return [row for row in rows if not task_ids or row["task_id"] in task_ids]

    fake.collect_douyin_interaction_users = collect
    monkeypatch.setitem(sys.modules, "douyin_api", fake)

    rows = _scheduled_douyin_interaction_users(10, [10])

    assert [row["username"] for row in rows] == ["本次客户"]
    assert requested == [{10}]


@pytest.mark.asyncio
async def test_batch_followups_reuse_the_collection_task_ids(monkeypatch):
    captured = []

    async def post_event(*_args, **_kwargs):
        return 200

    async def run_action(action, params):
        captured.append((action, params))
        return {"code": 200}

    monkeypatch.setattr("backend.app.api.h5_chat_channel._post_task_event", post_event)
    monkeypatch.setattr("backend.app.api.h5_chat_channel._load_scheduled_douyin_online_config_params", lambda action: {"action_config": action})
    monkeypatch.setattr("backend.app.api.h5_chat_channel._run_scheduled_douyin_sales_action", run_action)

    await _run_scheduled_douyin_batch_followups(
        object(),
        "https://server.example",
        {},
        "run-1",
        ["follow_comment", "direct_message"],
        [10, 12],
    )

    assert [item[0] for item in captured] == ["follow_comment", "direct_message"]
    assert all(item[1]["selected_task_ids"] == [10, 12] for item in captured)
    assert all(item[1]["customer_scope"] == "current_collection_batch" for item in captured)


@pytest.mark.asyncio
async def test_batch_followup_failure_does_not_block_remaining_actions(monkeypatch):
    calls = []

    async def post_event(*_args, **_kwargs):
        return 200

    async def run_action(action, _params):
        calls.append(action)
        if action == "follow_comment":
            raise RuntimeError("browser failed")
        return {"code": 200}

    monkeypatch.setattr("backend.app.api.h5_chat_channel._post_task_event", post_event)
    monkeypatch.setattr("backend.app.api.h5_chat_channel._load_scheduled_douyin_online_config_params", lambda _action: {})
    monkeypatch.setattr("backend.app.api.h5_chat_channel._run_scheduled_douyin_sales_action", run_action)

    results = await _run_scheduled_douyin_batch_followups(
        object(), "https://server.example", {}, "run-2", ["follow_comment", "direct_message"], [10]
    )

    assert calls == ["follow_comment", "direct_message"]
    assert results[0]["result"]["code"] == 500
    assert results[1]["result"]["code"] == 200


@pytest.mark.asyncio
async def test_reply_comments_limits_start_to_selected_task_ids(monkeypatch):
    starts = []
    fake = types.ModuleType("douyin_api")

    fake.douyin_tasks = [
        {"id": 10, "title": "target"},
        {"id": 11, "title": "other"},
    ]
    fake.ensure_douyin_task_shape = lambda row: dict(row)
    fake.get_commentable_douyin_tasks = lambda: [{"id": 10}, {"id": 11}, {"id": 12}]

    async def start_video_comment(request):
        starts.append(request)
        return {
            "code": 200,
            "msg": "started",
            "total": len(request["selected_task_ids"]),
            "interval_seconds_max": 0,
        }

    async def video_comment_status():
        return {
            "code": 200,
            "running": False,
            "state": {"total": 1, "processed": 1, "success": 1, "failed": 0},
        }

    async def idle_status(*_args, **_kwargs):
        return {"code": 200, "running": False, "state": {}}

    fake.douyin_start_video_comment = start_video_comment
    fake.douyin_video_comment_status = video_comment_status
    fake.douyin_follow_comment_status = idle_status
    fake.douyin_interaction_status = idle_status
    fake.douyin_mention_comment_status = idle_status
    monkeypatch.setitem(sys.modules, "douyin_api", fake)

    result = await _run_scheduled_douyin_sales_action(
        "reply_comments",
        {
            "selected_task_ids": [10],
            "max_users": 1,
            "comment_text": "hello",
            "interval_minutes_min": 1,
            "interval_minutes_max": 1,
        },
    )

    assert starts[0]["selected_task_ids"] == [10]
    assert result["total"] == 1
