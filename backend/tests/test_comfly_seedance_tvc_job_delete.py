from backend.app.services import comfly_seedance_tvc_job_store as store


def test_delete_job_removes_the_record_for_its_owner_only(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_JOB_STORE_FILE", tmp_path / "seedance_jobs.json")
    store._JOBS.clear()
    try:
        job_id = store.create_job_record(
            user_id=7,
            inp={"prompt": "分镜测试"},
            auto_save=False,
            job_output_dir=str(tmp_path),
        )
        assert store.get_job(job_id) is not None

        # somebody else's record stays
        assert store.delete_job(job_id, user_id=8) is False
        assert store.get_job(job_id) is not None

        assert store.delete_job(job_id, user_id=7) is True
        assert store.get_job(job_id) is None

        # deleting twice is reported, not silently ignored
        assert store.delete_job(job_id, user_id=7) is False
        assert store.delete_job("", user_id=7) is False
    finally:
        store._JOBS.clear()
