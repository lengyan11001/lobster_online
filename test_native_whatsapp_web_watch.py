from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from backend.app.services import native_whatsapp_engine as engine


@pytest.fixture()
def isolated_watch_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(engine, "STATE_DIR", tmp_path)
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(engine, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(engine, "LOG_PATH", tmp_path / "native_whatsapp.jsonl")
    monkeypatch.setattr(engine, "PROTOCOL_LOG_PATH", tmp_path / "whatsapp_protocol.jsonl")
    monkeypatch.setattr(engine, "WEB_CONTACT_CACHE", tmp_path / "web_contacts_cache.json")
    monkeypatch.setattr(engine, "WEB_LOGIN_STATE", tmp_path / "whatsapp_web_login.json")
    engine._MIGRATED_PATHS.clear()
    engine._WEB_WATCH.update({
        "proc": None, "reader": None, "state": "stopped", "lines": 0,
        "contacts_new": 0, "contacts_updated": 0, "chats": 0, "messages": 0,
        "last_event": "", "last_at": 0.0, "last_error": "", "logged_out": False,
    })
    engine._release_action()
    yield tmp_path
    engine._release_action()


def _events(tmp_path: Path) -> list:
    path = tmp_path / "whatsapp_protocol.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_watch_contact_events_persist_new_then_update(isolated_watch_state):
    """登录之后新加的好友：常驻把 contacts.upsert 直接落库，第一次算新增，第二次算更新。"""
    engine._handle_watch_event({
        "event": "contacts_upsert",
        "rows": [
            {"id": "8613900111222@s.whatsapp.net", "name": "", "notify": "新朋友", "verifiedName": "", "phoneNumber": ""},
            {"id": "", "notify": "没有 jid 的行会被忽略"},
        ],
    })

    stats = engine.contact_stats()
    assert stats["total"] == 1
    assert stats["with_phone"] == 1
    with engine._connect() as conn:
        row = conn.execute("select phone, display_name, source, jid from whatsapp_contacts limit 1").fetchone()
    assert row["phone"] == "8613900111222"
    assert row["display_name"] == "新朋友"
    assert row["source"] == "web_protocol_watch"
    assert row["jid"] == "8613900111222@s.whatsapp.net"

    status = engine.web_watch_status()
    assert status["contacts_new"] == 1 and status["contacts_updated"] == 0
    assert engine.web_watch_status()["last_event"] == "contacts_upsert"

    operations = engine.list_operations(limit=10)
    assert [item["action"] for item in operations["items"]][0] == "contact_added"

    # 同一个联系人再来一次（改名/改号）-> 算更新，不重复计数
    engine._handle_watch_event({
        "event": "contacts_update",
        "rows": [{"id": "8613900111222@s.whatsapp.net", "notify": "新朋友改名"}],
    })
    assert engine.contact_stats()["total"] == 1
    status = engine.web_watch_status()
    assert status["contacts_new"] == 1 and status["contacts_updated"] == 1

    deltas = [item for item in _events(isolated_watch_state) if item["event"] == "contact_delta"]
    assert deltas[0]["new"] == 1 and deltas[0]["updated"] == 0
    assert deltas[1]["new"] == 0 and deltas[1]["updated"] == 1


def test_watch_chat_and_message_events_persist(isolated_watch_state, tmp_path):
    engine._handle_watch_event({
        "event": "chats_upsert",
        "rows": [{"id": "120363000000000000@g.us", "name": "Team", "group": True, "unread": 3, "ts": 111}],
    })
    engine._handle_watch_event({
        "event": "groups_upsert",
        "rows": [{"id": "120363000000000001@g.us", "name": "Team 2", "group": True, "unread": 0, "ts": 112}],
    })
    engine._handle_watch_event({
        "event": "messages_upsert",
        "type": "notify",
        "rows": [{"id": "m9", "chat": "120363000000000000@g.us", "from_me": False, "ts": 5, "text": "在吗"}],
    })

    groups = engine.web_groups(refresh=False)
    assert groups["count"] == 2
    messages = engine.web_messages("120363000000000000@g.us", refresh=False)
    assert [row["content"] for row in messages["items"]] == ["在吗"]
    status = engine.web_watch_status()
    assert status["chats"] == 2 and status["messages"] == 1


def test_watch_handles_state_and_broken_payloads(isolated_watch_state):
    for payload in (
        {"event": "state", "value": "open", "attempt": 2},
        {"event": "heartbeat"},
        {"event": "resync", "reason": "interval"},
        {"event": "resync_failed", "error": "Connection Closed"},
        {"event": "contacts_upsert", "rows": None},
        {"event": "contacts_upsert", "rows": ["不是字典", {"id": ""}]},
        {"event": "unknown_thing"},
        {},
        None,
    ):
        engine._handle_watch_event(payload)  # 不能抛

    status = engine.web_watch_status()
    assert status["state"] == "open"
    assert status["attempt"] == 2
    assert status["last_error"] == "Connection Closed"
    assert status["lines"] == 8  # None 不是 dict，直接丢弃不计数


def test_watch_reader_parses_jsonl_stream_and_skips_junk(isolated_watch_state):
    stream = io.StringIO(
        "\n".join([
            json.dumps({"event": "state", "value": "ready"}),
            "这行不是 JSON，应该被跳过",
            json.dumps({"event": "contacts_upsert", "rows": [{"id": "8613800138000@s.whatsapp.net", "notify": "Ann"}]}),
            "",
        ])
    )

    class FakeProc:
        stdout = stream

    engine._WEB_WATCH["proc"] = FakeProc()
    engine._watch_reader(FakeProc())

    assert engine.contact_stats()["total"] == 1
    assert engine._WEB_WATCH["proc"] is None
    assert engine._WEB_WATCH["state"] == "stopped"  # reader 退出 -> 状态回 stopped（不是 logged_out）


def test_watch_start_requires_a_linked_session(isolated_watch_state, monkeypatch):
    monkeypatch.setattr(engine, "web_login_status", lambda: {"linked": False})
    with pytest.raises(RuntimeError):
        engine.web_watch_start(60)


def test_watch_defaults_to_stopped(isolated_watch_state):
    status = engine.web_watch_status()
    assert status["running"] is False
    assert status["state"] == "stopped"
    assert status["interval_seconds"] == engine.WATCH_DEFAULT_INTERVAL
    assert status["auth_dir"].endswith("whatsapp_web" + "\\" + "pc") or "whatsapp_web" in status["auth_dir"]


def test_web_logout_unlinks_remotely_and_reports(isolated_watch_state, monkeypatch):
    """解绑要真的通知 WhatsApp（sock.logout），不是只删本地文件。"""
    calls = {}

    def fake_bridge(argv, *, timeout=120, record=True):
        calls["argv"] = argv
        return {"ok": True, "remote": "removed", "local_removed": True, "error": ""}

    monkeypatch.setattr(engine, "_run_bridge", fake_bridge)
    result = engine.web_logout()

    assert calls["argv"][0] == "logout"
    assert result["remote"] == "removed"
    assert "已解绑" in result["message"]
    assert "手机上" in result["message"]
    events = [item["event"] for item in _events(isolated_watch_state)]
    assert "web_logout" in events


def test_web_logout_tells_the_user_to_unlink_on_the_phone_when_remote_fails(isolated_watch_state, monkeypatch):
    """远程没解掉就如实说，不许假装成功。"""
    monkeypatch.setattr(
        engine, "_run_bridge",
        lambda argv, **kwargs: {"ok": True, "remote": "failed", "local_removed": True, "error": "Connection Closed"},
    )
    result = engine.web_logout()

    assert result["remote"] == "failed"
    assert "没解除成功" in result["message"]
    assert "已连接的设备" in result["message"]
    operations = engine.list_operations(limit=5)
    assert operations["items"][0]["status"] == "skipped"
    assert "手动移除" in operations["items"][0]["message"]


def test_web_logout_without_local_creds_points_at_the_phone(isolated_watch_state, monkeypatch):
    monkeypatch.setattr(
        engine, "_run_bridge",
        lambda argv, **kwargs: {"ok": True, "remote": "skipped", "local_removed": True, "error": ""},
    )
    result = engine.web_logout()

    assert result["remote"] == "skipped"
    assert "手动移除" in result["message"]


def test_web_logout_stops_the_resident_watch_first(isolated_watch_state, monkeypatch):
    """解绑前必须先停常驻，否则它拿着会话继续连着，远程解绑会被它抢。"""
    class FakeProc:
        stdout = None
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

    proc = FakeProc()
    engine._WEB_WATCH["proc"] = proc
    engine._WEB_WATCH["state"] = "ready"
    monkeypatch.setattr(
        engine, "_run_bridge",
        lambda argv, **kwargs: {"ok": True, "remote": "removed", "local_removed": True, "error": ""},
    )

    engine.web_logout()

    assert proc.terminated is True
    assert engine._WEB_WATCH["proc"] is None
    assert engine.web_watch_status()["running"] is False
