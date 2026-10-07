# -*- coding: utf-8 -*-
"""同城爆款：没烧字幕/混 BGM 的合并草稿不能当发布素材（2026-10-06 线上事故回归）。

线上那次：同城爆款 Day1 的 merged_output.mp4（6,958,162B，无字幕无 BGM）被当成
final_video 交出去，发布动作把 6,958,162 的中间产物发到了抖音；4 分钟后发视频号用的
才是 bgm_final.mp4（3,812,890B）。这里锁死三件事：
1) 只有草稿时，发布素材必须带 draft 标（发布侧据此等待/跳过）；
2) 有字幕/BGM 成片时，只取成片，草稿不参与；
3) 节点取成片时，草稿要标 draft，成片要选 BGM 版。
"""
from __future__ import annotations

from backend.app.api import h5_chat_channel as ch

LOCAL_DRAFT_URL = "http://127.0.0.1:8000/api/comfly-ecommerce-detail/local-file/d2a032f75ffb?v=1"


def _incident_payload() -> dict:
    draft = {
        "asset_id": "",
        "url": LOCAL_DRAFT_URL,
        "path": "D:/runs/job_runs/81aa8a85/run_20261006_220059/merged_output.mp4",
        "kind": "merged_local",
        "hint": "Merged local video completed.",
    }
    return {
        "task_kind": "client_workflow",
        "action": "local_bestseller_daily_video",
        "local_result": {
            "ok": True,
            "mode": "daily_video",
            "day": 1,
            "item": {"id": "local-day-01", "day": 1, "video_url": LOCAL_DRAFT_URL, "video_asset_id": "", "final_video": draft},
            "video_result": {"final_video": draft, "job_result": {"result": {"final_video": draft}}},
        },
    }


def test_draft_merge_video_is_marked_and_not_publishable():
    payload = _incident_payload()

    assert ch._payload_needs_local_bestseller_post(payload) is True
    material = ch._extract_parent_material(payload, "video")
    assert material.get("draft") is True
    assert "merged_output.mp4" not in str(material.get("asset_id") or "")


def test_bgm_final_wins_over_merged_draft():
    payload = _incident_payload()
    bgm = {"asset_id": "b3c9d4a6178f", "source_url": "https://cdn.example/bgm_final.mp4", "kind": "local_bestseller_bgm_final"}
    captioned = {"asset_id": "a932efac7d10", "source_url": "https://cdn.example/captioned.mp4", "kind": "local_bestseller_captioned"}
    item = payload["local_result"]["item"]
    result = payload["local_result"]["video_result"]["job_result"]["result"]
    item["bgm_video"] = bgm
    item["captioned_video"] = captioned
    result["bgm_video"] = bgm
    result["captioned_video"] = captioned
    result["final_video"] = {**bgm, "kind": "local_bestseller_bgm_final"}

    assert ch._payload_needs_local_bestseller_post(payload) is False
    material = ch._extract_parent_material(payload, "video")
    assert material.get("asset_id") == "b3c9d4a6178f"
    assert not material.get("draft")


def test_local_bestseller_final_video_prefers_postprocessed_and_flags_draft():
    payload = _incident_payload()
    result = payload["local_result"]["video_result"]["job_result"]["result"]

    draft_job = {"job_id": "81aa8a85", "status": "completed", "result": result, "saved_assets": [], "post_status": "captioning"}
    draft = ch._local_bestseller_final_video(draft_job)
    assert draft.get("draft") is True
    assert draft.get("kind") == "merged_local"

    bgm = {"asset_id": "b3c9d4a6178f", "source_url": "https://cdn.example/bgm_final.mp4", "kind": "local_bestseller_bgm_final"}
    done_job = {
        "job_id": "81aa8a85",
        "status": "completed",
        "result": {**result, "bgm_video": bgm, "final_video": {**bgm, "kind": "local_bestseller_bgm_final"}},
        "saved_assets": [{"asset": bgm, "kind": "local_bestseller_bgm_final"}],
        "post_status": "completed",
        "post_stage": "completed",
    }
    done = ch._local_bestseller_final_video(done_job)
    assert done.get("asset_id") == "b3c9d4a6178f"
    assert not done.get("draft")