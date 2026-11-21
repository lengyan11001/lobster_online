from __future__ import annotations

import concurrent.futures
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_pipeline():
    path = ROOT / "skills/comfly_seedance_tvc_video/scripts/comfly_seedance_storyboard_pipeline.py"
    name = "test_seedance_recovery_pipeline"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Logger:
    def __init__(self):
        self.segments = []
        self.steps = []

    def segment(self, *args, **kwargs):
        self.segments.append((args, kwargs))

    def step(self, *args, **kwargs):
        self.steps.append((args, kwargs))


def test_soft_delete_keeps_task_ids_for_recovery(tmp_path, monkeypatch):
    from backend.app.services import comfly_seedance_tvc_job_store as store

    monkeypatch.setattr(store, "_JOB_STORE_FILE", tmp_path / "jobs.json")
    store._JOBS.clear()
    try:
        job_id = store.create_job_record(
            user_id=7,
            inp={"prompt": "60s"},
            auto_save=True,
            job_output_dir=str(tmp_path / "job"),
        )
        tasks = [{"index": 6, "task_id": "2123338043036667905", "video_url": ""}]
        assert store.update_job(job_id, video_tasks=tasks)
        assert store.delete_job(job_id, user_id=7) is True

        assert store.get_job(job_id) is None
        kept = store.get_job_any(job_id)
        assert kept is not None
        assert kept["deleted"] is True
        assert kept["video_tasks"] == tasks
        assert store.list_jobs_for_user(7) == []
    finally:
        store._JOBS.clear()


def test_wait_segment_futures_merges_completed_segments_on_deadline():
    mod = _load_pipeline()
    logger = _Logger()
    done = concurrent.futures.Future()
    done.set_result({"index": 1, "mp4url": "https://example.test/1.mp4"})
    slow = concurrent.futures.Future()
    futures = {
        done: {"index": 1},
        slow: {"index": 6},
    }
    results = {}
    failures = {}
    mod._wait_segment_futures(
        futures,
        results,
        failures,
        logger,
        deadline_seconds=1,
        label="test",
    )

    assert results[1]["mp4url"] == "https://example.test/1.mp4"
    assert failures[6]["reason"] == "deadline"
    assert 1 in [int(args[0]) for args, _ in logger.segments]
    assert any("deadline" in str(args[0]) or "deadline" in str(kwargs) for args, kwargs in logger.steps)


def test_video_tasks_from_manifest_keeps_task_channel(tmp_path):
    from backend.app.api.comfly_seedance_tvc import _video_tasks_from_manifest

    run_dir = tmp_path / "run_20261119_165000"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "config": {"segment_count": 1, "segment_duration_seconds": 10},
                "steps": {},
                "segments": {"6": {"final": {"status": "success", "task_id": "2123338043036667905"}}},
                "shots": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (run_dir / "segment_06_final.json").write_text(
        json.dumps(
            {
                "index": 6,
                "video_task_id": "2123338043036667905",
                "video_channel": "seedance",
                "video_model": "doubao-seedance-2-0-260128",
                "video_base_url": "https://bhzn.top/api/comfly-proxy",
                "status": "succeeded",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    tasks = _video_tasks_from_manifest({"job_output_dir": str(tmp_path)})
    assert tasks
    assert tasks[0]["task_id"] == "2123338043036667905"
    assert tasks[0]["video_channel"] == "seedance"
