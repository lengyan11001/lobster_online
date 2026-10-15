from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from backend.app.services import native_whatsapp_engine as engine


@pytest.fixture()
def isolated_protocol_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把库/日志都指到 tmp，跑完不碰真机的 data、logs。"""
    monkeypatch.setattr(engine, "STATE_DIR", tmp_path)
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(engine, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(engine, "LOG_PATH", tmp_path / "native_whatsapp.jsonl")
    monkeypatch.setattr(engine, "PROTOCOL_LOG_PATH", tmp_path / "whatsapp_protocol.jsonl")
    monkeypatch.setattr(engine, "WEB_CONTACT_CACHE", tmp_path / "web_contacts_cache.json")
    engine._MIGRATED_PATHS.clear()
    engine._SEND_RUNTIME.clear()
    engine._release_action()
    yield tmp_path
    engine._release_action()
    for registry in (engine._SEND_SCHEDULERS, engine._FRIEND_ADD_SCHEDULERS, engine._AUTO_REPLY_LOOPS):
        for task in list(registry.values()):
            try:
                task.cancel()
            except RuntimeError:  # 事件循环已关闭（TestClient 退出后）
                pass
        registry.clear()
    engine._SEND_WAKE_EVENTS.clear()
    engine._FRIEND_ADD_WAKE_EVENTS.clear()


def _protocol_lines(tmp_path: Path) -> list:
    raw = (tmp_path / "whatsapp_protocol.jsonl")
    if not raw.exists():
        return []
    out = []
    for line in raw.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def test_contacts_sync_persists_jid_phone_and_partial_flag(isolated_protocol_state, monkeypatch):
    tmp_path = isolated_protocol_state

    def fake_bridge(argv, *, timeout=120, record=True):
        assert argv[0] == "contacts"
        # 续传靠 --cache/--resume（--ledger 由 _run_bridge 统一追加，这里被替身挡掉了）
        assert "--cache" in argv and "--resume" in argv
        assert argv[argv.index("--cache") + 1].endswith("web_contacts_cache.json")
        return {
            "ok": True,
            "count": 2,
            "with_phone": 1,
            "partial": True,
            "history_ok": False,
            "resumed_from": 0,
            "contacts": [
                {"id": "8613800138000@s.whatsapp.net", "phone": "8613800138000", "name": "Ann", "notify": "Ann", "verifiedName": ""},
                {"id": "12025550123@s.whatsapp.net", "phone": "", "name": "", "notify": "Bob", "verifiedName": ""},
            ],
        }

    monkeypatch.setattr(engine, "_run_bridge", fake_bridge)
    result = engine.web_sync_contacts()

    assert result["count"] == 2
    assert result["with_phone"] == 1
    assert result["partial"] is True
    assert "部分" in result["message"]

    stats = engine.contact_stats()
    assert stats["total"] == 2
    assert stats["with_phone"] == 1

    with engine._connect() as conn:
        row = conn.execute("select jid, phone from whatsapp_contacts where phone=?", ("8613800138000",)).fetchone()
    assert row["jid"] == "8613800138000@s.whatsapp.net"

    events = [item["event"] for item in _protocol_lines(tmp_path)]
    assert "contacts_saved" in events
    saved = [item for item in _protocol_lines(tmp_path) if item["event"] == "contacts_saved"][0]
    assert saved["partial"] is True and saved["with_phone"] == 1


def test_run_bridge_records_every_command_node(isolated_protocol_state, monkeypatch):
    tmp_path = isolated_protocol_state
    calls = {}

    class FakeProc:
        def __init__(self):
            self.stdout = json.dumps({"ok": True, "count": 3, "partial": False}) + "\n"
            self.stderr = ""
            self.returncode = 0

    def fake_run(command, **kwargs):
        calls["command"] = command
        return FakeProc()

    monkeypatch.setattr(engine.subprocess, "run", fake_run)
    payload = engine._run_bridge(["chats", "--auth", "x"], timeout=5)

    assert payload["count"] == 3
    assert calls["command"][-2] == "--ledger"
    assert calls["command"][-1].endswith("whatsapp_protocol.jsonl")
    node = [item for item in _protocol_lines(tmp_path) if item["event"] == "cmd"][-1]
    assert node["cmd"] == "chats" and node["ok"] is True and node["elapsed_ms"] >= 0


def test_protocol_log_tail_and_filter(isolated_protocol_state):
    engine._append_protocol("cmd", cmd="contacts", ok=True, received=5)
    engine._append_protocol("contacts_chunk", progress=40, sample=[{"id": "x"}])
    engine._append_protocol("cmd", cmd="chats", ok=False, error="boom")

    tail = engine.protocol_log(limit=2)
    assert tail["count"] == 2
    assert tail["items"][0]["cmd"] == "chats"  # 新的在前

    only_cmd = engine.protocol_log(limit=50, event="cmd")
    assert [item["cmd"] for item in only_cmd["items"]] == ["chats", "contacts"]


def test_chats_and_messages_are_cached_then_read_locally(isolated_protocol_state, monkeypatch):
    def fake_bridge(argv, *, timeout=120, record=True):
        if argv[0] == "chats":
            return {"ok": True, "count": 2, "chats": [
                {"id": "8613800138000@s.whatsapp.net", "name": "Ann", "group": False, "unread": 1, "ts": 100},
                {"id": "120363000000000000@g.us", "name": "Team", "group": True, "unread": 0, "ts": 90},
            ]}
        if argv[0] == "messages":
            assert "--chat" in argv
            return {"ok": True, "scanned": 3, "count": 2, "messages": [
                {"id": "m1", "chat": "8613800138000@s.whatsapp.net", "from_me": False, "ts": 1, "text": "hi"},
                {"id": "m2", "chat": "8613800138000@s.whatsapp.net", "from_me": True, "ts": 2, "text": "hello"},
            ]}
        raise AssertionError("unexpected bridge cmd: %s" % argv)

    monkeypatch.setattr(engine, "_run_bridge", fake_bridge)
    chats = engine.web_chats(refresh=True)
    assert chats["count"] == 2 and chats["saved"] == 2

    groups = engine.web_groups(refresh=False)
    assert groups["count"] == 1 and groups["items"][0]["jid"].endswith("@g.us")

    messages = engine.web_messages("8613800138000@s.whatsapp.net", refresh=True)
    assert messages["count"] == 2
    assert [row["content"] for row in messages["items"]] == ["hi", "hello"]
    assert messages["items"][1]["from_me"] == 1


def test_send_whitelist_blocks_strangers_and_allows_existing_chat(isolated_protocol_state):
    alien = "19999999999@s.whatsapp.net"
    result = engine.enqueue_send([alien], text="hi")
    assert result["queued"] == 0
    assert "陌生号不发" in result["skipped"][0]["reason"]

    engine._persist_web_chats([{"id": alien, "name": "Ann", "group": False, "unread": 0, "ts": 1}])
    result = engine.enqueue_send([alien], text="hi")
    assert result["queued"] == 1
    assert result["summary"]["queued"] == 1

    # 队列里已有同号未发的任务 -> 不再重复入队（同号去重）
    again = engine.enqueue_send([alien], text="hi again")
    assert again["queued"] == 0
    assert "队列里已有" in again["skipped"][0]["reason"]


def test_send_batch_dedupe_and_control_clamps(isolated_protocol_state):
    jid = "8613800138000@s.whatsapp.net"
    engine._persist_web_chats([{"id": jid, "name": "Ann", "group": False, "unread": 0, "ts": 1}])
    result = engine.enqueue_send([jid, jid], text="hi")
    assert result["queued"] == 1
    assert result["skipped"][0]["reason"] == "同一批重复目标"

    control = engine.save_send_control("", min_gap=1, max_gap=9999, pause_every=0, pause_seconds=99999, daily_limit=9999)
    assert control["min_gap"] == 3
    assert control["max_gap"] == 900
    assert control["pause_every"] == 1
    assert control["pause_seconds"] == 3600
    assert control["daily_limit"] == 1000

    tight = engine.save_send_control("", min_gap=50, max_gap=10)
    assert tight["max_gap"] >= tight["min_gap"] == 50


def test_send_queue_marks_task_success_and_counts_today(isolated_protocol_state, monkeypatch):
    jid = "8613800138000@s.whatsapp.net"
    engine._persist_web_chats([{"id": jid, "name": "Ann", "group": False, "unread": 0, "ts": 1}])
    engine.save_send_control("", min_gap=3, max_gap=3, pause_every=100, pause_seconds=0, daily_limit=80)

    sent = []

    def fake_send(jid_value, payload):
        sent.append((jid_value, payload.get("text")))
        return {"ok": True, "id": "MSG-1", "elapsed_ms": 12}

    monkeypatch.setattr(engine, "_bridge_send_one", fake_send)
    queued = engine.enqueue_send([jid], text="hello from queue")
    assert queued["queued"] == 1

    async def run():
        await engine.start_send_queue()
        try:
            for _ in range(60):
                summary = engine.send_queue_summary()
                if summary["success"] >= 1:
                    break
                await asyncio.sleep(0.1)
            return engine.send_queue_summary()
        finally:
            await engine.stop_send_queue()

    summary = asyncio.run(run())
    assert sent == [(jid, "hello from queue")]
    assert summary["success"] == 1
    assert summary["queued"] == 0
    assert engine._send_count_today(engine.DEFAULT_ACCOUNT_ID) == 1

    records = engine.list_send_records(limit=10)
    assert records["total"] == 1
    assert records["items"][0]["status"] == "success"


def test_web_check_reports_registered_numbers(isolated_protocol_state, monkeypatch):
    def fake_bridge(argv, *, timeout=120, record=True):
        assert argv[0] == "check"
        return {"ok": True, "count": 2, "results": [
            {"input": "8613800138000", "digits": "8613800138000", "exists": True, "jid": "8613800138000@s.whatsapp.net"},
            {"input": "19999999999", "digits": "19999999999", "exists": False, "jid": ""},
        ]}

    monkeypatch.setattr(engine, "_run_bridge", fake_bridge)
    result = engine.web_check(["8613800138000", "19999999999"])
    assert result["count"] == 2 and result["exists"] == 1
    assert "1 个已注册" in result["message"]


def test_session_name_ignores_ui_labels(isolated_protocol_state):
    """UIA 会把窗口标题栏的 UI 标签当成会话名（实测出现「个人主页详情」「1」「晚上6:22」）。"""
    assert engine._looks_like_ui_label("个人主页详情") is True
    assert engine._looks_like_ui_label("1") is True
    assert engine._looks_like_ui_label("晚上6:22") is True
    assert engine._looks_like_ui_label("你：") is True
    assert engine._looks_like_ui_label("将联系人信息同步到手机") is True
    assert engine._looks_like_ui_label("hehao") is False


def test_session_name_picks_a_real_name_from_the_header(isolated_protocol_state):
    header = ["个人主页详情", "hehao", "视频通话", "语音通话", "搜索"]
    assert engine._session_display_name("个人主页详情", header) == "hehao"
    # 已经是对的名字就别动
    assert engine._session_display_name("hehao", header) == "hehao"
    # 标题栏里全是 UI 标签时，保持原样（不硬编一个假名字）
    assert engine._session_display_name("个人主页详情", ["搜索", "群聊"]) == "个人主页详情"


def test_session_name_prefers_a_known_contact(isolated_protocol_state):
    engine._persist_contact({"display_name": "Alice Chen", "phone": "8613800138000"})
    assert engine._session_display_name("个人主页详情", ["随便", "Alice Chen"]) == "Alice Chen"
    assert engine._session_display_name("个人主页详情", ["随便", "Bob"]) == "随便"


def test_persist_and_list_sessions_repair_the_stored_ui_label(isolated_protocol_state):
    snapshot = {
        "peer_name": "个人主页详情",
        "is_group": False,
        "header": ["个人主页详情", "hehao", "视频通话", "搜索"],
        "messages": [
            {"text": "nihao", "direction": "inbound"},
            {"text": "你好～在的，想了解点什么呢？", "direction": "outbound"},
        ],
        "last_message": {"text": "你好～在的，想了解点什么呢？", "direction": "outbound"},
    }
    peer = engine._persist_session_snapshot(snapshot)
    # 写库时就用标题栏里的真名
    assert peer["display_name"] == "hehao"

    page = engine.list_sessions(limit=10, offset=0)
    assert page["total"] == 1
    assert page["items"][0]["display_name"] == "hehao"
    messages = engine.list_messages(peer["peer_key"], limit=20, offset=0)
    assert [item["content"] for item in messages["items"]] == ["nihao", "你好～在的，想了解点什么呢？"]


def test_list_sessions_fixes_old_rows_without_rewriting_them(isolated_protocol_state):
    """老库里已经存成 UI 标签的行：读的时候按 raw.header 纠正，不改库也立刻显示对。"""
    peer_key = engine._stable_key("peer", "个人主页详情")
    with engine._DB_LOCK, engine._connect() as conn:
        conn.execute(
            "insert into whatsapp_sessions(id, account_id, peer_key, display_name, chat_type, unread_count, last_message, last_direction, raw_json, created_at, updated_at)"
            " values(?,?,?,?,?,?,?,?,?,?,?)",
            (
                peer_key, engine.DEFAULT_ACCOUNT_ID, peer_key, "个人主页详情", "direct", 0, "你：", "outbound",
                engine._json_text({"peer_name": "个人主页详情", "header": ["个人主页详情", "hehao", "搜索"]}),
                engine._now_iso(), engine._now_iso(),
            ),
        )
    page = engine.list_sessions(limit=10, offset=0)
    assert page["items"][0]["display_name"] == "hehao"
    assert page["items"][0]["display_name_from"] == "header"
