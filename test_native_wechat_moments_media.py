"""朋友圈发布素材类型：图文只发图片、视频只发视频，类型按文件头判定。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.services import native_wechat_engine as engine


JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 32
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32
MOV = b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 32


def _write(tmp_path: Path, name: str, payload: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def test_kind_ignores_misleading_suffix(tmp_path):
    """线上事故：`.jpg` 名的视频被判成图片 → 微信朋友圈「处理失败」。"""
    fake_jpg = _write(tmp_path, "moments-2.jpg", MP4)
    assert engine.native_wechat_file_kind(fake_jpg, "video/mp4") == "video"
    assert engine.native_wechat_file_kind(fake_jpg, "") == "video"      # 没有 content-type 也要看文件头
    assert engine.native_wechat_file_kind(_write(tmp_path, "a.jpg", JPEG), "image/jpeg") == "image"
    assert engine.native_wechat_file_kind(_write(tmp_path, "b.png", PNG), "") == "image"
    assert engine.native_wechat_file_kind(_write(tmp_path, "c.webp", WEBP), "") == "image"
    assert engine.native_wechat_file_kind(_write(tmp_path, "d.mov", MOV), "") == "video"


def test_image_post_drops_video_material(tmp_path):
    """图文节点只发图片：视频素材必须被剔除并记录原因。"""
    steps = []
    files = [
        {"local_path": str(_write(tmp_path, "ok.jpg", JPEG)), "filename": "ok.jpg", "kind": "image", "size": 1024},
        {"local_path": str(_write(tmp_path, "big.jpg", JPEG)), "filename": "big.jpg", "kind": "image", "size": 90 * 1024 * 1024},
        {"local_path": str(_write(tmp_path, "v.jpg", MP4)), "filename": "v.jpg", "kind": "video", "size": 59 * 1024 * 1024},
        {"local_path": str(_write(tmp_path, "w.jpg", WEBP)), "filename": "w.jpg", "kind": "image", "size": 33882},
    ]
    kept = engine._split_moments_media(files, media_type="image_text", steps=steps)
    assert [item["filename"] for item in kept] == ["ok.jpg"]
    steps_by_name = {step["step"]: step for step in steps}
    assert steps_by_name["skip_video_material_for_image_post"]["count"] == 1
    assert steps_by_name["skip_unusable_image_material"]["count"] == 2  # 90MB 与 webp


def test_image_post_with_only_videos_fails_with_reason(tmp_path):
    files = [{"local_path": str(_write(tmp_path, "v.mp4", MP4)), "filename": "v.mp4", "kind": "video", "size": 1024}]
    with pytest.raises(RuntimeError) as excinfo:
        engine._split_moments_media(files, media_type="image_text", steps=[])
    assert "没有可用图片素材" in str(excinfo.value)


def test_video_post_drops_image_material(tmp_path):
    steps = []
    files = [
        {"local_path": str(_write(tmp_path, "v.mp4", MP4)), "filename": "v.mp4", "kind": "video", "size": 20 * 1024 * 1024},
        {"local_path": str(_write(tmp_path, "p.jpg", JPEG)), "filename": "p.jpg", "kind": "image", "size": 1024},
    ]
    kept = engine._split_moments_media(files, media_type="video", steps=steps)
    assert [item["filename"] for item in kept] == ["v.mp4"]
    assert steps[0]["step"] == "skip_unusable_image_material"


def test_video_with_image_suffix_is_renamed_to_mp4(tmp_path):
    """扩展名要和真实内容一致，微信才按视频解析。"""
    import os

    os.environ.setdefault("LOBSTER_TEST_UPLOAD_ROOT", str(tmp_path))
    src = _write(tmp_path, "moments-2.jpg", MP4)
    aligned = engine._aligned_media_path(src, "video")
    assert aligned.suffix == ".mp4" and aligned.exists() and not src.exists()


def test_moments_rejection_detects_image_side_toast(monkeypatch):
    """微信图片侧只弹「处理失败」这类提示，以前识别不到只能等 30 秒超时。"""
    monkeypatch.setattr(engine, "_moments_publish_dialog_text", lambda _root: "图片处理失败，请重试")
    assert engine._moments_publish_rejection(object()) == "处理失败"
    monkeypatch.setattr(engine, "_moments_publish_dialog_text", lambda _root: "正常文案，没有报错")
    assert engine._moments_publish_rejection(object()) == ""
