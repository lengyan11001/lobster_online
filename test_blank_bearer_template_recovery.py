from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app.api.auth import get_current_user_for_local


ROOT = Path(__file__).resolve().parent


@pytest.mark.asyncio
async def test_local_auth_rejects_an_empty_bearer_before_contacting_auth_server():
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/local-test",
            "headers": [(b"x-installation-id", b"test-installation")],
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_for_local(request, token="")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "登录状态已失效，请重新登录"


def test_personal_settings_keeps_template_failure_visible():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")

    assert "templateLoadError: ''" in script
    assert "state.templateLoadError = err && err.message ? err.message : '模板加载失败';" in script
    assert "IP 人设模板加载失败：" in script


def test_shared_auth_headers_omit_blank_bearer_values():
    script = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "var h = { 'Content-Type': 'application/json' };" in script
    assert "if (token) h.Authorization = 'Bearer ' + token;" in script
    assert "'Authorization': 'Bearer ' + (token || '')" not in script


def test_task_center_does_not_poll_without_an_authenticated_session():
    script = (ROOT / "static" / "js" / "task-center.js").read_text(encoding="utf-8")

    assert "function hasAuthToken()" in script
    assert "if (!hasAuthToken())" in script
