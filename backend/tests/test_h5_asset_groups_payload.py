from backend.app.api.h5_chat_channel import _shanjian_video_create_payload


def test_h5_payload_carries_asset_groups():
    payload = _shanjian_video_create_payload(
        {
            "use_template": True,
            "style_id": "style-1",
            "template_scene": "realMan",
            "digital_human_asset_groups": ["数字人口播素材"],
        },
        virtualman_id="vm-1",
        title="测试",
        script="口播",
        voice="",
        audio_url="https://example.com/a.mp3",
        language="zh-CN",
    )
    assert payload["digital_human_asset_groups"] == ["数字人口播素材"]
