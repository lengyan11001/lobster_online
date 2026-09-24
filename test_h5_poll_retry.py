"""状态轮询遇到连接中断时要继续重试，而不是把整单判失败。"""

import asyncio

import httpx
import pytest

from backend.app.api import h5_chat_channel as channel


class _FastAsyncio:
    """只让本模块的 sleep 立即返回，避免测试真的等轮询间隔。"""

    def __getattr__(self, name):
        return getattr(asyncio, name)

    async def sleep(self, *_args, **_kwargs):
        return None


def test_transient_poll_error_only_matches_connection_interruptions():
    assert channel._is_transient_poll_error(httpx.ConnectTimeout("connect timeout")) is True
    assert channel._is_transient_poll_error(httpx.ConnectError("connect error")) is True
    assert (
        channel._is_transient_poll_error(
            RuntimeError("本机微信接管服务不可达（127.0.0.1:8000），无法访问 /api/x")
        )
        is True
    )
    assert channel._is_transient_poll_error(RuntimeError("图片生成失败：内容审核不通过")) is False
    assert channel._is_transient_poll_error(RuntimeError("cloud api connection missing")) is False


@pytest.mark.asyncio
async def test_image_studio_poll_retries_after_connection_drop(monkeypatch):
    attempts = {"count": 0}

    async def post_form(_path, _fields, **_kwargs):
        return {"ok": True, "job_id": "retry-job"}

    async def get_job(_path, **_kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ConnectTimeout("云端查询连接超时")
        return {
            "ok": True,
            "status": "completed",
            "images": [{"asset_id": "a1", "url": "https://example.com/a1.png"}],
        }

    monkeypatch.setattr(channel, "asyncio", _FastAsyncio())
    monkeypatch.setattr(channel, "_post_local_api_form", post_form)
    monkeypatch.setattr(channel, "_get_local_api_json", get_job)

    result = await channel._run_client_workflow_action(
        "image_studio_generate",
        {"prompt": "一张自然光产品图", "poll_timeout_seconds": 60},
        headers={},
        run_id="run-poll-retry",
    )

    assert attempts["count"] == 2
    assert result["result_refs"]["asset_ids"] == ["a1"]


@pytest.mark.asyncio
async def test_local_wechat_task_poll_retries_after_service_hickup(monkeypatch):
    attempts = {"count": 0}

    async def get_local(_path, **_kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError(
                "本机微信接管服务不可达（127.0.0.1:8000），无法访问 /api/native-wechat/tasks/t1"
            )
        return {"ok": True, "task": {"id": "t1", "status": "success"}}

    monkeypatch.setattr(channel, "asyncio", _FastAsyncio())
    monkeypatch.setattr(channel, "_get_local_api_json", get_local)

    result = await channel._wait_for_local_native_wechat_task(
        {"ok": True, "status": "running", "task": {"id": "t1", "status": "running"}},
        headers={},
        timeout_seconds=60.0,
    )

    assert attempts["count"] == 2
    assert result["queued"] is False
    assert result["task"]["status"] == "success"


@pytest.mark.asyncio
async def test_parent_workflow_wait_retries_after_connection_drop(monkeypatch):
    attempts = {"count": 0}

    async def resolve_parent(*_args, **_kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ConnectTimeout("等待上级节点连接超时")
        return [{"result_payload": {}}]

    monkeypatch.setattr(channel, "asyncio", _FastAsyncio())
    monkeypatch.setattr(channel, "_resolve_parent_workflow_results", resolve_parent)

    result = await channel._run_native_wechat_group_invite_followup(
        {"parent_wait_seconds": 60, "parent_poll_seconds": 15},
        account_id="acct-1",
        headers={},
        cloud=object(),
        base="https://bhzn.top",
        current_item=None,
    )

    assert attempts["count"] == 2
    assert result["skipped"] is True
    assert result["reason"] == "no_match"
