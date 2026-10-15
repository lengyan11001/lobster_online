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
