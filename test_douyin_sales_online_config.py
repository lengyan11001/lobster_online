from backend.app.api.h5_chat_channel import (
    _scheduled_douyin_changed_conversations,
    _scheduled_douyin_online_config_params,
    _scheduled_douyin_result_payload,
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


def test_sales_result_payload_exposes_readable_fields_at_top_level():
    result = {
        "code": 200,
        "msg": "主动私信执行完成",
        "summary": "共 2 人，成功 1 人，失败 1 人",
        "status": "done",
        "account_id": 2,
        "stats": {"total": 2, "processed": 2, "success": 1, "failed": 1},
        "final_state": {"running": False, "processed": 2},
        "users": [
            {"username": "客户甲", "status": "sent", "sent_text": "您好"},
            {"username": "客户乙", "status": "failed", "error": "账号限制"},
        ],
    }

    payload = _scheduled_douyin_result_payload("direct_message", result, {"account_id": 2})

    assert payload["summary"] == result["summary"]
    assert payload["stats"]["success"] == 1
    assert payload["users"][0]["sent_text"] == "您好"
    assert payload["final_state"]["running"] is False
    assert payload["mcp_result"] == result


def test_stranger_takeover_returns_changed_incoming_and_reply_content_only():
    before = [
        {
            "conversation_key": "old",
            "username": "旧客户",
            "incoming_message": "旧消息",
            "reply_status": "sent",
            "reply_message": "旧回复",
        }
    ]
    after = [
        dict(before[0]),
        {
            "conversation_key": "new",
            "username": "新客户",
            "incoming_message": "这个产品怎么报价？",
            "unread_count": 1,
            "reply_status": "sent",
            "reply_message": "您好，请告诉我需要的数量。",
            "reply_updated_at": "2026-08-05 16:00:00",
        },
    ]

    rows = _scheduled_douyin_changed_conversations(before, after)

    assert len(rows) == 1
    assert rows[0]["username"] == "新客户"
    assert rows[0]["incoming_message"] == "这个产品怎么报价？"
    assert rows[0]["reply_message"] == "您好，请告诉我需要的数量。"
