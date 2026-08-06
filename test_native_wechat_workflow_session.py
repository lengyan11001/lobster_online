from __future__ import annotations

import asyncio

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

    def accept_friend_requests(account_id):
        calls.append(("friends", account_id))
        return {"ok": True, "checked": 2, "accepted": 1, "failed": 0, "items": []}

    def sync_sessions(account_id, *, passive=False):
        calls.append(("sessions", account_id))
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
