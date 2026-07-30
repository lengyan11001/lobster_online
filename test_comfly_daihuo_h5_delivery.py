import json

from backend.app.api import comfly_daihuo
from backend.app.api.h5_chat_channel import _scheduled_tvc_completion


def _completed_job():
    return {
        "job_id": "7e760dec6d7744ddb174e5e5cb170946",
        "user_id": 31,
        "status": "completed",
        "result": {
            "run_dir": r"D:\lobster_online\skills\runs\job",
            "final_video": {
                "path": r"D:\lobster_online\skills\runs\job\merged_output.mp4",
                "url": None,
                "kind": "merged_local",
            },
        },
        "saved_assets": [
            {
                "source_url": "",
                "task_id": "merged_final",
                "asset": {
                    "asset_id": "merged-video-asset",
                    "source_url": "",
                    "media_type": "video",
                },
            }
        ],
    }


def test_completed_job_uploads_local_final_video_and_backfills_result(monkeypatch):
    updates = []
    monkeypatch.setattr(
        comfly_daihuo,
        "get_asset_public_url",
        lambda asset_id, user_id, request, db: "https://cdn.example.com/merged-output.mp4",
    )
    monkeypatch.setattr(comfly_daihuo, "update_job", lambda job_id, **fields: updates.append((job_id, fields)) or True)
    monkeypatch.setattr(comfly_daihuo, "get_job", lambda job_id: None)

    repaired = comfly_daihuo._repair_completed_job_delivery(
        _completed_job(),
        request=object(),
        db=object(),
    )

    assert repaired["result"]["final_video"]["url"] == "https://cdn.example.com/merged-output.mp4"
    assert repaired["saved_assets"][0]["source_url"] == "https://cdn.example.com/merged-output.mp4"
    assert repaired["saved_assets"][0]["asset"]["source_url"] == "https://cdn.example.com/merged-output.mp4"
    assert updates[0][0] == "7e760dec6d7744ddb174e5e5cb170946"


def test_completed_job_response_hides_local_paths():
    response = comfly_daihuo._job_status_response(_completed_job(), include_full=True)
    encoded = json.dumps(response, ensure_ascii=False)

    assert "D:\\lobster_online" not in encoded
    assert "run_dir" not in response["result"]
    assert "path" not in response["result"]["final_video"]


def test_scheduled_tvc_completion_returns_media_and_friendly_text():
    result = {
        "capability_id": "comfly.daihuo.pipeline",
        "result": {
            "status": "completed",
            "result": {
                "final_video": {
                    "url": "https://cdn.example.com/merged-output.mp4",
                    "kind": "merged_local",
                }
            },
            "saved_assets": [
                {
                    "task_id": "merged_final",
                    "source_url": "https://cdn.example.com/merged-output.mp4",
                    "asset": {"asset_id": "merged-video-asset", "media_type": "video"},
                }
            ],
        },
    }

    text, payload = _scheduled_tvc_completion(result)

    assert text == "爆款TVC已生成，点击查看成片。"
    assert payload["media_urls"] == ["https://cdn.example.com/merged-output.mp4"]
    assert payload["result_refs"]["asset_ids"] == ["merged-video-asset"]
    assert "D:\\" not in text
