import json

from desktop import build_desktop_exe, launcher


def _write_brand_registry(root):
    registry = {
        "default_mark": "bihuo",
        "marks": {
            "bihuo": {
                "document_title": "Bihuo AI",
                "icons": {
                    "loading_mark": "/static/bihu_64.png",
                    "home_visual": "/static/bihu_source.png",
                },
                "install": {"desktop_ico": "static/bihu_box.ico"},
            },
            "daka": {
                "document_title": "Daka AI",
                "icons": {
                    "loading_mark": "/static/daka_64.png",
                    "home_visual": "/static/daka_source.png",
                },
                "install": {"desktop_ico": "static/daka_box.ico"},
            },
        },
    }
    path = root / "static" / "branding" / "brands.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(registry), encoding="utf-8")


def test_desktop_branding_uses_installed_brand(tmp_path, monkeypatch):
    _write_brand_registry(tmp_path)
    (tmp_path / ".env").write_text("LOBSTER_BRAND_MARK=daka\n", encoding="utf-8")
    monkeypatch.delenv("LOBSTER_BRAND_MARK", raising=False)
    monkeypatch.delenv("LOBSTER_DESKTOP_TITLE", raising=False)
    monkeypatch.delenv("LOBSTER_IS_OVERSEAS_USER", raising=False)

    branding = launcher.load_desktop_branding(tmp_path)

    assert branding["mark"] == "daka"
    assert branding["document_title"] == "Daka AI"
    assert branding["icons"]["home_visual"] == "/static/daka_source.png"
    assert launcher.desktop_brand_title(branding=branding) == "Daka AI"


def test_desktop_branding_unknown_mark_falls_back(tmp_path, monkeypatch):
    _write_brand_registry(tmp_path)
    monkeypatch.setenv("LOBSTER_BRAND_MARK", "unknown")

    branding = launcher.load_desktop_branding(tmp_path)

    assert branding["mark"] == "bihuo"
    assert branding["document_title"] == "Bihuo AI"


def test_non_default_brand_title_wins_over_legacy_overseas_title(tmp_path, monkeypatch):
    _write_brand_registry(tmp_path)
    monkeypatch.setenv("LOBSTER_BRAND_MARK", "daka")
    monkeypatch.setenv("LOBSTER_IS_OVERSEAS_USER", "true")

    branding = launcher.load_desktop_branding(tmp_path)

    assert launcher.desktop_brand_title(branding=branding) == "Daka AI"


def test_lightweight_exe_build_uses_brand_icon(tmp_path, monkeypatch):
    _write_brand_registry(tmp_path)
    monkeypatch.setenv("LOBSTER_BRAND_MARK", "daka")

    mark, icon = build_desktop_exe.resolve_build_brand(tmp_path)

    assert mark == "daka"
    assert icon == tmp_path / "static" / "daka_box.ico"


def test_home_visual_is_applied_from_branding_registry():
    html = (launcher.ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = (launcher.ROOT / "static" / "js" / "init.js").read_text(encoding="utf-8")

    assert 'id="brandHomeVisual"' in html
    assert "icons.home_visual" in script


def test_header_partner_logo_is_only_configured_for_daka():
    registry = json.loads(
        (launcher.ROOT / "static" / "branding" / "brands.json").read_text(encoding="utf-8")
    )
    html = (launcher.ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = (launcher.ROOT / "static" / "js" / "init.js").read_text(encoding="utf-8")

    assert 'id="brandPartnerLogo"' in html
    assert 'id="brandPartnerLogo" src="" alt="" hidden' in html
    assert "icons.header_partner_logo" in script
    assert "header_partner_logo" not in registry["marks"]["bihuo"]["icons"]
    assert registry["marks"]["daka"]["icons"]["header_partner_logo"] == "/static/daka_header_partner.jpg"
    assert (launcher.ROOT / "static" / "daka_header_partner.jpg").is_file()
