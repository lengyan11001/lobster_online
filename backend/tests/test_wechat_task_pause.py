"""暂停（悬浮键 / Alt+R）必须真的拦得住自动化（2026-10-16 对照竞品 InputGuard 补齐）。

竞品（ai-bot-pc）：跑自动化时挂键盘钩子 + 屏幕状态条，Alt+R 暂停/恢复、Alt+Q 退出。
我们以前只有通讯录同步那两处长循环查标记：通过好友 / 加好友队列 / 批量发消息都不查，
用户按了暂停的感受就是"她还在控制，只能管客户端"。现在：
  1) 底层动作原语（点击/按键）前统一 _paused_guard()，任何循环都会立刻收手；
  2) _run_local_driver_operation 不再"新任务开始就清暂停标记"，而是暂停中直接拒绝启动；
  3) 悬浮键加全局热键 Alt+R（暂停/继续）、Alt+Q（停止并关闭）。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import native_wechat_engine as engine  # noqa: E402

ENGINE = ROOT / "backend" / "app" / "services" / "native_wechat_engine.py"
OVERLAY = ROOT / "scripts" / "task_overlay.py"


# ── 引擎侧 ────────────────────────────────────────────────────────────────
def test_paused_guard_raises_only_when_paused(monkeypatch):
    monkeypatch.setattr(engine, "_task_stop_requested", lambda: False)
    engine._paused_guard()  # 没暂停：放行
    monkeypatch.setattr(engine, "_task_stop_requested", lambda: True)
    with pytest.raises(engine.TaskPaused) as exc:
        engine._paused_guard()
    assert "已暂停" in str(exc.value)


def test_action_primitives_all_pass_the_pause_gate():
    src = ENGINE.read_text(encoding="utf-8")
    for fn in (
        "_uia_click",
        "_uia_click_screen_point",
        "_send_hotkey_quick",
        "_send_hotkey",
        "_key_down_fast",
        "_send_hotkey_fast",
    ):
        idx = src.index("def %s(" % fn)
        assert "_paused_guard()" in src[idx : idx + 1200], fn
    # _key_up_fast 是收尾（松开按住的键），必须还能跑，不能拦
    idx = src.index("def _key_up_fast(")
    assert "_paused_guard()" not in src[idx : idx + 400]


def test_pause_never_blocks_bringing_wechat_up():
    """2026-10-16 事故：_click_uia_node 也挂了闸门，点同步时连微信窗口都拉不起来/切不过去。

    切页、把窗口拉到前台是**导航**，不是对外动作 —— 暂停不该挡它。
    """
    src = ENGINE.read_text(encoding="utf-8")
    idx = src.index("def _click_uia_node(")
    # 只看"真的调用"（带注释的那种写法），注释里提到函数名不算
    assert "_paused_guard()  #" not in src[idx : idx + 600]


def _fake_control(monkeypatch, *, stopped: bool, calls: list):
    class _Ctl:
        @staticmethod
        def stop_requested():
            return stopped

        @staticmethod
        def clear_stop():
            calls.append("clear_stop")

        @staticmethod
        def begin_task(*args, **kwargs):
            calls.append("begin_task")
            return {}

        @staticmethod
        def end_task():
            calls.append("end_task")

        @staticmethod
        def heartbeat(*args, **kwargs):
            return {}

    monkeypatch.setattr(engine, "_task_control", lambda: _Ctl)
    monkeypatch.setattr(engine, "_prepare_local_automation_thread", lambda: None)


def test_driver_operation_refuses_to_start_while_paused(monkeypatch):
    calls: list = []
    _fake_control(monkeypatch, stopped=True, calls=calls)
    ran: list = []
    with pytest.raises(engine.TaskPaused):
        engine._run_local_driver_operation("acct", "同步通讯录", lambda: ran.append(1))
    assert not ran, "暂停中不能真的去操作微信"
    assert "clear_stop" not in calls, "不能再把用户按下的暂停悄悄清掉"
    assert "begin_task" not in calls


def test_driver_operation_runs_when_not_paused(monkeypatch):
    calls: list = []
    _fake_control(monkeypatch, stopped=False, calls=calls)
    result = engine._run_local_driver_operation("acct", "同步通讯录", lambda: {"ok": True})
    assert result == {"ok": True}
    assert "begin_task" in calls


def test_task_paused_is_not_retried_by_driver_recovery(monkeypatch):
    calls: list = []
    _fake_control(monkeypatch, stopped=False, calls=calls)
    monkeypatch.setattr(engine, "_latest_local_driver_recovery", lambda _acct: {})
    recovered: list = []
    monkeypatch.setattr(
        engine, "_recover_local_wechat_driver", lambda *a, **k: recovered.append(k) or {"ok": True}
    )

    def boom():
        raise engine.TaskPaused("已暂停")

    with pytest.raises(engine.TaskPaused):
        engine._run_local_driver_operation("acct", "发消息", boom)
    assert not recovered, "暂停不是故障：不该重建驱动、更不该重试"


# ── 悬浮键侧（抄竞品：Alt+R / Alt+Q）────────────────────────────────────────
def _load_overlay():
    spec = importlib.util.spec_from_file_location("lobster_task_overlay", OVERLAY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_overlay_registers_global_hotkeys():
    src = OVERLAY.read_text(encoding="utf-8")
    assert "RegisterHotKey" in src
    assert "_VK_R = 0x52" in src and "HOTKEY_ID_PAUSE" in src  # Alt+R = 暂停/继续
    assert "_VK_Q = 0x51" in src and "HOTKEY_ID_STOP" in src  # Alt+Q = 停止并关闭
    assert "Alt+R 暂停/继续 · Alt+Q 停止并关闭" in src
    assert "_start_hotkey_listener(api)" in src, "启动时必须真的把热键挂上"


def test_overlay_toggle_pause_and_stop(monkeypatch):
    overlay = _load_overlay()
    state = {"stopped": False, "running": True, "closed": 0}

    class _Ctl:
        @staticmethod
        def stop_requested():
            return state["stopped"]

        @staticmethod
        def request_stop(reason="overlay"):
            state["stopped"] = True
            return {"ok": True}

        @staticmethod
        def clear_stop():
            state["stopped"] = False
            return {"ok": True}

        @staticmethod
        def task_status():
            return {"running": state["running"], "stopped": state["stopped"], "label": "同步通讯录"}

        @staticmethod
        def overlay_status():
            return {"pid": 1}

        @staticmethod
        def overlay_heartbeat():
            return {}

        @staticmethod
        def stop_all(reason="overlay"):
            state["running"] = False
            return {"ok": True}

    api = overlay.OverlayApi(_Ctl, idle_seconds=8)
    api.attach(type("W", (), {"destroy": lambda self: state.__setitem__("closed", state["closed"] + 1)})())

    api.toggle_pause("hotkey")  # Alt+R：暂停
    assert state["stopped"] is True
    api.toggle_pause("hotkey")  # Alt+R：继续
    assert state["stopped"] is False
    api.stop_and_close("hotkey")  # Alt+Q：先停机再关窗
    assert state["stopped"] is True and state["closed"] == 1


# ── 暂停标记 TTL（2026-10-16 事故：15:01 按的暂停把 15:31 的同步整条掐死）──
def test_stop_flag_expires_after_ttl(monkeypatch, tmp_path):
    import os
    import time as _time

    from backend.app.services import task_control

    flag = tmp_path / "stop.flag"
    monkeypatch.setattr(task_control, "STOP_FLAG", flag, raising=False)

    flag.write_text('{"at": "2026-10-16T15:01:14", "reason": "overlay"}', encoding="utf-8")
    monkeypatch.setattr(task_control, "STOP_FLAG_TTL_SECONDS", 600, raising=False)
    assert task_control.stop_requested() is True, "新标记要生效"
    os.utime(flag, (_time.time() - 3600, _time.time() - 3600))
    assert task_control.stop_requested() is False, "过期标记必须自动作废，不能把产品锁死"
    assert not flag.exists(), "过期标记会被清掉"


def test_fresh_stop_flag_still_blocks(monkeypatch, tmp_path):
    from backend.app.services import task_control

    flag = tmp_path / "stop.flag"
    monkeypatch.setattr(task_control, "STOP_FLAG", flag, raising=False)
    flag.write_text("{}", encoding="utf-8")
    assert task_control.stop_requested() is True
