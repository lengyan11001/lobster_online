from pathlib import Path

from desktop import launcher


ROOT = Path(__file__).resolve().parent


def test_online_app_installs_shared_network_recovery():
    app = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "window.requestLobsterNetworkRecovery" in app
    assert "window.refreshCurrentLobsterView" in app
    assert "window.__LOBSTER_NETWORK_RECOVERY_STATE" in app
    assert "navigator.onLine" not in app


def test_online_fetch_wrapper_triggers_recovery_on_network_failures():
    app = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "return nativeFetch(requestInput, next).then(function(response)" in app
    assert "Failed to fetch|fetch(?:ing)?" in app
    assert "recoverBackend(url.origin, 'fetch')" in app
    assert "requestViewRecovery(url, 'fetch')" in app
    assert "navigator.onLine" not in app


def test_online_fetch_wrapper_recovers_auth_without_replaying_unsafe_network_posts():
    app = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "syncLatestToken(headers, url)" in app
    assert "credentialFailureResponse(response, headers)" in app
    assert "recoverAuthentication(url, usedToken)" in app
    assert "retryableRequest(input, init) || (isNativeTask && hasIdempotencyKey)" in app
    assert "if (canRetryAfterRecovery && attempt < NETWORK_RETRY_DELAYS.length - 1)" in app
    assert "return jsonResponse(RECOVERY_MESSAGE, 503, 503)" in app


def test_online_recovery_masks_raw_auth_and_fetch_errors():
    app = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "LOGIN_EXPIRED_MESSAGE = '登录状态已失效，请重新登录'" in app
    assert "RECOVERY_MESSAGE = '连接正在自动恢复，请稍后重试'" in app
    assert "window.normalizeLobsterRecoverableMessage" in app
    assert "X-Lobster-Recovered-Error" in app
    assert "recoverablePayloadKind(value)" in app
    assert "sanitizeRecoverablePayload(value, force)" in app
    assert "payloadErrorText(value)" in app


def test_douyin_bridge_uses_the_same_auth_and_network_recovery_policy():
    bridge = (ROOT / "static" / "douyin-origin" / "lobster-bridge.js").read_text(encoding="utf-8")

    assert "parent.__lobsterRecoverRequest" in bridge
    assert "api.recover_local_services" in bridge
    assert "readLatestToken()" in bridge
    assert "var canRetry = method === 'GET' || method === 'HEAD' || !!headers.get('X-Lobster-Request-Id')" in bridge
    assert "jsonResponse(LOGIN_EXPIRED_MESSAGE, 401)" in bridge
    assert "jsonResponse(RECOVERY_MESSAGE, 503)" in bridge
    assert "payloadFailureKind(value)" in bridge
    assert "sanitizePayload(value, force)" in bridge
    assert "payloadErrorText(value)" in bridge


def test_every_douyin_page_loads_the_current_recovery_bridge():
    pages = sorted((ROOT / "static" / "douyin-origin").glob("*.html"))
    bridge_pages = [page for page in pages if "lobster-bridge.js" in page.read_text(encoding="utf-8")]

    assert bridge_pages
    for page in bridge_pages:
        html = page.read_text(encoding="utf-8")
        assert "lobster-bridge.js?v=20260826-auto-request-recovery-v1" in html, page.name


def test_chat_view_exports_recovery_refresh():
    chat = (ROOT / "static" / "js" / "mastra-chat.js").read_text(encoding="utf-8")

    assert "window.initMastraOnlineChat = init;" in chat
    assert "window.refreshMastraOnlineChat = refresh;" in chat
    assert "closeAllStreams();" in chat


def test_active_view_tracking_is_persisted_for_recovery():
    init = (ROOT / "static" / "js" / "init.js").read_text(encoding="utf-8")

    assert "window.__LOBSTER_LAST_ACTIVE_VIEW = view;" in init


def test_desktop_bridge_exposes_on_demand_service_recovery():
    launcher = (ROOT / "desktop" / "launcher.py").read_text(encoding="utf-8")

    assert "def recover_local_services(self, reason: str = \"network\")" in launcher
    assert "js_api=DesktopApi(recover_local_services)" in launcher
    assert "result = recover_local_services(\"backend_watchdog\")" in launcher


def test_desktop_recovery_bridge_calls_the_registered_service_recovery():
    reasons = []
    api = launcher.DesktopApi(lambda reason: reasons.append(reason) or {"ok": True})

    assert api.recover_local_services("fetch") == {"ok": True}
    assert reasons == ["fetch"]


def test_xhr_audio_upload_masks_raw_errors_and_starts_recovery_without_replay():
    audio = (ROOT / "static" / "js" / "audio-transcription.js").read_text(encoding="utf-8")

    assert "window.normalizeLobsterRecoverableMessage(detail)" in audio
    assert "window.requestLobsterNetworkRecovery({ reason: 'audio_upload_network' })" in audio
    assert "reject(new Error('连接正在自动恢复，请稍后重试'))" in audio
    assert audio.count("xhr.send(form);") == 1
