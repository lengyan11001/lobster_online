from desktop.launcher import LOADING_HTML, _startup_detail_for_ui


def test_loading_html_has_spotlight_logo_and_no_extraction_logs():
    assert "logo-stage" in LOADING_HTML
    assert "logoBreathe" in LOADING_HTML
    assert "spotlightPulse" in LOADING_HTML
    assert "startupLogs" not in LOADING_HTML
    assert "log-line" not in LOADING_HTML


def test_extraction_details_are_hidden_from_loading_ui():
    assert _startup_detail_for_ui("extract_start file=update.zip") == ""
    assert _startup_detail_for_ui("正在解压更新包") == ""
    assert _startup_detail_for_ui("准备检查更新和启动本地服务。") == "准备检查更新和启动本地服务。"
    assert _startup_detail_for_ui("端口 8000") == "端口 8000"
    assert _startup_detail_for_ui("x" * 141) == ""


def test_loading_page_is_never_blank_without_branding(monkeypatch):
    """2026-11-27 客户现场：开机自启后白屏。

    品牌解析失败时 desktop_loading_html 以前返回空 body（BLANK_HTML），
    窗口就一直是纯白。现在必须仍然渲染带进度/文案的启动页。
    """
    from desktop import launcher as L

    monkeypatch.setattr(L, "_ACTIVE_DESKTOP_BRANDING", {}, raising=False)
    html = L.desktop_loading_html("http://127.0.0.1:8000/?desktop=1", "必火AI员工")
    assert "正在启动" in html
    assert "logo-stage" in html
    assert L._TRANSPARENT_PIXEL_DATA_URI in html

    monkeypatch.setattr(
        L, "_ACTIVE_DESKTOP_BRANDING", {"mark": "bihuo", "_branding_unavailable": True}, raising=False
    )
    html2 = L.desktop_loading_html("http://127.0.0.1:8000/?desktop=1", "x")
    assert "正在启动" in html2
    assert "logo-stage" in html2
