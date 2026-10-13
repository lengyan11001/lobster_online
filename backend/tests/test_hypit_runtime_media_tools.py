"""视频复刻「安装运行依赖」必须同时补齐 ffmpeg/ffprobe。

背景：hypit_local 的依赖自检里本来就查 ffmpeg/ffprobe 两项，但 _run_runtime_install
原先只装 npm/python 依赖，缺 ffprobe 的机器点多少次「安装运行依赖」都补不上，
最后自检只报「缺少 ffprobe」。这里锁住修复：安装流程必须调用统一的音视频工具修复。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.api import hypit_local  # noqa: E402
from backend.app.services import runtime_dependency_repair as repair_api  # noqa: E402


def test_repair_media_tools_public_wrapper(monkeypatch):
    seen = {}

    def fake(timeout):
        seen["timeout"] = timeout
        return {"ok": True, "missing": [], "message": "本机 ffmpeg/ffprobe 已就绪", "log": ""}

    monkeypatch.setattr(repair_api, "_repair_media_tools", fake)
    out = repair_api.repair_media_tools(120)
    assert out["ok"] is True
    assert seen["timeout"] == 120


def test_install_runtime_repairs_ffmpeg_ffprobe(monkeypatch):
    calls = {"media": 0, "log": [], "stages": []}

    def fake_media(timeout=0):
        calls["media"] += 1
        calls["media_timeout"] = timeout
        return {"ok": True, "missing": [], "message": "已补齐 deps/ffmpeg：ffprobe", "log": ""}

    monkeypatch.setattr(hypit_local, "repair_media_tools", fake_media)
    monkeypatch.setattr(hypit_local, "_hypit_root", lambda: pathlib.Path("."))
    monkeypatch.setattr(hypit_local, "_node_executable", lambda: "node")
    monkeypatch.setattr(hypit_local, "_npm_executable", lambda: "npm")
    monkeypatch.setattr(hypit_local, "_installed_chrome_path", lambda: "chrome")
    monkeypatch.setattr(hypit_local, "_install_hypit_node_modules", lambda root: None)
    monkeypatch.setattr(hypit_local, "_ensure_uv", lambda: None)
    monkeypatch.setattr(hypit_local, "_stream_hypit_command", lambda *a, **k: None)
    monkeypatch.setattr(hypit_local, "_configure_runtime_browser", lambda ws: None)
    monkeypatch.setattr(hypit_local, "_install_hypit_package", lambda *a, **k: None)
    monkeypatch.setattr(
        hypit_local, "_dependency_status",
        lambda: {"ready": True, "missing": [], "error": "", "checks": []},
    )
    monkeypatch.setattr(hypit_local, "_append_runtime_log", lambda text: calls["log"].append(str(text)))
    monkeypatch.setattr(
        hypit_local, "_write_runtime_state",
        lambda **kw: (calls["stages"].append(str(kw.get("stage") or kw.get("status"))), {})[1],
    )

    asyncio.run(hypit_local._run_runtime_install())

    assert calls["media"] == 1, "安装运行依赖时必须调用音视频工具修复（否则 ffprobe 永远补不上）"
    assert calls["media_timeout"] == 900
    assert any("音视频工具" in s for s in calls["stages"]), calls["stages"]
    assert any("音视频工具" in s for s in calls["log"]), calls["log"]
