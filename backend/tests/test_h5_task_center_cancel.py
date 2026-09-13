from pathlib import Path

from datetime import datetime, timezone

import pytest

from backend.app.api import h5_chat_channel as channel


def _workflow_item(action: str) -> dict:
    return {
        "task_kind": "client_workflow" if action == "native_wechat_poll" else "douyin_leads",
        "payload": {
            "action": action,
            "h5_context": {"workflow_node_id": "node-1"},
        },
    }


def test_only_private_wechat_takeover_uses_workflow_hard_deadline():
    assert channel._workflow_node_uses_hard_deadline(_workflow_item("native_wechat_poll")) is True
    assert channel._workflow_node_uses_hard_deadline(_workflow_item("precise_touch")) is False
    assert channel._workflow_node_uses_hard_deadline(_workflow_item("account_nurture")) is False


def test_precise_touch_waits_for_natural_action_completion():
    launch = {"total": 100, "interval_seconds_max": 3600}

    assert channel._scheduled_douyin_completion_timeout(
        {"_wait_for_natural_completion": True}, launch
    ) is None
    assert channel._scheduled_douyin_completion_timeout({}, launch) == 7200.0


@pytest.mark.asyncio
async def test_local_task_center_cancel_proxies_cloud_then_stops_local_worker(monkeypatch):
    calls = []

    async def proxy(request, method, path, **_kwargs):
        calls.append((request, method, path))
        if method == "GET":
            return {
                "ok": True,
                "run": {
                    "id": "run-wechat",
                    "status": "processing",
                    "task_kind": "client_workflow",
                    "payload": {
                        "action": "native_wechat_poll",
                        "params": {"account_id": "pc-wechat-default"},
                    },
                },
            }
        return {"ok": True, "cancelled": True, "status": "cancelled"}

    async def stop_local(item, *, headers):
        assert item["id"] == "run-wechat"
        assert headers == {"Authorization": "Bearer test"}
        return {"action": "native_wechat_poll", "stop_requested": True}

    request = object()
    monkeypatch.setattr(channel, "_proxy_cloud_json", proxy)
    monkeypatch.setattr(channel, "_cloud_headers_from_request", lambda _request: {"Authorization": "Bearer test"})
    monkeypatch.setattr(channel, "_stop_workflow_node_with_timeout", stop_local)

    result = await channel.proxy_cancel_scheduled_task_run("run-wechat", request, None)

    assert [(method, path) for _request, method, path in calls] == [
        ("GET", "/api/scheduled-tasks/runs/run-wechat"),
        ("POST", "/api/scheduled-tasks/runs/run-wechat/cancel"),
    ]
    assert result["cancelled"] is True
    assert result["local_stop"]["stop_requested"] is True


@pytest.mark.asyncio
async def test_wechat_workflow_stop_sets_native_stop_flag_directly(monkeypatch):
    run_id = "run-wechat"
    channel._active_client_workflow_actions[run_id] = "native_wechat_poll"
    calls = []
    monkeypatch.setattr(
        channel.native_wechat_engine,
        "request_auto_reply_stop",
        lambda account_id: calls.append(account_id)
        or {"ok": True, "account_id": account_id, "requested": True},
    )
    try:
        result = await channel._stop_workflow_node_local_execution(
            {
                "id": run_id,
                "task_kind": "client_workflow",
                "payload": {
                    "action": "native_wechat_poll",
                    "params": {"account_id": "wechat-account-a"},
                },
            },
            headers={},
        )
    finally:
        channel._active_client_workflow_actions.pop(run_id, None)

    assert calls == ["wechat-account-a"]
    assert result["stop_requested"] is True
    assert result["local"]["requested"] is True


def test_task_center_stop_button_uses_cancel_endpoint():
    source = (Path(__file__).resolve().parents[2] / "static" / "js" / "task-center.js").read_text(
        encoding="utf-8"
    )

    assert "'/api/scheduled-tasks/runs/' + encodeURIComponent(id) + '/cancel'" in source
    assert "{ method: 'POST', headers: headers() }" in source


def test_takeover_deadline_report_shows_what_the_session_actually_did():
    state = {
        "completed_rounds": 4,
        "replied": 3,
        "skipped": 2,
        "failed": 0,
        "friend_requests_checked": 5,
        "friend_requests_accepted": 2,
        "friend_requests_failed": 1,
        "group_invite_candidates": 1,
        "duration_seconds": 372.0,
        "rounds": [{"summary_text": "个微私信自动接管汇总\n- 已自动回复：1 个会话"}],
    }

    report = channel._takeover_deadline_report(state)
    text = channel._workflow_node_deadline_message(
        datetime.now(timezone.utc),
        takeover_report=report,
        takeover_node=True,
    )

    assert "个微私信接管正常收工" in text
    assert "已巡检：4 轮，耗时 6 分 12 秒" in text
    assert "自动回复：3 个会话；跳过：2 个；失败：0 个" in text
    assert "新好友申请：检查 5 个，已同意 2 个，失败 1 个" in text
    assert "疑似加群线索：1 个会话" in text
    assert "已自动回复：1 个会话" in text
    assert "本次任务已自动停止" not in text


