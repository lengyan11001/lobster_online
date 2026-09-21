from __future__ import annotations

import asyncio
import re
import time

from pathlib import Path

import pytest

from backend.app.services import native_whatsapp_engine as engine


@pytest.fixture()
def isolated_whatsapp_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(engine, "STATE_DIR", tmp_path)
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(engine, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(engine, "LOG_PATH", tmp_path / "native_whatsapp.jsonl")
    engine._release_action()
    yield
    engine._release_action()
    for registry in (engine._FRIEND_ADD_SCHEDULERS, engine._AUTO_REPLY_LOOPS):
        for task in list(registry.values()):
            try:
                task.cancel()
            except RuntimeError:  # 事件循环已关闭（TestClient 退出后）
                pass
        registry.clear()
    engine._FRIEND_ADD_WAKE_EVENTS.clear()


def test_session_snapshot_persists_messages_and_filters(isolated_whatsapp_state):
    peer = engine._persist_session_snapshot(
        {
            "peer_name": "NplusTech",
            "is_group": False,
            "messages": [
                {"text": "Hello", "direction": "inbound"},
                {"text": "Hi, how can I help?", "direction": "outbound"},
            ],
            "last_message": {"text": "Hi, how can I help?", "direction": "outbound"},
        }
    )

    sessions = engine.list_sessions(limit=20, offset=0, keyword="Nplus")
    assert sessions["total"] == 1
    assert sessions["items"][0]["last_direction"] == "outbound"

    messages = engine.list_messages(peer["peer_key"], limit=20, offset=0)
    assert messages["total"] == 2
    assert [item["direction"] for item in messages["items"]] == ["inbound", "outbound"]


def test_contacts_and_operations_are_searchable(isolated_whatsapp_state):
    engine._persist_contact(
        {
            "display_name": "Alice Chen",
            "first_name": "Alice",
            "last_name": "Chen",
            "username": "alice_wa",
            "phone": "+8613800138000",
        }
    )
    engine._record_operation("add_contact", "alice_wa", "success", "saved")

    contacts = engine.list_contacts(limit=20, offset=0, keyword="alice_wa")
    records = engine.list_operations(limit=20, offset=0, keyword="add_contact")

    assert contacts["total"] == 1
    assert contacts["items"][0]["phone"] == "+8613800138000"
    assert records["total"] == 1
    assert records["items"][0]["status"] == "success"


def test_desktop_actions_share_one_lock(isolated_whatsapp_state):
    engine._claim_action("sync")
    with pytest.raises(RuntimeError, match="sync正在执行"):
        engine._claim_action("send")
    engine._release_action()
    engine._claim_action("send")
    engine._release_action()


# ── 批量加好友队列 / 常驻接管（对齐微信协议助手，2026-09-21）─────────────────


def test_batch_targets_are_parsed_and_deduped(isolated_whatsapp_state):
    targets = engine.normalize_friend_targets(
        [
            "张三,13800138000",
            "+8613800138001",
            "@alice_wa",
            "13800138000",          # 与第一行重复（同号码）
            "李四 13800138002",
        ]
    )
    assert [item["phone"] for item in targets if item["phone"]] == ["13800138000", "13800138001", "13800138002"]
    assert targets[0]["first_name"] == "张三"
    assert targets[0]["country_code"] == "+86"
    assert any(item["username"] == "alice_wa" for item in targets)
    assert engine._target_label({"country_code": "+86", "phone": "13800138000"}) == "+8613800138000"


def test_friend_add_queue_enqueues_and_lists_records(isolated_whatsapp_state):
    task = engine.create_add_contact_task(["13800138000", "李四,13800138001"], apply_message="你好", queue_only=True)
    assert task["queued_total"] == 2
    assert task["status"] == "queued"

    records = engine.list_friend_records(limit=10, offset=0)
    assert records["count"] == 2
    assert {item["status"] for item in records["items"]} == {"queued"}

    summary = engine.friend_add_queue_summary()
    assert summary["queued"] == 2
    assert summary["success"] == 0


def test_friend_add_task_runs_with_mocked_desktop(isolated_whatsapp_state, monkeypatch):
    calls = {"add": [], "send": []}

    def fake_add_contact(**kwargs):
        calls["add"].append(kwargs)
        return {"ok": True}

    def fake_send_message(target, content):
        calls["send"].append((target, content))
        return {"ok": True, "sent": True}

    monkeypatch.setattr(engine, "add_contact", fake_add_contact)
    monkeypatch.setattr(engine, "send_message", fake_send_message)

    engine.create_add_contact_task(["王五,13800138003"], apply_message="您好，我是小王")
    claimed = engine._claim_next_queued_task(engine.DEFAULT_ACCOUNT_ID)
    outcome = asyncio.run(engine._process_add_contact_task(claimed))

    assert outcome["status"] == "success"
    assert calls["add"][0]["phone"] == "13800138003"
    assert calls["add"][0]["first_name"] == "王五"
    assert calls["send"][0] == ("+8613800138003", "您好，我是小王")

    records = engine.list_friend_records(limit=5, offset=0)
    assert records["items"][0]["status"] == "success"
    assert records["items"][0]["success"] == 1
    assert engine.friend_add_queue_summary()["today_success"] == 1


def test_friend_add_daily_limit_defers_task(isolated_whatsapp_state, monkeypatch):
    monkeypatch.setattr(engine, "add_contact", lambda **kwargs: {"ok": True})
    engine.save_friend_add_control(interval_seconds=5, daily_limit=1)
    engine.create_add_contact_task(["13800138004", "13800138005"])

    first = engine._claim_next_queued_task(engine.DEFAULT_ACCOUNT_ID)
    outcome = asyncio.run(engine._process_add_contact_task(first))
    assert outcome["status"] == "success"

    second = engine._claim_next_queued_task(engine.DEFAULT_ACCOUNT_ID)
    outcome2 = asyncio.run(engine._process_add_contact_task(second))
    assert outcome2.get("deferred") is True
    refreshed = engine._task_row_by_id(second["id"])
    assert refreshed["status"] == "queued"      # 退回队列等额度重置，而不是失败
    assert refreshed["payload"]["pending_targets"]


def test_friend_add_queue_start_and_stop(isolated_whatsapp_state):
    async def scenario():
        started = await engine.start_friend_add_queue()
        assert started["enabled"] is True
        stopped = await engine.stop_friend_add_queue()
        assert stopped["enabled"] is False
        control = engine.get_friend_add_control()
        assert control["interval_seconds"] >= 1
        return control

    control = asyncio.run(scenario())
    assert control["enabled"] is False


def test_auto_reply_loop_and_diagnostics(isolated_whatsapp_state, monkeypatch):
    rounds = {"n": 0}

    async def fake_run_once(*, auth_context=None, config_override=None):
        rounds["n"] += 1
        return {"ok": True, "summary_text": "本轮无未读"}

    monkeypatch.setattr(engine, "run_once", fake_run_once)

    async def scenario():
        state = await engine.start_auto_reply_loop(interval_seconds=5)
        assert state["running"] is True
        await asyncio.sleep(0.05)
        stopped = engine.stop_auto_reply_loop()
        assert stopped["running"] is False
        return stopped

    asyncio.run(scenario())
    assert rounds["n"] >= 1

    engine.save_config(reply_instruction="用中文简短回复")
    diagnostics = engine.auto_reply_diagnostics(limit=5)
    assert diagnostics["ok"] is True
    assert diagnostics["state"]["running"] is False
    assert engine.get_config()["reply_instruction"] == "用中文简短回复"

# ── 加好友 / 接管 HTTP 端到端（对齐微信协议助手的接口命名）────────────────────


def _whatsapp_test_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api import native_whatsapp as api
    from backend.app.api.auth import _ServerUser, get_current_user_for_local

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_current_user_for_local] = lambda: _ServerUser(1)
    return TestClient(app)


