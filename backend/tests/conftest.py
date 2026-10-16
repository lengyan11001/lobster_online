"""测试隔离：别让本机真实的「暂停」标记污染用例（2026-10-16）。

悬浮键 / Alt+R 会在 data/task_control/stop.flag 落一个真实标记；引擎的 _paused_guard()
会据此拒绝一切自动化动作 —— 这对生产是对的，但会让"本机恰好按过暂停"时的用例集体失败。
所以每个用例前后把标记保存 / 清掉 / 还原。
"""
from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FLAG = ROOT / "data" / "task_control" / "stop.flag"


@pytest.fixture(autouse=True)
def _isolate_task_stop_flag():
    saved = FLAG.read_bytes() if FLAG.exists() else None
    if FLAG.exists():
        FLAG.unlink()
    try:
        yield
    finally:
        try:
            if saved is not None:
                FLAG.parent.mkdir(parents=True, exist_ok=True)
                FLAG.write_bytes(saved)
            elif FLAG.exists():
                FLAG.unlink()
        except Exception:
            pass
