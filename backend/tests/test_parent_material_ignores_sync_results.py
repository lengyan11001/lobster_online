# -*- coding: utf-8 -*-
"""朋友圈图文不能把采集回来的视频号素材当配图（2026-11-04 线上：微信弹「无效文件」）。"""
from __future__ import annotations

from backend.app.api import h5_chat_channel as ch


def test_sync_result_video_is_not_publish_material():
    payload = {
        "sync_results": {
            "result_snapshot": {
                "items": [
                    {
                        "platform": "wechat_channels",
                        "media_type": "video",
                        "public_url": "http://wxapp.tc.qq.com/251/20302/stodownload?encfilekey=x",
                    }
                ]
            }
        },
        "records_by_task": {
            "moments_candidate": [
                {"image_asset_id": "img-1", "image_url": "https://tos.example/da1718b62e16.png"}
            ]
        },
    }
    material = ch._extract_parent_material(payload)
    assert material.get("media_type") == "image"
    assert material.get("asset_id") == "img-1"


def test_sync_result_video_alone_yields_no_material():
    payload = {
        "sync_results": {
            "result_snapshot": {
                "items": [
                    {"platform": "wechat_channels", "media_type": "video", "public_url": "http://wxapp.tc.qq.com/x.mp4"}
                ]
            }
        }
    }
    assert ch._extract_parent_material(payload, preferred_media_type="image") == {}
