"""数字人节点口播来源：单选/多选归一 + 多选时随机取一种 + 接口选择。"""
from __future__ import annotations

import asyncio

import pytest

from backend.app.api import h5_chat_channel as channel

INDUSTRY = "ip_daily_industry_hot_oral"
PROFESSIONAL = "ip_daily_professional_ip_oral"


def test_oral_sources_normalizes_list_and_legacy():
    assert channel.shanjian_oral_sources({}) == [INDUSTRY]
    assert channel.shanjian_oral_sources({"script_source": INDUSTRY}) == [INDUSTRY]
    assert channel.shanjian_oral_sources({"script_sources": [PROFESSIONAL]}) == [PROFESSIONAL]
    assert channel.shanjian_oral_sources(
        {"script_sources": [PROFESSIONAL, INDUSTRY, "bogus", PROFESSIONAL]}
    ) == [PROFESSIONAL, INDUSTRY]
    assert channel.shanjian_oral_sources({"script_sources": [], "script_source": PROFESSIONAL}) == [PROFESSIONAL]


def test_oral_source_random_when_multiple(monkeypatch):
    source = {"script_sources": [INDUSTRY, PROFESSIONAL]}
    monkeypatch.setattr(channel.random, "choice", lambda items: items[-1])
    assert channel.shanjian_oral_source(source) == PROFESSIONAL
    monkeypatch.setattr(channel.random, "choice", lambda items: items[0])
    assert channel.shanjian_oral_source(source) == INDUSTRY
    assert channel.shanjian_oral_source({"script_source": INDUSTRY}) == INDUSTRY


def test_uses_ip_daily_script_flags():
    assert channel.shanjian_uses_ip_daily_script({}) is False
    assert channel.shanjian_uses_ip_daily_script({"script_source": INDUSTRY}) is True
    assert channel.shanjian_uses_ip_daily_script({"script_sources": [PROFESSIONAL]}) is True
    assert channel.shanjian_uses_ip_daily_script({"script_sources": []}) is False
    assert channel._provided_shanjian_workflow_script({"script_source": INDUSTRY, "prompt": "x"}) == ""


def _stub_generation(monkeypatch, calls):
    async def fake_post(path, payload, **kwargs):
        calls.append({"path": path, "payload": payload})
        return {"records": [{"title": "测试文案", "content": "测试正文"}], "group_id": "g1"}

    async def fake_progress(factory, **kwargs):
        return await factory()

    async def fake_event(*args, **kwargs):
        return None

    monkeypatch.setattr(channel, "_post_cloud_api_json", fake_post)
    monkeypatch.setattr(channel, "_await_cloud_json_with_progress", fake_progress)
    monkeypatch.setattr(channel, "_workflow_event", fake_event)


def _base_source(**extra):
    source = {
        "requirements": {"persona": "x"},
        "keyword_ids": [1],
        "keyword_texts": ["关键词"],
        "memory_doc_ids": ["doc1"],
        "memory_docs": [{"title": "doc", "content": "y"}],
    }
    source.update(extra)
    return source


def test_industry_source_calls_industry_endpoint(monkeypatch):
    calls = []
    _stub_generation(monkeypatch, calls)
    result = asyncio.run(
        channel._generate_shanjian_workflow_script(
            source=_base_source(script_sources=[INDUSTRY]),
            cloud=object(),
            base="https://cloud.example",
            headers={},
            run_id="run-1",
        )
    )
    assert calls[0]["path"] == "/api/ip-content/generate/industry-hot-oral"
    assert calls[0]["payload"]["keyword_texts"] == ["关键词"]
    assert result["script"] == "测试正文"
    assert result["ip_daily_task"] == "industry_hot_oral"
    assert result["oral_source"] == INDUSTRY


def test_professional_source_calls_ip_endpoint_without_keywords(monkeypatch):
    calls = []
    _stub_generation(monkeypatch, calls)
    result = asyncio.run(
        channel._generate_shanjian_workflow_script(
            source=_base_source(script_sources=[PROFESSIONAL], competitor_ids=[7, 8]),
            cloud=object(),
            base="https://cloud.example",
            headers={},
            run_id="run-2",
        )
    )
    assert calls[0]["path"] == "/api/ip-content/generate/professional-ip-oral"
    assert calls[0]["payload"]["competitor_ids"] == [7, 8]
    assert "keyword_texts" not in calls[0]["payload"]
    assert result["ip_daily_task"] == "professional_ip_oral"
    assert result["oral_source"] == PROFESSIONAL


def test_professional_source_does_not_require_keywords(monkeypatch):
    """选了 IP 口播就不能再要求行业关键词，否则用户选了也跑不起来。"""
    calls = []
    _stub_generation(monkeypatch, calls)
    source = _base_source(script_sources=[PROFESSIONAL])
    source.pop("keyword_ids")
    source.pop("keyword_texts")
    asyncio.run(
        channel._generate_shanjian_workflow_script(
            source=source, cloud=object(), base="https://cloud.example", headers={}, run_id="run-3"
        )
    )
    assert calls and calls[0]["path"].endswith("professional-ip-oral")


def test_multi_source_picks_one_of_both(monkeypatch):
    calls = []
    _stub_generation(monkeypatch, calls)
    source = _base_source(script_sources=[INDUSTRY, PROFESSIONAL])
    asyncio.run(
        channel._generate_shanjian_workflow_script(
            source=source, cloud=object(), base="https://cloud.example", headers={}, run_id="run-4"
        )
    )
    assert calls[0]["path"] in {
        "/api/ip-content/generate/industry-hot-oral",
        "/api/ip-content/generate/professional-ip-oral",
    }
