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
    def __init__(self, default_item, templates):
        self._default_item = default_item
        self._templates = templates

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        if url.endswith("/api/ip-content/personal-default"):
            return _FakeResponse({"item": self._default_item})
        if url.endswith("/api/ip-content/schedule-templates"):
            return _FakeResponse({"items": self._templates})
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
