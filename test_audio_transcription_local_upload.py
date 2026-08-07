import io
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import audio_transcription_local as module
from backend.app.api.auth import _ServerUser, get_current_user_for_local


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[get_current_user_for_local] = lambda: _ServerUser(id=31)
    return TestClient(app)


def test_local_audio_upload_is_saved_and_queued_without_cloud_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "JOBS_ROOT", tmp_path)
    started = []
    monkeypatch.setattr(module, "_start_upload", lambda *args: started.append(args))

    response = _client().post(
        "/api/audio-transcription/local-uploads",
        headers={"Authorization": "Bearer test-token", "X-Installation-Id": "desktop-test"},
        files={"file": ("meeting.mp3", io.BytesIO(b"audio-bytes"), "audio/mpeg")},
    )

    assert response.status_code == 202
    job = response.json()["job"]
    assert job["status"] == "queued"
    assert job["file_size"] == len(b"audio-bytes")
    assert started and started[0][1:] == (31, "Bearer test-token", "desktop-test", "bihuo")
    stored = module._read_job(31, job["job_id"])
    assert Path(stored["local_path"]).read_bytes() == b"audio-bytes"


def test_interrupted_local_upload_becomes_retryable(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "JOBS_ROOT", tmp_path)
    module._ACTIVE_TASKS.clear()
    job = {
        "job_id": "interrupted-job",
        "user_id": 31,
        "file_name": "meeting.mp3",
        "file_size": 12,
        "local_path": str(tmp_path / "meeting.mp3"),
        "status": "uploading",
        "stage": "cloud_upload",
        "attempt": 1,
        "error": "",
        "record": None,
        "created_at": module._now(),
        "updated_at": module._now(),
    }
    module._write_job(job)

    response = _client().get("/api/audio-transcription/local-uploads", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["status"] == "failed"
    assert "重试" in item["error"]


def test_active_local_upload_cannot_be_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "JOBS_ROOT", tmp_path)
    job_id = "active-job"
    source = tmp_path / "31" / job_id / "meeting.mp3"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
    module._write_job(
        {
            "job_id": job_id,
            "user_id": 31,
            "file_name": source.name,
            "file_size": source.stat().st_size,
            "local_path": str(source),
            "status": "uploading",
            "stage": "cloud_upload",
            "attempt": 1,
            "error": "",
            "record": None,
            "created_at": module._now(),
            "updated_at": module._now(),
        }
    )

    class ActiveTask:
        @staticmethod
        def done():
            return False

    monkeypatch.setitem(module._ACTIVE_TASKS, job_id, ActiveTask())
    response = _client().delete(
        f"/api/audio-transcription/local-uploads/{job_id}",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 409
    assert module._job_file(31, job_id).is_file()
    assert source.is_file()


def test_cloud_forward_disables_system_proxy(tmp_path, monkeypatch):
    source = tmp_path / "meeting.mp3"
    source.write_bytes(b"audio")
    observed = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "record": {"id": 99, "status": "processing"}}

    class FakeClient:
        def __init__(self, **kwargs):
            observed.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, **kwargs):
            observed["url"] = url
            observed["headers"] = kwargs["headers"]
            observed["file_name"] = kwargs["files"]["file"][0]
            return FakeResponse()

    monkeypatch.setattr(module.httpx, "Client", FakeClient)
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(auth_server_base="https://bhzn.test"))
    payload = module._cloud_upload(
        {
            "local_path": str(source),
            "file_name": source.name,
            "content_type": "audio/mpeg",
        },
        "Bearer test-token",
        "desktop-test",
        "bihuo",
    )

    assert payload["record"]["id"] == 99
    assert observed["trust_env"] is False
    assert observed["url"] == "https://bhzn.test/api/h5/recorder/files"
    assert observed["headers"]["Authorization"] == "Bearer test-token"


def test_online_transcription_ui_uses_local_background_queue():
    source = Path("static/js/audio-transcription.js").read_text(encoding="utf-8")
    assert "LOCAL_API_BASE" in source
    assert "/api/audio-transcription/local-uploads" in source
    assert "音频已保存到本机，正在后台上传并转写" in source
    assert "xhr.ontimeout" in source
