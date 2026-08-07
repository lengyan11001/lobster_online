import base64
import json
from pathlib import Path
import subprocess

import pytest

from desktop import oem_branding, oem_configurator
from scripts import pack_full_project_zip, pack_slim_zip


def _profile(root: Path) -> dict:
    cached = root / "static" / "branding" / "cache" / "hikong" / "v1" / "client_launcher.exe"
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"brand-launcher")
    return {
        "mark": "hikong",
        "display_name": "Hikong AI Agent",
        "install": {
            "launcher_exe": "/static/branding/cache/hikong/v1/client_launcher.exe",
            "launcher_filename": "HikongAI.exe",
        },
    }


def test_oem_branding_download_uses_bundled_ca_without_system_proxy(monkeypatch):
    observed = {}
    ssl_context = object()

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read(_limit):
            return b'{"ok": true}'

    class FakeOpener:
        @staticmethod
        def open(request, timeout):
            observed["url"] = request.full_url
            observed["timeout"] = timeout
            return FakeResponse()

    def _build_opener(*handlers):
        observed["handlers"] = handlers
        return FakeOpener()

    monkeypatch.setattr(oem_branding, "_ssl_context", lambda: ssl_context)
    monkeypatch.setattr(oem_branding.urllib.request, "build_opener", _build_opener)

    assert oem_branding._fetch_json("https://brand.example/bootstrap", 6) == {"ok": True}
    proxy = next(handler for handler in observed["handlers"] if isinstance(handler, oem_branding.urllib.request.ProxyHandler))
    https = next(handler for handler in observed["handlers"] if isinstance(handler, oem_branding.urllib.request.HTTPSHandler))
    assert proxy.proxies == {}
    assert https._context is ssl_context
    assert observed["timeout"] == 6


def test_oem_branding_timeout_is_retried_and_reported_in_chinese(monkeypatch):
    calls = []

    def _timeout(_request, timeout):
        calls.append(timeout)
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr(oem_branding, "_urlopen_direct", _timeout)
    monkeypatch.setattr(oem_branding.time, "sleep", lambda _seconds: None)

    with pytest.raises(oem_branding.OemBrandingError, match="读取 OEM 品牌配置超时"):
        oem_branding._fetch_json("https://brand.example/bootstrap", 6)
    assert calls == [6, 6, 6]


