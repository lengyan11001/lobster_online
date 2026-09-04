from __future__ import annotations

import asyncio

import httpx
import pytest
from starlette.requests import Request

from backend.app.api import auth
from backend.app.api import h5_chat_channel as channel


@pytest.mark.asyncio
async def test_task_event_uses_bounded_retry_after_connect_error():
    class FakeResponse:
        status_code = 200

    class FakeClient:
        def __init__(self):
            self.calls = []

        async def post(self, url, **kwargs):
            self.calls.append((url, kwargs.get("timeout")))
            if len(self.calls) == 1:
                raise httpx.ConnectTimeout("temporary DNS/connectivity failure")
            return FakeResponse()

    client = FakeClient()
    status = await channel._post_task_event(
        client,
        "https://cloud.test",
        {"Authorization": "Bearer test"},
        "run-id",
        "heartbeat",
        {"heartbeat": True},
    )

    assert status == 200
    assert len(client.calls) == 2
    timeout = client.calls[0][1]
    assert timeout is not None
    assert timeout.read == channel._SCHEDULED_TASK_EVENT_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_failed_completion_is_retried_without_raising(monkeypatch):
    calls = []

    async def post_control(*_args, **kwargs):
        calls.append(kwargs.get("label"))
        return 503 if kwargs.get("label") == "completion" else 200

    monkeypatch.setattr(channel, "_post_task_control_request", post_control)
    monkeypatch.setattr(channel, "_SCHEDULED_TASK_COMPLETION_RETRY_SECONDS", 0.5)
    monkeypatch.setattr(channel, "_SCHEDULED_TASK_COMPLETION_RETRY_DELAY_SECONDS", 0.01)

    await channel._complete_task_run(
        object(),
        "https://cloud.test",
        {"Authorization": "Bearer test"},
        "run-id-resilient",
        result_text="done",
    )
    await asyncio.sleep(0.08)

    assert calls[0] == "completion"
    assert "completion-retry" in calls
    assert "run-id-resilient" not in channel._pending_task_completion_run_ids


@pytest.mark.asyncio
async def test_auth_uses_recent_cache_during_network_outage(monkeypatch):
    token = "header.payload.signature"
    installation_id = "u216-test"
    brand_mark = "bihuo"
    cache_key = channel.hashlib.sha256(
        f"{token}\0{installation_id}\0{brand_mark}".encode("utf-8")
    ).hexdigest()
    now = auth.time.monotonic()
    auth._AUTH_ME_CACHE[cache_key] = (now - 1.0, 216)

    class OfflineClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, *_args, **_kwargs):
            raise httpx.ConnectError("auth server offline")

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/native-wechat/auto-reply/run-once",
        "headers": [(b"authorization", f"Bearer {token}".encode()), (b"x-installation-id", installation_id.encode())],
        "query_string": b"",
        "server": ("127.0.0.1", 8000),
        "client": ("127.0.0.1", 12345),
        "scheme": "http",
    }
    monkeypatch.setattr(auth.httpx, "AsyncClient", lambda **_kwargs: OfflineClient())
    monkeypatch.setattr(auth.settings, "auth_server_base", "https://cloud.test")
    monkeypatch.setattr(auth.settings, "auth_me_cache_ttl_seconds", 120)
    monkeypatch.setattr(auth.settings, "auth_me_stale_cache_grace_seconds", 900)

    try:
        user = await auth.get_current_user_for_local(Request(scope), token=token)
    finally:
        auth._AUTH_ME_CACHE.pop(cache_key, None)

    assert user.id == 216
