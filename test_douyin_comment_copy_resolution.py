# -*- coding: utf-8 -*-
"""Regression: touch copy comes from Online's saved settings, not workflow nodes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.app.api.h5_chat_channel import (  # noqa: E402
    _merge_scheduled_douyin_precise_touch_params,
    _scheduled_douyin_fixed_text,
    _scheduled_douyin_online_config_params,
)

VIDEO_AI_SETTINGS = {
    "douyin-video-comment-presets-v1": {
        "activeIndex": 0,
        "presets": [
            {
                "mode": "ai",
                "comment_text": "",
                "comment_prompt": "结合视频内容写一句评论",
                "comment_seed_text": "",
            }
        ],
    }
}

SHARED_AI_PLANS = [
    {
        "id": 1,
        "type": "collect_precise",
        "updated_at": "2026-10-18 10:00:00",
        "reply_precise_comments": True,
        "reply_comment_mode": "ai",
        "reply_comment_prompt": "计划里的方向",
    }
]


def test_precise_touch_uses_online_video_comment_ai_preset():
    params = _scheduled_douyin_online_config_params(
        "precise_touch", plans=[], local_settings=VIDEO_AI_SETTINGS
    )
    assert params["comment_mode"] == "ai"
    assert params["comment_prompt"] == "结合视频内容写一句评论"


def test_follow_comment_uses_follow_preset_then_video_preset():
    settings = {
        "douyin-follow-comment-presets-v1": {
            "activeIndex": 0,
            "presets": [{"mode": "fixed", "comment_text": ""}],
        },
        "douyin-video-comment-presets-v1": {
            "activeIndex": 0,
            "presets": [{"mode": "ai", "comment_prompt": "视频评论方向"}],
        },
    }
    params = _scheduled_douyin_online_config_params(
        "follow_comment", plans=[], local_settings=settings
    )
    assert params["comment_mode"] == "ai"
    assert params["comment_prompt"] == "视频评论方向"


def test_older_install_falls_back_to_scheduler_plan_copy():
    params = _scheduled_douyin_online_config_params(
        "follow_comment", plans=SHARED_AI_PLANS, local_settings={}
    )
    assert params["comment_mode"] == "ai"
    assert params["comment_prompt"] == "计划里的方向"


def test_workflow_node_copy_does_not_override_online_config():
    merged = _merge_scheduled_douyin_precise_touch_params(
        {"comment_mode": "fixed", "comment_text": "online"},
        {"reply_comment_mode": "ai", "reply_comment_prompt": "node",
         "touch_actions": ["follow_comment"]},
    )
    assert merged["comment_mode"] == "fixed"
    assert merged["comment_text"] == "online"
    assert "reply_comment_mode" not in merged
    assert "comment_prompt" not in merged


def test_direct_message_uses_online_interaction_preset():
    settings = {
        "douyin-interaction-message-presets-v1": {
            "activeIndex": 1,
            "presets": ["第一条", "第二条私信"],
        },
        "douyin-interaction-mode-v1": "fixed",
    }
    params = _scheduled_douyin_online_config_params(
        "direct_message", plans=[], local_settings=settings
    )
    assert params["message_mode"] == "fixed"
    assert params["message"] == "第二条私信"


def test_missing_comment_copy_never_falls_back_to_hardcoded_text():
    assert _scheduled_douyin_fixed_text({}, "comment") == ""

def test_active_preset_slot_wins_over_older_filled_slot():
    settings = {
        "douyin-video-comment-presets-v1": {
            "activeIndex": 2,
            "presets": [
                {"mode": "fixed", "comment_text": "旧话术"},
                {"mode": "fixed", "comment_text": "旧话术2"},
                {"mode": "ai", "comment_prompt": "当前选中的话术槽"},
            ],
        }
    }
    params = _scheduled_douyin_online_config_params(
        "precise_touch", plans=[], local_settings=settings
    )
    assert params["comment_mode"] == "ai"
    assert params["comment_prompt"] == "当前选中的话术槽"

def test_resolved_copy_reports_online_source():
    local_params = _scheduled_douyin_online_config_params(
        "follow_comment", plans=[], local_settings=VIDEO_AI_SETTINGS
    )
    assert local_params["_copy_source"] == "local:video"
    plan_params = _scheduled_douyin_online_config_params(
        "follow_comment", plans=SHARED_AI_PLANS, local_settings={}
    )
    assert plan_params["_copy_source"] == "plan:collect_precise"
