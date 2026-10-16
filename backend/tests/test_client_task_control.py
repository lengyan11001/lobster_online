"""全局暂停控制（悬浮键）的本地逻辑：标记文件、运行状态、悬浮键命令行。

真机行为（pywebview 置顶小窗、点一下能不能停住同步）要在客户端里验；
这里钉的是"不依赖 GUI / 不依赖登录态"的那一半。
"""
from __future__ import annotations

import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import task_control  # noqa: E402

ENGINE_PATH = ROOT / "backend" / "app" / "services" / "native_wechat_engine.py"


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(task_control, "STATE_DIR", tmp_path)
    monkeypatch.setattr(task_control, "STOP_FLAG", tmp_path / "stop.flag")
    monkeypatch.setattr(task_control, "STATUS_FILE", tmp_path / "status.json")
    monkeypatch.setattr(task_control, "OVERLAY_PID_FILE", tmp_path / "overlay.pid")
    task_control.clear_stop()
    yield
    task_control.clear_stop()


def test_stop_flag_round_trip():
    assert task_control.stop_requested() is False
    task_control.request_stop("test")
    assert task_control.stop_requested() is True
    assert task_control.stop_payload().get("reason") == "test"
    task_control.clear_stop()
    assert task_control.stop_requested() is False


def test_task_status_reports_running_and_elapsed():
    task_control.begin_task("wechat", "同步通讯录", "acct-1")
    status = task_control.task_status()
    assert status["running"] is True
    assert status["kind"] == "wechat"
    assert status["label"] == "同步通讯录"
    assert status["elapsed_seconds"] >= 0
    task_control.end_task()
    assert task_control.task_status()["running"] is False


def test_task_status_carries_the_stop_flag():
    """悬浮键靠这个显示"已请求暂停"。"""
    task_control.begin_task("wechat", "同步通讯录")
    task_control.request_stop("overlay")
    assert task_control.task_status()["stopped"] is True


def test_overlay_command_uses_the_running_python_and_the_script():
    argv = task_control.overlay_command(idle_seconds=45)
    assert argv[0] == sys.executable, "必须用当前（客户端自带）的 python 拉起悬浮键"
    assert argv[1].endswith("task_overlay.py")
    assert "--idle-seconds" in argv and "45" in argv


def test_overlay_script_exists_and_declares_on_top_window():
    script = ROOT / "scripts" / "task_overlay.py"
    assert script.exists()
    text = script.read_text(encoding="utf-8")
    assert "on_top=True" in text, "悬浮键必须置顶，否则被微信窗口盖住就点不到"
    assert "request_stop" in text, "点一下要写暂停标记"
    assert "task_status" in text, "要显示谁在跑"


def test_overlay_status_without_process_is_not_running():
    status = task_control.overlay_status()
    assert status["running"] is False
    assert status["script_exists"] is True


def test_stop_all_sets_the_flag_even_if_engines_fail(monkeypatch):
    """各引擎没有就绪时，至少标记要置位（循环靠它收尾）。"""
    monkeypatch.setattr(task_control, "end_task", task_control.end_task)
    result = task_control.stop_all("test")
    assert task_control.stop_requested() is True
    assert "engines" in result


def test_wechat_loops_consult_the_stop_flag():
    """三个循环都必须查暂停标记：键盘扫描、老点击路径、以及任务包装器。"""
    source = ENGINE_PATH.read_text(encoding="utf-8")
    assert "def _task_stop_requested()" in source
    assert "def _task_control()" in source
    keyboard = source.split("def _contact_sync_keyboard_scan(")[1].split("def _sync_local_contacts_from_uia(")[0]
    assert "_task_stop_requested()" in keyboard, "键盘走列表要每步可中断"
    assert 'stage="contacts_keyboard_stopped"' in keyboard
    legacy = source.split("def _sync_local_contacts_from_uia(")[1]
    assert 'stage="contacts_stopped_by_user"' in legacy, "老点击路径也要能被暂停"
    wrapper = source.split("def _run_local_driver_operation(")[1].split("def _new_wxauto4_client(")[0]
    assert "begin_task(" in wrapper and "end_task()" in wrapper, "任务包装器要报「谁在跑」"


def test_overlay_is_autostarted_only_for_long_tasks(monkeypatch):
    """跑够 6 秒还在跑才拉起悬浮键 —— 秒级操作（发一条消息）不该弹窗。"""
    monkeypatch.setattr(task_control, "OVERLAY_AUTOSTART_AFTER_SECONDS", 0.2)
    spawned = []
    monkeypatch.setattr(task_control, "ensure_overlay", lambda **kw: spawned.append(kw) or {"running": True})

    task_control.begin_task("wechat", "同步通讯录")
    time.sleep(1.0)
    assert spawned, "长任务要自动拉起悬浮键"

    spawned.clear()
    task_control.begin_task("wechat", "发一条消息")
    task_control.end_task()          # 0.2 秒内就结束了
    time.sleep(1.0)
    assert not spawned, "秒级操作不该弹悬浮键"