def test_friend_add_http_surface_matches_wechat_protocol_names():
    from backend.app.api import native_whatsapp as api

    paths = {route.path for route in api.router.routes}
    expected = {
        "/api/native-whatsapp/friends/add",
        "/api/native-whatsapp/friends/records",
        "/api/native-whatsapp/friends/queue",
        "/api/native-whatsapp/friends/queue/settings",
        "/api/native-whatsapp/friends/queue/start",
        "/api/native-whatsapp/friends/queue/stop",
        "/api/native-whatsapp/auto-reply/config",
        "/api/native-whatsapp/auto-reply/run-once",
        "/api/native-whatsapp/auto-reply/loop/start",
        "/api/native-whatsapp/auto-reply/loop/stop",
        "/api/native-whatsapp/auto-reply/diagnostics",
    }
    assert expected <= paths


def test_friend_queue_http_flow_enqueue_records_settings_and_queue(isolated_whatsapp_state, monkeypatch):
    # 桌面动作在测试里必须被替换掉，避免真的操作本机 WhatsApp
    monkeypatch.setattr(engine, "add_contact", lambda **kwargs: {"ok": True})
    monkeypatch.setattr(engine, "send_message", lambda target, content: {"ok": True, "sent": True})
    payload = {
        "targets": ["张三,13800138000", "@alice_wa", "13800138000"],
        "apply_message": "你好",
        "remark": "展会",
        "interval_seconds": 45,
        "daily_limit": 12,
        "client_request_id": "batch-1",
    }

    # with 让同一个事件循环贯穿多个请求：调度器 / 常驻循环任务才不会被请求结束回收
    with _whatsapp_test_client() as client:
        added = client.post("/api/native-whatsapp/friends/add", json=payload)
        assert added.status_code == 200, added.text
        body = added.json()
        assert body["ok"] is True and body["queued"] is True
        assert body["task"]["planned_total"] == 2  # 同号码去重
        assert body["task"]["queued_total"] == 2

        repeat = client.post("/api/native-whatsapp/friends/add", json=payload).json()
        assert repeat["task"]["deduped"] is True  # 同请求 id 幂等，不重复入队
        assert repeat["task"]["queued_total"] == 2

        records = client.get("/api/native-whatsapp/friends/records?limit=10&offset=0").json()
        assert records["count"] == 2 and records["total"] == 2
        assert {item["status"] for item in records["items"]} == {"queued"}
        assert {item["apply_message"] for item in records["items"]} == {"你好"}

        queue = client.get("/api/native-whatsapp/friends/queue").json()
        assert queue["summary"]["queued"] == 2
        assert queue["control"]["enabled"] is False and queue["control"]["running"] is False
        assert queue["control"]["interval_seconds"] == 45 and queue["control"]["daily_limit"] == 12

        settings = client.post(
            "/api/native-whatsapp/friends/queue/settings",
            json={"account_id": engine.DEFAULT_ACCOUNT_ID, "interval_seconds": 30, "daily_limit": 0},
        ).json()
        assert settings["control"]["interval_seconds"] == 30 and settings["control"]["daily_limit"] == 0

        started = client.post(
            "/api/native-whatsapp/friends/queue/start",
            json={"account_id": engine.DEFAULT_ACCOUNT_ID, "interval_seconds": 30, "daily_limit": 0},
        ).json()
        assert started["control"]["enabled"] is True and started["control"]["running"] is True
        time.sleep(0.4)  # 让调度器真的跑一轮（桌面动作已替换为桩）
        assert client.get("/api/native-whatsapp/friends/queue").json()["summary"]["success"] >= 1

        stopped = client.post(
            "/api/native-whatsapp/friends/queue/stop",
            json={"account_id": engine.DEFAULT_ACCOUNT_ID},
        ).json()
        assert stopped["control"]["enabled"] is False
        # 停止是协作式的：调度器跑完当前这一条后退出，轮询等它真的停下
        deadline = time.time() + 5.0
        while time.time() < deadline and client.get(
            "/api/native-whatsapp/friends/queue"
        ).json()["control"]["running"]:
            time.sleep(0.05)
        assert client.get("/api/native-whatsapp/friends/queue").json()["control"]["running"] is False


