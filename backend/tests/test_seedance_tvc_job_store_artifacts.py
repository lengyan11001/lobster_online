import json

from backend.app.services.comfly_seedance_tvc_job_store import read_manifest_artifacts


def test_manifest_reference_upload_does_not_mark_generated_image_ready(tmp_path):
    run_dir = tmp_path / "run_20260812_000000"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "config": {"segment_count": 1, "segment_duration_seconds": 10},
                "steps": {
                    "01_reference_upload_01": {
                        "status": "success",
                        "payload": {
                            "reference_image_url": "https://cdn.example.com/uploaded-reference.jpg"
                        },
                    }
                },
                "segments": {},
                "shots": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    artifacts = read_manifest_artifacts(str(tmp_path))

    assert artifacts is not None
    assert artifacts["image_ready_count"] == 0
    assert artifacts["segments"][0]["image_url"] == ""
    assert artifacts["segments"][0]["reference_image_url"] == "https://cdn.example.com/uploaded-reference.jpg"
