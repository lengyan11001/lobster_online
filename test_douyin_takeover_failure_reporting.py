"""抖音私信接管：每轮"没能开始"时必须记录原因、立刻收工、并把原因写进任务结果。

2026-10-12 用户反馈"私信接管都是错误的"：实际是每轮都被 400 早退（缺记忆文件等），
而循环只打印「会话 0 个」、最后还报「正常收工」，原因全被吞掉，无法排查。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _stub_douyin_api(monkeypatch, *, result, calls):
    """把 douyin_api 换成桩：接管分支里是 `from douyin_api import ...`（函数内导入）。"""
    async def _fake_once(**kwargs):
        calls.append(kwargs)
        return result

    stub = types.ModuleType("douyin_api")
    stub.run_douyin_h5_stranger_message_task_once = _fake_once
    stub.douyin_stranger_message_stop_requested = False
    monkeypatch.setitem(sys.modules, "douyin_api", stub)
    return stub


@pytest.mark.asyncio
async def test_takeover_stops_immediately_and_reports_reason(monkeypatch, caplog):
    from backend.app.api import h5_chat_channel as mod

    calls: list = []
    _stub_douyin_api(monkeypatch, calls=calls, result={
        "status": "failed",
        "code": 400,
        "message": "抖音私信记忆接管缺少记忆文件：请先在 Online「抖音获客 → 私信引流」里选 1 份记忆文件（例如「百问百答」）。",
    })
    monkeypatch.setattr(mod, "_install_douyin_origin_import_path", lambda: None)

    with caplog.at_level("INFO"):
        res = await mod._run_scheduled_douyin_sales_action(
            "stranger_message",
            {"reply_mode": "ai_memory", "auto_reply_enabled": True,
             "message_poll_interval_seconds": 15, "takeover_session_minutes": 30,
             "wechat_add_friend_enabled": True},
        )

    assert len(calls) == 1, "配置类错误不该空转到节点结束（原来会跑 120 轮）"
    assert res["status"] == "failed"
    assert res["takeover_rounds"] == 1
    assert res["takeover_stop_reason"] == "config_error"
    assert "缺少记忆文件" in res["message"]
    assert "没能开始" in res["message"]
    assert res["takeover_error"].startswith("抖音私信记忆接管缺少记忆文件")
    assert int(res.get("code") or 0) == 400
    assert "第 1 轮没能开始" in caplog.text and "缺少记忆文件" in caplog.text, caplog.text


@pytest.mark.asyncio
async def test_takeover_account_not_logged_in_stops_and_reports(monkeypatch):
    from backend.app.api import h5_chat_channel as mod

    calls: list = []
    _stub_douyin_api(monkeypatch, calls=calls, result={
        "status": "skipped", "code": 400, "reason": "account_waiting_login",
        "message": "账号 1 还没有完成登录，请先检查或登录。",
    })
    monkeypatch.setattr(mod, "_install_douyin_origin_import_path", lambda: None)

    res = await mod._run_scheduled_douyin_sales_action(
        "stranger_message",
        {"reply_mode": "fixed", "message": "你好", "auto_reply_enabled": True,
         "message_poll_interval_seconds": 15, "takeover_session_minutes": 30,
         "wechat_add_friend_enabled": True},
    )
    assert len(calls) == 1
    assert res["status"] == "failed" and res["takeover_stop_reason"] == "account_waiting_login"
    assert "还没有完成登录" in res["message"]


@pytest.mark.asyncio
async def test_takeover_zero_sessions_but_healthy_says_so(monkeypatch):
    """账号正常、只是这段时间没有新私信：文案要说清"没有需要接管的会话"，并且不算失败。"""
    from backend.app.api import h5_chat_channel as mod

    calls: list = []
    _stub_douyin_api(monkeypatch, calls=calls, result={
        "status": "success", "code": 200,
        "stats": {"total": 0}, "reply": {"total": 0, "success": 0, "failed": 0},
    })
    monkeypatch.setattr(mod, "_install_douyin_origin_import_path", lambda: None)
    clock = {"t": 0.0}

    def _fast_clock():
        # 每次调用往前跳 3000s：第一轮结束的判断就会命中"节点时间到"，测试里别真等 30 分钟
        value = clock["t"]
        clock["t"] += 3000.0
        return value

    monkeypatch.setattr(mod, "_takeover_monotonic", _fast_clock)

    res = await mod._run_scheduled_douyin_sales_action(
        "stranger_message",
        {"reply_mode": "fixed", "message": "你好", "auto_reply_enabled": True,
         "message_poll_interval_seconds": 15, "takeover_session_minutes": 30,
         "wechat_add_friend_enabled": True},
    )
    assert res["takeover_rounds"] == 1
    assert "没有需要接管的私信会话" in res["message"]
    assert res.get("status") != "failed"
    assert "takeover_error" not in res
