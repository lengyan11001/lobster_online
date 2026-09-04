from __future__ import annotations

import asyncio

from backend.app.services import native_wechat_engine as engine


def _prepare(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "native_wechat_engine.db")
    monkeypatch.setattr(engine, "_find_local_account", lambda account_id: {"account_id": account_id})
    monkeypatch.setattr(engine, "_local_friend_request_count_today", lambda account_id: 0)
    monkeypatch.setattr(engine, "get_strategy", lambda: dict(engine.DEFAULT_STRATEGY))
    engine._FRIEND_ADD_SCHEDULERS.clear()
    engine._FRIEND_ADD_WAKE_EVENTS.clear()
    engine.init_db()


def test_queue_only_splits_targets_and_does_not_start_worker(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path)
    result = asyncio.run(
        engine.create_add_friend_task(
            "pc-wechat-test",
            ["wx_a", "13800138000"],
            apply_message="hello",
            queue_only=True,
            client_request_id="request-1",
        )
    )

    assert result["status"] == "queued"
    assert len(result["tasks"]) == 2
    assert all(item["status"] == "queued" for item in result["tasks"])
    assert not engine._FRIEND_ADD_SCHEDULERS
    assert engine.list_friend_records("pc-wechat-test")["count"] == 2


def test_friend_queue_processes_one_target_per_interval(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path)
    asyncio.run(
        engine.create_add_friend_task(
            "pc-wechat-test", ["first", "second"], queue_only=True, client_request_id="request-2"
        )
    )
    processed = []

    async def fake_process(task):
        processed.append(task["targets"][0])
        engine._finish_task(task["id"], "success", 1, 1, 0, "")

    monkeypatch.setattr(engine, "_process_add_friend_task", fake_process)

    async def run():
        engine.save_friend_add_control("pc-wechat-test", interval_seconds=1)
        await engine.start_friend_add_queue("pc-wechat-test")
        await asyncio.sleep(0.2)
        assert processed == ["first"]
        await asyncio.sleep(1.2)
        assert processed == ["first", "second"]
        await engine.stop_friend_add_queue("pc-wechat-test")

    asyncio.run(run())
