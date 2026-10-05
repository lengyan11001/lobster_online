"""守护进程健康探针别把 backend.log 刷满。

2026-10-05 用户机器 backend.log：25,415 条请求里 22,754 条是 /api/health?fast=1
（89.5%，约每 2.4 秒一条），看着像"一直在刷新"。探活不需要每次记访问日志。
"""
from __future__ import annotations

import logging
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _access_record(path: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:12345", "GET", path, "1.1", 200),
        exc_info=None,
    )


def test_health_probe_access_log_filter_is_installed():
    from backend import run as backend_run

    access = logging.getLogger("uvicorn.access")
    assert any(isinstance(item, backend_run._SuppressHealthProbeAccessFilter) for item in access.filters)


def test_health_probe_records_are_dropped_but_real_requests_kept():
    from backend import run as backend_run

    flt = backend_run._SuppressHealthProbeAccessFilter()

    assert flt.filter(_access_record("/api/health?fast=1")) is False
    assert flt.filter(_access_record("/api/health?fast=1&ts=1")) is False
    # 前端网络恢复探针（recovery_probe）要保留，出问题时能看到
    assert flt.filter(_access_record("/api/health?fast=1&recovery_probe=1")) is True
    # 普通健康检查、其它接口不受影响
    assert flt.filter(_access_record("/api/health")) is True
    assert flt.filter(_access_record("/api/scheduled-tasks/runs?limit=1")) is True


def test_backend_watchdog_interval_is_relaxed():
    source = (ROOT / "desktop" / "launcher.py").read_text(encoding="utf-8")
    assert "stop_event.wait(5.0)" in source
    assert "stop_event.wait(2.0)" not in source
