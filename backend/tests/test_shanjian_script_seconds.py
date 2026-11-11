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
