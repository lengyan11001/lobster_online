"""微信会话读不到时的 UIA 现场快照（2026-10-13）。

线上两台机器（微信 4.1.7.59）反复出现「界面可见 N 个会话，wxauto4 + UIA 双失败」，
只凭错误文本分不清是窗口没在前台、Tab 不在聊天页、还是控件类名变了。
这里验证：失败时会抓一份 UIA 现场，并且它会挂到下一次诊断记录里（只挂一次）。
"""
import json

from backend.app.services import native_wechat_engine as engine


def _stub_probes(monkeypatch, log_path):
    monkeypatch.setattr(engine, "NATIVE_WECHAT_DIAGNOSTIC_LOG", log_path)
    monkeypatch.setattr(engine, "_wechat_process_brief", lambda: [])
    monkeypatch.setattr(engine, "_scan_local_wechat_windows", lambda **_kwargs: [])
    monkeypatch.setattr(engine, "_raw_wechat_window_candidates", lambda: {"ok": True, "items": []})
    monkeypatch.setattr(engine, "_ensure_local_wechat_window_visible", lambda **_kwargs: {"ok": False})


def test_snapshot_is_attached_to_next_diagnostic_once(tmp_path, monkeypatch):
    log_path = tmp_path / "native_wechat_diagnostics.jsonl"
    _stub_probes(monkeypatch, log_path)
    monkeypatch.setattr(
        engine,
        "_LAST_WECHAT_UIA_SNAPSHOT",
        {"hwnd": 14156450, "node_count": 16, "top_classes": [["Qt51514QWindowIcon", 16]], "foreground_is_wechat": False},
    )

    engine.create_native_wechat_diagnostic(
        "auto_reply_run_once", error="会话列表读取为空", account_id="pc-wechat-default"
    )
    engine.create_native_wechat_diagnostic("auto_reply_run_once", error="第二次", account_id="pc-wechat-default")

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records) == 2
    first = records[0]["extra"]["wechat_uia_snapshot"]
    assert first["node_count"] == 16
    assert first["foreground_is_wechat"] is False
    # 只挂一次，避免每条诊断都重复贴同一份现场
    assert "wechat_uia_snapshot" not in records[1]["extra"]
    assert engine._LAST_WECHAT_UIA_SNAPSHOT == {}


def test_capture_without_window_does_not_raise(monkeypatch):
    monkeypatch.setattr(engine, "_local_wechat_hwnd", lambda _account_id: 0)
    monkeypatch.setattr(engine, "_LAST_WECHAT_UIA_SNAPSHOT", {})

    engine._capture_wechat_uia_failure("pc-wechat-default")

    snapshot = engine._LAST_WECHAT_UIA_SNAPSHOT
    assert snapshot["account_id"] == "pc-wechat-default"
    assert snapshot["hwnd"] == 0
    assert "error" not in snapshot


def test_merge_skips_when_extra_already_has_snapshot(monkeypatch):
    monkeypatch.setattr(engine, "_LAST_WECHAT_UIA_SNAPSHOT", {"node_count": 3})
    merged = engine._merge_wechat_uia_snapshot({"wechat_uia_snapshot": {"node_count": 9}})
    assert merged["wechat_uia_snapshot"]["node_count"] == 9
    # 已经有现场就不覆盖，但缓存要留着给下一条诊断
    assert engine._LAST_WECHAT_UIA_SNAPSHOT == {"node_count": 3}
