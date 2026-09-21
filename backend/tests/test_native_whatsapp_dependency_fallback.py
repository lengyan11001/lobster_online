"""WhatsApp 接管：没有 uiautomation / psutil / pyperclip 时的兜底验证（2026-09-21）。"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

# 背景：diag_20260921073448_831a0dad 里客户机 status.dependencies 全 false，
# UIA 控件不可用 → 登录状态永远“未登录”；进程名/路径为空 → 候选窗口混进系统窗口。
# 这里锁住三件事：随包内置副本、ctypes 进程枚举、剪贴板多级兜底。
ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import native_whatsapp_engine as engine  # noqa: E402
from backend.app.services import win_process_scan as scan  # noqa: E402


def _read_clipboard() -> str:
    import win32clipboard  # type: ignore
    import win32con  # type: ignore

    win32clipboard.OpenClipboard()
    try:
        if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return ""
        return str(win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) or "")
    finally:
        win32clipboard.CloseClipboard()


def test_vendor_bundle_ships_uiautomation_and_comtypes():
    vendor = ROOT / "backend" / "app" / "vendor"
    assert engine.VENDOR_DIR == vendor
    assert (vendor / "uiautomation" / "__init__.py").is_file()
    assert (vendor / "uiautomation" / "uiautomation.py").is_file()
    assert (vendor / "uiautomation" / "bin").is_dir()
    assert (vendor / "comtypes" / "__init__.py").is_file()
    # psutil 的 C 扩展与 pyperclip 也随包下发：客户机缺这两个包时不必再退化到兜底
    assert (vendor / "psutil" / "__init__.py").is_file()
    assert (vendor / "psutil" / "_psutil_windows.pyd").is_file()
    assert (vendor / "pyperclip" / "__init__.py").is_file()
    assert str(vendor) in sys.path, "engine 导入时就应该把内置依赖目录挂进 sys.path"


def test_vendored_psutil_and_pyperclip_fill_missing_packages(monkeypatch):
    """site-packages 里没有 psutil / pyperclip 时，要从内置副本补上（客户机场景）。"""
    monkeypatch.setattr(sys, "path", [p for p in sys.path if "site-packages" not in p.lower()])
    for name in ("psutil", "pyperclip", "psutil._psutil_windows"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    scan.reset_cache()
    ok, error = engine._module_probe("psutil")
    assert ok, error
    vendor_prefix = str(engine.VENDOR_DIR).replace("\\", "/")
    assert vendor_prefix in str(sys.modules["psutil"].__file__).replace("\\", "/")
    assert engine._module_source(sys.modules["psutil"]) == "vendor"
    assert scan.process_scan_backend() == "psutil"
    rows = scan.snapshot_processes(ttl=0.0)
    assert any(row["pid"] == os.getpid() for row in rows)
    clip_ok, clip_error = engine._module_probe("pyperclip")
    assert clip_ok, clip_error
    scan.reset_cache()


def test_process_scan_uses_ctypes_when_psutil_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", None)
    scan.reset_cache()
    assert scan.process_scan_backend() == "ctypes"
    rows = scan.snapshot_processes(ttl=0.0)
    assert rows, "ctypes 全表枚举不应为空"
    mine = [row for row in rows if row["pid"] == os.getpid()]
    assert mine and str(mine[0]["name"]).lower().startswith("python")
    name, exe = scan.process_image(os.getpid())
    assert name.lower().startswith("python")
    if os.name == "nt":
        assert exe.lower().endswith(".exe")
    scan.reset_cache()


def test_load_uia_exposes_source():
    module, error, source = engine.load_uia(refresh=True)
    assert module is not None, error
    assert source in {"installed", "vendor"}
    assert error == ""


def test_load_uia_falls_back_to_vendor_when_installed_package_fails(monkeypatch):
    """系统安装版导入失败时必须落到内置副本（客户机就是这样）。"""
    monkeypatch.setattr(sys, "path", [p for p in sys.path if "site-packages" not in p.lower()])
    monkeypatch.delitem(sys.modules, "uiautomation", raising=False)
    monkeypatch.delitem(sys.modules, "comtypes", raising=False)
    monkeypatch.delitem(sys.modules, "comtypes.client", raising=False)
    monkeypatch.delitem(sys.modules, "comtypes.gen", raising=False)
    module, error, source = engine.load_uia(refresh=True)
    assert module is not None, error
    assert source == "vendor"
    assert "vendor" in str(getattr(module, "__file__", "")).replace("\\", "/")


@pytest.mark.parametrize(
    "class_name,title,expected",
    [
        ("IME", "Default IME", True),
        ("MSCTFIME UI", "MSCTFIME UI", True),
        ("GDI+ Hook Window Class", "GDI+ Window (WhatsApp.Root.exe)", True),
        (".NET-BroadcastEventWindow.b7ab7b.0", ".NET-BroadcastEventWindow", True),
        ("H.NotifyIcon_990643c3", "H.NotifyIcon", True),
        ("WinUIDesktopWin32WindowClass", "WhatsApp", False),
        ("Chrome_WidgetWin_1", "WhatsApp", False),
        ("ApplicationFrameWindow", "WhatsApp", False),
    ],
)
def test_auxiliary_window_filter(class_name, title, expected):
    assert engine.is_auxiliary_window(class_name=class_name, title=title) is expected


def test_status_exposes_dependency_diagnostics():
    status = engine.status()
    assert set(status["dependencies"]) >= {"uiautomation", "win32gui", "win32process", "pyperclip", "psutil"}
    assert set(status["dependency_sources"]) == set(status["dependencies"])
    assert set(status["capabilities"]) == {"process_scan", "clipboard", "uia"}
    assert isinstance(status["dependency_errors"], dict)
    assert "auto_repair" in status
    if status["dependencies"]["uiautomation"]:
        assert status["dependency_sources"]["uiautomation"] in {"installed", "vendor"}
    else:
        assert status["dependency_errors"].get("uiautomation")
        assert "UIA 控件不可用" in status["reason"]


def test_clipboard_fallback_roundtrip():
    if os.name != "nt":
        pytest.skip("windows only")
    original = _read_clipboard()
    marker = "lobster-test-clipboard-20260921"
    backend = engine.set_clipboard_text(marker)
    try:
        assert backend in {"pyperclip", "win32clipboard", "ctypes"}
        assert _read_clipboard() == marker
    finally:
        engine.set_clipboard_text(original)
    assert _read_clipboard() == original


def test_repair_module_covers_whatsapp_group():
    from backend.app.services import runtime_dependency_repair as repair

    groups = {key: modules for key, _label, modules in repair._IMPORT_GROUPS}
    assert "whatsapp" in groups
    for module in ("uiautomation", "comtypes", "win32clipboard", "win32gui", "win32process", "pyperclip", "psutil"):
        assert module in groups["whatsapp"]


def test_vendor_aware_import_verification_accepts_bundled_copy():
    from backend.app.services import runtime_dependency_repair as repair

    ok, message = repair._verify_import("uiautomation")
    assert ok, message


def test_frontend_surfaces_dependency_diagnostics():
    js = (ROOT / "static" / "js" / "personal-whatsapp.js").read_text(encoding="utf-8")
    assert "personalWhatsappRepairDeps" in js
    assert "/api/settings/repair-runtime-dependencies" in js
    assert "dependency_sources" in js
    assert "capabilities" in js
    # 诊断块必须横跨整行，否则状态卡网格会把它挤成窄条、按钮和表格叠在一起
    assert "grid-column:1/-1" in js
    # 已有兜底的缺失（psutil→ctypes / pyperclip→win32clipboard）不该再报红
    assert "fallbackReady" in js
    assert "已兜底" in js
    registry = (ROOT / "static" / "js" / "view-registry.js").read_text(encoding="utf-8")
    assert "personal-whatsapp-tidy-v10" in registry


def test_frontend_hides_group_and_contact_tabs():
    """群聊/通讯录不再单独开 tab：群聊在会话页可筛，通讯录能力留在接口层。"""
    html = (ROOT / "static" / "views" / "personal-whatsapp.html").read_text(encoding="utf-8")
    assert 'data-pwa-tab="groups"' not in html
    assert 'data-pwa-tab="contacts"' not in html
    assert 'data-pwa-panel="groups"' not in html
    assert 'data-pwa-panel="contacts"' not in html
    assert 'data-pwa-tab="sessions"' in html and 'data-pwa-tab="friends"' in html
    js = (ROOT / "static" / "js" / "personal-whatsapp.js").read_text(encoding="utf-8")
    assert "personalWhatsappSyncGroupsBtn" not in js
    assert "personalWhatsappSyncContactsBtn" not in js
    # 诊断信息默认收起，不再直接刷在屏幕上
    assert 'id="personalWhatsappDiagDetail" style="display:none' in js
    assert "personalWhatsappDiagToggle" in js
