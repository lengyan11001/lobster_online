import asyncio

import backend.app.api.h5_chat_channel as h5_chat_channel
from backend.app.api.h5_chat_channel import (
    _merge_scheduled_douyin_collection_params,
    _merge_scheduled_douyin_stranger_params,
    _scheduled_douyin_changed_conversations,
    _scheduled_douyin_followup_actions,
    _scheduled_douyin_online_config_params,
    _scheduled_douyin_result_payload,
    _scheduled_douyin_sales_action_from_context,
    _scheduled_douyin_search_keywords,
)


def test_h5_employee_editor_exposes_collection_followup_actions():
    from pathlib import Path

    root = Path(__file__).resolve().parent
    script = (root / "static" / "js" / "views" / "h5-employees.js").read_text(encoding="utf-8")
    html = (root / "static" / "views" / "h5-employees.html").read_text(encoding="utf-8")

    assert "oeNodeDouyinFollowupField" in html
    assert "customer_scope:'current_collection_batch'" in script
    assert "migrateDouyinFollowupNodes" in script
    for field_id in (
        "oeNodeDouyinKeyword",
        "oeNodeDouyinRegions",
        "oeNodeDouyinMaxResults",
        "oeNodeDouyinMode",
        "oeNodeDouyinFollowupReplyComments",
        "oeNodeDouyinFollowupMentionComment",
        "oeNodeDouyinFollowupFollowComment",
        "oeNodeDouyinFollowupDirectMessage",
    ):
        assert f'id="{field_id}"' in html
    assert "row.params.keyword=keyword" in script
    assert "el('oeNodeDouyinKeyword').value" in script
    assert "return saveTemplate().then" in script


def test_collection_followups_default_all_but_keep_explicit_empty():
    assert _scheduled_douyin_followup_actions(None, default_all=True) == [
        "reply_comments",
        "mention_comment",
        "follow_comment",
        "direct_message",
    ]
    assert _scheduled_douyin_followup_actions([], default_all=True) == []


def test_collection_node_params_override_online_defaults():
    params = _merge_scheduled_douyin_collection_params(
        {
            "keyword": "Online 全局关键词",
            "regions": ["全国"],
            "max_results": 50,
            "mode": "script",
        },
        {
            "keyword": "服务器节点关键词",
            "regions": ["深圳", "东莞"],
            "max_results": 80,
            "mode": "api",
            "followup_actions": ["direct_message", "reply_comments"],
        },
    )

    assert params["keyword"] == "服务器节点关键词"
    assert params["regions"] == ["深圳", "东莞"]
    assert params["max_results"] == 80
    assert params["mode"] == "api"
    assert params["followup_actions"] == ["reply_comments", "direct_message"]
    assert params["customer_scope"] == "current_collection_batch"


def test_collection_node_explicit_empty_actions_remains_empty():
    params = _merge_scheduled_douyin_collection_params({}, {"followup_actions": []})
    assert params["followup_actions"] == []


def test_collection_workflow_title_keeps_online_keywords_and_followups():
    params = _merge_scheduled_douyin_collection_params(
        {
            "keyword": "AI赚钱",
            "keywords": ["AI赚钱", "AI直播", "AI短视频", "AI获客", "AI智能体", "AI"],
            "max_results": 50,
        },
        {
            "keyword": "抖音获客·关键词抓取精准客户",
            "followup_actions": [
                "reply_comments",
                "mention_comment",
                "follow_comment",
                "direct_message",
            ],
        },
    )

    assert params["keyword"] == "AI赚钱"
    assert params["keywords"] == ["AI赚钱", "AI直播", "AI短视频", "AI获客", "AI智能体", "AI"]
    assert params["followup_actions"] == [
        "reply_comments",
        "mention_comment",
        "follow_comment",
        "direct_message",
    ]
    assert params["customer_scope"] == "current_collection_batch"


def test_stranger_workflow_explicit_false_overrides_online_config():
    params = _merge_scheduled_douyin_stranger_params(
        {"wechat_add_friend_enabled": True, "message": "saved reply"},
        {"wechat_add_friend_enabled": False},
    )

    assert params["wechat_add_friend_enabled"] is False
    assert params["message"] == "saved reply"


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
        "keywords": ["本机配置关键词", "旧搜索关键词"],
        "keyword": "本机配置关键词",
        "max_results": 36,
        "max_videos_per_run": 4,
        "comment_scroll_rounds": 88,
        "comment_max_comments": 166,
        "mode": "script",
    }


def test_search_action_uses_all_enabled_online_keywords():
    params = _scheduled_douyin_online_config_params(
        "search_collect",
        plans=[
            {"type": "collect_precise", "keyword": "工业机器人", "enabled": True, "updated_at": "2"},
            {"type": "collect_precise", "keyword": "数控加工", "enabled": True, "updated_at": "1"},
            {"type": "collect_precise", "keyword": "已停用", "enabled": False, "updated_at": "3"},
        ],
        search_sessions=[
            {"keyword": "精密零件", "updated_at": 3},
            {"keyword": "工业机器人", "updated_at": 2},
        ],
    )

    assert params["keywords"] == ["工业机器人", "数控加工", "精密零件"]
    assert _scheduled_douyin_search_keywords(params) == ["工业机器人", "数控加工", "精密零件"]


def test_multi_keyword_collection_runs_each_keyword_and_merges_tasks(monkeypatch):
    calls = []

    async def fake_single(params):
        keyword = params["keyword"]
        calls.append(keyword)
        task_id = len(calls)
        return {
            "code": 200,
            "msg": "ok",
            "keyword": keyword,
            "search_total": 10,
            "selected_task_ids": [task_id],
            "selected_videos_total": 1,
            "selected_item_keys": [f"video:{task_id}"],
            "session_id": f"session:{task_id}",
            "items": [{"title": keyword}],
        }

    async def fake_wait(task_ids):
        return {"status": "done", "tasks": [{"id": task_ids[0], "status": "completed"}]}

    monkeypatch.setattr(h5_chat_channel, "_run_scheduled_douyin_single_search_collect_action", fake_single)
    monkeypatch.setattr(h5_chat_channel, "_wait_for_douyin_collect_completion", fake_wait)

    result = asyncio.run(
        h5_chat_channel._run_scheduled_douyin_search_collect_action(
            {"keywords": ["工业机器人", "数控加工", "精密零件"]}
        )
    )

    assert calls == ["工业机器人", "数控加工", "精密零件"]
    assert result["selected_task_ids"] == [1, 2, 3]
    assert result["search_total"] == 30
    assert [row["collection_status"] for row in result["keyword_summaries"]] == ["done", "done", "done"]


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
                "wechat_add_friend_enabled": True,
            }
        ],
    )

    assert params["account_id"] == 5
    assert params["interval_minutes"] == 9
    assert params["max_users"] == 40
    assert params["auto_reply_enabled"] is True
    assert params["reply_mode"] == "ai_lead"
    assert params["contact_value"] == "wx-local"
    assert params["wechat_add_friend_enabled"] is True


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
