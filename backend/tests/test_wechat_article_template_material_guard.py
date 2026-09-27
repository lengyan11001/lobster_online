"""复刻资料来源守卫：IP 人设模板没选资料必须 400 拦住，且不兜底。"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from backend.app.api import wechat_article as wa


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.content = b"{}"

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, default_item, templates, status_code=200):
        self._default_item = default_item
        self._templates = templates
        self._status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        if url.endswith("/api/ip-content/personal-default"):
            return _FakeResponse({"item": self._default_item}, status_code=self._status_code)
        if url.endswith("/api/ip-content/schedule-templates"):
            return _FakeResponse({"items": self._templates}, status_code=self._status_code)
        raise AssertionError(url)


def _resolve(monkeypatch, default_item, templates):
    monkeypatch.setattr(wa, "_server_proxy_base", lambda: "http://server.test")
    monkeypatch.setattr(wa.httpx, "AsyncClient", lambda **kwargs: _FakeClient(default_item, templates))
    return asyncio.run(wa._resolve_template_material("tok", "inst"))


def test_current_template_without_material_is_blocked(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        _resolve(
            monkeypatch,
            {"id": 1, "meta": {"current_template_id": "t2"}},
            [{"id": "t2", "name": "空模板", "memory_doc_ids": [], "survey_id": None, "requirements": {}}],
        )
    assert exc.value.status_code == 400
    assert "还没有选资料" in exc.value.detail


def test_no_fallback_to_other_template_with_material(monkeypatch):
    """当前模板为空时，不允许回退到“资料最全的模板”。"""
    with pytest.raises(HTTPException) as exc:
        _resolve(
            monkeypatch,
            {"id": 1, "meta": {"current_template_id": "t2"}},
            [
                {"id": "t2", "name": "当前空模板", "memory_doc_ids": []},
                {"id": "t9", "name": "别的有资料模板", "memory_doc_ids": ["doc-9"], "survey_id": 8},
            ],
        )
    assert exc.value.status_code == 400


def test_template_with_memory_doc_passes(monkeypatch):
    out = _resolve(
        monkeypatch,
        {"id": 1, "meta": {"current_template_id": "t2"}},
        [{"id": "t2", "name": "有资料", "memory_doc_ids": ["doc-1"], "requirements": {}}],
    )
    assert out["memory_doc_ids"] == ["doc-1"]


def test_template_with_survey_only_passes(monkeypatch):
    out = _resolve(
        monkeypatch,
        {"id": 1, "meta": {"current_template_id": "t2"}},
        [{"id": "t2", "name": "只有资料调查", "memory_doc_ids": [], "survey_id": 5}],
    )
    assert out["survey_ids"] == ["5"]


def test_read_failure_is_not_reported_as_missing_material(monkeypatch):
    """接口读不到（500/网络）时必须报"读取失败"，不能误报"没选资料"。"""
    monkeypatch.setattr(wa, "_server_proxy_base", lambda: "http://server.test")
    monkeypatch.setattr(
        wa.httpx, "AsyncClient",
        lambda **kwargs: _FakeClient({}, [], status_code=500),
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wa._resolve_template_material("tok", "inst"))
    assert exc.value.status_code == 503
    assert "读取 IP 人设模板失败" in exc.value.detail
    assert "还没有选资料" not in exc.value.detail


def test_missing_cloud_base_is_reported_as_read_failure(monkeypatch):
    monkeypatch.setattr(wa, "_server_proxy_base", lambda: "")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wa._resolve_template_material("tok", "inst"))
    assert exc.value.status_code == 503


class _PipelineBody:
    idea = ""
    topic = ""
    source_url = "https://mp.weixin.qq.com/s/abc"
    memory_document_ids = []
    memory_document_titles = []
    survey_ids = []
    survey_names = []
    extra_material = ""
    style = ""
    audience = ""
    theme = "professional-clean"
    include_images = False
    image_model = ""
    image_style = ""
    image_aspect_ratio = "3:2"
    image_count = 1
    selected_image_urls = []
    selected_asset_ids = []
    upload_article_images = False


class _PipelineSentinel(Exception):
    pass


class _PipelineUser:
    id = 31


def test_pipeline_accepts_remix_without_idea(monkeypatch):
    """复刻只给链接、idea 为空时不能报"请输入主题"。"""
    async def fake_generate(*args, **kwargs):
        raise _PipelineSentinel()

    monkeypatch.setattr(wa, "generate_wechat_article", fake_generate)
    with pytest.raises(_PipelineSentinel):
        asyncio.run(wa.run_wechat_article_pipeline(_PipelineBody(), None, _PipelineUser(), None))


def test_pipeline_still_requires_idea_or_link():
    body = _PipelineBody()
    body.source_url = ""
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wa.run_wechat_article_pipeline(body, None, _PipelineUser(), None))
    assert exc.value.status_code == 400
    assert "主题" in exc.value.detail
