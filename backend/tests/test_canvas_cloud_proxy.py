"""画布本机代理：未登录 401、登录后带 token 转发、上游状态码原样透传。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import canvas_cloud_proxy


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(canvas_cloud_proxy.router)
    return TestClient(app)


def test_without_login_returns_401(monkeypatch, client):
    monkeypatch.setattr(canvas_cloud_proxy, "auth_context", lambda: ("", ""))
    resp = client.get("/canvas-api/api/v3/mcp/models")
    assert resp.status_code == 401
    assert "登录" in resp.json()["detail"]


def test_forwards_with_cloud_token(monkeypatch, client):
    monkeypatch.setattr(canvas_cloud_proxy, "auth_context", lambda: ("jwt-token", "inst-1"))
    monkeypatch.setattr(canvas_cloud_proxy, "cloud_base", lambda: "https://bhzn.top")
    seen = {}

    class FakeResponse:
        status_code = 402
        content = '{"detail": "算力不足"}'.encode("utf-8")
        headers = {"content-type": "application/json"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, content=None, headers=None, params=None):
            seen.update({"method": method, "url": url, "content": content, "headers": headers, "params": params})
            return FakeResponse()

    monkeypatch.setattr(canvas_cloud_proxy.httpx, "AsyncClient", FakeClient)

    resp = client.post(
        "/canvas-api/api/v3/tasks/create?lang=zh-CN",
        json={"model": "veo3"},
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 402
    assert "算力不足" in resp.text
    assert seen["method"] == "POST"
    assert seen["url"] == "https://bhzn.top/canvas-api/api/v3/tasks/create"
    assert seen["headers"]["Authorization"] == "Bearer jwt-token"
    assert seen["headers"]["X-Installation-Id"] == "inst-1"
    assert seen["headers"]["content-type"] == "application/json"
    assert seen["params"] == {"lang": "zh-CN"}
    assert b'"model"' in seen["content"]


def test_missing_server_base_is_503(monkeypatch, client):
    monkeypatch.setattr(canvas_cloud_proxy, "auth_context", lambda: ("jwt-token", "inst-1"))
    monkeypatch.setattr(canvas_cloud_proxy, "cloud_base", canvas_cloud_proxy.cloud_base)
    monkeypatch.setattr(canvas_cloud_proxy.settings, "auth_server_base", "", raising=False)
    resp = client.get("/canvas-api/api/v3/mcp/models")
    assert resp.status_code == 503
