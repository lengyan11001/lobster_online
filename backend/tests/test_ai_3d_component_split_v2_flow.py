import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.api import ai_3d_model


ROOT = Path(__file__).resolve().parents[2]


def _function_body(script: str, name: str) -> str:
    marker = f"function {name}("
    start = script.index(marker)
    next_function = script.find("\n  function ", start + len(marker))
    return script[start:] if next_function == -1 else script[start:next_function]


def test_component_split_v2_initial_generation_asks_for_instruction() -> None:
    script = (ROOT / "static" / "js" / "ai-3d-model.js").read_text(encoding="utf-8")
    body = _function_body(script, "startComponentsJob")

    assert "componentSplitMode && userInstruction === undefined" in body
    assert "currentWorkflowMode !== 'component_split_v2'" not in body


def test_component_images_instruction_refreshes_plan_for_missing_parts() -> None:
    assert ai_3d_model._should_refresh_component_plan(
        component_split_mode=True,
        plan_only=False,
        role_filter="",
        user_instruction="补一个可拆卸的蓝色背包部件",
    )


def test_merge_component_ai_plan_preserves_existing_and_appends_new_parts() -> None:
    existing = {
        "asset_type": "character",
        "parts": [
            {"role": "head", "label": "头部", "image_prompt": "old head"},
            {"role": "body", "label": "身体", "image_prompt": "old body"},
        ],
    }
    incoming = {
        "asset_type": "character",
        "strategy": "part_batch",
        "parts": [
            {"role": "body", "label": "身体更新", "image_prompt": "new body"},
            {"role": "backpack", "label": "背包", "image_prompt": "new backpack"},
        ],
    }

    merged = ai_3d_model._merge_component_ai_plan(existing, incoming)

    assert [part["role"] for part in merged["parts"]] == ["head", "body", "backpack"]
    assert merged["parts"][1]["image_prompt"] == "new body"
    assert merged["parts"][2]["label"] == "背包"


def test_ai_3d_job_delete_rejects_running_job(monkeypatch) -> None:
    monkeypatch.setattr(ai_3d_model.store, "load_job", lambda job_id: {"job_id": job_id, "status": "splitting_parts"})

    with pytest.raises(HTTPException) as exc:
        ai_3d_model._delete_ai3d_job("running-job")

    assert exc.value.status_code == 409


def test_ai_3d_job_delete_removes_job_directory(tmp_path, monkeypatch) -> None:
    job_dir = tmp_path / "old-job"
    job_dir.mkdir()
    (job_dir / "manifest.json").write_text('{"job_id":"old-job","status":"failed"}', encoding="utf-8")

    monkeypatch.setattr(ai_3d_model.store, "load_job", lambda job_id: {"job_id": job_id, "status": "failed"})
    monkeypatch.setattr(ai_3d_model.store, "job_dir", lambda job_id: job_dir)

    result = ai_3d_model._delete_ai3d_job("old-job")

    assert result == {"ok": True, "job_id": "old-job"}
    assert not job_dir.exists()


def test_ai_3d_config_reads_meshy_balance_from_server(monkeypatch) -> None:
    class FakeRequest:
        headers = {}

    async def fail_local_balance():
        pytest.fail("online must not query Meshy balance from the local machine")

    async def fake_server_balance(_request):
        return {"configured": True, "balance": 88, "balance_unit": "credits"}

    monkeypatch.setattr(ai_3d_model.meshy, "is_configured", lambda: False)
    monkeypatch.setattr(ai_3d_model.meshy, "get_balance", fail_local_balance)
    monkeypatch.setattr(ai_3d_model, "_query_server_meshy_balance", fake_server_balance)

    data = asyncio.run(ai_3d_model.ai_3d_model_config(FakeRequest()))

    assert data["configured"] is True
    assert data["balance"] == 88
    assert data["balance_source"] == "server"
