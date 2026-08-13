import sys
import types

import pytest

from backend.app.api.h5_chat_channel import _run_scheduled_douyin_sales_action


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
