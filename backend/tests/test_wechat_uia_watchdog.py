"""UIA 常驻执行器 + 卡死看门狗（2026-10-16，抄竞品 ai-bot-pc 的 _UIAExecutor）。

背景：15:00:52 那次同步卡在 UIA/COM 里 5 分 44 秒零日志、按暂停也没反应。
竞品的做法：所有 UIA 调用跑在常驻执行线程上，单次调用超时就报
「微信界面自动化调用超时（N s 无响应）」并「执行器线程已重建」。
我们照抄语义：常驻执行线程 + 「多久没动作」的看门狗 + 纪元闸门拦僵尸线程。
"""
from __future__ import annotations

import pathlib
import sys
import threading
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import native_wechat_engine as engine  # noqa: E402

ENGINE = ROOT / "backend" / "app" / "services" / "native_wechat_engine.py"


@pytest.fixture(autouse=True)
def _clean_executor(monkeypatch):
    """每个用例都从"干净的执行器"开始，别把线程/进度状态带到别的用例。"""
    monkeypatch.setattr(engine, "_UI_EXECUTOR_STATE", {"epoch": 0, "executor": None}, raising=False)
    monkeypatch.setattr(engine, "_UI_PROGRESS", {"at": 0.0, "what": ""}, raising=False)
    yield
    engine._UI_EXECUTOR_STATE["executor"] = None


def test_stalled_operation_is_aborted_and_executor_rebuilt(monkeypatch):
    monkeypatch.setattr(engine, "UIA_STALL_TIMEOUT_SECONDS", 1.0, raising=False)

    def stuck():
        time.sleep(30)  # 全程没有任何动作心跳 —— 就是今天那次"卡死"

    started = time.time()
    with pytest.raises(RuntimeError) as exc:
        engine._call_in_ui_executor(stuck, operation="同步通讯录", account_id="acct")
    assert time.time() - started < 15, "看门狗要在超时后很快放行调用方"
    message = str(exc.value)
    assert "超时" in message and "没有任何动作" in message
    assert "重建执行器" in message
    assert int(engine._UI_EXECUTOR_STATE["epoch"]) == 1, "卡死要 +1 纪元"
    assert engine._UI_EXECUTOR_STATE["executor"] is None, "卡住的执行器要被丢掉（下次自动重建）"


def test_active_operation_is_not_aborted(monkeypatch):
    monkeypatch.setattr(engine, "UIA_STALL_TIMEOUT_SECONDS", 1.0, raising=False)

    def busy():
        for _ in range(12):
            engine._ui_progress_tick("action")
            time.sleep(0.2)
        return "ok"

    assert engine._call_in_ui_executor(busy, operation="同步通讯录") == "ok"
    assert int(engine._UI_EXECUTOR_STATE["epoch"]) == 0, "一直在动就不该被判卡死"


def test_executor_thread_is_reused_across_operations():
    """wxauto4/UIA 句柄线程绑定：所有操作都该跑在同一个常驻线程上，不能每单换线程。"""
    seen = []

    def where():
        seen.append(threading.get_ident())

    engine._call_in_ui_executor(where, operation="op1")
    engine._call_in_ui_executor(where, operation="op2")
    assert len(set(seen)) == 1, seen
    assert seen[0] != threading.get_ident(), "不能跑在调用方线程上（调用方只负责等 + 盯卡死）"


def test_errors_propagate_back_from_the_executor():
    class Boom(RuntimeError):
        pass

    def boom():
        raise Boom("boom")

    with pytest.raises(Boom):
        engine._call_in_ui_executor(boom, operation="op")


def test_abandoned_epoch_blocks_zombie_actions(monkeypatch):
    """卡超时被放弃的老线程，下次想动微信必须被 _epoch_guard() 拦住。"""
    monkeypatch.setattr(engine, "_UI_EXECUTOR_STATE", {"epoch": 7, "executor": None}, raising=False)
    # monkeypatch 负责用例结束时还原这个 thread-local 属性，别自己 del
    monkeypatch.setattr(engine._LOCAL_WECHAT_THREAD_STATE, "epoch", 3, raising=False)
    with pytest.raises(engine.TaskAbandoned):
        engine._epoch_guard()
    engine._LOCAL_WECHAT_THREAD_STATE.epoch = 7
    engine._epoch_guard()  # 当代线程放行



def test_action_primitives_check_the_epoch():
    src = ENGINE.read_text(encoding="utf-8")
    for fn in (
        "_uia_click",
        "_uia_click_screen_point",
        "_send_hotkey",
        "_send_hotkey_quick",
        "_key_down_fast",
        "_send_hotkey_fast",
        "_click_uia_node",  # 导航动作：不过暂停闸门，但僵尸线程依然不许点
    ):
        idx = src.index("def %s(" % fn)
        assert "_epoch_guard()" in src[idx : idx + 1200], fn
