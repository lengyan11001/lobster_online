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


def test_viral_tvc_records_have_a_delete_button():
    script = _read("static/js/viral-tvc-studio.js")

    assert 'data-viral-tvc-record-delete="' in script
    assert "function deleteRecord(jobId, btn)" in script
    assert "/api/comfly-seedance-tvc/pipeline/jobs/" in script
    assert "/api/creative-jobs/" in script
    assert "deleteRecord(btn.getAttribute('data-viral-tvc-record-delete'), btn)" in script


def test_ecommerce_detail_records_have_a_delete_button():
    script = _read("static/js/comfly-ecommerce-detail.js")
    api = _read("backend/app/api/comfly_ecommerce_detail.py")

    assert 'data-task-delete="' in script
    assert "ecom-task-card-delete" in script
    assert "function _deleteRecentJob(jobId, btn)" in script
    assert "_deleteRecentJob(btn.getAttribute('data-task-delete') || '', btn)" in script
    assert '@router.delete("/api/comfly-ecommerce-detail/pipeline/jobs/{job_id}")' in api


def test_cutcli_template_records_have_a_delete_button():
    script = _read("static/js/cutcli-template-studio.js")
    api = _read("backend/app/api/cutcli_templates_local.py")

    assert 'data-cutcli-delete-job="' in script
    assert "function deleteJob(jobId, btn)" in script
    assert "findJobCard(grid, jobId)" in script
    assert '@router.delete("/api/cutcli/local/templates/jobs/{job_id}"' in api


def test_extra_workbench_asset_versions_are_bumped():
    index = _read("static/index.html")
    registry = _read("static/js/view-registry.js")

    assert "comfly-ecommerce-detail.js?v=20260914-record-delete-v1" in index
    assert "cutcli-template-studio.js?v=20260914-record-delete-v1" in index
    assert "viral-tvc-studio.js?v=20260914-record-delete-v1" in registry
