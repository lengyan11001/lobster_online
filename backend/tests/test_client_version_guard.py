"""版本守卫 + 关键接口自检回归（防止再出现"界面新、后端旧"的半包）。"""
import json
from pathlib import Path
from types import SimpleNamespace

from backend.app.api import version_api

ROOT = Path(__file__).resolve().parents[2]


def test_expected_routes_are_registered_in_this_tree():
    """这个清单里任何一条缺失，都说明后端代码不是当前包（2026-09-21 twilio-whatsapp 404 就是这种）。"""
    source = (ROOT / "backend" / "app" / "create_app.py").read_text(encoding="utf-8")
    modules = {
        "/api/native-whatsapp/status": "native_whatsapp",
        "/api/native-whatsapp/sessions/sync": "native_whatsapp",
        "/api/twilio-whatsapp/config": "twilio_whatsapp",
    }
    for route, module in modules.items():
        api_file = ROOT / "backend" / "app" / "api" / (module + ".py")
        text = api_file.read_text(encoding="utf-8")
        assert route.replace("/api", "", 1) in text or route in text, (route, module)
        assert "from .api.%s import router" % module in source

    routers = {
        "/api/alibaba-inquiries/accounts": "alibaba_inquiries",
        "/api/version": "version_api",
    }
    for route, module in routers.items():
        assert "from .api.%s import router" % module in source, route


def test_version_payload_reports_missing_routes():
    app = SimpleNamespace(routes=[SimpleNamespace(path=p) for p in ("/api/health", "/api/version")])

    payload = version_api.version_payload(SimpleNamespace(app=app))

    assert payload["ok"] is False
    assert "/api/twilio-whatsapp/config" in payload["expected_routes_missing"]
    assert payload["registered_routes"] == 2
    assert payload["root_dir"]


def test_frontend_guard_is_loaded_and_compares_versions():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    guard = (ROOT / "static" / "js" / "client-version-guard.js").read_text(encoding="utf-8")

    assert "/static/js/client-version-guard.js" in index
    assert "/api/version" in guard and "/static/client_version.json" in guard
    assert "expected_routes_missing" in guard
    assert "backend_started_at" in guard and "applied_at" in guard     # 后端进程比包旧也要报
    assert "location.reload()" in guard


def test_whatsapp_window_match_covers_release_variants():
    from backend.app.services import native_whatsapp_engine as engine

    assert engine.whatsapp_window_match(process_name="WhatsApp.exe",
                                       class_name="WinUIDesktopWin32WindowClass", title="WhatsApp")
    assert engine.whatsapp_window_match(process_name="WhatsApp.root.exe",
                                       class_name="ApplicationFrameWindow", title="WhatsApp Business")
    assert engine.whatsapp_window_match(process_name="WhatsAppBeta.exe",
                                       class_name="Chrome_WidgetWin_1", title="WhatsApp")
    assert engine.whatsapp_window_match(process_name="WhatsApp.exe",
                                       class_name="UnknownClass", title="WhatsApp - 3 条新消息")
    assert not engine.whatsapp_window_match(process_name="chrome.exe",
                                            class_name="Chrome_WidgetWin_1", title="WhatsApp Web")
    assert not engine.whatsapp_window_match(process_name="WhatsAppCrashHandler.exe",
                                            class_name="WinUIDesktopWin32WindowClass", title="WhatsApp")
