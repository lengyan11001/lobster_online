"""微信加好友的频率口径与失败分类（2026-10-16 对照鹰牛 pyweixin 收敛）。

鹰牛原文（FriendSettings.add_new_friend / Contacts.check_new_friends 的 docstring）：
  · 主动加：「该方法用来添加新朋友,不建议频繁使用,会封号!」
  · 通过验证：「limit:单次≤8人,每日≤4次,间隔≥2小时,无论新老号超过这个频率大概率封号」
我们原来是「每 2 条歇 10 分钟、日限 20」，而且**通过好友申请这条链完全没有限速**。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import native_wechat_engine as engine  # noqa: E402

ENGINE_PATH = ROOT / "backend" / "app" / "services" / "native_wechat_engine.py"


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "STATE_DIR", tmp_path)
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "engine.db")
    return tmp_path


def test_active_friend_add_rate_matches_their_numbers():
    strategy = engine.get_strategy()
    assert int(strategy["daily_friend_add_limit"]) == 8, "照鹰牛：单次≤8、日≤4 次的口径"
    assert int(strategy["friend_add_batch_size"]) == 8
    assert int(strategy["friend_add_batch_sleep"]) >= 7200, "批与批之间至少 2 小时"


def test_accept_request_rate_constants():
    assert engine.FRIEND_ACCEPT_MAX_PER_BATCH == 8
    assert engine.FRIEND_ACCEPT_DAILY_BATCHES == 4
    assert engine.FRIEND_ACCEPT_MIN_GAP_SECONDS == 7200


def test_accept_rate_state_empty_is_not_blocked(temp_db):
    engine.init_db()
    state = engine._friend_accept_rate_state("acct")
    assert state["blocked"] is False
    assert state["batches_today"] == 0


def test_first_batch_then_blocked_for_two_hours(temp_db):
    engine.init_db()
    engine._record_friend_accept_batch("acct", 5)
    state = engine._friend_accept_rate_state("acct")
    assert state["blocked"] is True
    assert "分钟" in state["reason"]


def test_fourth_batch_hits_the_daily_cap(temp_db):
    engine.init_db()
    for _ in range(4):
        engine._record_friend_accept_batch("acct", 2)
    state = engine._friend_accept_rate_state("acct")
    assert state["batches_today"] == 4
    assert state["blocked"] is True
    assert "上限" in state["reason"]


def test_gate_returns_skipped_not_raise(temp_db, monkeypatch):
    """被闸门挡住时不能抛错：自动回复循环要照常跑，只是这一轮不通过申请。"""
    engine.init_db()
    engine._record_friend_accept_batch("acct", 1)
    called = []
    monkeypatch.setattr(engine, "_run_local_driver_operation",
                        lambda *a, **k: called.append(a) or {"ok": True})
    result = engine.accept_local_friend_requests("acct", max_accepts=20, max_scrolls=1)
    assert result["ok"] is True and result["skipped"] is True
    assert result["reason"] == "rate_limited"
    assert not called, "被限速时不该真的去操作微信"


def test_max_accepts_is_clamped_to_eight(temp_db, monkeypatch):
    engine.init_db()
    seen = {}
    # 让假的 driver 真的去调 callback，否则 clamped 参数根本走不到被调函数
    monkeypatch.setattr(engine, "_run_local_driver_operation",
                        lambda account_id, op, cb, **kw: cb())
    monkeypatch.setattr(engine, "_accept_local_friend_requests_once",
                        lambda account_id, **kw: seen.update(kw) or {"ok": True, "accepted": 0})
    engine.accept_local_friend_requests("acct", max_accepts=50, max_scrolls=1)
    assert seen.get("max_accepts") == engine.FRIEND_ACCEPT_MAX_PER_BATCH


def test_failure_is_classified_into_three_kinds():
    """照鹰牛的三态判定：搜不到 / 已是好友 / 对方不允许被添加。"""
    source = ENGINE_PATH.read_text(encoding="utf-8")
    assert "class FriendSearchNotFound(" in source
    assert "class FriendAddBlocked(" in source
    assert "class FriendAlreadyAdded(" in source
    assert "def _classify_local_add_friend_failure(" in source
    assert "三次搜索都没出结果，可能未搜到用户、已是好友或微信限制" not in source, "泛化报错要换成分类"
    assert "未找到搜索结果里的添加入口，可能未搜到用户、已是好友或微信限制" not in source


def test_apply_permission_defaults_to_chat_only():
    """照鹰牛 add_new_friend(chat_only) 的默认：一加好友别就给陌生人开朋友圈。"""
    source = ENGINE_PATH.read_text(encoding="utf-8")
    assert 'permission: str = "仅聊天",' in source
    assert 'permission: str = "朋友圈",' not in source
    api = (ROOT / "backend" / "app" / "api" / "native_wechat.py").read_text(encoding="utf-8")
    assert 'permission: str = Field(default="仅聊天"' in api
