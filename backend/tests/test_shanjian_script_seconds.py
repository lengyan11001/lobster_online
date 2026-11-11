"""IP 设置里的「口播预估时长」要能读到，并用于文案长度。"""
from __future__ import annotations

import asyncio

import pytest

from backend.app.api import h5_chat_channel as ch


class _Resp:
    def __init__(self, payload):
        self._payload = payload
        self.content = b"x"

    def json(self):
        return self._payload


class _Cloud:
    def __init__(self, personal, templates=None):
        self.personal = personal
        self.templates = templates or {"items": []}

    async def get(self, url, headers=None, timeout=None):
        if url.endswith("/api/ip-content/personal-default"):
            return _Resp(self.personal)
        if url.endswith("/api/ip-content/schedule-templates"):
            return _Resp(self.templates)
        raise AssertionError(url)


def test_reads_seconds_from_personal_default():
    cloud = _Cloud({"item": {"meta": {"digital_human_script_seconds": 45}}})
    assert asyncio.run(ch._fetch_personal_digital_human_script_seconds(cloud, "http://x", {})) == 45


def test_reads_seconds_from_current_template():
    cloud = _Cloud(
        {"item": {"meta": {"current_template_id": 7}}},
        {"items": [{"id": 7, "meta": {"digital_human_script_seconds": 60}}]},
    )
    assert asyncio.run(ch._fetch_personal_digital_human_script_seconds(cloud, "http://x", {})) == 60


def test_returns_zero_when_not_configured():
    cloud = _Cloud({"item": {"meta": {}}})
    assert asyncio.run(ch._fetch_personal_digital_human_script_seconds(cloud, "http://x", {})) == 0


def test_ignores_out_of_range_value():
    cloud = _Cloud({"item": {"meta": {"digital_human_script_seconds": 9999}}})
    assert asyncio.run(ch._fetch_personal_digital_human_script_seconds(cloud, "http://x", {})) == 0


def test_trim_chinese_script_to_budget():
    text = "这是第一句话，用来占位。" * 30
    out = ch._trim_script_to_budget(text, language="zh-CN", max_units=120)
    assert ch._script_unit_count(out, "zh-CN") <= 120
    assert out[-1] in "。！？；，"
    assert len(out) < len(text)


def test_trim_keeps_short_script():
    text = "短文案，不用截断。"
    assert ch._trim_script_to_budget(text, language="zh-CN", max_units=120) == text


def test_trim_english_script_by_words():
    text = " ".join(f"word{i}" for i in range(200)) + "."
    out = ch._trim_script_to_budget(text, language="en-US", max_units=60)
    assert len(out.split()) <= 60
