# -*- coding: utf-8 -*-
"""Regression: 视频评论 copy must follow the Online setting, never a hardcoded line."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.app.api.h5_chat_channel import (  # noqa: E402
    _merge_scheduled_douyin_precise_touch_params,
    _scheduled_douyin_fixed_text,
    _scheduled_douyin_online_config_params,
)

SHARED_AI_PLANS = [
    {
        "id": 1,
        "type": "collect_precise",
        "updated_at": "2026-10-18 10:00:00",
        "reply_precise_comments": True,
        "reply_comment_mode": "ai",
        "reply_comment_prompt": "结合视频内容写一句评论",
    }
]


def test_precise_touch_uses_video_comment_ai_copy():
    params = _scheduled_douyin_online_config_params("precise_touch", plans=SHARED_AI_PLANS)
    assert params["comment_mode"] == "ai"
    assert params["comment_prompt"] == "结合视频内容写一句评论"


def test_follow_comment_with_empty_legacy_plan_falls_back_to_video_comment_copy():
    plans = [
        {"type": "follow_comment", "updated_at": "2026-10-18 11:00:00",
         "comment_mode": "fixed", "comment_text": ""},
        {"type": "collect_precise", "reply_comment_mode": "ai",
         "reply_comment_prompt": "p"},
    ]
    params = _scheduled_douyin_online_config_params("follow_comment", plans=plans)
    assert params["comment_mode"] == "ai"
    assert params["comment_prompt"] == "p"


def test_workflow_node_reply_comment_copy_wins_in_precise_touch_merge():
    merged = _merge_scheduled_douyin_precise_touch_params(
        {"comment_mode": "fixed", "comment_text": "old"},
        {"reply_comment_mode": "ai", "reply_comment_prompt": "x",
         "touch_actions": ["follow_comment"]},
    )
    assert merged["comment_mode"] == "ai"
    assert merged["comment_prompt"] == "x"


def test_missing_comment_copy_never_falls_back_to_hardcoded_text():
    assert _scheduled_douyin_fixed_text({}, "comment") == ""
