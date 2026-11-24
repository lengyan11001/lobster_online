import asyncio

import pytest

from backend.app.api import h5_chat_channel as channel


class _FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        yield "event: wake"
        yield 'data: {"id": 1, "kind": "scheduled_task"}'
        await asyncio.sleep(3600)


class _FakeStream:
    async def __aenter__(self):
        return _FakeResponse()

    async def __aexit__(self, *args):
        return False


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, *args, **kwargs):
        return _FakeStream()


def test_wake_stream_sets_event(monkeypatch):
    monkeypatch.setattr(channel.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(channel, "_cloud_base", lambda: "https://example.test")
    monkeypatch.setattr(channel, "_auth_context", lambda: ("token", "dev-a"))
    monkeypatch.setattr(channel, "_headers", lambda token, iid: {"Authorization": f"Bearer {token}"})

    async def run():
        event = asyncio.Event()
        task = asyncio.create_task(channel._scheduled_task_wake_stream(event))
        try:
            await asyncio.wait_for(event.wait(), timeout=2.0)
            assert event.is_set()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())
