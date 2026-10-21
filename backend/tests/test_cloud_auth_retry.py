# -*- coding: utf-8 -*-
"""云端 401 统一续签+重试（2026-10-21）。"""
from __future__ import annotations

import asyncio

from backend.app.services import cloud_auth_retry


class _Resp:
    def __init__(self, status):
        self.status_code = status


class _Client:
    def __init__(self):
        self.calls = []

    async def request(self, method, url, headers=None, **kwargs):
        self.calls.append(dict(headers or {}))
        return _Resp(401 if len(self.calls) == 1 else 200)


def test_401_refreshes_and_retries(monkeypatch):
    async def fake_refresh(token, *, reason=""):
        assert token == "old"
        return "new"

    monkeypatch.setattr(cloud_auth_retry, "refresh_token_after_401", fake_refresh)
    client = _Client()
    resp = asyncio.run(
        cloud_auth_retry.request_with_auth_retry(
            client, "GET", "https://cloud.test/x", headers={"Authorization": "Bearer old"}
        )
    )
    assert resp.status_code == 200
    assert len(client.calls) == 2
    assert client.calls[0]["Authorization"] == "Bearer old"
    assert client.calls[1]["Authorization"] == "Bearer new"


def test_refresh_failure_returns_original_401(monkeypatch):
    async def fake_refresh(token, *, reason=""):
        return ""

    monkeypatch.setattr(cloud_auth_retry, "refresh_token_after_401", fake_refresh)
    client = _Client()
    resp = asyncio.run(
        cloud_auth_retry.request_with_auth_retry(
            client, "POST", "https://cloud.test/y", headers={"Authorization": "Bearer old"}
        )
    )
    assert resp.status_code == 401
    assert len(client.calls) == 1


def test_non_401_not_retried(monkeypatch):
    called = []

    async def fake_refresh(token, *, reason=""):
        called.append(token)
        return "new"

    monkeypatch.setattr(cloud_auth_retry, "refresh_token_after_401", fake_refresh)

    class OkClient:
        def __init__(self):
            self.calls = 0

        async def request(self, *a, **k):
            self.calls += 1
            return _Resp(200)

    client = OkClient()
    resp = asyncio.run(
        cloud_auth_retry.request_with_auth_retry(
            client, "GET", "https://cloud.test/z", headers={"Authorization": "Bearer old"}
        )
    )
    assert resp.status_code == 200 and client.calls == 1 and not called