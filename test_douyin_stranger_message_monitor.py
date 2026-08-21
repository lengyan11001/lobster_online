import asyncio
import sys
from datetime import datetime
from pathlib import Path


DOUYIN_ORIGIN_ROOT = Path(__file__).resolve().parent / "backend" / "douyin_origin"
sys.path.insert(0, str(DOUYIN_ORIGIN_ROOT))

import douyin_api  # noqa: E402
from douyin_comment_scraper import conversation_time_is_older_than_24h  # noqa: E402
from douyin_api import (  # noqa: E402
    extract_douyin_mainland_mobile_numbers,
    generate_douyin_stranger_reply_message,
    normalize_douyin_stranger_message_monitor_state,
)


def test_empty_legacy_fixed_reply_migrates_to_ai_auto():
    state = normalize_douyin_stranger_message_monitor_state(
        {
            "reply_mode": "fixed",
            "reply_message": "",
            "wechat_add_friend_enabled": True,
        },
        account_id=7,
    )

    assert state["reply_mode"] == "ai_auto"
    assert state["wechat_add_friend_enabled"] is True


def test_extract_phone_numbers_from_new_message_text_only_once():
    numbers = extract_douyin_mainland_mobile_numbers(
        [
            {"incoming_message": "phone 139 2748 5337 or 139-2748-5337"},
            {"content": "another 18823851682, invalid 12812345678"},
        ]
    )

    assert numbers == ["13927485337", "18823851682"]


def test_extract_phone_numbers_ignores_outgoing_messages_and_stored_numbers():
    numbers = extract_douyin_mainland_mobile_numbers(
        [
            {
                "conversation_key": "same-contact",
                "last_message_is_user": True,
                "phone_numbers": ["18823851682", "13927485337"],
                "messages": [
                    {"direction": "outgoing", "text": "18823851682"},
                    {"direction": "incoming", "text": "13927485337"},
                ],
            }
        ]
    )

    assert numbers == ["13927485337"]


def test_ai_lead_reply_uses_incoming_context_and_appends_contact(monkeypatch):
    captured = {}

    def fake_ai(system_prompt, user_prompt, **kwargs):
        captured["user_prompt"] = user_prompt
        return "先回应一下\n稍等我整理"

    monkeypatch.setattr(douyin_api, "request_douyin_ai_comment", fake_ai)

    result = generate_douyin_stranger_reply_message(
        {
            "username": "客户A",
            "incoming_message": "想了解方案",
            "time_text": "刚刚",
            "unread_count": 1,
        },
        mode="ai_lead",
        prompt_text="先回答问题，再自然引导继续沟通",
        contact_value="wx_demo_001",
    )

    assert result.splitlines() == ["先回应一下", "稍等我整理", "麻烦您绿泡泡", "wx_demo_001"]
    assert "想了解方案" in captured["user_prompt"]
    assert "先回答问题，再自然引导继续沟通" in captured["user_prompt"]


def test_conversation_time_cutoff_stops_at_any_yesterday_row():
    now = datetime(2026, 8, 19, 2, 0, 0)

    # Yesterday 23:00 is only three hours ago, but it is still the list
    # boundary represented by Douyin's display timestamp.
    assert conversation_time_is_older_than_24h("昨天 23:00", now=now) is True
    assert conversation_time_is_older_than_24h("昨天", now=now) is True
    assert conversation_time_is_older_than_24h("今天 01:00", now=now) is False


def test_monitor_start_accepts_empty_legacy_fixed_reply(monkeypatch):
    account = {"id": 5, "status": "online"}

    monkeypatch.setattr(douyin_api, "load_global_config", lambda: {"douyin_accounts": [account]})
    monkeypatch.setattr(douyin_api, "get_douyin_account_by_id", lambda account_id, config: account)
    monkeypatch.setattr(douyin_api, "save_douyin_stranger_message_monitor_config", lambda: None)

    async def ensure_scheduler():
        return None

    monkeypatch.setattr(douyin_api, "ensure_douyin_stranger_message_monitor_scheduler", ensure_scheduler)
    try:
        result = asyncio.run(
            douyin_api.douyin_start_stranger_message_monitor(
                request={
                    "account_id": 5,
                    "auto_reply_enabled": True,
                    "reply_mode": "fixed",
                    "message": "",
                    "wechat_add_friend_enabled": True,
                }
            )
        )
    finally:
        douyin_api.douyin_stranger_message_monitor_states.pop("5", None)

    assert result["code"] == 200
    assert result["monitor"]["reply_mode"] == "ai_auto"
    assert result["monitor"]["wechat_add_friend_enabled"] is True


def test_current_stranger_leads_page_has_one_non_blocking_monitor_start():
    root = Path(__file__).resolve().parent
    script = (root / "static" / "douyin-origin" / "douyin-workbench-shared.js").read_text(encoding="utf-8")
    html = (root / "static" / "douyin-origin" / "douyin-stranger-leads.html").read_text(encoding="utf-8")

    assert script.count("function startDouyinStrangerMessageMonitor()") == 1
    assert "开启监控自动回复前，请先填写固定引流文案" not in script
    assert "wechat_add_friend_enabled:wechatAddFriendEnabled" in script
    assert '<option value="ai_auto" selected>' in html
    assert "20260816-douyin-takeover-v2" in html


