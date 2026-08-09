from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import types

import pytest

from backend.app.api import h5_chat_channel as channel
from backend.app.services import native_wechat_engine as engine


def test_friend_request_action_only_matches_pending_status_suffix():
    assert engine._friend_request_action_label("Alice\u6211\u662f Alice\u63a5\u53d7") == "\u63a5\u53d7"
    assert engine._friend_request_action_label("Bob requested to connect Accept") == "Accept"
    assert engine._friend_request_action_label("Alice\u6211\u662f Alice\u5df2\u6dfb\u52a0") == ""
    assert engine._friend_request_action_label("Alice\u6211\u5df2\u63a5\u53d7") == ""
    assert engine._friend_request_action_label("Alice\u7b49\u5f85\u9a8c\u8bc1") == "\u7b49\u5f85\u9a8c\u8bc1"


def test_waiting_verification_request_opens_detail_before_accepting(monkeypatch):
    node = object()
    item = {
        "node": node,
        "key": "request-key",
        "action": "\u7b49\u5f85\u9a8c\u8bc1",
        "rect": (10, 20, 210, 80),
    }
    calls = []

    monkeypatch.setattr(engine, "_uia_click", lambda target: calls.append(("click", target)))
    monkeypatch.setattr(
        engine,
        "_complete_friend_request_dialog",
        lambda hwnd, steps: (calls.append(("complete", hwnd)), steps.extend(["open_verification", "dialog_confirm"])),
    )
    monkeypatch.setattr(engine, "_find_pending_friend_request", lambda hwnd, key: None)
    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        engine,
        "_uia_click_screen_point",
        lambda *_args: pytest.fail("waiting verification must not use the inline-button coordinate"),
    )

    steps = engine._accept_visible_friend_request(123, item)

    assert calls == [("click", node), ("complete", 123)]
    assert steps == ["open_request_detail", "open_verification", "dialog_confirm"]


