from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.api import creative_film_studio as module
from backend.app.api.auth import _ServerUser, get_current_user_for_local


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[get_current_user_for_local] = lambda: _ServerUser(id=31)
    return TestClient(app)


def test_moment_image_failure_is_scoped_and_later_records_continue(monkeypatch):
    writebacks = []

    monkeypatch.setattr(module, "_clean_ip_moment_records", lambda records, limit: records[:limit])
    monkeypatch.setattr(module, "_ip_moment_prompts", lambda record: [f"{record['record_id']}-{index}" for index in range(1, 4)])
    monkeypatch.setattr(module, "_ip_moment_direct_prompt", lambda _record, prompt, _index, _extra: (prompt, "variant"))
    monkeypatch.setattr(module, "_compose_direct_image_prompt", lambda prompt, _memory: prompt)

    async def _generate(_request, _user, prompt, _model, _ratio, reference_image_urls=None):
        if prompt.startswith("failed-record"):
            raise HTTPException(status_code=502, detail="上游图片路由不可用")
        return f"https://images.example/{prompt}.png"

    async def _save(**kwargs):
        return {"asset_id": kwargs["image_prompt"], "source_url": kwargs["image_url"]}

    async def _writeback(_request, _user, record_id, payload):
        writebacks.append((record_id, payload))
        return {"ok": True}

    monkeypatch.setattr(module, "_generate_image", _generate)
    monkeypatch.setattr(module, "_save_generated_image_asset", _save)
    monkeypatch.setattr(module, "_post_server_ip_moment_image", _writeback)

    response = _client().post(
        "/api/ip-content/moments/images/generate",
        headers={"Authorization": "Bearer test-token"},
        json={
            "batch_id": "batch-test",
            "records": [
                {"record_id": "failed-record", "title": "失败文案", "body": "内容一"},
                {"record_id": "completed-record", "title": "成功文案", "body": "内容二"},
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["failed_count"] == 1
    assert data["completed_count"] == 1
    failed, completed = data["records"]
    assert failed["status"] == "failed"
    assert failed["failed_index"] == 1
    assert failed["error"] == "上游图片路由不可用"
    assert completed["status"] == "completed"
    assert completed["image_count"] == 3
    assert any(
        record_id == "failed-record"
        and payload["meta"]["image_error"] == "上游图片路由不可用"
        and payload["meta"]["image_failed_index"] == 1
        for record_id, payload in writebacks
    )
    assert any(record_id == "completed-record" and payload["meta"]["image_complete"] is True for record_id, payload in writebacks)