def test_auto_reply_loop_http_endpoints(isolated_whatsapp_state, monkeypatch):
    rounds = {"n": 0}

    async def fake_run_once(*, auth_context=None, config_override=None):
        rounds["n"] += 1
        return {"ok": True, "summary_text": "本轮无未读"}

    monkeypatch.setattr(engine, "run_once", fake_run_once)

    with _whatsapp_test_client() as client:
        config = client.get("/api/native-whatsapp/auto-reply/config").json()
        assert config["ok"] is True and config["state"]["running"] is False

        started = client.post(
            "/api/native-whatsapp/auto-reply/loop/start",
            json={
                "account_id": engine.DEFAULT_ACCOUNT_ID,
                "interval_seconds": 5,
                "config_override": {"reply_instruction": "用中文简短回复"},
            },
        ).json()
        assert started["state"]["running"] is True
        assert engine.get_config()["reply_instruction"] == "用中文简短回复"

        time.sleep(0.4)
        diagnostics = client.get("/api/native-whatsapp/auto-reply/diagnostics?limit=5").json()
        assert diagnostics["ok"] is True and diagnostics["state"]["running"] is True
        assert rounds["n"] >= 1

        stopped = client.post(
            "/api/native-whatsapp/auto-reply/loop/stop",
            json={"account_id": engine.DEFAULT_ACCOUNT_ID},
        ).json()
        assert stopped["state"]["running"] is False


