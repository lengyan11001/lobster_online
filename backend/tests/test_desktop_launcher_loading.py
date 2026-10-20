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
