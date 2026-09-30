"""同城爆款韧性自测：job 重启收尾 / 字幕锁按用户 / 下载总超时 / 前端兜底标记。"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest


def _store():
    from backend.app.services import comfly_seedance_tvc_job_store as store
    return store


def test_orphan_jobs_finalized_on_load(tmp_path, monkeypatch):
    """进程重启后从磁盘加载：未完成的 job 必须被收尾成中断，已完成的不动。"""
    store = _store()
    store_file = tmp_path / "seedance_tvc_job_store.json"
    now = time.time()
    store_file.write_text(json.dumps({
        "a" * 32: {"job_id": "a" * 32, "user_id": 7, "status": "running",
                   "created_at_ts": now - 600, "updated_at_ts": now - 600,
                   "post_status": "captioning", "post_stage": "burn_subtitle",
                   "meta": {"feature": "local_bestseller"}},
        "b" * 32: {"job_id": "b" * 32, "user_id": 7, "status": "completed",
                   "created_at_ts": now - 600, "updated_at_ts": now - 600,
                   "post_status": "completed", "post_stage": "completed"},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(store, "_JOB_STORE_FILE", store_file)
    store._JOBS.clear()

    orphan = store.get_job("a" * 32)
    assert orphan is not None
    assert orphan["status"] == "failed"
    assert orphan["post_status"] == "interrupted"
    assert "重启" in (orphan.get("post_error") or "")

    done = store.get_job("b" * 32)
    assert done["status"] == "completed" and done["post_status"] == "completed"
    store._JOBS.clear()


def test_caption_lock_is_per_user():
    """字幕锁不再是全局单锁：同用户复用同一把，不同用户互不相干。"""
    from backend.app.api import comfly_seedance_tvc as mod

    assert mod._caption_lock_for(1) is mod._caption_lock_for(1)
    assert mod._caption_lock_for(1) is not mod._caption_lock_for(2)
    assert mod.CAPTION_DOWNLOAD_TIMEOUT == 300.0


def test_caption_download_timeout_releases_lock(tmp_path, monkeypatch):
    """下载卡住 -> 300s 总超时抛错并释放锁（后面的任务不再被堵死）。"""
    from backend.app.api import comfly_seedance_tvc as mod
    store = _store()

    monkeypatch.setattr(store, "_JOB_STORE_FILE", tmp_path / "jobstore.json")
    store._JOBS.clear()
    jid = store.create_job_record(
        user_id=9, inp={}, auto_save=False, job_output_dir=str(tmp_path / "job"),
        meta={"feature": "local_bestseller", "subtitle_text": "测试字幕"},
    )

    async def slow_download(url, path):
        await asyncio.sleep(5)

    monkeypatch.setattr(mod, "_select_pipeline_video_download_ref", lambda result, job=None: "http://example.test/v.mp4")
    monkeypatch.setattr(mod, "_download_video_to_path", slow_download)
    monkeypatch.setattr(mod, "CAPTION_DOWNLOAD_TIMEOUT", 0.05)

    job = store.get_job(jid)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(mod._caption_local_bestseller_video_if_needed(job_id=jid, job=job, result={}))

    assert mod._caption_lock_for(9).locked() is False
    store._JOBS.clear()


def test_frontend_caption_stale_guard_present():
    """前端兜底标记必须在（防止以后被改回去导致"永久字幕合成中"）。"""
    js = (Path(__file__).resolve().parents[2] / "static" / "js" / "local-bestseller.js").read_text(encoding="utf-8", errors="replace")
    assert "CAPTION_STALE_MS" in js
    assert "字幕合成中断" in js