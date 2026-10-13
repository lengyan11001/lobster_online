"""启动期不再联网补齐运行依赖（2026-10-14）。

现象：start.bat 每次启动都跑完整 repair，缺 ffprobe 的客户机在启动时从 TOS 下载
      ffmpeg 整包（超时上限 20 分钟），机器卡到像死机。
要求：启动只做本地检查并提示「系统配置 → 修复运行依赖」；真正的下载只在界面点的时候发生。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

from backend.app.services import runtime_dependency_repair as repair  # noqa: E402


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


def test_start_bat_only_checks_dependencies():
    lines = [ln for ln in _read("start.bat").splitlines() if not ln.strip().upper().startswith("REM")]
    calls = [ln.strip() for ln in lines if "repair_runtime_dependencies.py\"" in ln and "%PYTHON%" in ln]
    assert calls, "start.bat 必须调用 repair_runtime_dependencies.py"
    assert all("--check-only" in ln for ln in calls), f"启动期只允许 --check-only，实际: {calls}"


def test_installers_skip_ffmpeg_download_when_flagged():
    for rel in ("install.bat", "install_slim.bat"):
        text = _read(rel)
        assert "LOBSTER_SKIP_FFMPEG_DOWNLOAD" in text, rel
        guard_at = text.index("LOBSTER_SKIP_FFMPEG_DOWNLOAD")
        download_at = text.index('"%~dp0scripts\\ensure_ffmpeg_windows.py"')
        assert guard_at < download_at, rel


def test_quick_start_scopes_the_flag():
    text = _read("一键启动.bat")
    assert 'set "LOBSTER_SKIP_FFMPEG_DOWNLOAD=1"' in text
    assert 'set "LOBSTER_SKIP_FFMPEG_DOWNLOAD="' in text


def test_status_reports_missing_without_downloading(monkeypatch):
    monkeypatch.setattr(repair, "_media_tools_missing", lambda: ["ffprobe"])
    monkeypatch.setattr(repair, "runtime_dependency_repair_needed", lambda: False)
    status = repair.runtime_dependency_status()
    assert status["ok"] is False
    assert status["missing"] == ["ffprobe"]
    assert "ffprobe" in status["message"]

    monkeypatch.setattr(repair, "_media_tools_missing", lambda: [])
    ok_status = repair.runtime_dependency_status()
    assert ok_status["ok"] is True
    assert ok_status["missing"] == []


def test_repair_media_tools_respects_startup_flag(monkeypatch):
    monkeypatch.setattr(repair, "_media_tools_missing", lambda: ["ffprobe"])
    monkeypatch.setenv("LOBSTER_SKIP_FFMPEG_DOWNLOAD", "1")
    called = []
    monkeypatch.setattr(repair, "_run", lambda *_a, **_k: called.append(1) or (0, ""))

    result = repair._repair_media_tools(600)

    assert called == [], "启动期不允许真的去下载"
    assert result["ok"] is False
    assert "修复运行依赖" in result["message"]


def test_check_only_entry_never_repairs(monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import repair_runtime_dependencies as entry  # noqa: E402

    monkeypatch.setattr(entry, "runtime_dependency_repair_needed", lambda: False)
    monkeypatch.setattr(
        "backend.app.services.runtime_dependency_repair.runtime_dependency_status",
        lambda: {"ok": False, "missing": ["ffprobe"], "message": "本机缺少：ffprobe", "repair_needed": False},
    )
    monkeypatch.setattr(sys, "argv", ["repair_runtime_dependencies.py", "--check-only"])
    assert entry.main() == 3

    monkeypatch.setattr(
        "backend.app.services.runtime_dependency_repair.runtime_dependency_status",
        lambda: {"ok": True, "missing": [], "message": "本机 ffmpeg/ffprobe 已就绪", "repair_needed": False},
    )
    assert entry.main() == 0

    # marker 过期（上次没跑完整 repair）不该导致启动报警：只看有没有真的缺工具
    monkeypatch.setattr(
        "backend.app.services.runtime_dependency_repair.runtime_dependency_status",
        lambda: {"ok": True, "missing": [], "message": "本机 ffmpeg/ffprobe 已就绪", "repair_needed": True},
    )
    assert entry.main() == 0
