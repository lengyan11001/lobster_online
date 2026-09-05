from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_hikong_theme_persists_across_the_online_workbench():
    css = (ROOT / "static" / "css" / "pc-workbench.css").read_text(encoding="utf-8")
    app = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    marker = "/* Hikong (OEM 0400): Douyin lead-generation palette on the Online home route only. */"
    theme = css.split(marker, 1)[1]

    assert 'html[data-brand="hikong"]' in theme
    assert theme.count('html[data-brand="hikong"]') >= 20
    assert ':has(#content-chat.visible)' in theme
    assert 'html[data-brand="hikong"] .page:has(#dashboard.visible) {' in theme
    assert 'html[data-brand="hikong"] .page:has(#dashboard.visible) .app-side-nav {' in theme
    assert "#0b1a3a" in theme
    assert "#1a3fa3" in theme
    assert "#13b7d8" in theme
    assert "document.documentElement.setAttribute('data-brand', next)" in app
    assert "pc-workbench.css?v=20260903-personal-template-layout-v1" in html
    assert 'id="brandLogoMark"' in html


def test_all_oem_home_logos_shake_once_on_hover():
    css = (ROOT / "static" / "css" / "pc-workbench.css").read_text(encoding="utf-8")

    assert "@keyframes online-home-logo-shake" in css
    assert "#content-chat .chat-home-visual:hover .chat-home-visual-core img" in css
    assert "animation: online-home-logo-shake 520ms" in css
    assert "animation: none" in css
