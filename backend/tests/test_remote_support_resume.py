from __future__ import annotations

import json
from pathlib import Path

from backend.app.api import settings_api


def _prepare_state(tmp_path: Path, monkeypatch, enabled: bool) -> Path:
    state_file = tmp_path / "data" / "remote_support.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(json.dumps({"enabled": enabled}), encoding="utf-8")
    monkeypatch.setattr(settings_api, "_TODSK_STATE_FILE", state_file)
    monkeypatch.setattr(settings_api, "_TODSK_PROCESS", None)
    return state_file


def test_resume_remote_support_starts_agent_from_persisted_enabled_state(tmp_path, monkeypatch):
    _prepare_state(tmp_path, monkeypatch, True)
    agent = tmp_path / "BHZN-ToDesk-Agent.exe"
    agent.write_bytes(b"stub")
    started: list[Path] = []
    monkeypatch.setattr(settings_api, "_todesk_agent_path", lambda: agent)
    monkeypatch.setattr(settings_api, "_todesk_process_alive", lambda: False)
    monkeypatch.setattr(settings_api, "_start_todesk_process", lambda exe: started.append(exe))

    assert settings_api.resume_remote_support_if_enabled() is True
    assert started == [agent]


def test_resume_remote_support_does_not_start_agent_when_disabled(tmp_path, monkeypatch):
    _prepare_state(tmp_path, monkeypatch, False)
    started: list[Path] = []
    monkeypatch.setattr(settings_api, "_start_todesk_process", lambda exe: started.append(exe))

    assert settings_api.resume_remote_support_if_enabled() is False
    assert started == []


def test_resume_remote_support_keeps_enabled_state_when_agent_missing(tmp_path, monkeypatch):
    state_file = _prepare_state(tmp_path, monkeypatch, True)
    monkeypatch.setattr(settings_api, "_todesk_agent_path", lambda: None)

    assert settings_api.resume_remote_support_if_enabled() is False
    assert json.loads(state_file.read_text(encoding="utf-8"))["enabled"] is True
