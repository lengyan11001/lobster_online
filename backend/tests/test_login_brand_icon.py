from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_login_uses_program_mark_icon():
    script = (ROOT / "static" / "js" / "init.js").read_text(encoding="utf-8")
    assert "/static/bihu_mark_64.png" in script
    assert "bihu_login_mark.svg" not in script
    assert not (ROOT / "static" / "bihu_login_mark.svg").exists()


def test_login_icon_has_shine_effect_and_cache_is_busted():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "index.css").read_text(encoding="utf-8")
    assert 'id="authLoginLogoShell"' in html
    assert "/static/js/init.js?v=20261020-login-shine-v1" in html
    assert "/static/css/index.css?v=20261020-login-shine-v1" in html
    assert "authLogoShine" in css
    assert "auth-logo" in css

def test_dashboard_header_has_top_breathing_room():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "pc-workbench.css").read_text(encoding="utf-8")
    assert "/static/css/pc-workbench.css?v=20261020-header-composer-spacing-v1" in html
    assert "min-height: 64px;" in css
    assert "padding: 8px 20px 0;" in css
    assert "height: calc(100dvh - 64px);" in css
    assert "padding-bottom: 32px;" in css
    assert "padding-bottom: 48px;" in css
