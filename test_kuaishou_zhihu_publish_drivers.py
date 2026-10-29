# -*- coding: utf-8 -*-
"""快手 / 知乎发布驱动的注册、纯函数与失败路径回归测试（不启动浏览器）。"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from publisher.drivers import DRIVERS
from publisher.platform_publish_limits import normalize_publish_texts
from skills._base import BaseDriver
from skills.kuaishou_publish import KuaishouDriver
from skills.kuaishou_publish.driver import (
    LOGIN_URL as KS_LOGIN_URL,
    MAX_HASHTAGS,
    compose_caption as ks_caption,
    is_image_media,
    limit_hashtags,
)
from skills.zhihu_publish import ZhihuDriver
from skills.zhihu_publish.driver import (
    LOGIN_URL as ZH_LOGIN_URL,
    WRITE_URL,
    compose_body as zh_body,
    media_kind,
)


def test_registry_has_new_platforms():
    assert DRIVERS.get("kuaishou") is KuaishouDriver
    assert DRIVERS.get("zhihu") is ZhihuDriver
    for key in ("kuaishou", "zhihu"):
        assert issubclass(DRIVERS[key], BaseDriver)
        assert isinstance(DRIVERS[key](), BaseDriver)


def test_login_urls_and_signature():
    assert KuaishouDriver().login_url() == KS_LOGIN_URL == "https://cp.kuaishou.com"
    assert ZhihuDriver().login_url() == ZH_LOGIN_URL == "https://www.zhihu.com/signin"
    assert WRITE_URL == "https://zhuanlan.zhihu.com/write"
    # run_publish_task 用 check_login(page, navigate=...) 调用
    for cls in (KuaishouDriver, ZhihuDriver):
        sig = inspect.signature(cls.check_login)
        assert sig.parameters["navigate"].default is True
        psig = inspect.signature(cls.publish)
        for name in ("file_path", "title", "description", "tags", "options", "cover_path"):
            assert name in psig.parameters


def test_kuaishou_hashtag_limit():
    tags, warnings = limit_hashtags("#a,#b,#c,#d,#e,#f")
    assert tags == "a,b,c,d"
    assert len(warnings) == 1 and "4" in warnings[0]
    ok, no_warn = limit_hashtags("a,b")
    assert ok == "a,b" and no_warn == []


def test_kuaishou_caption_merges_and_truncates():
    cap = ks_caption("标题", "描述", "a,b")
    assert cap == "标题\n描述\n#a #b"
    assert MAX_HASHTAGS == 4
    long_cap = ks_caption("t" * 400, "d" * 400, "a")
    assert len(long_cap) == 500


def test_kuaishou_media_detection():
    assert is_image_media("x.JPG")
    assert not is_image_media("x.mp4")


def test_zhihu_body_and_media_kind():
    assert zh_body("标题", "正文", "a,b") == "正文\n\n#a #b"
    assert zh_body("只有标题", "", "") == "只有标题"
    assert len(zh_body("t", "d" * 9000, "")) == 8000
    assert media_kind("a.mp4") == "video"
    assert media_kind("a.png") == "image"
    assert media_kind("a.txt") == "other"


@pytest.mark.asyncio
async def test_zhihu_rejects_video_without_touching_page():
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        path = f.name
    try:
        res = await ZhihuDriver().publish(None, path, "t", "d", "")
    finally:
        os.unlink(path)
    assert res["ok"] is False
    assert "视频" in res["error"]


@pytest.mark.asyncio
async def test_kuaishou_missing_file_returns_error():
    res = await KuaishouDriver().publish(None, "Z:/nope.mp4", "t", "d", "")
    assert res["ok"] is False
    assert "文件不存在" in res["error"]


def test_limits_kuaishou_and_zhihu():
    title, desc, tags, warns = normalize_publish_texts(
        "kuaishou", "a.mp4", "标题", "描述", "#a,#b,#c,#d,#e,#f"
    )
    assert title == ""
    assert desc == "标题\n描述\n#a #b #c #d"
    assert tags == "a,b,c,d"
    assert any("话题标签" in w for w in warns)

    t2, d2, _g, w2 = normalize_publish_texts("zhihu", "a.png", "标" * 120, "正" * 8100, "x")
    assert len(t2) == 100 and len(d2) == 8000
    assert len(w2) == 2


def test_backend_supported_platforms_include_new_ones():
    import pathlib

    src = pathlib.Path("backend/app/api/publish.py").read_text(encoding="utf-8")
    assert '"zhihu": {"name": "知乎"' in src
    assert '"kuaishou": {"name": "快手"' in src