def test_takeover_deadline_message_without_a_session_keeps_the_node_wording():
    text = channel._workflow_node_deadline_message(
        datetime.now(timezone.utc),
        takeover_report=None,
        takeover_node=True,
    )

    assert text.startswith("个微私信接管未执行")
    other = channel._workflow_node_deadline_message(datetime.now(timezone.utc), takeover_node=False)
    assert other == "节点时间已结束，本次任务已自动停止，后续节点继续执行。"


def test_takeover_deadline_report_without_a_finished_round_is_explicit():
    text = channel._workflow_node_deadline_message(
        datetime.now(timezone.utc),
        takeover_report=channel._takeover_deadline_report({"completed_rounds": 0}),
        takeover_node=True,
    )

    assert "接管已启动，但本节点时间内未完成一轮巡检" in text


def test_running_takeover_session_counters_reach_the_heartbeat_patch():
    patch = channel._takeover_progress_patch(
        {
            "completed_rounds": 3,
            "replied": 2,
            "skipped": 1,
            "failed": 0,
            "friend_requests_checked": 4,
            "friend_requests_accepted": 2,
            "friend_requests_failed": 1,
            "group_invite_candidates": 1,
        }
    )

    assert patch["completed_rounds"] == 3
    assert patch["replied"] == 2
    assert patch["friend_requests_accepted"] == 2
    assert patch["group_invite_candidates"] == 1


@pytest.mark.asyncio
async def test_deadline_event_carries_the_takeover_facts(monkeypatch):
    run_id = "run-takeover-deadline"
    item = {
        "id": run_id,
        "task_kind": "client_workflow",
        "payload": {
            "action": "native_wechat_poll",
            "params": {"account_id": "wechat-account-a"},
            "h5_context": {"workflow_node_id": "node-1"},
        },
    }
    channel._publish_takeover_session_state(
        run_id,
        {
            "completed_rounds": 2,
            "replied": 1,
            "skipped": 0,
            "failed": 0,
            "friend_requests_checked": 1,
            "friend_requests_accepted": 1,
            "friend_requests_failed": 0,
            "rounds": [],
        },
    )
    captured = {}

    async def fake_post_task_event(client, base, headers, rid, event_type, payload):
        captured["event"] = (rid, event_type, payload)
        return 409

    async def fake_complete(*_args, **_kwargs):
        captured["complete"] = True

    monkeypatch.setattr(channel, "_post_task_event", fake_post_task_event)
    monkeypatch.setattr(channel, "_complete_task_run", fake_complete)

    await channel._report_workflow_node_deadline_expired(
        None,
        "https://cloud.example",
        {},
        item,
        deadline=datetime.now(timezone.utc),
        phase="while_running",
        stop_result={"action": "native_wechat_poll", "stop_requested": True},
    )

    rid, event_type, payload = captured["event"]
    assert rid == run_id
    assert event_type == "cancelled"
    assert payload["reason"] == "workflow_node_deadline_expired"
    assert payload["takeover"]["replied"] == 1
    assert payload["takeover"]["completed_rounds"] == 2
    assert "个微私信接管正常收工" in payload["text"]
    assert payload["local_stop"]["stop_requested"] is True
    assert "complete" not in captured
    assert channel._consume_takeover_session_state(run_id) is None


@pytest.mark.asyncio
async def test_running_takeover_session_is_visible_to_the_deadline_report(monkeypatch):
    run_id = "run-live-takeover"
    rounds_served = []

    async def fake_local_api(path, body, *, headers, timeout_seconds):
        rounds_served.append(path)
        return {
            "replied": 1,
            "skipped": 0,
            "failed": 0,
            "friend_requests_checked": 2,
            "friend_requests_accepted": 1,
            "friend_requests_failed": 0,
            "items": [],
            "config": {},
        }

    monkeypatch.setattr(channel, "_post_local_api_json", fake_local_api)
    monkeypatch.setattr(
        channel.native_wechat_engine,
        "request_auto_reply_stop",
        lambda account_id: {"ok": True, "account_id": account_id, "requested": False},
    )

    try:
        output = await channel._run_native_wechat_takeover_session(
            account_id="wechat-account-a",
            headers={},
            cloud=None,
            base="",
            run_id=run_id,
            interval_seconds=0,
            session_seconds=1,
        )
        live = channel._TAKEOVER_SESSION_STATES.get(run_id)
    finally:
        channel._TAKEOVER_SESSION_STATES.pop(run_id, None)

    assert rounds_served
    assert output["completed_rounds"] >= 1
    assert live is not None
    assert live["replied"] >= 1
    assert live["friend_requests_accepted"] >= 1
