"""读不到微信号时按昵称搜发：捕获不丢人、发送前用名字核对。"""
from __future__ import annotations

import pytest

from backend.app.services import native_wechat_engine as engine

ACCOUNT = "wechat-account-nickname"


def _use_temp_native_wechat_db(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native_wechat_engine.db")
    monkeypatch.setattr(engine, "STATE_DIR", tmp_path)
    monkeypatch.setattr(engine, "ACCOUNTS_DIR", tmp_path / "accounts")
    monkeypatch.setattr(engine, "_write_auto_reply_diagnostic", lambda *args, **kwargs: None)
    engine.init_db()


def _no_local_wechat(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("no local wechat window in this test")

    monkeypatch.setattr(engine, "_get_wxauto4_client", _raise)


def test_scan_identity_falls_back_to_nickname_when_profile_has_no_id(monkeypatch, tmp_path):
    _use_temp_native_wechat_db(monkeypatch, tmp_path)
    _no_local_wechat(monkeypatch)
    monkeypatch.setattr(
        engine,
        "_read_current_private_chat_wx_no",
        lambda *args, **kwargs: {"ok": False, "wx_no": "", "reason": "profile_wx_no_missing"},
    )

    # 通讯录里没有这个名字（改过备注），当前打开的会话就是这个人（扫描会带上当前行
    # 名字），资料里也没有号 -> 用昵称身份继续。
    assert engine._resolve_scan_contact_wx_no(
        ACCOUNT,
        display_name="余老师工作号",
        current_chat_name="余老师工作号",
        attempts=1,
    ) == ("余老师工作号", "nickname_fallback:profile_wx_no_missing")


def test_page_capture_keeps_candidate_with_nickname_identity(monkeypatch, tmp_path):
    _use_temp_native_wechat_db(monkeypatch, tmp_path)
    _no_local_wechat(monkeypatch)
    monkeypatch.setattr(
        engine,
        "_read_current_private_chat_wx_no",
        lambda *args, **kwargs: {"ok": False, "wx_no": "", "reason": "profile_wx_no_missing"},
    )
    monkeypatch.setattr(
        engine,
        "_sync_local_messages_once",
        lambda *args, **kwargs: {
            "ok": True,
            "peer_id": "余老师工作号",
            "chat_info": {"chat_type": "friend", "chat_name": "余老师工作号"},
            "fresh_latest_message": {"direction": "in", "content": "在吗", "provider_message_id": "m-1"},
        },
    )

    captures = engine._capture_auto_reply_scan_page(
        ACCOUNT,
        [
            {
                "peer_id": "余老师工作号",
                "display_name": "余老师工作号",
                "last_content": "在吗",
                "session_time": (engine.datetime.now() - engine.timedelta(minutes=3)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "session_snapshot_fresh": True,
                "raw": {},
            }
        ],
    )

    capture = captures["余老师工作号"]
    assert capture["identity_mode"] == "nickname"
    assert capture["identity_target"] == "余老师工作号"
    assert capture["wechat_id"] == ""
    assert capture["inbound"]["content"] == "在吗"


def test_send_verification_accepts_nickname_identity_only_for_the_same_chat(monkeypatch):
    monkeypatch.setattr(
        engine,
        "_current_local_chat_info",
        lambda wx, fallback_name="": {"chat_name": "余老师工作号", "chat_type": "friend"},
    )
    verified = engine._verify_local_send_chat(
        object(), "余老师工作号", strict_private=True, nickname_identity=True
    )
    assert verified["chat_name"] == "余老师工作号"

    # 名字看起来像微信号（Jason 这种）时也必须核对，不能因为"像号"就放过。
    monkeypatch.setattr(
        engine,
        "_current_local_chat_info",
        lambda wx, fallback_name="": {"chat_name": "Peter", "chat_type": "friend"},
    )
    with pytest.raises(RuntimeError):
        engine._verify_local_send_chat(object(), "Jason", strict_private=True, nickname_identity=True)

    monkeypatch.setattr(
        engine,
        "_current_local_chat_info",
        lambda wx, fallback_name="": {"chat_name": "张深根-AI三域营销运营1", "chat_type": "friend"},
    )
    with pytest.raises(RuntimeError):
        engine._verify_local_send_chat(
            object(),
            "余老师工作号",
            strict_private=True,
            nickname_identity=True,
        )
