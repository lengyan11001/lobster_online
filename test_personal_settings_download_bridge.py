from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_personal_memory_download_uses_desktop_host_bridge_before_blob_fallback():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")

    assert "window.pywebview.api.save_text_file(filename, text)" in script
    assert "window.LobsterAndroid.saveTextFile(filename, 'text/markdown', text)" in script
    assert "return resp.text();" in script
    assert "return saveTextDownload(fallbackName" in script


def test_desktop_api_exposes_text_save_dialog_and_cache_key_is_current():
    launcher = (ROOT / "desktop" / "launcher.py").read_text(encoding="utf-8")
    registry = (ROOT / "static" / "js" / "view-registry.js").read_text(encoding="utf-8")
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "def save_text_file(self, suggested_name: str, content: str) -> dict:" in launcher
    assert "webview.SAVE_DIALOG" in launcher
    assert "20260826-memory-download-bridge-v1" in registry
    assert "20260826-memory-download-bridge-v1" in index
