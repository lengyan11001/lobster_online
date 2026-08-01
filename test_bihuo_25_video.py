import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.app.api import bihuo_25_video
from backend.app.api.bihuo_25_video import (
    Bihuo25StartBody,
    _build_generate_payload,
    _call_gateway,
    _gateway_content_text,
    _public_gateway_error,
)


def test_first_last_frame_keeps_five_second_duration():
    payload = _build_generate_payload(
        Bihuo25StartBody(
            mode="first_last",
            prompt="镜头从首帧自然过渡到尾帧",
            duration=5,
            resolution="720p",
            first_image_url="https://example.com/first.png",
            last_image_url="https://example.com/last.png",
        )
    )

    assert payload["functionMode"] == "first_last_frame"
    assert payload["duration"] == 5
    assert payload["resolution"] == "720p"


def test_gateway_content_text_reads_mcp_text_blocks():
    result = {
        "content": [
            {"type": "text", "text": "第一段"},
            {"type": "image", "data": "ignored"},
            {"type": "text", "text": "第二段"},
        ],
        "isError": True,
    }

    assert _gateway_content_text(result) == "第一段\n第二段"


def test_public_gateway_error_exposes_actionable_credit_shortage():
    raw = (
        "当前账户积分不足，无法调用该能力。"
        "（积分不足：按速推定价表本次预估至少 2600.0000 积分，"
        "当前余额 1221.2401。请充值后重试。）"
    )

    status_code, detail = _public_gateway_error(raw)

    assert status_code == 402
    assert detail == (
        "当前账户积分不足，本次预计需要 2600.0000 积分，"
        "当前余额 1221.2401 积分，请充值后重试"
    )
    assert "速推" not in detail


def test_public_gateway_error_hides_provider_details():
    status_code, detail = _public_gateway_error(
        "上游 apiz exception: https://api.apiz.ai/internal"
    )

    assert status_code == 502
    assert detail == "视频生成服务暂时不可用，请稍后重试"


def test_call_gateway_raises_for_mcp_tool_error(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "当前账户积分不足，无法调用该能力。"
                                "（积分不足：本次预估至少 2600 积分，当前余额 1221.2401。）"
                            ),
                        }
                    ],
                    "isError": True,
                }
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(bihuo_25_video.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    request = SimpleNamespace(
        headers={"Authorization": "Bearer test-token", "X-Installation-Id": "test-install"}
    )
    user = SimpleNamespace(id=31)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            _call_gateway(
                request,
                user,
                "video.generate",
                {"model": "hidden-provider-model"},
                timeout_seconds=10,
            )
        )

    assert exc_info.value.status_code == 402
    assert exc_info.value.detail == (
        "当前账户积分不足，本次预计需要 2600 积分，"
        "当前余额 1221.2401 积分，请充值后重试"
    )
