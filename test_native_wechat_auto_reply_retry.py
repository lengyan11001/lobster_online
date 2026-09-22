"""接管两条新规则（2026-09-22 用户口径）：

1. 客户本人就是配置的"群邀请主联系人"时：不生成、不回复（直接跳过）。
2. 上一轮已生成但没发出去的回复：同一条入站消息下一轮直接复用，跳过 AI 生成、只补发。
"""

import pytest

from backend.app.services import native_wechat_engine as engine


def _temp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "engine.db")
    engine.init_db()
    return tmp_path / "engine.db"


def test_peer_is_group_primary_contact_matches():
    # 配置的就是这个客户的微信号（线上事故：xkcmwu 是客户 Wendy 本人）
    assert engine._auto_reply_peer_is_group_primary_contact("xkcmwu", "", "xkcmwu", "Wendy") is True
    # 大小写/空格不敏感
    assert engine._auto_reply_peer_is_group_primary_contact(" XKCMWU ", "", "xkcmwu", "Wendy") is True
    # 客户不是主联系人 → 正常接管
    assert engine._auto_reply_peer_is_group_primary_contact("wu_manager", "", "xkcmwu", "Wendy") is False
    # 配置成昵称、当前会话 peer 就是该昵称
    assert engine._auto_reply_peer_is_group_primary_contact("吴经理", "", "wxid_wu", "吴经理") is True
    # 配置成昵称、解析出 wx no 后仍命中
    assert engine._auto_reply_peer_is_group_primary_contact("吴经理", "wxid_wu", "wxid_wu", "吴经理") is True
    # 没有任何配置 → 不跳过
    assert engine._auto_reply_peer_is_group_primary_contact("", "", "xkcmwu", "Wendy") is False


def _insert_history(peer_id: str, inbound: dict, *, reply: str, status: str) -> None:
    with engine._connect() as conn:
        conn.execute(
            """
            insert into wechat_auto_reply_history(
                id, account_id, peer_id, inbound_message_id, inbound_content,
                reply_content, category, status, error_message, created_at, updated_at
            ) values(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "row-1",
                "pc-wechat-default",
                peer_id,
                engine._auto_reply_inbound_id(peer_id, inbound),
                str(inbound.get("content") or ""),
                reply,
                "cooperation",
                status,
                "发送未确认",
                "2026-09-22T10:00:00",
                "2026-09-22T10:00:00",
            ),
        )
        conn.commit()


def test_cached_reply_is_reused_after_failed_send(monkeypatch, tmp_path):
    _temp_db(monkeypatch, tmp_path)
    peer = "xkcmwu"
    inbound = {"provider_message_id": "wxhash:abc", "content": "你在吗"}
    _insert_history(peer, inbound, reply="在的，马上安排吴经理对接", status="failed")

    cached = engine._cached_auto_reply_reply("pc-wechat-default", peer, inbound)

    assert cached["reply"] == "在的，马上安排吴经理对接"
    assert cached["should_reply"] is True
    assert cached["should_invite_group"] is False
    assert cached["previous_status"] == "failed"
    assert cached["reused_from_history"] is True


def test_cached_reply_not_reused_when_already_sent(monkeypatch, tmp_path):
    _temp_db(monkeypatch, tmp_path)
    peer = "xkcmwu"
    inbound = {"provider_message_id": "wxhash:abc", "content": "你在吗"}
    _insert_history(peer, inbound, reply="已经发过的文案", status="sent")

    assert engine._cached_auto_reply_reply("pc-wechat-default", peer, inbound) == {}


def test_cached_reply_not_reused_without_text(monkeypatch, tmp_path):
    _temp_db(monkeypatch, tmp_path)
    peer = "xkcmwu"
    inbound = {"provider_message_id": "wxhash:abc", "content": "你在吗"}
    # 被抑制（reply 为空）的行没有可复用内容
    _insert_history(peer, inbound, reply="", status="failed")

    assert engine._cached_auto_reply_reply("pc-wechat-default", peer, inbound) == {}


def test_cached_reply_keyed_by_inbound_message(monkeypatch, tmp_path):
    """客户换了一句话（inbound id 变了）就不复用旧文案，交给 AI 重新生成。"""
    _temp_db(monkeypatch, tmp_path)
    peer = "xkcmwu"
    old_inbound = {"provider_message_id": "wxhash:old", "content": "你在吗"}
    new_inbound = {"provider_message_id": "wxhash:new", "content": "十亩地怎么入驻？"}
    _insert_history(peer, old_inbound, reply="旧文案", status="failed")

    assert engine._cached_auto_reply_reply("pc-wechat-default", peer, new_inbound) == {}
