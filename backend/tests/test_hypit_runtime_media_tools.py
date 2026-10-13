"""视频复刻「安装运行依赖」的回归：既要在装依赖时补齐 ffmpeg/ffprobe，也要能重复点击。

背景：
- hypit_local 的依赖自检里本来就查 ffmpeg/ffprobe 两项，但 _run_runtime_install 原先只装
  npm/发行包/uv 依赖，缺 ffprobe 的机器点多少次「安装运行依赖」都补不上。
- 客户机第二次点「安装运行依赖」时，hypit runtime init 会抛
  CLI_ERROR「Runtime Profile already exists」，整条安装直接断掉。
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.api import hypit_local  # noqa: E402
from backend.app.services import runtime_dependency_repair as repair_api  # noqa: E402


def _load_ensure_script():
    script = ROOT / "scripts" / "ensure_ffmpeg_windows.py"
    spec = importlib.util.spec_from_file_location("_lobster_ensure_ffmpeg_multi", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_repair_media_tools_public_wrapper(monkeypatch):
    seen = {}

    def fake(timeout):
        seen["timeout"] = timeout
        return {"ok": True, "missing": [], "message": "本机 ffmpeg/ffprobe 已就绪", "log": ""}

    monkeypatch.setattr(repair_api, "_repair_media_tools", fake)
    out = repair_api.repair_media_tools(120)
    assert out["ok"] is True
    assert seen["timeout"] == 120


def test_media_repair_failure_message_carries_reason(monkeypatch):
    """下载/解压失败时必须把原因带进 message，否则界面只有「仍缺 ffprobe」。"""
    missing = {"value": ["ffprobe"]}
    monkeypatch.setattr(repair_api, "_media_tools_missing", lambda: list(missing["value"]))
    monkeypatch.setattr(repair_api, "_run", lambda cmd, timeout: (1, "==> 下载: https://x\nERROR: curl 下载失败 (exit 28)\n"))
    monkeypatch.setattr(repair_api.os, "name", "nt")
    monkeypatch.setattr(repair_api.Path, "is_file", lambda self: True, raising=False)

    result = repair_api._repair_media_tools(300)

    assert result["ok"] is False
    assert "原因" in result["message"], result["message"]
    assert "curl" in result["message"], result["message"]


def test_candidate_zip_urls_env_and_defaults(monkeypatch):
    module = _load_ensure_script()
    monkeypatch.delenv("LOBSTER_FFMPEG_WIN64_ZIP", raising=False)
    urls = module._candidate_zip_urls()
    assert len(urls) >= 2, "至少要有两个候选源，第一个拉不动时能换源"
    assert "tos-cn-guangzhou" in urls[0], "自家 TOS 副本必须排第一（客户端国内直连）"
    assert any("github.com/BtbN" in u for u in urls[1:]), "GitHub 仍要保留作兜底"
    monkeypatch.setenv("LOBSTER_FFMPEG_WIN64_ZIP", "https://a/x.zip, https://b/y.zip")
    assert module._candidate_zip_urls() == ["https://a/x.zip", "https://b/y.zip"]


def _stub_install(monkeypatch, workspace):
    calls = {"commands": [], "log": [], "stages": [], "media": 0}

    def fake_media(timeout=0):
        calls["media"] += 1
        calls["media_timeout"] = timeout
        return {"ok": True, "missing": [], "message": "已补齐 deps/ffmpeg：ffprobe", "log": ""}

    monkeypatch.setattr(hypit_local, "repair_media_tools", fake_media)
    monkeypatch.setattr(hypit_local, "RUNTIME_WORKSPACE_DIR", workspace)
    monkeypatch.setattr(hypit_local, "_hypit_root", lambda: pathlib.Path("."))
    monkeypatch.setattr(hypit_local, "_node_executable", lambda: "node")
    monkeypatch.setattr(hypit_local, "_npm_executable", lambda: "npm")
    monkeypatch.setattr(hypit_local, "_installed_chrome_path", lambda: "chrome")
    monkeypatch.setattr(hypit_local, "_install_hypit_node_modules", lambda root: None)
    monkeypatch.setattr(hypit_local, "_ensure_uv", lambda: None)
    monkeypatch.setattr(
        hypit_local, "_stream_hypit_command",
        lambda root, node, args, cwd: calls["commands"].append(list(args)),
    )
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
    return calls


def test_install_runtime_repairs_ffmpeg_ffprobe(monkeypatch, tmp_path):
    calls = _stub_install(monkeypatch, tmp_path / "_runtime")

    asyncio.run(hypit_local._run_runtime_install())

    assert calls["media"] == 1, "安装运行依赖时必须调用音视频工具修复（否则 ffprobe 永远补不上）"
    assert calls["media_timeout"] == 2700, "补 200MB 的二进制的下载要给足时间"
    assert any("音视频工具" in s for s in calls["stages"]), calls["stages"]
    assert any("音视频工具" in s for s in calls["log"]), calls["log"]


def test_install_skips_runtime_init_when_already_selected(monkeypatch, tmp_path):
    """选择指针已存在（正常重复点击）时不能再调 runtime init，否则 CLI 直接报 already exists。"""
    workspace = tmp_path / "_runtime"
    (workspace / ".hypit").mkdir(parents=True)
    (workspace / ".hypit" / "runtime").write_text("{}", encoding="utf-8")
    calls = _stub_install(monkeypatch, workspace)

    asyncio.run(hypit_local._run_runtime_install())

    verbs = [cmd[:2] for cmd in calls["commands"]]
    assert ["runtime", "init"] not in verbs, calls["commands"]
    assert any("跳过 runtime init" in s for s in calls["log"]), calls["log"]


def test_install_uses_runtime_use_when_only_profile_exists(monkeypatch, tmp_path):
    workspace = tmp_path / "_runtime"
    workspace.mkdir(parents=True)
    (workspace / "hypit.runtime.json").write_text("{}", encoding="utf-8")
    calls = _stub_install(monkeypatch, workspace)

    asyncio.run(hypit_local._run_runtime_install())

    used = [cmd for cmd in calls["commands"] if cmd[:2] == ["runtime", "use"]]
    assert used, calls["commands"]
    assert str(workspace / "hypit.runtime.json") in used[0]
