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
            "王五,王,13800138003",   # 姓名,姓氏,电话
        ]
    )
    assert [item["phone"] for item in targets if item["phone"]] == [
        "13800138000", "13800138001", "13800138002", "13800138003",
    ]
    assert targets[0]["first_name"] == "张三"
    assert targets[0]["country_code"] == "+86"
    assert targets[-1]["first_name"] == "王五" and targets[-1]["last_name"] == "王"
    assert any(item["username"] == "alice_wa" for item in targets)
    assert engine._target_label({"country_code": "+86", "phone": "13800138000"}) == "+8613800138000"


def test_friend_add_queue_enqueues_and_lists_records(isolated_whatsapp_state):
    task = engine.create_add_contact_task(["王五,13800138000", "李四,13800138001"], apply_message="你好", queue_only=True)
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

    engine.create_add_contact_task(["王五,王,13800138003"], apply_message="您好，我是小王")
    claimed = engine._claim_next_queued_task(engine.DEFAULT_ACCOUNT_ID)
    outcome = asyncio.run(engine._process_add_contact_task(claimed))

    assert outcome["status"] == "success"
    assert calls["add"][0]["phone"] == "13800138003"
    assert calls["add"][0]["first_name"] == "王五"
    assert calls["add"][0]["last_name"] == "王"   # 姓氏必须真的带进 WhatsApp 表单
    assert calls["send"][0] == ("+8613800138003", "您好，我是小王")

    records = engine.list_friend_records(limit=5, offset=0)
    assert records["items"][0]["status"] == "success"
    assert records["items"][0]["success"] == 1
    assert engine.friend_add_queue_summary()["today_success"] == 1


def test_friend_add_daily_limit_defers_task(isolated_whatsapp_state, monkeypatch):
    monkeypatch.setattr(engine, "add_contact", lambda **kwargs: {"ok": True})
    engine.save_friend_add_control(interval_seconds=5, daily_limit=1)
    engine.create_add_contact_task(["赵一,13800138004", "钱二,13800138005"])

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
        "targets": ["张三,13800138000", "李四,张,13800138002", "13800138000"],
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
        assert {item["first_name"] for item in records["items"]} == {"张三", "李四"}
        assert any(item["last_name"] == "张" and item["phone"] == "+8613800138002" for item in records["items"])

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


def test_personal_whatsapp_add_edit_forms_live_in_modals():
    '''新增/编辑表单必须收进弹窗，页面上只留列表与工具栏；且同一控件不允许重复绑定。'''
    root = Path(__file__).resolve().parent
    view = (root / "static" / "views" / "personal-whatsapp.html").read_text(encoding="utf-8")
    script = (root / "static" / "js" / "personal-whatsapp.js").read_text(encoding="utf-8")

    # 四个弹窗：添加好友 / 单条联系人 / 加好友设置 / 接管设置
    for modal_id in (
        "personalWhatsappFriendAddModal",
        "personalWhatsappContactModal",
        "personalWhatsappFriendSettingsModal",
        "personalWhatsappTakeoverSettingsModal",
    ):
        assert 'class="pwa-modal-mask" id="%s"' % modal_id in view, modal_id

    # 表单字段必须落在弹窗里，不能留在 pwa-panel 直系区域
    for panel in ('data-pwa-panel="friends"', 'data-pwa-panel="takeover"'):
        start = view.index(panel)
        panel_html = view[start:view.index("</section>", start)]
        for field in ("personalWhatsappFriendKeyword", "personalWhatsappContactFirstName", "personalWhatsappInstruction"):
            assert 'id="%s"' % field not in panel_html, field

    # 弹窗开关 + Esc 关闭
    for anchor in ("function openModal", "function closeModal", "function openFriendAddModal",
                   "function openFriendSettingsModal", "function openTakeoverSettingsModal"):
        assert anchor in script, anchor
    assert "event.key==='Escape'" in script
    assert "[data-pwa-modal-close]" in script

    # 同一个控件只允许绑定一次监听（此前 saveBtn 被绑了两次，导致重复保存请求）
    bound = re.findall(r"\$\('([A-Za-z0-9_-]+)'\)\.addEventListener", script)
    assert len(bound) >= 20  # 防止正则失配后空跑
    duplicated = {key for key in bound if bound.count(key) > 1}
    assert not duplicated, sorted(duplicated)


def test_batch_add_friend_requires_name_for_phone_targets(isolated_whatsapp_state):
    """WhatsApp 添加联系人表单必须有姓名：只写号码的目标不允许入队（不能照抄个微的关键词口径）。"""
    with pytest.raises(RuntimeError) as excinfo:
        engine.create_add_contact_task(["13800138000", "张三,13800138001"])
    assert "必须填姓名" in str(excinfo.value)
    assert engine.list_friend_records(limit=10, offset=0)["count"] == 0

    task = engine.create_add_contact_task(["张三,13800138000", "李四,张,13800138002", "王五,@wangwu"])
    assert task["queued_total"] == 3
    records = engine.list_friend_records(limit=10, offset=0)["items"]
    by_name = {item["first_name"]: item for item in records}
    assert by_name["李四"]["last_name"] == "张"
    assert by_name["王五"]["username"] == "wangwu"
    assert by_name["王五"]["phone"] == ""


def test_whatsapp_friend_fields_follow_desktop_form_not_wechat():
    """字段按 WhatsApp「新联系人」表单（名字/姓氏/用户名/国家地区+电话），不照抄个微的关键词+备注+标签。"""
    root = Path(__file__).resolve().parent
    view = (root / "static" / "views" / "personal-whatsapp.html").read_text(encoding="utf-8")
    script = (root / "static" / "js" / "personal-whatsapp.js").read_text(encoding="utf-8")

    for wechat_only in ("personalWhatsappFriendRemark", "personalWhatsappFriendPermission",
                        "personalWhatsappFriendTags", 'value="朋友圈"', 'value="仅聊天"'):
        assert wechat_only not in view, wechat_only

    for field in ("personalWhatsappContactFirstName", "personalWhatsappContactLastName",
                  "personalWhatsappContactUsername", "personalWhatsappContactPhone",
                  "personalWhatsappCountryCode"):
        assert 'id="%s"' % field in view, field

    assert "姓名,电话" in view and "必须有名字" in view
    assert "function namelessTargetLines" in script
    assert "personalWhatsappFriendAddError" in script
    assert "<th>姓名</th>" in script and "号码 / @用户名" in script