def test_index_html_is_branded_before_browser_paint(tmp_path, monkeypatch):
    profile_path = tmp_path / "0400.json"
    profile_path.write_text(
        json.dumps(
            {
                "profile": {
                    "mark": "hikong",
                    "document_title": "海康AI智能体",
                    "logo_primary": "海康",
                    "logo_accent": "AI智能体",
                    "hero_title": "海康AI智能体 - 您的私人 AI 助手",
                    "hero_subtitle": "海康的品牌介绍",
                    "icons": {
                        "favicon_32": "/oem/hikong/icon32.png",
                        "apple_touch": "/oem/hikong/icon256.png",
                        "logo_mark": "/oem/hikong/icon32.png",
                        "home_visual": "/oem/hikong/logo.png",
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOBSTER_BRAND_PROFILE_PATH", str(profile_path))
    monkeypatch.setenv("LOBSTER_OEM_CODE", "0400")

    from backend.app.api.branding import render_index_html

    rendered = render_index_html(
        "<title>__LOBSTER_DOCUMENT_TITLE__</title>"
        "<img src=__LOBSTER_LOGO_MARK__>"
        "<b>__LOBSTER_LOGO_PRIMARY__</b><b>__LOBSTER_LOGO_ACCENT__</b>"
        "<h1>__LOBSTER_HERO_TITLE__</h1><p>__LOBSTER_HERO_SUBTITLE__</p>"
        "<img src=__LOBSTER_HOME_VISUAL__>",
        "hikong",
    )

    assert "必火" not in rendered
    assert "海康AI智能体" in rendered
    assert "/oem/hikong/icon32.png" in rendered


def test_factory_config_installs_launcher_writes_code_and_runs_install(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    (tmp_path / ".env").write_text("AUTH_SERVER_BASE=https://brand.example\nLOBSTER_BRAND_MARK=bihuo\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(oem_configurator, "resolve_oem_branding", lambda *args, **kwargs: profile)
    monkeypatch.setattr(oem_configurator, "run_install", lambda root, code, mark: calls.append((root, code, mark)))

    name, launcher = oem_configurator.configure(tmp_path, "0400")

    assert name == "Hikong AI Agent"
    assert launcher == tmp_path / "HikongAI.exe"
    assert launcher.read_bytes() == b"brand-launcher"
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "LOBSTER_BRAND_MARK=hikong" in env_text
    assert "LOBSTER_OEM_CODE=0400" in env_text
    assert "AUTH_SERVER_BASE=https://brand.example" in env_text
    assert calls == [(tmp_path, "0400", "hikong")]


def test_factory_config_rejects_invalid_code_before_download(tmp_path, monkeypatch):
    called = False

    def _resolve(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(oem_configurator, "resolve_oem_branding", _resolve)

    with pytest.raises(RuntimeError, match="4 到 12 位数字"):
        oem_configurator.configure(tmp_path, "hikong")

    assert called is False


def test_factory_switch_removes_previous_brand_shell_only(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    (tmp_path / ".env").write_text(
        "AUTH_SERVER_BASE=https://brand.example\nLOBSTER_BRAND_MARK=0200\nLOBSTER_OEM_CODE=0200\n",
        encoding="utf-8",
    )
    old_cache = tmp_path / "static" / "branding" / "cache" / "daka" / "v1"
    old_cache.mkdir(parents=True)
    (old_cache / "icon.png").write_bytes(b"old-brand")
    old_record = tmp_path / "static" / "branding" / "cache" / "profiles" / "0200.json"
    old_record.parent.mkdir(parents=True, exist_ok=True)
    old_record.write_text(
        json.dumps(
            {
                "oem_code": "0200",
                "brand_mark": "daka",
                "profile": {
                    "mark": "daka",
                    "install": {
                        "launcher_filename": "DakaAI.exe",
                        "shortcut_lnk_name": "DakaAI.lnk",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    old_launcher = tmp_path / "DakaAI.exe"
    old_launcher.write_bytes(b"old-launcher")
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    old_shortcut = desktop / "DakaAI.lnk"
    old_shortcut.write_bytes(b"old-shortcut")
    stale_cache = tmp_path / "static" / "branding" / "cache" / "bihuo" / "v1"
    stale_cache.mkdir(parents=True)
    (stale_cache / "icon.png").write_bytes(b"stale-brand")
    stale_record = tmp_path / "static" / "branding" / "cache" / "profiles" / "0100.json"
    stale_record.write_text(
        json.dumps(
            {
                "oem_code": "0100",
                "brand_mark": "bihuo",
                "profile": {
                    "mark": "bihuo",
                    "install": {"launcher_filename": "BihuoAI.exe"},
                },
            }
        ),
        encoding="utf-8",
    )
    stale_launcher = tmp_path / "BihuoAI.exe"
    stale_launcher.write_bytes(b"stale-launcher")
    business_data = tmp_path / "browser_data" / "session.db"
    business_data.parent.mkdir()
    business_data.write_bytes(b"keep-user-data")

    monkeypatch.setattr(oem_configurator, "resolve_oem_branding", lambda *args, **kwargs: profile)
    monkeypatch.setattr(oem_configurator, "run_install", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(oem_configurator, "_desktop_directories", lambda: [desktop])

    _, launcher = oem_configurator.configure(tmp_path, "0400")

    assert launcher.is_file()
    assert not old_launcher.exists()
    assert not old_shortcut.exists()
    assert not old_cache.parent.exists()
    assert not old_record.exists()
    assert not stale_cache.parent.exists()
    assert not stale_record.exists()
    assert not stale_launcher.exists()
    assert business_data.read_bytes() == b"keep-user-data"


def test_factory_switch_failure_restores_previous_brand(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    original_env = b"LOBSTER_BRAND_MARK=0200\r\nLOBSTER_OEM_CODE=0200\r\nCUSTOM=value\r\n"
    (tmp_path / ".env").write_bytes(original_env)
    cleanup_called = False

    monkeypatch.setattr(oem_configurator, "resolve_oem_branding", lambda *args, **kwargs: profile)

    def _fail_install(*_args, **_kwargs):
        raise RuntimeError("install failed")

    def _cleanup(*_args, **_kwargs):
        nonlocal cleanup_called
        cleanup_called = True

    monkeypatch.setattr(oem_configurator, "run_install", _fail_install)
    monkeypatch.setattr(oem_configurator, "cleanup_previous_oem", _cleanup)

    with pytest.raises(RuntimeError, match="install failed"):
        oem_configurator.configure(tmp_path, "0400")

    assert (tmp_path / ".env").read_bytes() == original_env
    assert cleanup_called is False


def test_factory_install_uses_cached_oem_profile_for_shortcut():
    root = oem_configurator.resolve_root()
    install = (root / "install.bat").read_text(encoding="utf-8")
    shortcut = (root / "scripts" / "create_desktop_shortcut.ps1").read_text(encoding="utf-8")

    assert "static\\branding\\cache\\profiles\\%LOBSTER_PROFILE_KEY%.json" in install
    assert "set \"LOBSTER_PROFILE_KEY=%LOBSTER_OEM_CODE%\"" in install
    assert 'if /i "%%~a"=="LOBSTER_OEM_CODE" set "LOBSTER_OEM_CODE=%%b"' in install
    assert '-BrandProfilePath "%LOBSTER_BRAND_PROFILE_PATH%"' in install
    assert "$runtimeCode -eq $m" in shortcut
    assert "$inst.launcher_filename" in shortcut
    assert "if not defined LOBSTER_SKIP_INSTALL_PAUSE pause" in install
    assert "\npause\n" not in install.replace("\r\n", "\n")


def test_factory_configurator_is_included_in_full_factory_packages():
    filename = "OEM配置启动器.exe"

    assert (oem_configurator.resolve_root() / filename).is_file()
    assert not pack_full_project_zip.should_exclude("lobster_online", filename)
    assert not pack_slim_zip.is_excluded(Path("lobster_online") / filename)


def test_factory_full_package_excludes_local_brand_and_runtime_data():
    configurator = pack_full_project_zip.FACTORY_CONFIGURATOR_EXE

    assert not pack_full_project_zip.should_exclude(
        "lobster_online", configurator, factory_oem=True
    )
    assert pack_full_project_zip.should_exclude(
        "lobster_online", "brand-launcher.exe", factory_oem=True
    )
    assert pack_full_project_zip.should_exclude(
        "lobster_online", ".env", factory_oem=True
    )
    assert not pack_full_project_zip.should_exclude(
        "lobster_online", ".env.example", factory_oem=True
    )
    assert pack_full_project_zip.should_exclude(
        "lobster_online", "data/app_state.db", factory_oem=True
    )
    assert pack_full_project_zip.should_exclude(
        "lobster_online", "_probe_apiz_sdk_src/README.md", factory_oem=True
    )
    assert pack_full_project_zip.should_exclude(
        "lobster_online", ".installed", factory_oem=True
    )
    assert pack_full_project_zip.should_exclude(
        "lobster_online", "lobster.db.bak-20260806", factory_oem=True
    )
    assert pack_full_project_zip.should_exclude(
        "lobster_online", "brand-launcher.spec", factory_oem=True
    )
    assert not pack_full_project_zip.should_exclude(
        "lobster_online", "models/sam/sam_vit_b.pth", factory_oem=True
    )
    assert not pack_full_project_zip.should_exclude(
        "lobster_online", "deps/vc_redist.x64.exe", factory_oem=True
    )


def test_factory_oem_code_seeds_env_from_example(tmp_path):
    (tmp_path / ".env.example").write_text(
        "AUTH_SERVER_BASE=https://example.test\nLOBSTER_BRAND_MARK=bihuo\n",
        encoding="utf-8",
    )

    oem_configurator.write_oem_code(tmp_path, "0400", "hikong")

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "AUTH_SERVER_BASE=https://example.test" in env_text
    assert "LOBSTER_BRAND_MARK=hikong" in env_text
    assert "LOBSTER_OEM_CODE=0400" in env_text


def test_factory_scripts_load_with_bundled_python():
    root = oem_configurator.resolve_root()
    runtime = root / "python" / "python.exe"
    if not runtime.is_file():
        pytest.skip("bundled Python is not available")

    configurator = subprocess.run(
        [str(runtime), str(root / "desktop" / "oem_configurator.py"), "--help"],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert configurator.returncode == 0, configurator.stderr

    launcher_probe = (
        "import pathlib, runpy; "
        f"ns=runpy.run_path({str(root / 'desktop' / 'launcher.py')!r}); "
        f"result=ns['resolve_factory_oem_branding'](pathlib.Path({str(root)!r}), 'invalid', 'https://bhzn.top'); "
        "assert result is None"
    )
    launcher = subprocess.run(
        [str(runtime), "-c", launcher_probe],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert launcher.returncode == 0, launcher.stderr


def test_factory_process_protocol_is_ascii_and_utf8_safe():
    root = oem_configurator.resolve_root()
    runtime = root / "python" / "python.exe"
    if not runtime.is_file():
        pytest.skip("bundled Python is not available")

    result = subprocess.run(
        [str(runtime), str(root / "desktop" / "oem_configurator.py"), "--code", "invalid", "--root", str(root)],
        cwd=str(root),
        capture_output=True,
        timeout=20,
    )
    line = result.stdout.strip().splitlines()[-1]
    kind, encoded = line.split(b"\t", 1)

    assert result.returncode == 1
    assert kind == b"ERROR64"
    assert all(byte < 128 for byte in line)
    assert "请输入 4 到 12 位数字 OEM 编号" in base64.b64decode(encoded).decode("utf-8")

    stub = (root / "desktop" / "oem_configurator_stub.cs").read_text(encoding="utf-8")
    assert 'psi.EnvironmentVariables["PYTHONUTF8"] = "1"' in stub
    assert 'psi.EnvironmentVariables["SSL_CERT_FILE"] = caBundle' in stub
    assert 'psi.EnvironmentVariables["NO_PROXY"] = "*"' in stub
    assert 'fields[0] == "OK64"' in stub