def test_h5_stranger_task_is_one_shot_and_adds_friends_once(monkeypatch):
    account = {"id": 5, "status": "online", "port": 9336}
    rows = [
        {
            "conversation_key": "with-phone",
            "username": "有手机号",
            "last_message_text": "我的电话是13927485337",
            "last_message_is_user": True,
            "messages": [{"text": "我的电话是13927485337", "is_incoming": True}],
        },
        {
            "conversation_key": "without-phone",
            "username": "没有手机号",
            "last_message_text": "想了解一下",
            "last_message_is_user": True,
            "messages": [{"text": "想了解一下", "is_incoming": True}],
        },
        {
            "conversation_key": "last-is-self",
            "username": "最后是自己",
            "last_message_text": "已发送资料",
            "last_message_is_user": False,
            "messages": [{"text": "已发送资料", "is_incoming": False}],
        },
    ]
    stored_rows = []
    sent_rows = []
    add_friend_calls = []
    call_order = []

    class FakeScraper:
        async def collect_stranger_private_messages(self, **kwargs):
            call_order.append("stranger")
            assert kwargs["max_conversations"] >= 10000
            assert kwargs["include_details"] is True
            return [
                await kwargs["item_callback"](dict(rows[0]), object()),
                await kwargs["item_callback"](dict(rows[2]), object()),
            ]

        async def collect_chat_page_private_messages(self, **kwargs):
            call_order.append("normal")
            assert kwargs["max_conversations"] >= 10000
            return [await kwargs["item_callback"](dict(rows[1]), object())]

        async def send_open_chat_message(self, page, message, **kwargs):
            sent_rows.append({"username": kwargs.get("username"), "message": message})
            return {"success": True}

        async def close(self):
            return None

    def store(account_id, incoming):
        by_key = {
            douyin_api.stranger_message_row_key(row): dict(row)
            for row in stored_rows
            if douyin_api.stranger_message_row_key(row)
        }
        order = list(by_key)
        for row in incoming:
            normalized = douyin_api.normalize_douyin_stranger_message_row({**row, "account_id": account_id})
            key = douyin_api.stranger_message_row_key(normalized)
            if key not in order:
                order.append(key)
            by_key[key] = {**by_key.get(key, {}), **normalized}
        stored_rows[:] = [by_key[key] for key in order]
        return len(stored_rows)

    async def fake_add_friend(numbers):
        add_friend_calls.append(list(numbers))
        return {"enabled": True, "queued": True, "targets": list(numbers), "task_id": "friend-task-1"}

    monkeypatch.setattr(douyin_api, "load_global_config", lambda: {"douyin_accounts": [account]})
    monkeypatch.setattr(douyin_api, "get_douyin_account_by_id", lambda account_id, config: account)
    monkeypatch.setattr(douyin_api, "is_douyin_stranger_message_monitor_busy", lambda account_id: (False, ""))
    monkeypatch.setattr(douyin_api, "create_douyin_message_scraper", lambda account, config: FakeScraper())
    monkeypatch.setattr(douyin_api, "collect_douyin_stranger_message_results", lambda account_id=0: list(stored_rows))
    monkeypatch.setattr(douyin_api, "merge_douyin_stranger_message_results", store)
    monkeypatch.setattr(douyin_api, "merge_douyin_inbox_results", lambda account_id, incoming: len(incoming))
    monkeypatch.setattr(douyin_api, "_queue_douyin_wechat_friend_add", fake_add_friend)

    result = asyncio.run(
        douyin_api.run_douyin_h5_stranger_message_task_once(
            account_id=5,
            fixed_message="请留下手机号，我安排同事联系您",
            wechat_add_friend_enabled=True,
        )
    )

    assert result["status"] == "completed"
    assert result["processed_user_last"] == 2
    assert result["normal_conversations"] == 1
    assert result["stranger_conversations"] == 2
    assert call_order == ["normal", "stranger"]
    assert result["extracted_phone_numbers"] == ["13927485337"]
    assert result["wechat_add_targets"] == ["13927485337"]
    assert [row["username"] for row in sent_rows] == ["没有手机号"]
    assert add_friend_calls == [["13927485337"]]

    second_result = asyncio.run(
        douyin_api.run_douyin_h5_stranger_message_task_once(
            account_id=5,
            fixed_message="请留下手机号，我安排同事联系您",
            wechat_add_friend_enabled=True,
        )
    )

    assert second_result["status"] == "completed"
    assert second_result["wechat_add_targets"] == []
    assert second_result["skipped_duplicate_reply"] == 1
    assert [row["username"] for row in sent_rows] == ["没有手机号"]
    assert add_friend_calls == [["13927485337"]]


def test_current_detail_direction_wins_over_stored_row(monkeypatch):
    old_row = douyin_api.normalize_douyin_stranger_message_row(
        {
            "account_id": 5,
            "conversation_key": "same-contact",
            "username": "同一个联系人",
            "last_message_text": "我之前发的",
            "last_message_is_user": False,
            "reply_status": "sent",
        }
    )
    monkeypatch.setattr(douyin_api, "douyin_stranger_message_results", [old_row])
    monkeypatch.setattr(douyin_api, "save_douyin_stranger_message_results", lambda: None)

    douyin_api.merge_douyin_stranger_message_results(
        5,
        [
            {
                "account_id": 5,
                "conversation_key": "same-contact",
                "username": "同一个联系人",
                "last_message_text": "我刚发的",
                "last_message_is_user": True,
                "detail_read_status": "ok",
            }
        ],
    )

    current = douyin_api.collect_douyin_stranger_message_results(5)[0]
    assert current["last_message_text"] == "我刚发的"
    assert current["last_message_is_user"] is True
    assert current["reply_status"] == "sent"
