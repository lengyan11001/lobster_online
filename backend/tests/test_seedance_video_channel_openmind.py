"""影梦 1.0（yunwu-veo3.1-plus）必须走 OpenMind，不许再打 yunwu。

背景（2026-09-30）：分镜/创意视频里选「影梦 1.0 Plus」时，客户端把请求固定成
channel=yunwu，上游 yunwu 余额不足，整段报「余额不足，未成功提交任何分镜任务」。
用户口径：生成能力统一走 OpenMind / 我们自己的通道，yunwu 通道彻底停用。

本测试锁死这条链路：H5 映射、分镜台接口层、带货整包、前端工作台、技能管线都不许再出现 yunwu 通道。
"""
from __future__ import annotations

from pathlib import Path

from backend.app.api import h5_chat_channel as channel

ROOT = Path(__file__).resolve().parents[2]


def test_h5_yingmeng_1_0_maps_to_openmind():
    assert channel._seedance_tvc_video_request({"model": "yunwu-veo3.1-plus"}) == ("veo3.1", "openmind")


def test_seedance_video_chains_have_no_yunwu_channel():
    api_src = (ROOT / "backend/app/api/comfly_seedance_tvc.py").read_text(encoding="utf-8")
    assert 'video_channel = "yunwu"' not in api_src
    assert api_src.count('video_channel = "openmind"') >= 2

    daihuo_src = (ROOT / "backend/app/api/comfly_daihuo.py").read_text(encoding="utf-8")
    assert '"channel": "yunwu"' not in daihuo_src

    h5_src = (ROOT / "backend/app/api/h5_chat_channel.py").read_text(encoding="utf-8")
    assert 'return "veo3.1", "yunwu"' not in h5_src
    assert 'return "veo3.1", "openmind"' in h5_src

    for rel in (
        "static/js/comfly-seedance-tvc-studio.js",
        "static/js/viral-tvc-studio.js",
    ):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "channel: 'yunwu'" not in src
        assert "channel: 'openmind'" in src

    for rel in (
        "skills/comfly_seedance_tvc_video/scripts/comfly_seedance_storyboard_pipeline.py",
        "skills/comfly_veo3_daihuo_video/scripts/comfly_storyboard_pipeline.py",
    ):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert '        return "yunwu"\n' not in src, rel
