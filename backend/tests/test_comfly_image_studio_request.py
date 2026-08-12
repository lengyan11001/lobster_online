import pytest
from starlette.requests import Request

from backend.app.api import comfly_image_studio as image_studio


class _FakeResponse:
    status_code = 200
    text = '{"data":[{"url":"https://example.com/out.png"}]}'
    content = text.encode("utf-8")

    def json(self):
        return {"data": [{"url": "https://example.com/out.png"}]}


class _FakeStatusResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)
        self.content = self.text.encode("utf-8")

    def json(self):
        return self._payload


def _request(headers=None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/comfly-image-studio/generate/start",
            "headers": [(str(k).lower().encode("latin-1"), str(v).encode("latin-1")) for k, v in (headers or {}).items()],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
        }
    )


@pytest.mark.asyncio
async def test_multipart_image_edit_keeps_url_response_format(monkeypatch):
    captured = {}

    class _FakeAsyncClient:
        def __init__(self, *, timeout=None, **_kwargs):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers=None, data=None, files=None, json=None):
            captured["url"] = url
            captured["headers"] = dict(headers or {})
            captured["data"] = dict(data or {})
            captured["files"] = list(files or [])
            captured["json"] = json
            return _FakeResponse()

    monkeypatch.setattr(image_studio.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(
        image_studio,
        "_resolve_comfly_credentials",
        lambda _user_id, _db, _request: ("https://bhzn.top/api/comfly-proxy", "jwt"),
    )

    async def _fake_save_image_studio_results(**_kwargs):
        return []

    monkeypatch.setattr(image_studio, "_save_image_studio_results", _fake_save_image_studio_results)

    result = await image_studio._generate_image_studio_core(
        request=_request(),
        current_user=type("User", (), {"id": 69})(),
        db=None,
        prompt="把参考图里的产品换成洗发水",
        model="gpt-image-2",
        aspect_ratio="9:16",
        quality="high",
        background="auto",
        upload_payloads=[
            {
                "filename": "reference.png",
                "content_type": "image/png",
                "bytes": b"fake-image-bytes",
            }
        ],
        timeout_seconds=360,
    )

    assert result["ok"] is True
    assert captured["url"].endswith("/api/comfly-proxy/v1/images/edits")
    assert captured["json"] is None
    assert captured["data"]["response_format"] == "url"
    assert captured["data"]["size"] == "1080x1920"
    assert captured["timeout"] == 360.0


@pytest.mark.asyncio
async def test_multipart_image_edit_sends_client_request_id(monkeypatch):
    captured = {}

    class _FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers=None, data=None, files=None, json=None):
            captured["data"] = dict(data or {})
            return _FakeResponse()

    monkeypatch.setattr(image_studio.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(
        image_studio,
        "_resolve_comfly_credentials",
        lambda _user_id, _db, _request: ("https://bhzn.top/api/comfly-proxy", "jwt"),
    )

    async def _fake_save_image_studio_results(**_kwargs):
        return []

    monkeypatch.setattr(image_studio, "_save_image_studio_results", _fake_save_image_studio_results)

    result = await image_studio._generate_image_studio_core(
        request=_request({"X-Client-Request-Id": "abc123"}),
        current_user=type("User", (), {"id": 69})(),
        db=None,
        prompt="生成产品图",
        model="gpt-image-2",
        aspect_ratio="9:16",
        quality="high",
        background="auto",
        upload_payloads=[
            {
                "filename": "reference.png",
                "content_type": "image/png",
                "bytes": b"fake-image-bytes",
            }
        ],
        timeout_seconds=360,
    )

    assert result["ok"] is True
    assert captured["data"]["client_request_id"] == "abc123"


@pytest.mark.asyncio
async def test_multipart_image_edit_retries_pending_duplicate(monkeypatch):
    captured = {"calls": 0}

    class _FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers=None, data=None, files=None, json=None):
            captured["calls"] += 1
            if captured["calls"] == 1:
                return _FakeStatusResponse(409, {"detail": "同一个图片任务仍在生成中，请稍后刷新结果，不会重复扣费"})
            return _FakeResponse()

    async def _no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(image_studio.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(image_studio.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(
        image_studio,
        "_resolve_comfly_credentials",
        lambda _user_id, _db, _request: ("https://bhzn.top/api/comfly-proxy", "jwt"),
    )

    async def _fake_save_image_studio_results(**_kwargs):
        return []

    monkeypatch.setattr(image_studio, "_save_image_studio_results", _fake_save_image_studio_results)

    result = await image_studio._generate_image_studio_core(
        request=_request({"X-Client-Request-Id": "abc123"}),
        current_user=type("User", (), {"id": 69})(),
        db=None,
        prompt="生成产品图",
        model="gpt-image-2",
        aspect_ratio="9:16",
        quality="high",
        background="auto",
        upload_payloads=[
            {
                "filename": "reference.png",
                "content_type": "image/png",
                "bytes": b"fake-image-bytes",
            }
        ],
        timeout_seconds=360,
    )

    assert result["ok"] is True
    assert captured["calls"] == 2


@pytest.mark.asyncio
async def test_image_generation_retries_transient_disconnect_and_ignores_system_proxy(monkeypatch):
    captured = {"calls": 0, "trust_env": []}

    class _FakeAsyncClient:
        def __init__(self, *, timeout=None, trust_env=None, **_kwargs):
            captured["timeout"] = timeout
            captured["trust_env"].append(trust_env)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers=None, data=None, files=None, json=None):
            captured["calls"] += 1
            if captured["calls"] == 1:
                raise image_studio.httpx.RemoteProtocolError("Server disconnected without sending a response.")
            captured["url"] = url
            captured["json"] = dict(json or {})
            return _FakeResponse()

    async def _no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(image_studio.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(image_studio.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(
        image_studio,
        "_resolve_comfly_credentials",
        lambda _user_id, _db, _request: ("https://bhzn.top/api/comfly-proxy", "jwt"),
    )

    async def _fake_save_image_studio_results(**_kwargs):
        return []

    monkeypatch.setattr(image_studio, "_save_image_studio_results", _fake_save_image_studio_results)

    result = await image_studio._generate_image_studio_core(
        request=_request(),
        current_user=type("User", (), {"id": 70})(),
        db=None,
        prompt="生成一张干净的产品图",
        model="gpt-image-2",
        aspect_ratio="1:1",
        quality="high",
        background="auto",
        upload_payloads=[],
    )

    assert result["ok"] is True
    assert captured["calls"] == 2
    assert captured["trust_env"] == [False, False]
