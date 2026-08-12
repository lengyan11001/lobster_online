from backend.app.services.creative_job_cloud_sync import compact_cloud_job_payload


def test_compact_cloud_job_payload_omits_base64_and_data_url_fields():
    payload = {
        "data": [
            {
                "url": "https://cdn.example.com/out.png",
                "data_url": "data:image/png;base64," + ("x" * 5000),
                "b64_json": "y" * 5000,
            }
        ],
        "raw_response": {"body": "z" * 20000},
    }

    compacted = compact_cloud_job_payload(payload)

    assert compacted["data"][0]["url"] == "https://cdn.example.com/out.png"
    assert compacted["data"][0]["data_url"]["omitted"] is True
    assert compacted["data"][0]["b64_json"]["kind"] == "base64"
    assert compacted["raw_response"]["kind"] == "raw_payload"
