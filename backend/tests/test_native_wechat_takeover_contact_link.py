"""取号策略：通讯录优先；查不到才读一次资料，并把新名字对齐写回通讯录。"""
from __future__ import annotations

from backend.app.services import native_wechat_engine as engine

ACCOUNT = "wechat-account-takeover"


def _use_temp_native_wechat_db(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native_wechat_engine.db")
    monkeypatch.setattr(engine, "STATE_DIR", tmp_path)
    monkeypatch.setattr(engine, "ACCOUNTS_DIR", tmp_path / "accounts")
    monkeypatch.setattr(engine, "_write_auto_reply_diagnostic", lambda *args, **kwargs: None)
    engine.init_db()


def _contacts() -> list[dict]:
    with engine._connect() as conn:
        rows = conn.execute(
            "select contact_key, display_name, remark, wx_no, source from wechat_contacts "
            "where account_id=? order by contact_key",
            (ACCOUNT,),
        ).fetchall()
    return [dict(row) for row in rows]


def _seed(contact_key: str, display_name: str, wx_no: str = "", source: str = "pc_wechat_uia_contacts") -> None:
    engine._persist_contact(
        ACCOUNT,
        {
            "contact_key": contact_key,
            "display_name": display_name,
            "wxNo": wx_no,
            "source": source,
        },
    )


def test_scan_identity_reuses_saved_contact_mapping_without_reading_profile(monkeypatch, tmp_path):
    _use_temp_native_wechat_db(monkeypatch, tmp_path)
    _seed("余老师", "余老师", "laoshi2020")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("通讯录已经唯一命中，不应该再去读资料弹窗")

    monkeypatch.setattr(engine, "_read_current_private_chat_wx_no", fail_if_called)

    assert engine._resolve_scan_contact_wx_no(ACCOUNT, display_name="余老师") == (
        "laoshi2020",
        "local_contact_unique_mapping",
    )


def test_scan_identity_reads_profile_once_and_links_renamed_contact(monkeypatch, tmp_path):
    _use_temp_native_wechat_db(monkeypatch, tmp_path)
    _seed("余老师", "余老师", "laoshi2020")
    reads: list[str] = []

    def fake_profile(account_id, *, expected_display_name=""):
        reads.append(expected_display_name)
        return {"ok": True, "wx_no": "laoshi2020", "reason": "profile_popup"}

    monkeypatch.setattr(engine, "_read_current_private_chat_wx_no", fake_profile)

    # 用户在微信里把备注改成了"余老师工作号"：按新名字查不到，兜底读一次资料。
    assert engine._resolve_scan_contact_wx_no(ACCOUNT, display_name="余老师工作号") == (
        "laoshi2020",
        "profile_popup",
    )
    assert reads == ["余老师工作号"]

    # 读到的号回写进通讯录：新名字直接命中，旧名字也还能查到，且不重复建档。
    assert engine._resolve_unique_local_contact_wx_no(ACCOUNT, "余老师工作号") == "laoshi2020"
    assert engine._resolve_unique_local_contact_wx_no(ACCOUNT, "余老师") == "laoshi2020"
    assert engine._resolve_scan_contact_wx_no(ACCOUNT, display_name="余老师工作号") == (
        "laoshi2020",
        "local_contact_unique_mapping",
    )
    rows = [row for row in _contacts() if row["wx_no"] == "laoshi2020"]
    assert len(rows) == 1
    assert rows[0]["display_name"] == "余老师工作号"
    assert rows[0]["remark"] == "余老师"


def test_link_local_contact_wx_no_fills_missing_id(monkeypatch, tmp_path):
    _use_temp_native_wechat_db(monkeypatch, tmp_path)
    _seed("吴子圆（转账）", "吴子圆（转账）", source="wxauto4_session")

    linked = engine._link_local_contact_wx_no(ACCOUNT, "吴子圆（转账）", "wuziyuan123")

    assert linked["action"] == "wx_no_filled"
    assert engine._resolve_unique_local_contact_wx_no(ACCOUNT, "吴子圆（转账）") == "wuziyuan123"


def test_link_local_contact_wx_no_creates_unknown_contact(monkeypatch, tmp_path):
    _use_temp_native_wechat_db(monkeypatch, tmp_path)

    linked = engine._link_local_contact_wx_no(ACCOUNT, "张新客", "zhangxinke1")

    assert linked["action"] == "contact_created"
    assert engine._resolve_unique_local_contact_wx_no(ACCOUNT, "张新客") == "zhangxinke1"


def test_link_local_contact_wx_no_refuses_conflicting_id(monkeypatch, tmp_path):
    _use_temp_native_wechat_db(monkeypatch, tmp_path)
    _seed("老王", "老王", "laowang_old")

    linked = engine._link_local_contact_wx_no(ACCOUNT, "老王", "laowang_new")

    assert linked["action"] == "name_conflict"
    assert engine._resolve_local_contact_wx_no(ACCOUNT, "老王") == "laowang_old"


def test_link_local_contact_wx_no_is_idempotent(monkeypatch, tmp_path):
    _use_temp_native_wechat_db(monkeypatch, tmp_path)
    _seed("余老师", "余老师", "laoshi2020")

    first = engine._link_local_contact_wx_no(ACCOUNT, "余老师工作号", "laoshi2020")
    before = len(_contacts())
    second = engine._link_local_contact_wx_no(ACCOUNT, "余老师工作号", "laoshi2020")

    assert first["action"] == "renamed"
    assert second["action"] == "already_linked"
    assert len(_contacts()) == before
    assert engine._resolve_unique_local_contact_wx_no(ACCOUNT, "余老师") == "laoshi2020"


def test_link_local_contact_wx_no_replaces_junk_stored_id(monkeypatch, tmp_path):
    _use_temp_native_wechat_db(monkeypatch, tmp_path)
    # 线上真的出现过这种脏数据：值不是微信号的形状（长度/字符都不对），
    # 不能让它挡住重新读到正确号。
    _seed("澳洲专线物流", "澳洲专线物流", "lucy")

    linked = engine._link_local_contact_wx_no(ACCOUNT, "澳洲专线物流", "ID20010218")

    assert linked["action"] == "wx_no_filled"
    assert engine._resolve_unique_local_contact_wx_no(ACCOUNT, "澳洲专线物流") == "ID20010218"
