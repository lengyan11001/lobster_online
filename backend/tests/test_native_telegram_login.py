"""Telegram 登录状态机：手机号 → 验证码 →（2FA 密码）→ linked。

本机没装 telethon，所以这里塞一个假 telethon 进去：状态机、事件协议、
session 落盘、错误分支全部能验证。P1 真机上只剩"telethon 真能连上"这一件事要验。
"""
from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "scripts" / "telegram_bridge.py"


def _load_bridge():
    spec = importlib.util.spec_from_file_location("telegram_bridge_under_test", BRIDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeMe(object):
    id = 4242
    username = "tester"
    first_name = "Test"
    last_name = "User"
    phone = "8613800000000"


class _FakeErrors(object):
    class SessionPasswordNeededError(Exception):
        pass


def _install_fake_telethon(monkeypatch, bridge, calls):
    class FakeClient(object):
        def __init__(self, session_file, api_id, api_hash, **kwargs):
            self.session_file = str(session_file)
            self.api_id = int(api_id)
            self.api_hash = api_hash
            self.kwargs = kwargs
            calls.append(("init", self.session_file, self.api_id, self.api_hash))

        def connect(self):
            calls.append(("connect",))

        def send_code_request(self, phone):
            calls.append(("send_code_request", phone))

        def _write_session(self):
            target = pathlib.Path(self.session_file)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("fake-session", encoding="utf-8")

        def sign_in(self, **kwargs):
            calls.append(("sign_in", kwargs))
            if "code" in kwargs:
                if kwargs["code"] == "11111":
                    raise _FakeErrors.SessionPasswordNeededError("需要 2FA")
                if kwargs["code"] == "00000":
                    raise RuntimeError("PHONE_CODE_INVALID")
                self._write_session()
                return _FakeMe()
            if kwargs.get("password") == "bad":
                raise RuntimeError("PASSWORD_HASH_INVALID")
            self._write_session()
            return _FakeMe()

        def get_me(self):
            return _FakeMe()

        def disconnect(self):
            calls.append(("disconnect",))

    fake = types.SimpleNamespace(TelegramClient=FakeClient)
    monkeypatch.setattr(bridge, "_load_telethon", lambda: (fake, _FakeErrors))
    return fake


@pytest.fixture
def bridge():
    return _load_bridge()


def _session(bridge, tmp_path, phone=""):
    return bridge.LoginSession(auth_dir=str(tmp_path / "auth"), api_id="1234567", api_hash="hash", phone=phone)


def test_phone_then_code_links_and_writes_session(bridge, tmp_path, monkeypatch):
    calls = []
    _install_fake_telethon(monkeypatch, bridge, calls)
    session = _session(bridge, tmp_path)

    events = session.handle({"cmd": "phone", "value": "138 0000 0000"})
    assert events[-1]["event"] == "need_code"
    assert ("send_code_request", "+8613800000000") in calls, "手机号要先归一化再发给 Telegram"

    events = session.handle({"cmd": "code", "value": "22222"})
    assert events[-1]["event"] == "linked"
    assert events[-1]["me"]["username"] == "tester"
    assert pathlib.Path(session.session_file).exists(), "linked 之后 session 必须已经落盘"
    assert ("disconnect",) in calls, "登录完要把连接收掉"
    assert session.step == "linked"


def test_code_with_2fa_asks_for_password_then_links(bridge, tmp_path, monkeypatch):
    _install_fake_telethon(monkeypatch, bridge, [])
    session = _session(bridge, tmp_path)
    session.handle({"cmd": "phone", "value": "+8613800000000"})

    events = session.handle({"cmd": "code", "value": "11111"})
    assert events[-1]["event"] == "need_password"
    assert not pathlib.Path(session.session_file).exists()

    events = session.handle({"cmd": "password", "value": "correct"})
    assert events[-1]["event"] == "linked"
    assert pathlib.Path(session.session_file).exists()


def test_invalid_code_reports_error_and_stops(bridge, tmp_path, monkeypatch):
    _install_fake_telethon(monkeypatch, bridge, [])
    session = _session(bridge, tmp_path)
    session.handle({"cmd": "phone", "value": "+8613800000000"})

    events = session.handle({"cmd": "code", "value": "00000"})
    assert events[-1]["event"] == "error"
    assert events[-1]["error"] == "sign_in_failed"
    assert "PHONE_CODE_INVALID" in events[-1]["message"]
    assert session.step == "error"


def test_wrong_2fa_password_reports_error(bridge, tmp_path, monkeypatch):
    _install_fake_telethon(monkeypatch, bridge, [])
    session = _session(bridge, tmp_path)
    session.handle({"cmd": "phone", "value": "+8613800000000"})
    session.handle({"cmd": "code", "value": "11111"})

    events = session.handle({"cmd": "password", "value": "bad"})
    assert events[-1]["event"] == "error"
    assert events[-1]["error"] == "password_failed"


def test_empty_phone_and_unknown_command(bridge, tmp_path, monkeypatch):
    _install_fake_telethon(monkeypatch, bridge, [])
    session = _session(bridge, tmp_path)
    assert session.handle({"cmd": "phone", "value": ""})[-1]["error"] == "bad_phone"
    assert session.handle({"cmd": "nonsense"})[-1]["error"] == "unknown_command"
    assert session.handle({"cmd": "cancel"})[-1]["event"] == "cancelled"


def test_run_loop_emits_protocol_and_exits_zero_only_when_linked(bridge, tmp_path, monkeypatch):
    _install_fake_telethon(monkeypatch, bridge, [])
    session = _session(bridge, tmp_path)
    stdin = io.StringIO(
        json.dumps({"cmd": "phone", "value": "+8613800000000"}) + "\n" +
        json.dumps({"cmd": "code", "value": "22222"}) + "\n" +
        json.dumps({"cmd": "code", "value": "99999"}) + "\n"
    )
    stdout = io.StringIO()

    code = session.run(stdin, stdout)

    assert code == 0, "登录成功就该退 0"
    events = [json.loads(line) for line in stdout.getvalue().strip().splitlines()]
    assert events[0]["event"] == "need_phone", "开场先要手机号"
    assert events[-1]["event"] == "linked"
    assert not any(event.get("event") == "error" for event in events), "linked 之后不该再有事件"


def test_run_loop_returns_nonzero_when_not_linked(bridge, tmp_path, monkeypatch):
    _install_fake_telethon(monkeypatch, bridge, [])
    session = _session(bridge, tmp_path)
    stdin = io.StringIO(json.dumps({"cmd": "cancel"}) + "\n")
    stdout = io.StringIO()
    assert session.run(stdin, stdout) == 2


def test_ledger_records_login_key_nodes(bridge, tmp_path, monkeypatch):
    _install_fake_telethon(monkeypatch, bridge, [])
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(bridge, "LEDGER", str(ledger))
    session = _session(bridge, tmp_path)
    session.handle({"cmd": "phone", "value": "+8613800000000"})
    session.handle({"cmd": "code", "value": "22222"})

    lines = [json.loads(line) for line in ledger.read_text(encoding="utf-8").strip().splitlines()]
    events = [row.get("event") for row in lines]
    assert "login_code_sent" in events
    assert "login_linked" in events
