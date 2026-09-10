import asyncio

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app.api import auth


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/test",
            "headers": [
                (b"authorization", b"Bearer expired-token"),
                (b"x-installation-id", b"test-installation"),
                (b"x-lobster-brand", b"bihuo"),
            ],
        }
    )


def test_explicit_401_is_cached_briefly(monkeypatch):
    calls = 0

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            return httpx.Response(401, request=httpx.Request("GET", "https://example.test/auth/me"))

    monkeypatch.setattr(auth.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(auth.settings, "auth_server_base", "https://example.test")
    monkeypatch.setattr(auth.settings, "auth_me_cache_ttl_seconds", 120)
    auth._AUTH_ME_CACHE.clear()
    auth._AUTH_ME_INVALID_CACHE.clear()

    async def run():
        for _ in range(2):
            with pytest.raises(HTTPException) as exc_info:
                await auth.get_current_user_for_local(_request(), token="expired-token")
            assert exc_info.value.status_code == 401

    asyncio.run(run())
    assert calls == 1


def test_concurrent_success_checks_share_one_remote_request(monkeypatch):
    calls = 0

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return httpx.Response(
                200,
                json={"id": 123},
                request=httpx.Request("GET", "https://example.test/auth/me"),
            )

    monkeypatch.setattr(auth.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(auth.settings, "auth_server_base", "https://example.test")
    monkeypatch.setattr(auth.settings, "auth_me_cache_ttl_seconds", 300)
    monkeypatch.setattr(auth, "persist_channel_fallback_for_login", lambda **kwargs: None)
    auth._AUTH_ME_CACHE.clear()
    auth._AUTH_ME_INVALID_CACHE.clear()
    auth._AUTH_ME_VALIDATION_LOCKS.clear()

    async def run():
        users = await asyncio.gather(
            *(auth.get_current_user_for_local(_request(), token="valid-token") for _ in range(20))
        )
        assert [user.id for user in users] == [123] * 20

    asyncio.run(run())
    assert calls == 1
