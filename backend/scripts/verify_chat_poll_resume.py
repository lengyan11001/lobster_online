#!/usr/bin/env python3
"""自测：直连图生任务在自动 task.get_result 轮询前应发出带 task_id 的 task_poll / tool_start。
运行（在 lobster 仓库根目录）:
  python backend/scripts/verify_chat_poll_resume.py
失败则 exit 1。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


async def _test_after_generate_emits_task_poll() -> None:
    from backend.app.api import chat as M

    events: list[dict] = []

    async def cb(ev: dict) -> None:
        events.append(dict(ev))

    n_tr = {"c": 0}

    real_exec = M._exec_tool

    async def fake_exec_tool(name, args, token="", sutui_token=None, progress_cb=None, request=None, db=None, user_id=None):
        cap = (args.get("capability_id") or "").strip() if name == "invoke_capability" else ""
        if cap == "task.get_result":
            n_tr["c"] += 1
            if n_tr["c"] == 1:
                return json.dumps({"status": "processing"})
            return json.dumps({"status": "success", "saved_assets": []})
        return "{}"

    M._exec_tool = fake_exec_tool  # type: ignore[assignment]
    orig_sleep = M.asyncio.sleep

    async def no_sleep(_t: float = 0) -> None:
        return None

    M.asyncio.sleep = no_sleep  # type: ignore[assignment]
    try:
        gen_res = json.dumps({"task_id": "sutui_img_task_test_001", "status": "queued"})
        invoke_args = {"capability_id": "image.generate", "payload": {"prompt": "小猫"}}
        await M._after_generate_auto_task_result(
            invoke_args, gen_res, "token", None, cb, None
        )
    finally:
        M.asyncio.sleep = orig_sleep
        M._exec_tool = real_exec  # type: ignore[assignment]

    polls = [e for e in events if e.get("type") == "task_poll"]
    assert polls, f"expected task_poll events, got types={[e.get('type') for e in events]}"
    assert any(
        e.get("task_id") == "sutui_img_task_test_001" for e in polls
    ), polls
    # 注：此处 mock 掉整段 _exec_tool，不会收到真实 tool_start；tool_start 见下一测


async def _test_tool_start_has_task_id_via_mock_mcp() -> None:
    from backend.app.api import chat as M

    events: list[dict] = []

    async def cb(ev: dict) -> None:
        events.append(dict(ev))

    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "jsonrpc": "2.0",
                "result": {
                    "content": [{"type": "text", "text": '{"status":"success"}'}],
                    "isError": False,
                },
            }

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return FakeResp()

    real_client = M.httpx.AsyncClient
    M.httpx.AsyncClient = FakeClient  # type: ignore[misc]
    try:
        args = M._normalize_invoke_task_get_result_args(
            {
                "capability_id": "task.get_result",
                "payload": {"task_id": "tid_mcp_mock_abc"},
            }
        )
        await M._exec_tool(
            "invoke_capability", args, token="t", progress_cb=cb, request=None
        )
    finally:
        M.httpx.AsyncClient = real_client

    tss = [e for e in events if e.get("type") == "tool_start"]
    assert tss, events
    assert tss[0].get("task_id") == "tid_mcp_mock_abc", tss[0]


async def main() -> int:
    await _test_after_generate_emits_task_poll()
    await _test_tool_start_has_task_id_via_mock_mcp()
    print("verify_chat_poll_resume: OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except AssertionError as e:
        print("verify_chat_poll_resume: FAIL", e, file=sys.stderr)
        raise SystemExit(1)
