import json
import sys
from types import SimpleNamespace

from desktop import launcher


def test_service_command_ownership_requires_exact_root():
    assert launcher.command_line_looks_this_root_service(
        r'"D:\lobster_online\python\python.exe" backend\run.py'
    )
    assert launcher.command_line_looks_this_root_service(
        r'D:\lobster_online\nodejs\node.exe D:\lobster_online\nodejs\node_modules\openclaw\openclaw.mjs gateway --port 18789'
    )
    assert launcher.command_line_looks_this_root_service(
        r'C:\Windows\system32\cmd.exe /c ""D:\lobster_online\start.bat" "'
    )
    assert not launcher.command_line_looks_this_root_service(
        r'"D:\lobster_online_backup\python\python.exe" backend\run.py'
    )
    assert not launcher.command_line_looks_this_root_service(
        r'"C:\other\python.exe" -m http.server 8000'
    )


def test_cleanup_stops_only_orphaned_same_root_services(monkeypatch):
    rows = [
        {"ProcessId": 101, "CommandLine": r'"D:\lobster_online\python\python.exe" backend\run.py'},
        {"ProcessId": 102, "CommandLine": r'D:\lobster_online\nodejs\node.exe D:\lobster_online\nodejs\node_modules\openclaw\openclaw.mjs gateway --port 18789'},
        {"ProcessId": 103, "CommandLine": r'"D:\lobster_online_backup\python\python.exe" backend\run.py'},
        {"ProcessId": 104, "CommandLine": r'"C:\other\python.exe" -m http.server 8000'},
    ]
    killed = []
    scans = iter((json.dumps(rows), "[]"))

    monkeypatch.setattr(launcher.subprocess, "check_output", lambda *args, **kwargs: next(scans))
    monkeypatch.setattr(launcher, "kill_pid_tree", lambda pid, name: killed.append((pid, name)))
    monkeypatch.setattr(launcher, "netstat_listening_pids", lambda port: set())
    monkeypatch.setattr(launcher, "wait_port_closed", lambda port, seconds=0: True)

    launcher.cleanup_owned_services(ports=(8000, 8001, 18789))
    launcher.cleanup_owned_services(ports=(8000, 8001, 18789))

    assert [pid for pid, _name in killed] == [101, 102]


def test_run_window_always_cleans_services(monkeypatch):
    cleanup_calls = []

    class ClosingEvent:
        def __init__(self):
            self.handlers = []

        def __iadd__(self, handler):
            self.handlers.append(handler)
            return self

    window = SimpleNamespace(
        events=SimpleNamespace(closing=ClosingEvent()),
        load_url=lambda _url: None,
        load_html=lambda *_args, **_kwargs: None,
    )
    fake_webview = SimpleNamespace(
        settings={},
        create_window=lambda *_args, **_kwargs: window,
        start=lambda callback, args, **_kwargs: callback(*args),
    )

    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.setattr(launcher, "webview2_runtime_available", lambda: True)
    monkeypatch.setattr(launcher, "ensure_desktop_runtime", lambda _env: True)
    monkeypatch.setattr(launcher, "find_fixed_webview2_runtime", lambda: None)
    monkeypatch.setattr(
        launcher,
        "start_services_blocking",
        lambda *_args, **_kwargs: (True, "http://127.0.0.1:8000", None, None, ""),
    )
    monkeypatch.setattr(
        launcher,
        "cleanup_owned_services",
        lambda backend=None, mcp=None, *, ports=(): cleanup_calls.append((backend, mcp, ports)),
    )

    ok, _backend, _mcp = launcher.run_window(
        "http://127.0.0.1:8000",
        "Lobster",
        1200,
        800,
        8000,
        8001,
        {},
        5,
    )

    assert ok is True
    assert cleanup_calls == [(None, None, (8000, 8001, 18789))]
