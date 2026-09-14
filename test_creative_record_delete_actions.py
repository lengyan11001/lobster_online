from pathlib import Path


ROOT = Path(__file__).resolve().parent


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_seedance_studio_records_have_a_delete_button():
    script = _read("static/js/comfly-seedance-tvc-studio.js")

    assert 'data-seedance-job-delete="' in script
    assert "function deleteJobRecord(jobId, btn)" in script
    assert "method: 'DELETE'" in script
    assert "/api/comfly-seedance-tvc/pipeline/jobs/" in script
    assert "/api/creative-jobs/" in script
    assert "state.recentJobs = (state.recentJobs || []).filter" in script
    assert "deleteJobRecord(btn.getAttribute('data-seedance-job-delete'), btn)" in script


def test_image_studio_records_have_a_delete_button():
    script = _read("static/js/comfly-image-studio.js")

    assert 'data-imglab-job-delete="' in script
    assert 'data-imglab-result-delete="' in script
    assert "imglab-task-card-delete" in script
    assert "function removeJobRecord(jobId, btn)" in script
    assert "function removeResultRecord(index)" in script
    assert "removeJobRecord(btn.getAttribute('data-imglab-job-delete'), btn)" in script
    assert "removeResultRecord(btn.getAttribute('data-imglab-result-delete'))" in script
    assert "renderResultSurface();" in script


def test_batch_creative_video_records_have_a_delete_button():
    script = _read("static/js/batch-creative-video.js")

    assert 'data-batch-delete="' in script
    assert "function deleteTask(index)" in script
    assert "target.closest('[data-batch-delete]')" in script
    assert "delete state.polling[index];" in script
    assert "/api/comfly-seedance-tvc/pipeline/jobs/" in script


def test_delete_button_css_and_asset_version_are_shipped():
    css = _read("static/css/index.css")
    index = _read("static/index.html")
    registry = _read("static/js/view-registry.js")

    assert ".imglab-task-card-delete" in css
    assert ".imglab-task-card { position:relative; }" in css
    assert "index.css?v=20260914-record-delete-v1" in index
    assert "comfly-image-studio.js?v=20260914-record-delete-v1" in index
    assert "comfly-seedance-tvc-studio.js?v=20260914-record-delete-v1" in index
    assert "batch-creative-video.js?v=20260914-record-delete-v1" in registry


def test_local_backend_exposes_the_pipeline_job_delete_route():
    api = _read("backend/app/api/comfly_seedance_tvc.py")
    store = _read("backend/app/services/comfly_seedance_tvc_job_store.py")

    assert '@router.delete("/api/comfly-seedance-tvc/pipeline/jobs/{job_id}")' in api
    assert "delete_job(" in api
    assert "def delete_job(job_id: str, *, user_id: int) -> bool:" in store
