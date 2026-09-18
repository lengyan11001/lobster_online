from __future__ import annotations

import asyncio

import pytest

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


FAST_STRATEGY = dict(engine.DEFAULT_STRATEGY, retry_max=0, retry_sleep=0)


def _queue_raw_task(account_id, targets):
    """????? queued ????????????????????"""
    return engine._create_wechat_task(
        account_id=account_id,
        task_type="add_friend",
        target_type="friend_keyword",
        targets=targets,
        payload={"apply_message": "", "remark": "", "tags": [], "permission": "\u670b\u53cb\u5708", "queue_only": True},
        strategy=dict(FAST_STRATEGY),
        planned_total=1,
        client_request_id="",
        start_worker=False,
        initial_status="queued",
    )


def test_bulk_import_queues_every_target(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path)
    targets = ["wx_%03d" % index for index in range(120)]

    with pytest.raises(RuntimeError):
        asyncio.run(
            engine.create_add_friend_task(
                "pc-wechat-test", targets, queue_only=True, client_request_id="plain-120"
            )
        )

    result = asyncio.run(
        engine.create_add_friend_task(
            "pc-wechat-test", targets, queue_only=True, bulk_import=True, client_request_id="bulk-120"
        )
    )

    assert result["status"] == "queued"
    assert result["queued_total"] == 120
    assert len(result["tasks"]) == 120
    assert engine.list_friend_records("pc-wechat-test")["count"] == 120


def test_bulk_import_skips_daily_enqueue_guard(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(engine, "_local_friend_request_count_today", lambda account_id: 25)

    with pytest.raises(RuntimeError):
        asyncio.run(
            engine.create_add_friend_task(
                "pc-wechat-test", ["wx_a", "wx_b"], queue_only=True, client_request_id="plain-quota"
            )
        )

    result = asyncio.run(
        engine.create_add_friend_task(
            "pc-wechat-test", ["wx_a", "wx_b"], queue_only=True, bulk_import=True, client_request_id="bulk-quota"
        )
    )

    assert result["queued_total"] == 2
    assert all(item["status"] == "queued" for item in result["tasks"])


def test_daily_quota_exhausted_defers_task_instead_of_burning_it(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path)
    task = _queue_raw_task("pc-wechat-test", ["wx_a", "wx_b", "wx_c"])

    def _quota_reached(account_id):
        raise engine.FriendAddDailyLimitReached("daily friend add limit reached: 20")

    monkeypatch.setattr(engine, "_enforce_local_friend_add_rate", _quota_reached)
    claimed = engine._claim_next_queued_friend_task("pc-wechat-test")
    outcome = asyncio.run(engine._process_add_friend_task(claimed))

    assert outcome["deferred"] is True
    assert outcome["remaining"] == 3
    row = engine._task_row_by_id(task["id"])
    assert row["status"] == "queued"
    assert (row["processed"], row["success"], row["failed"]) == (0, 0, 0)
    assert row["payload"]["pending_targets"] == ["wx_a", "wx_b", "wx_c"]

    async def _no_sleep(strategy, idx, total, *, kind):
        return None

    async def _fake_run(func, account_id, target, **kwargs):
        return {"ok": True}

    monkeypatch.setattr(engine, "_sleep_between_targets", _no_sleep)
    monkeypatch.setattr(engine, "_run_local_wechat_async", _fake_run)
    monkeypatch.setattr(engine, "_enforce_local_friend_add_rate", lambda account_id: None)

    resumed = engine._claim_next_queued_friend_task("pc-wechat-test")
    outcome2 = asyncio.run(engine._process_add_friend_task(resumed))

    assert outcome2 == {"deferred": False, "status": "success", "processed": 3, "success": 3, "failed": 0}
    row2 = engine._task_row_by_id(task["id"])
    assert row2["status"] == "success"
    assert row2["payload"]["pending_targets"] == []


def test_scheduler_waits_for_quota_reset_before_reclaiming(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path)
    asyncio.run(
        engine.create_add_friend_task(
            "pc-wechat-test", ["first", "second"], queue_only=True, bulk_import=True, client_request_id="defer-1"
        )
    )
    calls = []
    deferred_ids = set()

    async def fake_process(task):
        calls.append(task["targets"][0])
        if task["id"] not in deferred_ids:
            deferred_ids.add(task["id"])
            engine._finish_task(task["id"], "queued", 0, 0, 0, "daily friend add limit reached: 20")
            return {"deferred": True}
        engine._finish_task(task["id"], "success", 1, 1, 0, "")
        return {"deferred": False}

    monkeypatch.setattr(engine, "_process_add_friend_task", fake_process)
    monkeypatch.setattr(engine, "_friend_add_quota_reset_seconds", lambda: 0.3)

    async def run():
        engine.save_friend_add_control("pc-wechat-test", interval_seconds=1)
        await engine.start_friend_add_queue("pc-wechat-test")
        await asyncio.sleep(0.15)
        # ??????????????????????????????
        assert calls == ["first"]
        await asyncio.sleep(1.6)
        assert "second" in calls
        await engine.stop_friend_add_queue("pc-wechat-test")

    asyncio.run(run())


def test_add_friend_body_accepts_bulk_import_chunk():
    from backend.app.api.native_wechat import AddFriendBody

    body = AddFriendBody(
        account_id="pc-wechat-test",
        keywords=["wx_%03d" % index for index in range(100)],
        bulk_import=True,
        queue_only=True,
    )

    assert body.bulk_import is True
    assert len(body.keywords) == 100
