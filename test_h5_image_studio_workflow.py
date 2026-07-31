import pytest

from backend.app.api import h5_chat_channel as channel


def test_image_studio_text_prompt_is_unchanged_without_reference():
    assert channel._image_studio_prompt_with_reference_hints(
        "一张自然光产品图",
        [],
        [],
    ) == "一张自然光产品图"


@pytest.mark.asyncio
async def test_h5_image_studio_workflow_supports_text_to_image(monkeypatch):
    submitted = {}

    async def post_form(_path, fields, **_kwargs):
        submitted.update(fields)
        return {"ok": True, "job_id": "text-image-job"}

    async def get_job(_path, **_kwargs):
        return {
            "ok": True,
            "status": "completed",
            "images": [{"asset_id": "text-image-asset", "url": "https://example.com/text-image.png"}],
        }

    monkeypatch.setattr(channel, "_post_local_api_form", post_form)
    monkeypatch.setattr(channel, "_get_local_api_json", get_job)

    result = await channel._run_client_workflow_action(
        "image_studio_generate",
        {"prompt": "一张自然光产品图", "aspect_ratio": "1:1"},
        headers={},
        run_id="run-text-image",
    )

    assert submitted["prompt"] == "一张自然光产品图"
    assert submitted["reference_image_urls"] == ""
    assert submitted["aspect_ratio"] == "1:1"
    assert result["result_refs"]["asset_ids"] == ["text-image-asset"]


@pytest.mark.asyncio
async def test_h5_image_studio_workflow_submits_reference_and_returns_assets(monkeypatch):
    submissions = []

    async def post_form(path, fields, **kwargs):
        submissions.append((path, fields, kwargs))
        return {"ok": True, "job_id": "image-job-1", "status": "running"}

    async def get_job(path, **kwargs):
        assert path == "/api/comfly-image-studio/jobs/image-job-1"
        return {
            "ok": True,
            "job_id": "image-job-1",
            "status": "completed",
            "images": [
                {
                    "asset_id": "image-asset-1",
                    "source_url": "https://example.com/final.png",
                    "data_url": "data:image/png;base64,not-returned",
                }
            ],
            "saved_assets": [
                {
                    "index": 0,
                    "source_url": "https://example.com/final.png",
                    "asset": {"asset_id": "image-asset-1", "source_url": "https://example.com/final.png"},
                }
            ],
            "meta": {"aspect_ratio": "9:16", "reference_count": 1},
        }

    monkeypatch.setattr(channel, "_post_local_api_form", post_form)
    monkeypatch.setattr(channel, "_get_local_api_json", get_job)

    result = await channel._run_client_workflow_action(
        "image_studio_generate",
        {
            "prompt": "只修改包装上的标题",
            "model": "gpt-image-2",
            "aspect_ratio": "9:16",
            "quality": "high",
            "background": "auto",
            "reference_image_urls": ["https://example.com/reference.png"],
            "reference_purposes": ["local_edit"],
        },
        headers={"Authorization": "Bearer test"},
        run_id="run-1",
    )

    path, fields, _kwargs = submissions[0]
    assert path == "/api/comfly-image-studio/generate/start"
    assert fields["reference_image_urls"] == "https://example.com/reference.png"
    assert "参考图1用于局部修改" in fields["prompt"]
    assert fields["prompt"].endswith("用户提示词：只修改包装上的标题")
    assert fields["model"] == "gpt-image-2"
    assert fields["aspect_ratio"] == "9:16"
    assert result["images"] == [
        {
            "asset_id": "image-asset-1",
            "url": "https://example.com/final.png",
            "source_url": "https://example.com/final.png",
            "media_type": "image",
        }
    ]
    assert result["result_refs"]["asset_ids"] == ["image-asset-1"]
    assert result["result_refs"]["urls"] == ["https://example.com/final.png"]
    assert "data_url" not in result["images"][0]
    assert channel._client_workflow_result_text("image_studio_generate", result) == "AI设计图已生成，共 1 张。"
