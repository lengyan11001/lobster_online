from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import sys
import types

import pytest

from backend.app.api import h5_chat_channel as channel
from backend.app.services import native_wechat_engine as engine


@pytest.mark.asyncio
async def test_takeover_does_not_mark_skipped_running_as_completed(monkeypatch):
    async def post_local(path, payload, *, headers, timeout_seconds):
        assert path == "/api/native-wechat/auto-reply/run-once"
        return {"ok": True, "skipped": True, "reason": "running", "config": {"running": True}}

    monkeypatch.setattr(channel, "_post_local_api_json", post_local)

    result = await channel._run_native_wechat_takeover_session(
        account_id=engine.LOCAL_DEFAULT_ACCOUNT_ID,
        headers={},
        cloud=None,
        base="",
        run_id="run-id",
        session_seconds=30,
        interval_seconds=15,
    )

    assert result["ok"] is False
    assert result["completed_rounds"] == 0
    assert result["stop_reason"] == "local_wechat_busy"
    assert result["failed"] == 1


def test_group_picker_rejects_normal_chat_root(monkeypatch):
    monkeypatch.setattr(engine, "_uia_foreground_or_main_root", lambda _hwnd: "chat-root")
    monkeypatch.setattr(engine, "_uia_control_class", lambda _root: "mmui::ChatWindow")
    monkeypatch.setattr(engine, "_uia_find_by_names", lambda *args, **kwargs: None)

    assert engine._group_picker_root(123) is None
    assert engine._find_group_picker_search_edit(None) is None


def test_group_picker_accepts_session_picker_root(monkeypatch):
    picker_root = object()
    monkeypatch.setattr(engine, "_uia_foreground_or_main_root", lambda _hwnd: picker_root)
    monkeypatch.setattr(engine, "_uia_control_class", lambda _root: "mmui::SessionPickerWindow")

    assert engine._group_picker_root(123) is picker_root


def test_clipboard_write_isolated_in_child_process(monkeypatch):
    calls = []

    class Completed:
        returncode = 0
        stderr = b""

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return Completed()

    monkeypatch.setattr(engine.subprocess, "run", run)
    engine._clipboard_text("你好")

    assert calls
    assert calls[0][0][0] == sys.executable
    assert calls[0][1]["input"] == "你好".encode("utf-8")
    assert calls[0][1]["timeout"] == 5.0


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


