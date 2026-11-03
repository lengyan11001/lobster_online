import asyncio

from skills.wechat_channels_publish import driver


def test_without_publish_time_skips_schedule(monkeypatch):
    called = []

    async def fake_schedule(page, value, step):
        called.append(value)
        return True

    monkeypatch.setattr(driver, "_apply_publish_schedule", fake_schedule)
    ok = asyncio.run(
        driver._apply_optional_publish_options(
            None, "image", {}, None, lambda *args, **kwargs: None
        )
    )
    assert ok is True
    assert called == []


def test_with_publish_time_applies_schedule(monkeypatch):
    called = []

    async def fake_schedule(page, value, step):
        called.append(value)
        return True

    monkeypatch.setattr(driver, "_apply_publish_schedule", fake_schedule)
    ok = asyncio.run(
        driver._apply_optional_publish_options(
            None,
            "image",
            {"wechat_channels_publish_time": "2026-11-04 09:00"},
            None,
            lambda *args, **kwargs: None,
        )
    )
    assert ok is True
    assert called == ["2026-11-04 09:00"]