def test_personal_whatsapp_view_mirrors_wechat_protocol_friend_flow():
    root = Path(__file__).resolve().parent
    view = (root / "static" / "views" / "personal-whatsapp.html").read_text(encoding="utf-8")
    script = (root / "static" / "js" / "personal-whatsapp.js").read_text(encoding="utf-8")
    registry = (root / "static" / "js" / "view-registry.js").read_text(encoding="utf-8")

    ids = set(re.findall('id="([A-Za-z0-9_-]+)"', view))
    refs = set(re.findall("[$][(]'([A-Za-z0-9_-]+)'[)]", script))
    assert refs <= ids, sorted(refs - ids)
    paged = set(re.findall("renderPagination[(]'([A-Za-z0-9_-]+)'", script))
    assert paged <= ids, sorted(paged - ids)

    tabs = set(re.findall('data-pwa-tab="([A-Za-z0-9_-]+)"', view))
    panels = set(re.findall('data-pwa-panel="([A-Za-z0-9_-]+)"', view))
    assert tabs == panels
    assert "friends" in tabs and "takeover" in tabs

    for endpoint in (
        "/api/native-whatsapp/friends/add",
        "/api/native-whatsapp/friends/records?",
        "/api/native-whatsapp/friends/queue?",
        "/api/native-whatsapp/friends/queue/settings",
        "/api/native-whatsapp/friends/queue/start",
        "/api/native-whatsapp/friends/queue/stop",
        "/api/native-whatsapp/auto-reply/config",
        "/api/native-whatsapp/auto-reply/loop/start",
        "/api/native-whatsapp/auto-reply/loop/stop",
        "/api/native-whatsapp/auto-reply/diagnostics?limit=20",
    ):
        assert endpoint in script, endpoint

    for anchor in (
        "function submitFriendQueue",
        "function saveFriendQueueSettings",
        "function startFriendQueue",
        "function stopFriendQueue",
        "function renderFriendRecords",
        "function loadDiagnostics",
        "function startAutoReplyLoop",
        "var ids = ['personalWhatsappRefreshBtn'",
        "personalWhatsappFriendQueueStartBtn",
    ):
        assert anchor in script, anchor

    assert "personal-whatsapp-friends-queue" in registry
    assert "/static/js/personal-whatsapp.js?v=" in registry