@pytest.mark.asyncio
async def test_auto_reply_can_skip_friend_requests_after_takeover_initial_scan(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    monkeypatch.setattr(
        engine,
        "_load_auto_reply_memory_context",
        lambda *args, **kwargs: {"text": "", "document_count": 0, "titles": []},
    )
    monkeypatch.setattr(
        engine,
        "accept_local_friend_requests",
        lambda *_args, **_kwargs: pytest.fail("follow-up message rounds must not scan friend requests"),
    )

    def sync_sessions(account_id, *, passive=False, recent_only=False):
        calls.append(("sessions", account_id))
        return {"ok": True, "items": []}

    monkeypatch.setattr(engine, "sync_local_sessions", sync_sessions)

    result = await engine.run_auto_reply_once(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        force=True,
        check_friend_requests=False,
    )

    assert calls == [("sessions", engine.LOCAL_DEFAULT_ACCOUNT_ID)]
    assert result["friend_requests_checked_this_run"] is False
    assert result["friend_requests"]["reason"] == "session_initial_check_completed"
    assert result["friend_requests_checked"] == 0


def test_session_preview_change_without_unread_badge_is_not_replied(tmp_path, monkeypatch):
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
    assert engine._session_needs_auto_reply_check(changed) is False


def test_auto_reply_candidate_allows_same_content_when_message_id_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    engine.init_db()
    inbound = {
        "peer_id": "customer-a",
        "direction": "in",
        "content": "same inbound text",
        "provider_message_id": "provider-id-a",
        "id": "local-id-a",
        "created_at": "2026-08-12T13:00:00",
    }
    assert engine._record_auto_reply_history(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        "customer-a",
        {**inbound, "auto_reply_inbound_id": "provider-id-a"},
        reply="reply once",
        status="sent",
    )
    monkeypatch.setattr(
        engine,
        "_latest_message_record",
        lambda *_args, **_kwargs: {
            **inbound,
            "provider_message_id": "provider-id-b",
            "id": "local-id-b",
            "created_at": "2026-08-12T13:30:00",
        },
    )

    candidate = engine._latest_auto_reply_candidate(engine.LOCAL_DEFAULT_ACCOUNT_ID, "customer-a")
    assert candidate is not None
    assert candidate["auto_reply_inbound_id"] == "provider-id-b"


def test_auto_reply_candidate_allows_same_message_id_after_unread_trigger(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    engine.init_db()
    inbound = {
        "peer_id": "customer-a",
        "direction": "in",
        "content": "same inbound text",
        "provider_message_id": "provider-id-a",
        "id": "local-id-a",
        "created_at": "2026-08-12T13:00:00",
    }
    assert engine._record_auto_reply_history(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        "customer-a",
        {**inbound, "auto_reply_inbound_id": "provider-id-a"},
        reply="reply once",
        status="sent",
    )
    monkeypatch.setattr(
        engine,
        "_latest_message_record",
        lambda *_args, **_kwargs: {
            **inbound,
            "provider_message_id": "provider-id-a",
            "id": "local-id-b",
            "created_at": "2026-08-12T13:30:00",
        },
    )

    candidate = engine._latest_auto_reply_candidate(engine.LOCAL_DEFAULT_ACCOUNT_ID, "customer-a")
    assert candidate is not None
    assert candidate["auto_reply_inbound_id"] == "provider-id-a"


def test_takeover_message_sync_does_not_download_media(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    engine.init_db()

    class ImageMessage:
        content = "[图片]"
        type = "image"
        attr = "friend"
        sender = "customer-a"
        id = "image-message-a"

        def download(self):
            raise AssertionError("takeover message sync must not download media")

    item = engine._persist_message_obj(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        "customer-a",
        ImageMessage(),
        download_attachments=False,
    )

    assert item["msg_type"] == "image"
    assert item["attachments"] == []


def test_recent_local_reply_with_missing_self_attr_stays_outbound(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    engine.init_db()
    now = engine._now_iso()
    with engine._connect() as conn:
        conn.execute(
            """
            insert into wechat_messages(
                id, account_id, peer_id, direction, msg_type, content, client_id, status, raw_json, created_at
            ) values(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "local-outbound-a",
                engine.LOCAL_DEFAULT_ACCOUNT_ID,
                "customer-a",
                "out",
                "text",
                "same reply",
                "client-a",
                "sent",
                "{}",
                now,
            ),
        )

    item = engine._persist_message_obj(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        "customer-a",
        {
            "content": "same reply",
            "type": "text",
            "attr": "",
            "sender": "",
            "id": "visible-message-a",
        },
        download_attachments=False,
    )

    assert item["direction"] == "out"


def test_auto_reply_history_is_a_record_not_a_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    engine.init_db()
    inbound = {
        "peer_id": "customer-a",
        "direction": "in",
        "content": "same inbound text",
        "provider_message_id": "provider-id-a",
        "id": "local-id-a",
        "created_at": "2026-08-12T13:00:00",
    }
    assert engine._record_auto_reply_history(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        "customer-a",
        {**inbound, "auto_reply_inbound_id": "provider-id-a"},
        reply="first reply",
        status="sent",
    ) is True
    assert engine._record_auto_reply_history(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        "customer-a",
        {**inbound, "auto_reply_inbound_id": "provider-id-a"},
        reply="second reply",
        status="sent",
    ) is True
    with engine._connect() as conn:
        row = conn.execute(
            """
            select reply_content from wechat_auto_reply_history
            where account_id=? and peer_id=? and inbound_message_id=?
            """,
            (engine.LOCAL_DEFAULT_ACCOUNT_ID, "customer-a", "provider-id-a"),
        ).fetchone()

    assert row["reply_content"] == "second reply"


def test_auto_reply_candidate_rejects_self_message_even_if_direction_was_stored_as_in(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    engine.init_db()
    with engine._connect() as conn:
        conn.execute(
            """
            insert into wechat_messages(
                id, account_id, peer_id, direction, msg_type, content, provider_message_id, status, raw_json, created_at
            )
            values(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "self-message-row",
                engine.LOCAL_DEFAULT_ACCOUNT_ID,
                "customer-a",
                "in",
                "text",
                "this is our own message",
                "self-message-provider",
                "sent",
                engine._json_dumps({"attr": "self"}),
                "2026-08-12T14:00:00",
            ),
        )

    assert engine._latest_auto_reply_candidate(engine.LOCAL_DEFAULT_ACCOUNT_ID, "customer-a") is None


def test_new_session_snapshot_does_not_reply_to_historical_content():
    assert engine._session_needs_auto_reply_check(
        {
            "last_content": "historical message",
            "unread_count": 0,
            "changed": True,
            "message_preview_changed": False,
        }
    ) is False


def test_no_badge_scan_rejects_old_inbound_after_our_newer_preview():
    session = {
        "last_content": "哈哈何总过奖了，我就是跟着张老师多学习。",
        "unread_count": 0,
        "message_preview_changed": True,
    }
    inbound = {"content": "你好厉害", "direction": "in"}

    assert engine._session_needs_auto_reply_check(session) is False
    assert engine._session_preview_matches_inbound(session, inbound) is False


def test_no_badge_scan_accepts_truncated_inbound_preview():
    session = {
        "last_content": "我想了解一下你们的企业版价",
        "unread_count": 0,
        "message_preview_changed": True,
    }
    inbound = {"content": "我想了解一下你们的企业版价格和服务范围", "direction": "in"}

    assert engine._session_preview_matches_inbound(session, inbound) is True


def test_auto_reply_only_processes_unread_non_call_sessions():
    assert engine._session_needs_auto_reply_check(
        {"last_content": "客户刚发来的新消息", "unread_count": 1}
    ) is True
    assert engine._session_needs_auto_reply_check(
        {"last_content": "已在其它设备接听", "unread_count": 1}
    ) is False


def test_takeover_scan_candidate_does_not_require_unread_badge():
    assert engine._session_is_scan_candidate(
        {"peer_id": "customer-a", "last_content": "customer message", "unread_count": 0}
    ) is True
    assert engine._session_is_scan_candidate(
        {"peer_id": "customer-a", "last_content": "\u5df2\u5728\u5176\u4ed6\u8bbe\u5907\u63a5\u542c", "unread_count": 0}
    ) is False


def test_latest_auto_reply_candidate_rejects_system_message_after_customer_message(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    engine.init_db()
    with engine._connect() as conn:
        conn.execute(
            """
            insert into wechat_messages(
                id, account_id, peer_id, direction, msg_type, content, provider_message_id, status, raw_json, created_at
            ) values(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "customer-message",
                engine.LOCAL_DEFAULT_ACCOUNT_ID,
                "customer-a",
                "in",
                "text",
                "customer message",
                "customer-message",
                "received",
                engine._json_dumps({"attr": "friend", "sender": "customer"}),
                "2026-08-15T10:00:00.000000",
            ),
        )
        conn.execute(
            """
            insert into wechat_messages(
                id, account_id, peer_id, direction, msg_type, content, provider_message_id, status, raw_json, created_at
            ) values(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "system-message",
                engine.LOCAL_DEFAULT_ACCOUNT_ID,
                "customer-a",
                "system",
                "system",
                "\u5df2\u5728\u5176\u4ed6\u8bbe\u5907\u63a5\u542c",
                "system-message",
                "received",
                engine._json_dumps({"attr": "system", "sender": "system", "type": "system"}),
                "2026-08-15T10:00:01.000000",
            ),
        )

    assert engine._latest_auto_reply_candidate(engine.LOCAL_DEFAULT_ACCOUNT_ID, "customer-a") is None


def test_moments_publish_uses_shared_wechat_ui_lock(monkeypatch):
    captured = {}

    def run(account_id, operation, callback, *, retry_on_failure=True):
        captured.update(
            account_id=account_id,
            operation=operation,
            retry_on_failure=retry_on_failure,
        )
        return {"ok": True, "locked": True}

    monkeypatch.setattr(engine, "_run_local_driver_operation", run)

    result = engine.publish_moments_local("pc-wechat-default", "朋友圈正文")

    assert result == {"ok": True, "locked": True}
    assert captured == {
        "account_id": "pc-wechat-default",
        "operation": "发布朋友圈",
        "retry_on_failure": False,
    }


def test_moments_file_dialog_uses_targeted_controls(monkeypatch):
    class FakeNode:
        pass

    filename = FakeNode()
    open_button = FakeNode()
    calls = []

    class FakeRoot:
        def EditControl(self, **kwargs):
            calls.append(("edit", kwargs))
            return filename if kwargs.get("AutomationId") == "1148" else None

        def ButtonControl(self, **kwargs):
            calls.append(("button", kwargs))
            return open_button if kwargs.get("AutomationId") == "1" else None

    root = FakeRoot()

    assert engine._file_dialog_filename_edit(root) is filename
    assert engine._file_dialog_open_button(root) is open_button
    assert calls == [
        ("edit", {"searchDepth": 8, "AutomationId": "1148"}),
        ("button", {"searchDepth": 8, "AutomationId": "1"}),
    ]


def test_moments_publish_entry_accepts_initial_file_picker(monkeypatch):
    page_root = object()
    picker_root = object()
    publish_entry = object()
    roots = iter([page_root, picker_root])
    clicked = []
    steps = []

    monkeypatch.setattr(engine, "_moments_publish_hwnd", lambda _hwnd: 321)
    monkeypatch.setattr(engine, "_focus_local_wechat", lambda _hwnd: None)
    monkeypatch.setattr(engine, "_uia_foreground_or_main_root", lambda _hwnd: next(roots))
    monkeypatch.setattr(engine, "_moments_publish_dialog_ready", lambda _root: False)
    monkeypatch.setattr(engine, "_uia_find_by_names", lambda *_args, **_kwargs: publish_entry)
    monkeypatch.setattr(engine, "_uia_click", lambda node: clicked.append(node))
    monkeypatch.setattr(engine, "_uia_control_text", lambda _node: "发表")
    monkeypatch.setattr(
        engine,
        "_file_dialog_filename_edit",
        lambda root: object() if root is picker_root else None,
    )

    hwnd = engine._click_moments_publish_entry(123, steps, expect_file_picker=True)

    assert hwnd == 321
    assert clicked == [publish_entry]
    assert steps[-1] == {"step": "moments_file_picker_ready", "ok": True, "attempt": 1}


def test_moments_publish_selects_initial_picker_before_compose(monkeypatch):
    files = [{"local_path": "C:/temp/post.jpg", "filename": "post.jpg", "kind": "image", "size": 10}]
    picker_root = object()
    calls = []

    monkeypatch.setattr(engine, "_find_local_account", lambda _account_id: {"hwnd": 123})
    monkeypatch.setattr(engine, "_normalize_attachments", lambda _attachments: files)
    monkeypatch.setattr(engine, "_enforce_local_moments_publish_rate", lambda _account_id: None)
    monkeypatch.setattr(engine, "_open_local_moments", lambda _hwnd, _steps: calls.append("open_moments"))
    monkeypatch.setattr(
        engine,
        "_click_moments_publish_entry",
        lambda _hwnd, _steps, *, expect_file_picker=False: calls.append(("open_entry", expect_file_picker)) or 456,
    )
    monkeypatch.setattr(engine, "_uia_foreground_or_main_root", lambda _hwnd: picker_root)
    monkeypatch.setattr(engine, "_file_dialog_filename_edit", lambda root: object() if root is picker_root else None)
    monkeypatch.setattr(engine, "_select_files_in_open_dialog", lambda _hwnd, _files, _steps: calls.append("select_files"))
    monkeypatch.setattr(engine, "_wait_for_moments_publish_dialog", lambda _hwnd, _steps: calls.append("wait_compose"))
    monkeypatch.setattr(engine, "_focus_moments_publish_text", lambda _hwnd, _steps: calls.append("focus_text"))
    monkeypatch.setattr(engine, "_fill_moments_publish_text", lambda _hwnd, _text, _steps: calls.append("fill_text"))
    monkeypatch.setattr(engine, "_submit_moments_publish", lambda _hwnd, _steps: calls.append("submit"))

    result = engine._publish_moments_local_once(
        "pc-wechat-default",
        "正文",
        attachments=files,
    )

    assert result["ok"] is True
    assert calls == [
        "open_moments",
        ("open_entry", True),
        "select_files",
        "wait_compose",
        "focus_text",
        "fill_text",
        "submit",
    ]


def test_moments_publish_rejects_video_longer_than_wechat_limit(monkeypatch):
    files = [{"local_path": "C:/temp/long.mp4", "filename": "long.mp4", "kind": "video", "size": 10}]

    monkeypatch.setattr(engine, "_find_local_account", lambda _account_id: {"hwnd": 123})
    monkeypatch.setattr(engine, "_normalize_attachments", lambda _attachments: files)
    monkeypatch.setattr(engine, "_probe_moments_video_duration", lambda _path: 50.916667)

    with pytest.raises(engine._MomentsPublishError, match="最长支持30秒") as error:
        engine._publish_moments_local_once("pc-wechat-default", attachments=files)

    assert error.value.steps == [
        {"step": "validate_moments_video", "ok": False, "duration_seconds": 50.917}
    ]


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


def test_local_driver_send_failure_is_retried_after_driver_recovery(monkeypatch):
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

    def send_once(*_args, **_kwargs):
        calls.append("send")
        if len(calls) == 1:
            raise RuntimeError("send result unknown")
        return {"ok": True, "verified": True}

    monkeypatch.setattr(engine, "_send_text_local_slow_once", send_once)

    result = engine._send_text_local_slow(engine.LOCAL_DEFAULT_ACCOUNT_ID, "Alice", "hello")

    assert calls == ["send", "send"]
    assert result["driver_recovered"] is True


def test_typed_message_clicks_send_and_verifies_new_outbound(monkeypatch):
    messages = [
        {"id": "incoming-1", "content": "你好", "attr": "friend", "type": "text"},
    ]
    clicks = []

    class FakeWx:
        def GetAllMessage(self):
            return list(messages)

    def click_send(_hwnd):
        clicks.append("send")
        messages.append({"id": "outgoing-1", "content": "您好，有什么可以帮您？", "attr": "self", "type": "text"})
        return "uia_send_button"

    monkeypatch.setattr(engine, "_click_local_wechat_send_button", click_send)
    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)

    result = engine._submit_local_wechat_typed_message(
        FakeWx(),
        123,
        "您好，有什么可以帮您？",
        verify_timeout=0.05,
    )

    assert clicks == ["send"]
    assert result == {"ok": True, "verified": True, "send_method": "uia_send_button", "attempts": 1}


def test_local_send_uses_verified_ui_path(monkeypatch):
    calls = []

    def verified_send(account_id, peer_id, text, raw_meta=None):
        calls.append((account_id, peer_id, text, raw_meta))
        return {"ok": True, "verified": True, "driver": "pc_wechat_slow_typing"}

    monkeypatch.setattr(engine, "_send_text_local_slow", verified_send)

    result = engine._send_text_local("local-account", "张深根（私人号）", "明天上午聊")

    assert result["verified"] is True
    assert calls == [
        (
            "local-account",
            "张深根（私人号）",
            "明天上午聊",
            {"driver": "native_wechat_verified_send", "source": "send_text"},
        )
    ]


def test_group_picker_selects_exact_contact_checkbox_and_verifies(monkeypatch):
    class Rect:
        def __init__(self, left, top, right, bottom):
            self.left = left
            self.top = top
            self.right = right
            self.bottom = bottom

    class Node:
        def __init__(self, name="", class_name="", control_type="", rect=None, children=None):
            self.Name = name
            self.ClassName = class_name
            self.ControlTypeName = control_type
            self.BoundingRectangle = rect or Rect(0, 0, 100, 30)
            self._children = list(children or [])
            self.IsOffscreen = False
            self.IsEnabled = True
            self.IsSelected = False

        def GetChildren(self):
            return list(self._children)

    count = Node("已选择1个联系人")
    checkbox = Node("", "mmui::XCheckBox", "CheckBoxControl", Rect(10, 10, 34, 34))
    own_contact = Node(
        "张深根\n（私人号）",
        "mmui::ContactsCellItemView",
        "ListItemControl",
        Rect(0, 0, 360, 48),
        [checkbox, Node("张深根（私人号）", "mmui::XTextView", "TextControl")],
    )
    other_contact = Node(
        "张深根-AI三域营销运营",
        "mmui::ContactsCellItemView",
        "ListItemControl",
        Rect(0, 55, 360, 103),
        [Node("张深根-AI三域营销运营", "mmui::XTextView", "TextControl")],
    )
    root = Node("", "mmui::SessionPickerWindow", children=[count, own_contact, other_contact])
    clock = [0.0]
    clicks = []

    monkeypatch.setattr(engine, "_group_picker_root", lambda _hwnd: root)
    monkeypatch.setattr(engine, "_find_group_picker_search_edit", lambda _root: Node("search", "Edit"))
    monkeypatch.setattr(engine, "_uia_set_text", lambda _node, _text: None)
    monkeypatch.setattr(engine.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(engine.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def click(node):
        clicks.append(node)
        node.IsSelected = True
        count.Name = "已选择2个联系人"

    monkeypatch.setattr(engine, "_uia_click", click)

    assert engine._find_group_picker_contact_node(root, "张深根-AI三域营销运营") is other_contact

    steps = []
    engine._select_group_picker_contact(123, engine.LOCAL_DEFAULT_ACCOUNT_ID, "张深根（私人号）", steps)

    assert clicks == [checkbox]
    assert steps[-1]["target"] == "张深根（私人号）"
    assert steps[-1]["selection_method"] == "mmui::XCheckBox"
    assert steps[-1]["verified"] is True


def test_typed_message_retries_button_when_text_remains_in_draft(monkeypatch):
    clicks = []

    class FakeWx:
        def GetAllMessage(self):
            return [{"id": "incoming-1", "content": "你好", "attr": "friend", "type": "text"}]

    monkeypatch.setattr(
        engine,
        "_click_local_wechat_send_button",
        lambda _hwnd: clicks.append("send") or "coordinate_send_button",
    )
    monkeypatch.setattr(engine, "_local_wechat_draft_text", lambda _hwnd: "这是一条未发出的回复")
    monkeypatch.setattr(engine, "_focus_local_wechat", lambda _hwnd: None)
    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="仍停留在输入框"):
        engine._submit_local_wechat_typed_message(
            FakeWx(),
            123,
            "这是一条未发出的回复",
            verify_timeout=0.01,
        )

    assert clicks == ["send", "send"]


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


def test_recent_session_sync_uses_twenty_page_limit(monkeypatch):
    captured = {}

    fake_uia = types.SimpleNamespace(ControlFromHandle=lambda _hwnd: object())

    def fake_collect_recent_sessions(hwnd, *, max_rounds=5):
        captured["hwnd"] = hwnd
        captured["max_rounds"] = max_rounds
        return {
            "items": [
                {
                    "peer_id": "today-session",
                    "display_name": "today-session",
                    "last_content": "你好",
                    "session_time": "2026-08-14 09:00",
                    "unread_count": 1,
                    "is_new": True,
                }
            ],
            "rounds": 2,
            "completed": False,
        }

    monkeypatch.setitem(sys.modules, "uiautomation", fake_uia)
    monkeypatch.setattr(engine, "_module_available", lambda _name: True)
    monkeypatch.setattr(engine, "_local_wechat_hwnd", lambda _account_id="": 123)
    monkeypatch.setattr(engine, "_ensure_local_tab", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "_uia_collect_recent_sessions", fake_collect_recent_sessions)
    monkeypatch.setattr(engine, "_persist_session", lambda _account_id, session, **_kwargs: {"changed": True, **session})

    result = engine._sync_local_sessions_from_uia(engine.LOCAL_DEFAULT_ACCOUNT_ID, recent_only=True)

    assert captured["hwnd"] == 123
    assert captured["max_rounds"] == 20
    assert result["recent_only"] is True
    assert result["source"] == "pc_wechat_uia_sessions_recent"
    assert result["count"] == 1


def test_recent_session_collect_does_not_stop_on_yesterday_label(monkeypatch):
    class FakeCell:
        def __init__(self, text: str):
            self.Name = text
            self.ClassName = "mmui::ChatSessionCell"
            self.BoundingRectangle = None

    pages = [
        [FakeCell("A\n今天 10:00")],
        [FakeCell("B\n昨天 12:45")],
        [FakeCell("C\n08/07")],
        [FakeCell("D\n星期六")],
        [FakeCell("E\n08/04")],
    ]
    state = {"page": 0}

    class FakeScrollTarget:
        def WheelUp(self, wheelTimes=1):
            return None

        def WheelDown(self, wheelTimes=1):
            state["page"] = min(state["page"] + 1, len(pages) - 1)
            return None

    fake_uia = types.SimpleNamespace(ControlFromHandle=lambda _hwnd: object())
    monkeypatch.setitem(sys.modules, "uiautomation", fake_uia)
    monkeypatch.setattr(engine, "_uia_session_cells", lambda _root: pages[state["page"]])
    monkeypatch.setattr(engine, "_uia_scroll_target_from_cells", lambda _cells, _root: FakeScrollTarget())

    result = engine._uia_collect_recent_sessions(123, max_rounds=5)

    assert result["rounds"] == 5
    assert [item["peer_id"] for item in result["items"]] == ["A", "B", "C", "D", "E"]


def test_uia_session_cell_parses_inline_unread_badge_and_mute_label():
    class FakeCell:
        Name = "雅涛花园纯业主\n[6条] 李华健: [文件] 8.14号 外贸实单采购.pdf\n14:56\n消息免打扰"
        ClassName = "mmui::ChatSessionCell"

    item = engine._session_from_uia_cell(FakeCell())

    assert item["peer_id"] == "雅涛花园纯业主"
    assert item["unread_count"] == 6
    assert item["is_new"] is True
    assert item["is_muted"] is True
    assert item["session_time"] == "14:56"
    assert item["last_content"] == "李华健: [文件] 8.14号 外贸实单采购.pdf"


def test_auto_reply_skips_non_private_sessions_before_opening_chat():
    assert engine._looks_like_group_session({"peer_id": "服务号", "last_content": "通知", "unread_count": 2}) is True
    assert engine._looks_like_group_session({"peer_id": "客户交流群", "last_content": "你好", "unread_count": 1}) is True
    assert engine._looks_like_group_session({"peer_id": "客户A", "last_content": "你好", "unread_count": 1}) is False


@pytest.mark.asyncio
async def test_poll_scans_recent_pages_but_only_reads_unread(monkeypatch):
    called = {"sessions_kwargs": None, "messages": 0}

    def sync_sessions(*_args, **kwargs):
        called["sessions_kwargs"] = dict(kwargs)
        return {
            "items": [
                {
                    "peer_id": "later-page-session",
                    "display_name": "later-page-session",
                    "last_content": "历史记录",
                    "session_time": "2026-08-14 10:00:00",
                    "unread_count": 0,
                    "is_new": False,
                }
            ]
        }

    def sync_messages(*_args, **_kwargs):
        called["messages"] += 1
        raise AssertionError("sync_local_messages should not run without unread sessions")

    monkeypatch.setattr(engine, "init_db", lambda: None)
    monkeypatch.setattr(engine, "_find_local_account", lambda _account_id: {})
    monkeypatch.setattr(engine, "sync_local_sessions", sync_sessions)
    monkeypatch.setattr(engine, "sync_local_messages", sync_messages)

    result = await engine.poll_updates(engine.LOCAL_DEFAULT_ACCOUNT_ID)

    assert called["sessions_kwargs"]["recent_only"] is True
    assert called["messages"] == 0
    assert result["unread_session_count"] == 0
    assert result["group_sync"]["skipped"] is True


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
async def test_takeover_action_uses_server_polling_settings(monkeypatch):
    captured = {}

    async def run_takeover(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(channel, "_run_native_wechat_takeover_session", run_takeover)

    result = await channel._run_client_workflow_action(
        "native_wechat_poll",
        {
            "account_id": "pc-wechat-default",
            "takeover_session_minutes": 60,
            "message_poll_interval_seconds": 15,
            "accept_friend_requests_once": True,
        },
        headers={},
        run_id="takeover-run",
        cloud=None,
        base="",
    )

    assert result == {"ok": True}
    assert "rounds" not in captured
    assert captured["interval_seconds"] == 15
    assert captured["session_seconds"] == 3600


@pytest.mark.asyncio
async def test_takeover_action_keeps_polling_defaults_for_old_tasks(monkeypatch):
    captured = {}

    async def run_takeover(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(channel, "_run_native_wechat_takeover_session", run_takeover)

    await channel._run_client_workflow_action(
        "native_wechat_poll",
        {"account_id": "pc-wechat-default"},
        headers={},
        run_id="legacy-takeover-run",
        cloud=None,
        base="",
    )

    assert "rounds" not in captured
    assert captured["interval_seconds"] == 15
    assert captured["session_seconds"] == 1800


@pytest.mark.asyncio
async def test_legacy_group_invite_action_finishes_without_waiting(monkeypatch):
    calls = []

    async def run_takeover(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(channel, "_run_native_wechat_takeover_session", run_takeover)

    result = await channel._run_client_workflow_action(
        "native_wechat_poll",
        {
            "account_id": "pc-wechat-default",
            "followup_action": "group_invite",
            "takeover_session_minutes": 30,
        },
        headers={},
        run_id="legacy-group-invite-run",
        cloud=None,
        base="",
    )

    assert result["skipped"] is True
    assert result["reason"] == "group_invite_folded_into_takeover"
    assert calls == []


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
    request_bodies = []

    async def post_local(path, body, **kwargs):
        assert path == "/api/native-wechat/auto-reply/run-once"
        assert body["force"] is True
        request_bodies.append(dict(body))
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
        interval_seconds=15,
    )

    assert sleeps == [15, 15]
    assert [body["check_friend_requests"] for body in request_bodies] == [True, False, False]
    assert result["completed_rounds"] == 3
    assert result["replied"] == 2
    assert result["skipped"] == 1
    assert result["friend_requests_checked"] == 2
    assert result["friend_requests_accepted"] == 1
    assert result["friend_requests_failed"] == 0
    assert result["friend_requests_checked_once"] is True
    assert result["group_invite_candidates"] == 1
    assert [item["round"] for item in result["items"]] == [1, 3]


@pytest.mark.asyncio
async def test_takeover_session_defaults_to_thirty_minutes_at_fifteen_second_intervals(monkeypatch):
    clock = {"now": 0.0}
    request_bodies = []
    sleeps = []

    async def post_local(path, body, **kwargs):
        request_bodies.append(dict(body))
        return {"ok": True, "items": []}

    async def no_sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(channel, "_takeover_monotonic", lambda: clock["now"])
    monkeypatch.setattr(channel, "_post_local_api_json", post_local)
    monkeypatch.setattr(channel.asyncio, "sleep", no_sleep)

    result = await channel._run_native_wechat_takeover_session(
        account_id="pc-wechat-default",
        headers={},
        cloud=None,
        base="",
        run_id="run",
    )

    assert result["session_minutes"] == 30
    assert result["completed_rounds"] == 120
    assert len(sleeps) == 120
    assert set(sleeps) == {15.0}
    assert request_bodies[0]["check_friend_requests"] is True
    assert all(body["check_friend_requests"] is False for body in request_bodies[1:])


@pytest.mark.asyncio
async def test_takeover_session_waits_after_each_round_and_finishes_last_started_round(monkeypatch):
    clock = {"now": 0.0}
    starts = []
    sleeps = []

    async def post_local(_path, _body, **_kwargs):
        starts.append(clock["now"])
        clock["now"] += 20.0
        return {"ok": True, "items": []}

    async def advance_sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(channel, "_takeover_monotonic", lambda: clock["now"])
    monkeypatch.setattr(channel, "_post_local_api_json", post_local)
    monkeypatch.setattr(channel.asyncio, "sleep", advance_sleep)

    result = await channel._run_native_wechat_takeover_session(
        account_id="pc-wechat-default",
        headers={},
        cloud=None,
        base="",
        run_id="run",
        rounds=120,
        interval_seconds=15,
        session_seconds=50,
    )

    assert starts == [0.0, 35.0]
    assert sleeps == [15]
    assert result["completed_rounds"] == 2
    assert result["duration_seconds"] == 55.0
    assert result["stop_reason"] == "session_deadline"


@pytest.mark.asyncio
async def test_takeover_session_stops_after_three_consecutive_driver_failures(monkeypatch):
    attempts = []
    sleeps = []

    async def post_local(_path, _body, **_kwargs):
        attempts.append(len(attempts) + 1)
        raise RuntimeError("未识别到可用的微信窗口")

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
        rounds=120,
        interval_seconds=15,
        session_seconds=1800,
    )

    assert attempts == [1, 2, 3]
    assert sleeps == [15.0, 15.0]
    assert result["completed_rounds"] == 0
    assert result["failed"] == 3
    assert result["ok"] is False
    assert result["stop_reason"] == "consecutive_driver_failures"
    assert result["last_error"] == "未识别到可用的微信窗口"


@pytest.mark.asyncio
async def test_takeover_session_stops_before_next_scan_when_slot_owner_changes(monkeypatch):
    event_statuses = iter([200, 409])
    local_calls = []

    async def post_event(*_args, **_kwargs):
        return next(event_statuses)

    async def post_local(*_args, **_kwargs):
        local_calls.append(_args[0] if _args else "")
        return {"ok": True, "items": []}

    monkeypatch.setattr(channel, "_post_task_event", post_event)
    monkeypatch.setattr(channel, "_post_local_api_json", post_local)

    result = await channel._run_native_wechat_takeover_session(
        account_id="pc-wechat-default",
        headers={},
        cloud=object(),
        base="https://example.test",
        run_id="run",
        rounds=10,
        interval_seconds=15,
        session_seconds=1800,
    )

    assert local_calls == ["/api/native-wechat/auto-reply/stop"]
    assert result["completed_rounds"] == 0
    assert result["ok"] is False
    assert result["stop_reason"] == "slot_ownership_changed"


@pytest.mark.asyncio
async def test_takeover_session_stops_after_current_round_when_cloud_run_is_cancelled(monkeypatch):
    event_statuses = iter([200, 200, 409])
    local_calls = []
    sleeps = []

    async def post_event(*_args, **_kwargs):
        return next(event_statuses)

    async def post_local(*_args, **_kwargs):
        local_calls.append(_args[0] if _args else "")
        return {"ok": True, "items": []}

    async def no_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(channel, "_post_task_event", post_event)
    monkeypatch.setattr(channel, "_post_local_api_json", post_local)
    monkeypatch.setattr(channel.asyncio, "sleep", no_sleep)

    result = await channel._run_native_wechat_takeover_session(
        account_id="pc-wechat-default",
        headers={},
        cloud=object(),
        base="https://example.test",
        run_id="run",
        rounds=10,
        interval_seconds=15,
        session_seconds=1800,
    )

    assert local_calls == [
        "/api/native-wechat/auto-reply/run-once",
        "/api/native-wechat/auto-reply/stop",
    ]
    assert sleeps == []
    assert result["completed_rounds"] == 1
    assert result["ok"] is False
    assert result["stop_reason"] == "slot_ownership_changed"


@pytest.mark.asyncio
async def test_takeover_session_keeps_running_on_transient_event_failure(monkeypatch):
    clock = {"now": 0.0}
    local_calls = []

    async def post_event(*_args, **_kwargs):
        return 500

    async def post_local(*_args, **_kwargs):
        local_calls.append(True)
        clock["now"] += 25.0
        return {"ok": True, "items": [], "summary_text": "checked"}

    monkeypatch.setattr(channel, "_takeover_monotonic", lambda: clock["now"])
    monkeypatch.setattr(channel, "_post_task_event", post_event)
    monkeypatch.setattr(channel, "_post_local_api_json", post_local)

    result = await channel._run_native_wechat_takeover_session(
        account_id="pc-wechat-default",
        headers={},
        cloud=object(),
        base="https://example.test",
        run_id="run",
        interval_seconds=15,
        session_seconds=20,
    )

    assert local_calls == [True]
    assert result["completed_rounds"] == 1
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_client_workflow_reports_explicit_local_failure_to_cloud(monkeypatch):
    completions = []

    async def post_event(*_args, **_kwargs):
        return None

    async def run_action(*_args, **_kwargs):
        return {
            "ok": False,
            "stop_reason": "consecutive_driver_failures",
            "last_error": "未识别到可用的微信窗口",
        }

    async def complete(*_args, **kwargs):
        completions.append(kwargs)

    monkeypatch.setattr(channel, "_post_task_event", post_event)
    monkeypatch.setattr(channel, "_run_client_workflow_action", run_action)
    monkeypatch.setattr(channel, "_complete_task_run", complete)

    await channel._run_client_workflow(
        object(),
        "https://example.test",
        {},
        {
            "id": "run-id",
            "payload": {
                "action": "native_wechat_poll",
                "params": {"account_id": "pc-wechat-default"},
            },
        },
    )

    assert len(completions) == 1
    assert completions[0]["error"] == "未识别到可用的微信窗口"
    assert completions[0]["result_payload"]["local_result"]["ok"] is False


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
                "dedup_key": "",
                "source_peer_id": "customer-a",
                "source_inbound_message_id": "message-a",
                "group_invite_reason": "客户明确要求预约体验",
                "matched_group_keywords": ["预约体验"],
            },
        )
    ]
    assert result["matched"] == 1
    assert result["queued"] == 1


@pytest.mark.asyncio
async def test_group_invite_prefers_platform_account_config_over_source(monkeypatch):
    async def resolve_parent(*args, **kwargs):
        return [
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
        ]

    calls = []

    async def post_local(path, body, **kwargs):
        calls.append((path, body))
        return {"ok": True, "task": {"id": "group-task"}}

    monkeypatch.setattr(channel, "_resolve_parent_workflow_results", resolve_parent)
    monkeypatch.setattr(channel.asyncio, "sleep", lambda _seconds: None)
    monkeypatch.setattr(channel, "_post_local_api_json", post_local)
    monkeypatch.setattr(
        channel.native_wechat_engine,
        "get_auto_reply_config",
        lambda _account_id: {
            "group_invite_primary_contact": "九变",
            "group_invite_primary_contact_name": "九变",
            "group_invite_welcome_message": "这是平台账号配置的欢迎语。",
        },
    )

    result = await channel._run_native_wechat_group_invite_followup(
        {
            "source_workflow_node_id": "wechat-private",
            "parent_wait_seconds": 0,
            "parent_poll_seconds": 15,
            "group_invite_primary_contact": "军争",
            "group_invite_primary_contact_name": "军争",
            "group_invite_welcome_message": "旧的节点值。",
        },
        account_id="pc-wechat-default",
        headers={},
        cloud=object(),
        base="https://example.com",
        current_item={},
    )

    assert calls == [
        (
            "/api/native-wechat/groups/create",
            {
                "account_id": "pc-wechat-default",
                "contacts": ["customer-a", "九变"],
                "welcome_message": "这是平台账号配置的欢迎语。",
                "dedup_key": "",
                "source_peer_id": "customer-a",
                "source_inbound_message_id": "message-a",
                "group_invite_reason": "客户明确要求预约体验",
                "matched_group_keywords": ["预约体验"],
            },
        )
    ]
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
        language="en-US",
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
    assert first["language"] == "en"
    assert second["memory_doc_ids"] == ["faq-doc"]
    assert second["language"] == "en"
    assert second["group_invite_memory_doc_id"] == "group-rule-doc"
    assert second["group_invite_keywords"] == "咨询报价，预约体验"
    assert second["group_invite_contacts"] == ["销售经理"]
    assert second["group_invite_primary_contact"] == "销售经理"
    assert second["group_invite_primary_contact_name"] == "王经理"
    assert second["group_invite_welcome_message"].startswith("您好")


def test_auto_reply_config_allows_short_intervals(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    monkeypatch.setattr(engine, "_find_local_account", lambda _account_id: {"account_id": _account_id})
    monkeypatch.setattr(engine, "ensure_auto_reply_worker", lambda *args, **kwargs: None)

    cfg = engine.save_auto_reply_config(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        enabled=False,
        interval_seconds=15,
    )

    assert cfg["interval_seconds"] == 15
    assert engine._auto_reply_due(
        {
            "interval_seconds": 15,
            "last_checked_at": (datetime.utcnow() - timedelta(seconds=16)).isoformat(),
        }
    ) is True
    assert engine._auto_reply_due(
        {
            "interval_seconds": 15,
            "last_checked_at": (datetime.utcnow() - timedelta(seconds=14)).isoformat(),
        }
    ) is False


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
            "group_verified": True,
            "verified_member_count": 3,
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


@pytest.mark.asyncio
async def test_auto_reply_match_immediately_queues_group_with_primary_contact(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    engine.init_db()
    engine._persist_contact(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        {
            "contact_key": "张老师",
            "display_name": "张老师",
            "wx_no": "wxid_primary_contact",
            "source": "test",
        },
    )
    calls = []

    async def create_group(account_id, contacts, **kwargs):
        calls.append((account_id, contacts, kwargs))
        return {"id": "group-task", "status": "pending"}

    monkeypatch.setattr(engine, "create_group_task", create_group)
    result = await engine._queue_auto_reply_group_invite(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        "客户A",
        {"auto_reply_inbound_id": "message-a", "content": "可以"},
        {
            "should_invite_group": True,
            "matched_group_keywords": ["明确同意对接"],
            "group_invite_reason": "客户同意对接负责人",
        },
        {
            "group_invite_enabled": True,
            "group_invite_primary_contact": "张老师",
            "group_invite_primary_contact_name": "张老师",
            "group_invite_welcome_message": "欢迎进群",
        },
    )

    assert calls[0][0] == engine.LOCAL_DEFAULT_ACCOUNT_ID
    assert calls[0][1] == ["客户A", "wxid_primary_contact"]
    assert calls[0][2]["source_inbound_message_id"] == "message-a"
    assert calls[0][2]["dedup_key"].startswith("auto-invite-")
    assert calls[0][2]["use_current_chat"] is True
    assert calls[0][2]["customer_wx_no"] == ""
    assert calls[0][2]["execute_now"] is True
    assert result["queued"] is True
    assert result["task_id"] == "group-task"


@pytest.mark.asyncio
async def test_auto_reply_group_invite_rejects_customer_as_primary_contact(monkeypatch):
    monkeypatch.setattr(
        engine,
        "create_group_task",
        lambda *_args, **_kwargs: pytest.fail("invalid group members must not create a task"),
    )

    result = await engine._queue_auto_reply_group_invite(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        "张老师",
        {"auto_reply_inbound_id": "message-a", "content": "行"},
        {"should_invite_group": True},
        {
            "group_invite_enabled": True,
            "group_invite_primary_contact": "张老师",
            "group_invite_primary_contact_name": "张老师",
        },
    )

    assert result == {"ok": True, "skipped": True, "reason": "primary_contact_is_customer"}


@pytest.mark.asyncio
async def test_unexecutable_group_invite_suppresses_promised_reply(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    monkeypatch.setattr(
        engine,
        "_load_auto_reply_memory_context",
        lambda *args, **kwargs: {"text": "", "document_count": 0, "titles": []},
    )
    monkeypatch.setattr(
        engine,
        "sync_local_sessions",
        lambda *_args, **_kwargs: {
            "items": [
                {
                    "peer_id": "张老师",
                    "display_name": "张老师",
                    "last_content": "行",
                    "unread_count": 1,
                }
            ]
        },
    )
    monkeypatch.setattr(
        engine,
        "sync_local_messages",
        lambda *_args, **_kwargs: {"peer_id": "张老师", "chat_info": {"chat_type": "direct"}},
    )
    monkeypatch.setattr(
        engine,
        "_latest_auto_reply_candidate",
        lambda *_args, **_kwargs: {
            "peer_id": "张老师",
            "direction": "in",
            "content": "行",
            "auto_reply_inbound_id": "message-a",
        },
    )
    monkeypatch.setattr(engine, "_recent_conversation_text", lambda *_args, **_kwargs: "我: 我来建个群\n对方: 行")

    async def reply(**_kwargs):
        return {
            "should_reply": True,
            "reply": "好的，我这就建群",
            "category": "cooperation",
            "should_invite_group": True,
            "matched_group_keywords": ["明确同意拉群"],
            "group_invite_reason": "客户已同意",
        }

    async def queue(*_args, **_kwargs):
        return {"ok": True, "skipped": True, "reason": "primary_contact_is_customer"}

    monkeypatch.setattr(engine, "_call_auto_reply_llm", reply)
    monkeypatch.setattr(engine, "_queue_auto_reply_group_invite", queue)
    monkeypatch.setattr(
        engine,
        "_send_text_local_slow",
        lambda *_args, **_kwargs: pytest.fail("a failed group action must not send a promise to the customer"),
    )

    result = await engine.run_auto_reply_once(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        force=True,
        check_friend_requests=False,
    )

    assert result["replied"] == 0
    assert result["skipped"] == 1
    assert result["items"][0]["status"] == "group_invite_not_executable"
    assert result["items"][0]["reply_suppressed"] is True
    assert "不能与当前客户相同" in result["items"][0]["error"]


@pytest.mark.asyncio
async def test_verified_group_skips_before_llm_or_conversation_context(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    engine.init_db()
    monkeypatch.setattr(
        engine,
        "get_auto_reply_config",
        lambda _account_id: {
            "enabled": True,
            "group_invite_enabled": True,
            "group_invite_primary_contact": "sales-contact",
            "group_invite_contacts": ["sales-contact"],
            "memory_doc_ids": [],
            "group_invite_memory_doc_id": "",
            "group_invite_keywords": "",
            "group_invite_welcome_message": "welcome",
        },
    )
    monkeypatch.setattr(
        engine,
        "_load_auto_reply_memory_context",
        lambda *args, **kwargs: {"text": "", "document_count": 0, "titles": []},
    )
    async def flush_outbox(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(engine, "_flush_wechat_intelligence_outbox", flush_outbox)
    monkeypatch.setattr(engine, "sync_local_sessions", lambda *_args, **_kwargs: {
        "items": [{"peer_id": "customer-a", "display_name": "Customer A", "last_content": "please invite me"}],
        "scroll_rounds": 1,
    })
    monkeypatch.setattr(engine, "_enrich_sessions_with_message_counts", lambda _account_id, items: items)
    monkeypatch.setattr(
        engine,
        "sync_local_messages",
        lambda *_args, **_kwargs: {"peer_id": "customer-a", "chat_info": {"chat_type": "direct"}},
    )
    monkeypatch.setattr(
        engine,
        "_latest_auto_reply_candidate",
        lambda *_args, **_kwargs: {
            "peer_id": "customer-a",
            "direction": "in",
            "content": "please invite me",
            "auto_reply_inbound_id": "message-a",
        },
    )
    monkeypatch.setattr(engine, "_resolve_local_contact_wx_no", lambda *_args, **_kwargs: "wx-sales")
    monkeypatch.setattr(engine, "_has_verified_group_invite", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        engine,
        "_recent_conversation_text",
        lambda *_args, **_kwargs: pytest.fail("verified group must not load conversation context"),
    )
    monkeypatch.setattr(
        engine,
        "_load_wechat_intelligence_context",
        lambda *_args, **_kwargs: pytest.fail("verified group must not query intelligence context"),
    )
    monkeypatch.setattr(
        engine,
        "_call_auto_reply_llm",
        lambda **_kwargs: pytest.fail("verified group must not call the LLM"),
    )
    monkeypatch.setattr(
        engine,
        "_send_text_local_slow",
        lambda *_args, **_kwargs: pytest.fail("verified group must not send a reply"),
    )

    result = await engine.run_auto_reply_once(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        force=True,
        check_friend_requests=False,
    )

    assert result["replied"] == 0
    assert result["skipped"] == 1
    assert result["items"][0]["status"] == "group_already_verified"
    assert result["items"][0]["verification_source"] == "local_task"


@pytest.mark.asyncio
async def test_group_session_is_skipped_before_reply_or_group_decision(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    monkeypatch.setattr(
        engine,
        "get_auto_reply_config",
        lambda _account_id: {
            "enabled": True,
            "group_invite_enabled": True,
            "group_invite_primary_contact": "sales-contact",
            "group_invite_contacts": ["sales-contact"],
            "memory_doc_ids": [],
            "group_invite_memory_doc_id": "",
        },
    )
    monkeypatch.setattr(
        engine,
        "_load_auto_reply_memory_context",
        lambda *args, **kwargs: {"text": "", "document_count": 0, "titles": []},
    )
    async def flush_outbox(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(engine, "_flush_wechat_intelligence_outbox", flush_outbox)
    monkeypatch.setattr(
        engine,
        "sync_local_sessions",
        lambda *_args, **_kwargs: {
            "items": [
                {
                    "peer_id": "peer-unknown-chat-type",
                    "display_name": "客户会话",
                    "last_content": "请介绍一下",
                    "unread_count": 1,
                }
            ]
        },
    )
    read_info_calls = []

    def sync_messages(*_args, **kwargs):
        read_info_calls.append(kwargs.get("read_chat_info"))
        return {
            "peer_id": "peer-unknown-chat-type",
            "chat_info": {"chat_type": "group"},
        }

    monkeypatch.setattr(engine, "sync_local_messages", sync_messages)
    monkeypatch.setattr(
        engine,
        "_call_auto_reply_llm",
        lambda **_kwargs: pytest.fail("群聊不能进入模型判断"),
    )
    monkeypatch.setattr(
        engine,
        "_send_text_local_slow",
        lambda *_args, **_kwargs: pytest.fail("群聊不能发送回复"),
    )
    monkeypatch.setattr(
        engine,
        "_queue_auto_reply_group_invite",
        lambda *_args, **_kwargs: pytest.fail("群聊不能触发拉群"),
    )

    result = await engine.run_auto_reply_once(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        force=True,
        check_friend_requests=False,
    )

    assert read_info_calls == [True]
    assert result["replied"] == 0
    assert result["skipped_groups"] == 1
    assert result["items"][0]["status"] == "skipped_group"


@pytest.mark.asyncio
async def test_group_invite_success_keeps_default_welcome_message(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    monkeypatch.setattr(
        engine,
        "_load_auto_reply_memory_context",
        lambda *args, **kwargs: {"text": "", "document_count": 0, "titles": []},
    )
    monkeypatch.setattr(
        engine,
        "get_auto_reply_config",
        lambda _account_id: {
            "enabled": True,
            "group_invite_enabled": True,
            "group_invite_primary_contact": "销售经理",
            "group_invite_primary_contact_name": "王经理",
            "group_invite_welcome_message": "您好，我把王经理拉进群了。",
            "memory_doc_ids": [],
            "group_invite_memory_doc_id": "",
            "group_invite_keywords": "预约体验",
            "group_invite_contacts": ["销售经理"],
        },
    )
    monkeypatch.setattr(
        engine,
        "sync_local_sessions",
        lambda *_args, **_kwargs: {
            "items": [
                {
                    "peer_id": "张老师",
                    "display_name": "张老师",
                    "last_content": "行",
                    "unread_count": 1,
                }
            ]
        },
    )
    monkeypatch.setattr(
        engine,
        "sync_local_messages",
        lambda *_args, **_kwargs: {"peer_id": "张老师", "chat_info": {"chat_type": "direct"}},
    )
    monkeypatch.setattr(
        engine,
        "_latest_auto_reply_candidate",
        lambda *_args, **_kwargs: {
            "peer_id": "张老师",
            "direction": "in",
            "content": "行",
            "auto_reply_inbound_id": "message-a",
        },
    )
    monkeypatch.setattr(engine, "_recent_conversation_text", lambda *_args, **_kwargs: "我: 我来建个群\n对方: 行")

    async def reply(**_kwargs):
        return {
            "should_reply": True,
            "reply": "好的，我这就建群",
            "category": "cooperation",
            "should_invite_group": True,
            "matched_group_keywords": ["明确同意拉群"],
            "group_invite_reason": "客户已同意",
        }

    async def queue(*_args, **_kwargs):
        return {"ok": True, "queued": True, "deduped": False, "task_id": "group-task"}

    monkeypatch.setattr(engine, "_call_auto_reply_llm", reply)
    monkeypatch.setattr(engine, "_queue_auto_reply_group_invite", queue)
    monkeypatch.setattr(
        engine,
        "_send_text_local_slow",
        lambda *_args, **_kwargs: pytest.fail("group invite success should not send a separate customer reply"),
    )

    result = await engine.run_auto_reply_once(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        force=True,
        check_friend_requests=False,
    )

    assert result["replied"] == 0
    assert result["group_invite_queued"] == 1
    assert result["items"][0]["status"] == "group_invite_queued"
    assert result["items"][0]["group_invite_welcome_message"] == "您好，我把王经理拉进群了。"


@pytest.mark.asyncio
async def test_create_group_task_deduplicates_same_auto_invite(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    monkeypatch.setattr(engine, "_find_local_account", lambda _account_id: {"hwnd": 1})
    monkeypatch.setattr(engine, "_ensure_task_worker", lambda _account_id: None)

    first = await engine.create_group_task(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        ["客户A", "张老师"],
        dedup_key="auto-invite-same-message",
        source_peer_id="客户A",
        source_inbound_message_id="message-a",
    )
    second = await engine.create_group_task(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        ["客户A", "张老师"],
        dedup_key="auto-invite-same-message",
        source_peer_id="客户A",
        source_inbound_message_id="message-a",
    )

    assert second["id"] == first["id"]
    assert second["deduped"] is True


@pytest.mark.asyncio
async def test_failed_group_invite_can_retry_after_primary_contact_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    monkeypatch.setattr(engine, "_find_local_account", lambda _account_id: {"hwnd": 1})
    monkeypatch.setattr(engine, "_ensure_task_worker", lambda _account_id: None)
    engine.init_db()

    inbound = {
        "peer_id": "customer-a",
        "direction": "in",
        "content": "how much is it",
        "provider_message_id": "message-a",
        "created_at": "2026-08-14T09:44:00",
        "auto_reply_inbound_id": "message-a",
    }
    engine._record_auto_reply_history(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        "customer-a",
        inbound,
        reply="",
        category="price",
        status="skipped",
        error="group invite queued",
    )
    failed = await engine.create_group_task(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        ["customer-a", "old-sales"],
        dedup_key="auto-invite-old-sales",
        source_peer_id="customer-a",
        source_inbound_message_id="message-a",
    )
    engine._finish_task(failed["id"], "failed", 0, 0, 2, "not found old-sales")
    engine._mark_auto_reply_group_invite_failed(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        "customer-a",
        "message-a",
        "not found old-sales",
    )

    candidate = engine._latest_failed_group_invite_candidate(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        "customer-a",
        "new-sales",
    )

    assert candidate is not None
    assert candidate["content"] == "how much is it"
    assert candidate["auto_reply_inbound_id"] == "message-a"
    assert candidate["previous_group_invite_primary_contact"] == "old-sales"
    with engine._connect() as conn:
        row = conn.execute(
            """
            select status,error_message from wechat_auto_reply_history
            where account_id=? and peer_id=? and inbound_message_id=?
            """,
            (engine.LOCAL_DEFAULT_ACCOUNT_ID, "customer-a", "message-a"),
        ).fetchone()
    assert row["status"] == "failed"
    assert "not found old-sales" in row["error_message"]


@pytest.mark.asyncio
async def test_failed_group_invite_retry_is_throttled_for_same_primary_contact(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    monkeypatch.setattr(engine, "_find_local_account", lambda _account_id: {"hwnd": 1})
    monkeypatch.setattr(engine, "_ensure_task_worker", lambda _account_id: None)
    engine.init_db()
    inbound = {
        "peer_id": "customer-a",
        "direction": "in",
        "content": "please invite me",
        "provider_message_id": "message-a",
        "created_at": "2026-08-14T09:44:00",
        "auto_reply_inbound_id": "message-a",
    }
    engine._record_auto_reply_history(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        "customer-a",
        inbound,
        reply="",
        category="price",
        status="skipped",
        error="group invite queued",
    )
    failed = await engine.create_group_task(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        ["customer-a", "same-sales"],
        dedup_key="auto-invite-same-sales",
        source_peer_id="customer-a",
        source_inbound_message_id="message-a",
    )
    engine._finish_task(failed["id"], "failed", 0, 0, 2, "not found same-sales")

    assert (
        engine._latest_failed_group_invite_candidate(
            engine.LOCAL_DEFAULT_ACCOUNT_ID,
            "customer-a",
            "same-sales",
            retry_cooldown_seconds=300,
        )
        is None
    )
    assert (
        engine._latest_failed_group_invite_candidate(
            engine.LOCAL_DEFAULT_ACCOUNT_ID,
            "customer-a",
            "same-sales",
            retry_cooldown_seconds=0,
        )
        is not None
    )


@pytest.mark.asyncio
async def test_unverified_group_success_does_not_block_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native-wechat.sqlite3")
    monkeypatch.setattr(engine, "_find_local_account", lambda _account_id: {"hwnd": 1})
    monkeypatch.setattr(engine, "_ensure_task_worker", lambda _account_id: None)

    first = await engine.create_group_task(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        ["客户A", "张老师"],
        dedup_key="auto-invite-v2-same-contact",
        source_peer_id="客户A",
        source_inbound_message_id="message-a",
    )
    engine._finish_task(first["id"], "success", 2, 2, 0)
    engine._update_task_payload(first["id"], {"group_verified": False})

    retry = await engine.create_group_task(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        ["客户A", "张老师"],
        dedup_key="auto-invite-v2-same-contact",
        source_peer_id="客户A",
        source_inbound_message_id="message-b",
    )
    assert retry["id"] != first["id"]
    assert retry.get("deduped") is not True

    engine._finish_task(retry["id"], "success", 2, 2, 0)
    engine._update_task_payload(retry["id"], {"group_verified": True})
    deduped = await engine.create_group_task(
        engine.LOCAL_DEFAULT_ACCOUNT_ID,
        ["客户A", "张老师"],
        dedup_key="auto-invite-v2-same-contact",
        source_peer_id="客户A",
        source_inbound_message_id="message-c",
    )
    assert deduped["id"] == retry["id"]
    assert deduped["deduped"] is True


def test_unverified_group_claim_is_detected_and_sanitized():
    assert engine._reply_claims_existing_group("张老师，您已经在群里了哈") is True
    assert engine._reply_claims_existing_group("我先确认一下，再帮您安排") is False
    sanitized = engine._strip_unverified_group_claims("已将张老师拉入群聊，群内对接")
    assert "群状态未核验" in sanitized


def test_local_contact_wx_no_index_prefers_wxno(monkeypatch):
    class FakeWx:
        def GetFriendDetails(self, n=0, timeout=0):
            return [
                {
                    "display_name": "张深根-AI三域营销运营",
                    "remark": "张老师",
                    "wxNo": "AIZhang7891",
                    "username": "AIZhang7891",
                    "contact_key": "AIZhang7891",
                }
            ]

    monkeypatch.setattr(engine, "_get_wxauto4_client", lambda *_args, **_kwargs: FakeWx())
    index = engine._build_local_contact_wx_no_index(100)

    assert index[engine._normalize_contact_lookup_key("张深根-AI三域营销运营")] == "AIZhang7891"
    assert index[engine._normalize_contact_lookup_key("张老师")] == "AIZhang7891"
    assert index[engine._normalize_contact_lookup_key("AIZhang7891")] == "AIZhang7891"




def test_visible_contact_cell_names_walks_nested_children(monkeypatch):
    class Node:
        def __init__(self, name="", class_name="", children=None):
            self.Name = name
            self.ClassName = class_name
            self._children = list(children or [])

        def GetChildren(self):
            return list(self._children)

    nested = Node("???-AI??????", "mmui::ContactsCellItemView")
    wrapper = Node(children=[Node(children=[nested])])
    assert engine._uia_visible_contact_cell_names(wrapper) == ["???-AI??????"]


def test_find_local_contact_list_falls_back_to_guess(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(engine, "_uia_walk", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(engine, "_uia_control_class", lambda _node: "Other")
    monkeypatch.setattr(engine, "_uia_control_text", lambda _node: "")
    monkeypatch.setattr(engine, "_uia_guess_contact_list", lambda _root: sentinel)

    assert engine._find_local_contact_list(object()) is sentinel

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
