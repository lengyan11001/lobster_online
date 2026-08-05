from backend.app.api.h5_chat_channel import (
    _scheduled_douyin_online_config_params,
    _scheduled_douyin_sales_action_from_context,
)


def test_old_sales_workflow_context_recovers_the_real_action():
    context = {"department_id": "sales", "ability_label": "抖音自己评论区接管"}

    assert _scheduled_douyin_sales_action_from_context(context) == "mention_comment"


def test_search_action_uses_online_plan_instead_of_ip_persona_fields():
    params = _scheduled_douyin_online_config_params(
        "search_collect",
        config={"douyin_default_account_id": 3, "comment_scroll_rounds": 88, "comment_max_comments": 166},
        plans=[
            {
                "type": "collect_precise",
                "keyword": "本机配置关键词",
                "max_results": 36,
                "max_videos_per_run": 4,
                "updated_at": "2026-08-05 12:00:00",
            }
        ],
        search_sessions=[{"keyword": "旧搜索关键词", "updated_at": 1}],
    )

    assert params == {
        "account_id": 3,
        "keyword": "本机配置关键词",
        "max_results": 36,
        "max_videos_per_run": 4,
        "comment_scroll_rounds": 88,
        "comment_max_comments": 166,
        "mode": "script",
    }


def test_direct_message_action_uses_online_interaction_plan():
    params = _scheduled_douyin_online_config_params(
        "direct_message",
        config={"douyin_default_account_id": 2},
        plans=[
            {
                "type": "interaction",
                "message_mode": "rewrite",
                "message_seed_text": "本机私信基准话术",
                "message_prompt": "按客户信息自然改写",
                "max_users_per_run": 12,
                "interaction_interval_minutes_min": 3,
                "interaction_interval_minutes_max": 7,
            }
        ],
    )

    assert params["account_id"] == 2
    assert params["message_mode"] == "rewrite"
    assert params["message_seed_text"] == "本机私信基准话术"
    assert params["max_users"] == 12
    assert params["interval_minutes_min"] == 3
    assert params["interval_minutes_max"] == 7


def test_stranger_takeover_reuses_online_monitor_configuration():
    params = _scheduled_douyin_online_config_params(
        "stranger_message",
        config={"douyin_default_account_id": 5},
        stranger_monitors=[
            {
                "account_id": 5,
                "enabled": True,
                "interval_minutes": 9,
                "max_conversations": 40,
                "auto_reply_enabled": True,
                "reply_mode": "ai_lead",
                "reply_prompt": "基于本机资料回复",
                "contact_value": "wx-local",
            }
        ],
    )

    assert params["account_id"] == 5
    assert params["interval_minutes"] == 9
    assert params["max_users"] == 40
    assert params["auto_reply_enabled"] is True
    assert params["reply_mode"] == "ai_lead"
    assert params["contact_value"] == "wx-local"
