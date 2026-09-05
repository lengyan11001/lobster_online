from pathlib import Path

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
