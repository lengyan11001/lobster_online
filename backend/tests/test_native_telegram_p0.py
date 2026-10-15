"""个人 Telegram（P0 骨架）接线检查。

P0 的验收标准只有一条：页面能打开、接口能通、桥接脚本能自检。
任何一环断了这里就该红 —— 所以这些断言都是对着真实文件读的，不是对着 mock。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import native_telegram_engine as engine  # noqa: E402

BRIDGE = ROOT / "scripts" / "telegram_bridge.py"


@pytest.fixture
def sandbox_engine(tmp_path, monkeypatch):
    """把引擎的落盘位置换到 tmp：测试不许往仓库里写 data/ 和 logs/。"""
    monkeypatch.setattr(engine, "STATE_DIR", tmp_path)
    monkeypatch.setattr(engine, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(engine, "AUTH_DIR", tmp_path / "session")
    monkeypatch.setattr(engine, "LOG_PATH", tmp_path / "logs" / "native_telegram.jsonl")
    monkeypatch.setattr(engine, "PROTOCOL_PATH", tmp_path / "logs" / "telegram_protocol.jsonl")
    monkeypatch.setattr(engine, "LEDGER_PATH", tmp_path / "protocol_ledger.jsonl")
    return engine


def test_status_is_readable_before_any_login(sandbox_engine):
    data = sandbox_engine.status()
    assert data["ok"] is True
    assert data["phase"] == "P0"
    assert data["linked"] is False, "还没登录就不该是已登录"
    assert data["login"]["step"] == "idle"
    for key in ("dependency", "transport", "counts", "limits", "logs"):
        assert key in data, "状态里缺 " + key
    assert data["counts"]["chats"] == 0
    assert data["dependency"]["bridge_exists"] is True


def test_config_never_returns_api_hash(sandbox_engine):
    saved = sandbox_engine.save_config(api_id="1234567", api_hash="secret-hash-value", phone="+86 138-0000-0000")
    assert "api_hash" not in saved, "api_hash 是凭据，接口不能回吐明文"
    assert saved["api_hash_set"] is True
    assert saved["api_id"] == "1234567"
    assert saved["phone"] == "+8613800000000", "手机号要归一化"
    again = sandbox_engine.get_config()
    assert "api_hash" not in again and again["api_hash_set"] is True


def test_login_start_refuses_without_api_id(sandbox_engine):
    data = sandbox_engine.login_start("+8613800000000")
    assert data["ok"] is False
    assert data["error"] == "api_id_missing"


def test_unimplemented_phases_are_labelled_not_faked(sandbox_engine):
    data = sandbox_engine.not_implemented("sync")
    assert data["ok"] is False
    assert data["error"] == "not_implemented"
    assert data["phase"] == "P2"
    assert data["message"]


def test_view_registry_registers_personal_telegram():
    registry = (ROOT / "static" / "js" / "view-registry.js").read_text(encoding="utf-8")
    assert "/static/views/personal-telegram.html?v=" in registry
    assert "/static/js/personal-telegram.js?v=" in registry
    assert "initPersonalTelegramView" in registry
    assert "registerLobsterView('personal-telegram'" in registry


def test_view_files_exist_and_export_init():
    html = ROOT / "static" / "views" / "personal-telegram.html"
    js = ROOT / "static" / "js" / "personal-telegram.js"
    assert html.exists() and js.exists()
    html_text = html.read_text(encoding="utf-8")
    js_text = js.read_text(encoding="utf-8")
    assert 'id="content-personal-telegram"' in html_text
    assert "personalTelegramLoginStartBtn" in html_text
    assert "window.initPersonalTelegramView" in js_text
    # 登录是三方交互（手机号/验证码/2FA），不是扫码
    for token in ("personalTelegramPhone", "personalTelegramCode", "personalTelegramPassword"):
        assert token in html_text


def test_skill_store_has_personal_telegram_card():
    skill = (ROOT / "static" / "js" / "skill.js").read_text(encoding="utf-8")
    assert "_renderPersonalTelegramCard" in skill
    assert "personal-telegram-card" in skill
    assert "_openPersonalTelegramView" in skill
    assert "personal_telegram_assistant" in skill


def test_skill_store_entry_is_server_gated_not_hardcoded():
    """技能商店入口是服务器权限控制的：服务端没下发这个技能包，本地就不该出现这张卡。"""
    skill = (ROOT / "static" / "js" / "skill.js").read_text(encoding="utf-8")
    assert "var personalTelegramPkg = pkgById('personal_telegram_assistant');" in skill, \
        "入口必须只看服务端下发的技能包，不能在本地兜一个写死的包"
    assert "var personalTelegramPkg = pkgById('personal_telegram_assistant') ||" not in skill, \
        "不许本地写死技能包"
    init = (ROOT / "static" / "js" / "init.js").read_text(encoding="utf-8")
    assert "'personal-telegram': 'personal_telegram_assistant'" in init, \
        "视图权限闸门要和 personal-whatsapp 一样交给服务端特性位"


def test_create_app_mounts_native_telegram_router():
    source = (ROOT / "backend" / "app" / "create_app.py").read_text(encoding="utf-8")
    assert "from .api.native_telegram import router as native_telegram_router" in source
    assert "app.include_router(native_telegram_router, prefix=\"\")" in source


def test_logs_api_whitelists_telegram_protocol_log():
    source = (ROOT / "backend" / "app" / "api" / "logs_api.py").read_text(encoding="utf-8")
    assert "logs/telegram_protocol.jsonl" in source


def test_bridge_selftest_runs_without_telethon(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(BRIDGE), "selftest", "--ledger", str(tmp_path / "ledger.jsonl")],
        capture_output=True, encoding="utf-8", errors="replace", timeout=60, cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads([ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1])
    assert payload["ok"] is True
    assert payload["count"] >= 7
    assert (tmp_path / "ledger.jsonl").exists(), "selftest 也要写账本"


def test_bridge_status_is_readable_without_telethon(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(BRIDGE), "status", "--auth", str(tmp_path / "auth")],
        capture_output=True, encoding="utf-8", errors="replace", timeout=60, cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads([ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1])
    assert payload["ok"] is True
    assert payload["linked"] is False
    assert "telethon" in payload["dependency"]


@pytest.mark.parametrize(
    "command,phase",
    [("contacts", "P2"), ("dialogs", "P2"), ("messages", "P2"), ("groups", "P2"),
     ("profile", "P2"), ("resolve", "P2"), ("send", "P3"), ("watch", "P4"), ("logout", "P1")],
)
def test_bridge_labels_unimplemented_commands_with_their_phase(command, phase, tmp_path):
    proc = subprocess.run(
        [sys.executable, str(BRIDGE), command, "--auth", str(tmp_path / "auth")],
        capture_output=True, encoding="utf-8", errors="replace", timeout=60, cwd=str(ROOT),
    )
    payload = json.loads([ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1])
    assert payload["ok"] is False
    assert payload["error"] == "not_implemented"
    assert payload["phase"] == phase, command + " 的阶段标错了"
    assert "还没接入" in payload["message"], "中文提示不该变成乱码（UTF-8 两端要对齐）"


def test_bridge_unknown_command_lists_known_ones(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(BRIDGE), "nope"],
        capture_output=True, encoding="utf-8", errors="replace", timeout=60, cwd=str(ROOT),
    )
    payload = json.loads([ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1])
    assert payload["ok"] is False
    assert payload["error"] == "unknown_command"
    assert "contacts" in payload["known"]


# ── 运行依赖体检 / 修复（照「视频复刻」hypit_local 那套）────────────────────
BUNDLED_PYTHON = ROOT / "python" / "python.exe"


def test_runtime_status_lists_dependencies(sandbox_engine):
    data = sandbox_engine.runtime_status()
    assert data["ok"] is True
    keys = [item["key"] for item in data["dependencies"]]
    assert keys == ["telethon", "pyaes", "bridge"], "体检要一条条列出来，缺哪条一目了然"
    for item in data["dependencies"]:
        assert item["label"] and item["detail"], "每条都要有中文名和说明"
    assert "status" in data["install"]
    assert data["install"]["running"] is False


def test_missing_module_points_at_the_offline_wheel(sandbox_engine):
    telethon = [item for item in sandbox_engine.runtime_dependencies() if item["key"] == "telethon"][0]
    if not telethon["ok"]:
        assert "离线包" in telethon["detail"], "缺依赖时要告诉用户离线包在不在，否则点修复也不知道能不能成"


def test_repair_is_wired_end_to_end():
    api = (ROOT / "backend" / "app" / "api" / "native_telegram.py").read_text(encoding="utf-8")
    assert '/api/native-telegram/runtime/status' in api
    assert '/api/native-telegram/runtime/install' in api
    html = (ROOT / "static" / "views" / "personal-telegram.html").read_text(encoding="utf-8")
    assert 'id="personalTelegramRepairDeps"' in html, "二级界面上必须有「安装 / 修复运行依赖」按钮"
    assert 'id="personalTelegramDepsList"' in html
    js = (ROOT / "static" / "js" / "personal-telegram.js").read_text(encoding="utf-8")
    assert "'/api/native-telegram/runtime/install'" in js
    assert "function repairDeps()" in js and "function pollInstall()" in js


def test_runtime_repair_knows_about_telegram():
    source = (ROOT / "backend" / "app" / "services" / "runtime_dependency_repair.py").read_text(encoding="utf-8")
    assert '("telegram", "Telegram 接管", ("telethon", "pyaes"))' in source, \
        "通用『修复运行依赖』也要能修 Telegram 这一组"


def test_offline_wheels_exist_for_telegram():
    wheels = ROOT / "deps" / "wheels"
    assert list(wheels.glob("telethon-*.whl")), "deps/wheels 里必须有 telethon 的离线包（客户机没网时要靠它）"
    assert list(wheels.glob("pyaes-*.whl")), "pyaes 只有 sdist，要预先构建成 wheel 放进来"


def test_requirements_list_telegram_stack():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "telethon" in req and "pyaes" in req
    full = (ROOT / "requirements-full.txt").read_text(encoding="utf-8")
    assert "telethon==" in full and "pyaes==" in full


def test_bundled_runtime_can_import_telethon():
    """客户端跑的是自带的 python：离线包装没装进去，只有它能证明。"""
    if not BUNDLED_PYTHON.exists():
        pytest.skip("没有自带 python（非客户端环境）")
    proc = subprocess.run(
        [str(BUNDLED_PYTHON), str(BRIDGE), "status", "--auth", str(ROOT / "data" / "native_telegram" / "session")],
        capture_output=True, encoding="utf-8", errors="replace", timeout=90, cwd=str(ROOT),
    )
    payload = json.loads([ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1])
    assert payload["dependency"]["telethon"] is True, \
        "自带运行时里 telethon 应该已经装好（离线包在 deps/wheels，README 说的一步就能补）"