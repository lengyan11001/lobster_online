from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_online_app_installs_shared_network_recovery():
    app = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "window.requestLobsterNetworkRecovery" in app
    assert "window.refreshCurrentLobsterView" in app
    assert "window.__LOBSTER_NETWORK_RECOVERY_STATE" in app
    assert "navigator.onLine" not in app


def test_online_fetch_wrapper_triggers_recovery_on_network_failures():
    app = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "nativeFetch(input, next).catch(function(err)" in app
    assert "Failed to fetch|NetworkError|Load failed" in app
    assert "window.requestLobsterNetworkRecovery({" in app
    assert "navigator.onLine" not in app


def test_chat_view_exports_recovery_refresh():
    chat = (ROOT / "static" / "js" / "mastra-chat.js").read_text(encoding="utf-8")

    assert "window.initMastraOnlineChat = init;" in chat
    assert "window.refreshMastraOnlineChat = refresh;" in chat
    assert "closeAllStreams();" in chat


def test_active_view_tracking_is_persisted_for_recovery():
    init = (ROOT / "static" / "js" / "init.js").read_text(encoding="utf-8")

    assert "window.__LOBSTER_LAST_ACTIVE_VIEW = view;" in init
