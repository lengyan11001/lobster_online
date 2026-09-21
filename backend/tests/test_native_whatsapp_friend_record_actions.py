"""加好友失败记录的手动重试 / 删除（2026-09-21）。"""
from __future__ import annotations

import json
import pathlib
import sys
import uuid

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services import native_whatsapp_engine as engine  # noqa: E402


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "STATE_DIR", tmp_path)
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "state.db")
    return tmp_path


def _insert_task(status: str = "failed", task_type: str = "add_friend", error: str = "号码没注册 WhatsApp") -> str:
    task_id = uuid.uuid4().hex
    now = engine._now_iso()
    targets = [{"first_name": "liuxin", "last_name": "", "username": "",
                "phone": "18124655127", "country_code": "+86"}]
    payload = {"apply_message": "hello", "label": "+8618124655127",
               "pending_targets": [], "deferred_reason": ""}
    with engine._connect() as conn:
        conn.execute(
            "insert into whatsapp_tasks(id, account_id, task_type, targets, payload, status, processed, "
            "success, failed, error_message, client_request_id, created_at, updated_at) "
            "values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, engine.DEFAULT_ACCOUNT_ID, task_type, json.dumps(targets), json.dumps(payload),
             status, 1, 0, 1, error, "", now, now),
        )
    return task_id


def test_retry_resets_failed_record(isolated_db):
    task_id = _insert_task()
    result = engine.retry_friend_record(task_id)
    assert result["ok"] is True
    assert result["status"] == "queued"
    task = engine._task_row_by_id(task_id)
    assert task["status"] == "queued"
    assert int(task["failed"]) == 0
    assert str(task["error_message"]) == ""
    assert any(item["id"] == task_id and item["status"] == "queued"
               for item in engine.list_friend_records()["items"])


def test_retry_keeps_original_target(isolated_db):
    task_id = _insert_task()
    engine.retry_friend_record(task_id)
    record = [item for item in engine.list_friend_records()["items"] if item["id"] == task_id][0]
    assert record["first_name"] == "liuxin"
    assert record["phone"] == "+8618124655127"
    assert record["apply_message"] == "hello"


def test_retry_rejects_running_record(isolated_db):
    task_id = _insert_task(status="running")
    with pytest.raises(Exception) as exc:
        engine.retry_friend_record(task_id)
    assert "正在执行" in str(exc.value)


def test_delete_removes_record(isolated_db):
    task_id = _insert_task()
    result = engine.delete_friend_record(task_id)
    assert result["ok"] is True
    assert result["deleted"] == task_id
    assert engine._task_row_by_id(task_id) is None
    assert all(item["id"] != task_id for item in engine.list_friend_records()["items"])


def test_delete_rejects_running_record(isolated_db):
    task_id = _insert_task(status="running")
    with pytest.raises(Exception) as exc:
        engine.delete_friend_record(task_id)
    assert "正在执行" in str(exc.value)


def test_unknown_record_is_rejected(isolated_db):
    with pytest.raises(Exception):
        engine.retry_friend_record("does-not-exist")
    with pytest.raises(Exception):
        engine.delete_friend_record("does-not-exist")


def test_other_task_types_are_not_touchable(isolated_db):
    task_id = _insert_task(task_type="send_message")
    with pytest.raises(Exception):
        engine.retry_friend_record(task_id)
    with pytest.raises(Exception):
        engine.delete_friend_record(task_id)


def test_frontend_and_api_expose_retry_delete():
    js = (ROOT / "static" / "js" / "personal-whatsapp.js").read_text(encoding="utf-8")
    assert "data-pwa-friend-retry" in js
    assert "data-pwa-friend-delete" in js
    assert "/friends/records/" in js
    assert "确认删除" in js
    api = (ROOT / "backend" / "app" / "api" / "native_whatsapp.py").read_text(encoding="utf-8")
    assert '/api/native-whatsapp/friends/records/{task_id}/retry' in api
    assert '@router.delete("/api/native-whatsapp/friends/records/{task_id}")' in api
    registry = (ROOT / "static" / "js" / "view-registry.js").read_text(encoding="utf-8")
    # 不锁具体版本串，避免每次 bump 都改测试；只确认视图/脚本都挂上了 cache buster
    assert "/static/views/personal-whatsapp.html?v=" in registry
    assert "/static/js/personal-whatsapp.js?v=" in registry