@pytest.mark.asyncio
async def test_auto_reply_checks_friend_requests_before_scanning_messages(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    monkeypatch.setattr(
        engine,
        "_load_auto_reply_memory_context",
        lambda *args, **kwargs: {"text": "", "document_count": 0, "titles": []},
    )

    def accept_friend_requests(account_id, *, max_accepts=20, max_scrolls=24):
        calls.append(("friends", account_id))
        assert max_accepts == 10
        assert max_scrolls == 6
        return {"ok": True, "checked": 2, "accepted": 1, "failed": 0, "items": []}

    def sync_sessions(account_id, *, passive=False, recent_only=False):
        calls.append(("sessions", account_id))
        assert recent_only is True
        return {"ok": True, "items": []}

    monkeypatch.setattr(engine, "accept_local_friend_requests", accept_friend_requests)
    monkeypatch.setattr(engine, "sync_local_sessions", sync_sessions)

    result = await engine.run_auto_reply_once(engine.LOCAL_DEFAULT_ACCOUNT_ID, force=True)

    assert calls == [
        ("friends", engine.LOCAL_DEFAULT_ACCOUNT_ID),
        ("sessions", engine.LOCAL_DEFAULT_ACCOUNT_ID),
    ]
    assert result["friend_requests_checked"] == 2
    assert result["friend_requests_accepted"] == 1
    assert result["friend_requests_failed"] == 0


def test_session_preview_change_is_checked_without_an_unread_badge(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    engine.init_db()
    first = engine._persist_session(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        {
            "peer_id": "Alice",
            "display_name": "Alice",
            "last_content": "previous",
            "session_time": "14:00",
            "unread_count": 0,
        },
        chat_type="direct",
    )
    changed = engine._persist_session(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        {
            "peer_id": "Alice",
            "display_name": "Alice",
            "last_content": "new message",
            "session_time": "14:03",
            "unread_count": 0,
        },
        chat_type="direct",
    )

    assert first["message_preview_changed"] is False
    assert engine._session_needs_auto_reply_check(first) is False
    assert changed["message_preview_changed"] is True
    assert engine._session_needs_auto_reply_check(changed) is True


def test_new_session_snapshot_does_not_reply_to_historical_content():
    assert engine._session_needs_auto_reply_check(
        {
            "last_content": "historical message",
            "unread_count": 0,
            "changed": True,
            "message_preview_changed": False,
        }
    ) is False


def test_native_wechat_lists_reach_rows_after_the_first_hundred(tmp_path, monkeypatch):
    account_id = "pagination-account"
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    engine.init_db()

    engine._replace_contacts_snapshot(
        account_id,
        [
            {
                "contact_key": f"contact-{index:03d}",
                "display_name": f"联系人 {index:03d}",
                "wx_no": f"wx-{index:03d}",
                "source": "local",
            }
            for index in range(205)
        ],
    )
    engine._replace_groups_snapshot(
        account_id,
        [
            {
                "group_key": f"group-{index:03d}",
                "display_name": f"群聊 {index:03d}",
                "source": "local",
            }
            for index in range(205)
        ],
    )
    engine._persist_local_group_members(
        account_id,
        "group-000",
        [
            {"member_key": f"member-{index:03d}", "display_name": f"成员 {index:03d}"}
            for index in range(205)
        ],
        replace=True,
    )

    contacts = engine.list_contacts(account_id, limit=100, offset=200)
    groups = engine.list_groups(account_id, limit=100, offset=200)
    members = engine.list_group_members(account_id, "group-000", limit=100, offset=200)

    assert contacts["count"] == 205
    assert groups["count"] == 205
    assert members["count"] == 205
    assert len(contacts["items"]) == len(groups["items"]) == len(members["items"]) == 5


def test_native_wechat_peer_pagination_and_keyword_search_cover_all_sessions(tmp_path, monkeypatch):
    account_id = "session-pagination-account"
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    engine.init_db()
    for index in range(205):
        engine._persist_session(
            account_id,
            {
                "peer_id": f"peer-{index:03d}",
                "display_name": f"客户 {index:03d}",
                "last_content": "needle-message" if index == 204 else f"消息 {index:03d}",
                "session_time": f"{index:03d}",
            },
            chat_type="direct",
        )

    last_page = engine.list_peers(account_id, limit=100, offset=200)
    matched = engine.list_peers(account_id, limit=100, offset=0, keyword="needle-message")

    assert last_page["count"] == 205
    assert len(last_page["items"]) == 5
    assert matched["count"] == 1
    assert matched["items"][0]["peer_id"] == "peer-204"


def test_native_wechat_frontend_paginates_every_local_data_list():
    root = Path(__file__).resolve().parent
    javascript = (root / "static/js/juhe-wechat.js").read_text(encoding="utf-8")
    html = (root / "static/views/juhe-wechat.html").read_text(encoding="utf-8")

    for key in ("peers", "contacts", "groups", "groupMembers", "contactPicker", "tasks"):
        assert f"{key}: {{ page: 1" in javascript
    for element_id in (
        "nativeWechatPeerPagination",
        "nativeWechatContactPagination",
        "nativeWechatGroupPagination",
        "nativeWechatGroupMemberPagination",
        "nativeWechatContactPickerPagination",
        "nativeWechatTaskPagination",
    ):
        assert f'id="{element_id}"' in html
    assert "limit=100&offset=0" not in javascript
    assert "limit=200&offset=0" not in javascript
    assert "matched.slice(0, 100)" not in javascript
    assert "loadMessages({ append: true })" in javascript
    assert "data-native-page-list" in javascript


def test_wxauto_client_rebuilds_after_initial_offline(monkeypatch):
    clients = iter([False, True])
    created = []
    recoveries = []

    class FakeWeChat:
        def __init__(self, online):
            self.online = online

        def IsOnline(self):
            return self.online

    def create_client(**_kwargs):
        client = FakeWeChat(next(clients))
        created.append(client)
        return client

    monkeypatch.setitem(sys.modules, "wxauto4", types.SimpleNamespace(WeChat=create_client))
    monkeypatch.setattr(engine, "_prepare_local_automation_thread", lambda: {})
    monkeypatch.setattr(engine, "_ensure_local_chat_tab", lambda _account_id="": None)
    monkeypatch.setattr(
        engine,
        "_recover_local_wechat_driver",
        lambda account_id, **kwargs: recoveries.append((account_id, kwargs)) or {"ok": True},
    )
    monkeypatch.setattr(
        engine,
        "_mark_local_driver_recovery",
        lambda _account_id, recovery, **kwargs: {**recovery, **kwargs},
    )

    client = engine._get_wxauto4_client(engine.LOCAL_DEFAULT_ACCOUNT_ID)

    assert client.online is True
    assert len(created) == 2
    assert len(recoveries) == 1
    assert "未识别到已登录" in recoveries[0][1]["error"]


def test_local_driver_read_recovers_and_retries_once(monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "_prepare_local_automation_thread", lambda: {})
    monkeypatch.setattr(
        engine,
        "_recover_local_wechat_driver",
        lambda *_args, **_kwargs: {"ok": True, "attempted": True},
    )
    monkeypatch.setattr(
        engine,
        "_mark_local_driver_recovery",
        lambda _account_id, recovery, **kwargs: {**recovery, **kwargs},
    )

    def read():
        calls.append("read")
        if len(calls) == 1:
            raise RuntimeError("stale UIA")
        return {"ok": True, "items": []}

    result = engine._run_local_driver_operation(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        "读取微信消息",
        read,
    )

    assert calls == ["read", "read"]
    assert result["driver_recovered"] is True
    assert result["driver_retry_count"] == 1


def test_local_driver_send_failure_is_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "_prepare_local_automation_thread", lambda: {})
    monkeypatch.setattr(
        engine,
        "_recover_local_wechat_driver",
        lambda *_args, **_kwargs: pytest.fail("send failure must not trigger a whole-operation retry"),
    )

    def send_once(*_args, **_kwargs):
        calls.append("send")
        raise RuntimeError("send result unknown")

    monkeypatch.setattr(engine, "_send_text_local_slow_once", send_once)

    with pytest.raises(RuntimeError, match="send result unknown"):
        engine._send_text_local_slow(engine.LOCAL_DEFAULT_ACCOUNT_ID, "Alice", "hello")

    assert calls == ["send"]


@pytest.mark.asyncio
async def test_poll_reports_driver_failure_instead_of_false_success(monkeypatch):
    monkeypatch.setattr(engine, "init_db", lambda: None)
    monkeypatch.setattr(engine, "_find_local_account", lambda _account_id: {})
    monkeypatch.setattr(
        engine,
        "sync_local_sessions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("stale UIA after retry")),
    )
    monkeypatch.setattr(engine, "local_driver_status", lambda: {"full_driver": {"usable": False}})
    monkeypatch.setattr(
        engine,
        "_latest_local_driver_recovery",
        lambda _account_id: {"attempted": True, "recovered": False},
    )

    result = await engine.poll_updates(engine.LOCAL_DEFAULT_ACCOUNT_ID)

    assert result["ok"] is False
    assert result["driver_recovered"] is False
    assert result["driver_retry_count"] == 1
    assert result["error"] == "stale UIA after retry"


def test_extract_mainland_mobile_numbers_only_reads_customer_message_fields():
    payload = {
        "conversations": [
            {
                "incoming_message": "我的电话是 +86 139-1234-5678，麻烦加一下",
                "preview_text": "也可以联系 188 2385 1682",
                "reply_message": "客服自己的号码 17700001111 不应提取",
            },
            {"incoming_message": "重复号码 13912345678"},
        ],
        "summary": "统计编号 13600002222 不属于客户消息",
    }

    assert channel._extract_mainland_mobile_numbers(payload) == ["13912345678", "18823851682"]


@pytest.mark.asyncio
async def test_add_friend_uses_phone_from_parent_douyin_private_message(monkeypatch):
    async def resolve_parent(*args, **kwargs):
        return [
            {
                "source_run_id": "douyin-parent",
                "result_payload": {
                    "conversations": [{"incoming_message": "加我手机 13927485337"}],
                },
            }
        ]

    calls = []

    async def post_local(path, body, **kwargs):
        calls.append((path, body))
        return {"ok": True, "task": {"id": "friend-task"}}

    monkeypatch.setattr(channel, "_resolve_parent_workflow_results", resolve_parent)
    monkeypatch.setattr(channel, "_post_local_api_json", post_local)

    result = await channel._run_client_workflow_action(
        "native_wechat_add_friend",
        {
            "account_id": "pc-wechat-default",
            "source_mode": "douyin_private_message_phone",
            "source_workflow_node_id": "douyin-private",
        },
        headers={},
        run_id="friend-child",
        cloud=object(),
        base="https://example.com",
    )

    assert calls == [
        (
            "/api/native-wechat/friends/add",
            {
                "account_id": "pc-wechat-default",
                "targets": ["13927485337"],
                "apply_message": "",
                "remark": "",
                "tags": [],
                "permission": "朋友圈",
                "prepare_only": False,
            },
        )
    ]
    assert result["extracted_phones"] == ["13927485337"]


@pytest.mark.asyncio
async def test_takeover_session_polls_repeatedly_and_aggregates_new_results(monkeypatch):
    responses = iter(
        [
            {
                "replied": 1,
                "skipped": 0,
                "failed": 0,
                "friend_requests_checked": 2,
                "friend_requests_accepted": 1,
                "friend_requests_failed": 0,
                "items": [{"peer_id": "a", "status": "sent"}],
            },
            {
                "replied": 0,
                "skipped": 1,
                "failed": 0,
                "friend_requests_checked": 1,
                "friend_requests_accepted": 0,
                "friend_requests_failed": 0,
                "items": [],
            },
            {
                "replied": 1,
                "skipped": 0,
                "failed": 0,
                "friend_requests_checked": 3,
                "friend_requests_accepted": 2,
                "friend_requests_failed": 1,
                "items": [{"peer_id": "b", "status": "sent", "should_invite_group": True}],
            },
        ]
    )
    sleeps = []

    async def post_local(path, body, **kwargs):
        assert path == "/api/native-wechat/auto-reply/run-once"
        assert body["force"] is True
        return next(responses)

    async def no_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(channel, "_post_local_api_json", post_local)
    monkeypatch.setattr(channel.asyncio, "sleep", no_sleep)

    result = await channel._run_native_wechat_takeover_session(
        account_id="pc-wechat-default",
        headers={},
        cloud=None,
        base="",
        run_id="run",
        rounds=3,
        interval_seconds=180,
    )

    assert sleeps == [180, 180]
    assert result["completed_rounds"] == 3
    assert result["replied"] == 2
    assert result["skipped"] == 1
    assert result["friend_requests_checked"] == 6
    assert result["friend_requests_accepted"] == 3
    assert result["friend_requests_failed"] == 1
    assert result["group_invite_candidates"] == 1
    assert [item["round"] for item in result["items"]] == [1, 3]


@pytest.mark.asyncio
async def test_group_invite_waits_for_parent_then_creates_group(monkeypatch):
    parent_responses = iter(
        [
            [],
            [
                {
                    "result_payload": {
                        "local_result": {
                            "items": [
                                {
                                    "peer_id": "customer-a",
                                    "display_name": "客户A",
                                    "inbound_message_id": "message-a",
                                    "should_invite_group": True,
                                    "matched_group_keywords": ["预约体验"],
                                    "group_invite_reason": "客户明确要求预约体验",
                                }
                            ]
                        }
                    }
                }
            ],
        ]
    )
    sleeps = []
    calls = []

    async def resolve_parent(*args, **kwargs):
        return next(parent_responses)

    async def no_sleep(seconds):
        sleeps.append(seconds)

    async def post_local(path, body, **kwargs):
        calls.append((path, body))
        return {"ok": True, "task": {"id": "group-task"}}

    monkeypatch.setattr(channel, "_resolve_parent_workflow_results", resolve_parent)
    monkeypatch.setattr(channel.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(channel, "_post_local_api_json", post_local)
    monkeypatch.setattr(
        channel.native_wechat_engine,
        "get_auto_reply_config",
        lambda _account_id: {
            "group_invite_primary_contact": "销售经理",
            "group_invite_primary_contact_name": "王经理",
            "group_invite_welcome_message": "您好，我把王经理拉进群了。",
        },
    )

    result = await channel._run_native_wechat_group_invite_followup(
        {"source_workflow_node_id": "wechat-private", "parent_wait_seconds": 120, "parent_poll_seconds": 15},
        account_id="pc-wechat-default",
        headers={},
        cloud=object(),
        base="https://example.com",
        current_item={},
    )

    assert sleeps == [15]
    assert calls == [
        (
            "/api/native-wechat/groups/create",
            {
                "account_id": "pc-wechat-default",
                "contacts": ["customer-a", "销售经理"],
                "welcome_message": "您好，我把王经理拉进群了。",
            },
        )
    ]
    assert result["matched"] == 1
    assert result["queued"] == 1


@pytest.mark.asyncio
async def test_group_invite_reports_missing_companion_contact(monkeypatch):
    async def resolve_parent(*args, **kwargs):
        return [
            {
                "result_payload": {
                    "items": [
                        {"peer_id": "customer-a", "should_invite_group": True, "inbound_message_id": "message-a"}
                    ]
                }
            }
        ]

    monkeypatch.setattr(channel, "_resolve_parent_workflow_results", resolve_parent)
    monkeypatch.setattr(channel.native_wechat_engine, "get_auto_reply_config", lambda _account_id: {})

    result = await channel._run_native_wechat_group_invite_followup(
        {"source_workflow_node_id": "wechat-private", "parent_wait_seconds": 0},
        account_id="pc-wechat-default",
        headers={},
        cloud=object(),
        base="https://example.com",
        current_item={},
    )

    assert result["skipped"] is True
    assert result["reason"] == "missing_group_contacts"
    assert result["matched"] == 1


def test_auto_reply_config_preserves_memory_when_only_toggle_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    monkeypatch.setattr(engine, "_find_local_account", lambda _account_id: {"account_id": _account_id})
    monkeypatch.setattr(engine, "ensure_auto_reply_worker", lambda *args, **kwargs: None)

    first = engine.save_auto_reply_config(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        enabled=False,
        memory_doc_ids=["faq-doc"],
        group_invite_memory_doc_id="group-rule-doc",
        group_invite_keywords="咨询报价，预约体验",
        group_invite_contacts=["销售经理"],
        group_invite_primary_contact="销售经理",
        group_invite_primary_contact_name="王经理",
        group_invite_welcome_message="您好，我把王经理拉进群了。",
    )
    second = engine.save_auto_reply_config(engine.LOCAL_DEFAULT_ACCOUNT_ID, enabled=True)

    assert first["memory_doc_ids"] == ["faq-doc"]
    assert second["memory_doc_ids"] == ["faq-doc"]
    assert second["group_invite_memory_doc_id"] == "group-rule-doc"
    assert second["group_invite_keywords"] == "咨询报价，预约体验"
    assert second["group_invite_contacts"] == ["销售经理"]
    assert second["group_invite_primary_contact"] == "销售经理"
    assert second["group_invite_primary_contact_name"] == "王经理"
    assert second["group_invite_welcome_message"].startswith("您好")


@pytest.mark.asyncio
async def test_create_group_task_sends_welcome_after_group_is_created(monkeypatch):
    finished = []
    payload_updates = []
    sent = []

    monkeypatch.setattr(
        engine,
        "create_local_group",
        lambda _account_id, _targets: {
            "ok": True,
            "selected": 2,
            "group": {"group_key": "客户A、王经理"},
        },
    )
    monkeypatch.setattr(
        engine,
        "_send_text_local_slow",
        lambda account_id, peer_id, text, raw_meta=None: sent.append((account_id, peer_id, text, raw_meta)) or {"ok": True},
    )
    monkeypatch.setattr(engine, "_update_task_payload", lambda task_id, patch: payload_updates.append((task_id, patch)))
    monkeypatch.setattr(engine, "_finish_task", lambda *args: finished.append(args))

    await engine._process_create_group_task(
        {
            "id": "group-task",
            "account_id": engine.LOCAL_DEFAULT_ACCOUNT_ID,
            "targets": ["客户A", "王经理"],
            "payload": {"welcome_message": "您好，我把王经理拉进群了。"},
        }
    )

    assert sent[0][:3] == (
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        "客户A、王经理",
        "您好，我把王经理拉进群了。",
    )
    assert payload_updates[0][1]["welcome_sent"] is True
    assert finished[0][1] == "success"


def test_selected_memory_context_only_loads_selected_document(monkeypatch):
    from backend.app.api import openclaw_memory

    docs = [
        {"id": "profile", "title": "个人介绍", "content": "介绍内容", "status": "active"},
        {"id": "faq", "title": "产品FAQ", "content": "FAQ内容", "status": "active"},
    ]
    monkeypatch.setattr(openclaw_memory, "_load_index", lambda _user_id: docs)
    monkeypatch.setattr(
        openclaw_memory,
        "_read_canonical_memory_content",
        lambda doc, max_chars=1800: str(doc.get("content") or "")[:max_chars],
    )

    memory = engine._load_auto_reply_memory_context(31, selected_doc_ids=["faq"])

    assert memory["titles"] == ["产品FAQ"]
    assert "FAQ内容" in memory["text"]
    assert "介绍内容" not in memory["text"]
