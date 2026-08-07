from pathlib import Path

from backend.app.api import branding


ROOT = Path(__file__).resolve().parent
SHANJIAN_JS = (ROOT / "static/js/shanjian-digital-human.js").read_text(encoding="utf-8")
INIT_JS = (ROOT / "static/js/init.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "static/index.html").read_text(encoding="utf-8")


def test_branding_profile_is_exposed_to_feature_views():
    assert "window.__LOBSTER_BRANDING = b;" in INIT_JS
    assert "new CustomEvent('lobster:branding-ready', { detail: b })" in INIT_JS


def test_avatar_authorization_uses_current_oem_brand_name():
    assert "branding.logo_primary || branding.display_name || branding.document_title" in SHANJIAN_JS
    assert "bihuo: '必火'" in SHANJIAN_JS
    assert "daka: '大咖'" in SHANJIAN_JS
    assert "jinghai: '鲸海'" in SHANJIAN_JS
    assert "hikong: '海康'" in SHANJIAN_JS
    assert "我授权【' + brandName + '】使用视频中的肖像、声音" in SHANJIAN_JS
    assert "并在本人【' + brandName + '】账号中创作使用" in SHANJIAN_JS
    assert "我授权本平台使用视频" not in SHANJIAN_JS


def test_both_avatar_creation_modes_render_the_dynamic_authorization_text():
    assert SHANJIAN_JS.count("${defaultShanjianAuthText()}") == 2
    assert SHANJIAN_JS.count('data-brand-auth-default="1"') == 2
    assert "window.addEventListener('lobster:branding-ready', refreshDefaultShanjianAuthText)" in SHANJIAN_JS
    assert "20260807-oem-auth-text-v1" in INDEX_HTML


def test_legacy_numeric_brand_mark_resolves_cached_oem_profile(tmp_path, monkeypatch):
    registry = tmp_path / "brands.json"
    registry.write_text('{"marks":{"bihuo":{"logo_primary":"必火"}}}', encoding="utf-8")
    profile_path = tmp_path / "cache" / "profiles" / "0400.json"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(
        '{"profile":{"mark":"hikong","logo_primary":"海康","document_title":"海康AI智能体"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(branding, "_brands_path", lambda: registry)
    monkeypatch.setattr(branding.settings, "lobster_brand_mark", "0400")
    monkeypatch.setattr(branding.settings, "lobster_oem_code", "0400")
    monkeypatch.delenv("LOBSTER_BRAND_PROFILE_PATH", raising=False)
    monkeypatch.setenv("LOBSTER_OEM_CODE", "0400")

    result = branding.get_branding()

    assert result["mark"] == "hikong"
    assert result["logo_primary"] == "海康"


def test_named_brand_mark_uses_settings_oem_code_for_runtime_profile(tmp_path, monkeypatch):
    registry = tmp_path / "brands.json"
    registry.write_text('{"marks":{"bihuo":{"logo_primary":"必火"}}}', encoding="utf-8")
    profile_path = tmp_path / "cache" / "profiles" / "0300.json"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(
        '{"profile":{"mark":"jinghai","logo_primary":"鲸海","document_title":"鲸海AI员工"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(branding, "_brands_path", lambda: registry)
    monkeypatch.setattr(branding.settings, "lobster_brand_mark", "jinghai")
    monkeypatch.setattr(branding.settings, "lobster_oem_code", "0300")
    monkeypatch.delenv("LOBSTER_BRAND_PROFILE_PATH", raising=False)
    monkeypatch.delenv("LOBSTER_OEM_CODE", raising=False)

    result = branding.get_branding()

    assert result["mark"] == "jinghai"
    assert result["logo_primary"] == "鲸海"
