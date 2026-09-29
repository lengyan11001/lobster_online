"""控制面请求（事件/心跳/完成上报）失败后入本地队列补投（2026-09-21）。

现场：`event:heartbeat unavailable after 3 attempts` / `proxy request failed` 之后事件就没了，
服务端可能把任务当成停滞。现在改成：入队 + 轮询时重投，过期即丢。
"""

import asyncio
from datetime import datetime, timezone

import httpx

from backend.app.api import h5_chat_channel as h5


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code
        self.text = ""


class _RecordingClient:
    def __init__(self, *, status=200, error=None):
        self.status = status
        self.error = error
        self.calls = []

    async def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        if self.error is not None:
            raise self.error
        return _FakeResponse(self.status)


def _clear_outbox():
    h5._scheduled_task_control_outbox[:] = []


def test_failed_control_post_is_queued(monkeypatch):
    _clear_outbox()
    monkeypatch.setattr(h5, "_SCHEDULED_TASK_EVENT_ATTEMPTS", 1)
    client = _RecordingClient(error=httpx.ConnectError("boom"))

    status = asyncio.run(
        h5._post_task_control_request(
            client,
            "https://bhzn.top/api/scheduled-tasks/runs/run1/event",
            {"type": "event:heartbeat", "payload": {"text": "本地执行中"}},
            {"Authorization": "Bearer t"},
            label="event:heartbeat",
            run_id="run1",
        )
    )

    assert status == 0
    assert len(h5._scheduled_task_control_outbox) == 1
    queued = h5._scheduled_task_control_outbox[0]
    assert queued["label"] == "event:heartbeat"
    assert queued["run_id"] == "run1"
    assert queued["body"]["payload"]["text"] == "本地执行中"
    _clear_outbox()


def test_outbox_is_redelivered_on_next_poll(monkeypatch):
    _clear_outbox()
    monkeypatch.setattr(h5, "_SCHEDULED_TASK_EVENT_ATTEMPTS", 1)
    failing = _RecordingClient(error=httpx.ConnectError("boom"))
    asyncio.run(
        h5._post_task_control_request(
            failing,
            "https://bhzn.top/api/scheduled-tasks/runs/run2/finish",
            {"status": "completed"},
            {"Authorization": "Bearer t"},
            label="finish",
            run_id="run2",
        )
    )
    assert len(h5._scheduled_task_control_outbox) == 1

    healthy = _RecordingClient(status=200)
    asyncio.run(h5._flush_task_control_outbox(healthy, {"Authorization": "Bearer t2"}))

    assert h5._scheduled_task_control_outbox == [], h5._scheduled_task_control_outbox
    assert healthy.calls and healthy.calls[0]["url"].endswith("/runs/run2/finish")
    assert healthy.calls[0]["json"] == {"status": "completed"}


def test_outbox_drops_expired_items(monkeypatch):
    _clear_outbox()
    monkeypatch.setattr(h5, "_SCHEDULED_TASK_CONTROL_OUTBOX_MAX_AGE_SECONDS", 1.0)
    h5._scheduled_task_control_outbox.append(
        {
            "url": "https://bhzn.top/api/scheduled-tasks/runs/old/finish",
            "body": {"status": "completed"},
            "label": "finish",
            "run_id": "old",
            "queued_at": datetime.now(timezone.utc).timestamp() - 60,
            "attempts": 0,
        }
    )
    client = _RecordingClient(status=200)

    asyncio.run(h5._flush_task_control_outbox(client, {}))

    assert h5._scheduled_task_control_outbox == []
    assert client.calls == [], "过期项不该再发"


def test_outbox_overflow_drops_oldest(monkeypatch):
    _clear_outbox()
    monkeypatch.setattr(h5, "_SCHEDULED_TASK_CONTROL_OUTBOX_LIMIT", 2)
    for index in range(3):
        h5._enqueue_task_control_outbox(
            url=f"https://bhzn.top/api/scheduled-tasks/runs/run{index}/event",
            body={"type": "event:heartbeat"},
            label=f"event:heartbeat#{index}",
            run_id=f"run{index}",
        )

    assert [item["run_id"] for item in h5._scheduled_task_control_outbox] == ["run1", "run2"]
    _clear_outbox()


def test_transient_retry_sleep_has_jitter(monkeypatch):
    _clear_outbox()
    sleeps = []
    monkeypatch.setattr(h5, "_SCHEDULED_TASK_EVENT_ATTEMPTS", 2)
    monkeypatch.setattr(h5.asyncio, "sleep", lambda value: sleeps.append(value) or _done())
    monkeypatch.setattr(h5.random, "uniform", lambda low, high: high)
    client = _RecordingClient(status=502)

    status = asyncio.run(
        h5._post_task_control_request(
            client,
            "https://bhzn.top/api/scheduled-tasks/runs/run3/event",
            {"type": "event:heartbeat"},
            {},
            label="event:heartbeat",
            run_id="run3",
        )
    )

    assert status == 502
    assert sleeps and sleeps[0] == 0.25 * 1.4, sleeps
    _clear_outbox()


async def _done():
    return None
