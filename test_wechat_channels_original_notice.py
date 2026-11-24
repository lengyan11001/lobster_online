import asyncio

from skills.wechat_channels_publish import driver


class StepRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, label, ok, **kwargs):
        self.calls.append((label, ok, kwargs))


def _run_notice(monkeypatch, probes, clicks, *, declare_original=True, enabled=True):
    seq = list(probes)
    clicked = []

    async def fake_probe(page):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    async def fake_click(page, tag):
        clicked.append(tag)
        return clicks.get(tag, True)

    async def fake_delay(*args, **kwargs):
        return None

    monkeypatch.setattr(driver, "_probe_publish_dialogs", fake_probe)
    monkeypatch.setattr(driver, "_click_tagged", fake_click)
    monkeypatch.setattr(driver, "_delay", fake_delay)

    step = StepRecorder()
    result = asyncio.run(
        driver._handle_original_notice(None, enabled, step, declare_original=declare_original)
    )
    return result, clicked, step


def _dialog(**over):
    base = {
        "notice": True,
        "has_checkbox": True,
        "checkbox_checked": False,
        "has_continue": True,
        "continue_text": "声明原创",
        "has_cancel": True,
        "buttons": ["取消", "声明原创"],
        "frames_with_hit": [],
    }
    base.update(over)
    return base


def test_cancel_path_when_original_disabled(monkeypatch):
    result, clicked, _ = _run_notice(
        monkeypatch,
        probes=[_dialog()],
        clicks={"notice-cancel": True},
        declare_original=False,
    )
    assert result == "取消"
    assert clicked[-1] == "notice-cancel"
    assert "notice-continue" not in clicked


def test_consent_checked_then_declare(monkeypatch):
    result, clicked, steps = _run_notice(
        monkeypatch,
        probes=[_dialog(), _dialog(checkbox_checked=True)],
        clicks={"notice-checkbox": True, "notice-continue": True},
        declare_original=True,
    )
    assert result == "声明原创"
    assert clicked == ["notice-checkbox", "notice-continue"]
    assert any(label == "勾选原创提示不再提醒/同意条款" and ok for label, ok, _ in steps.calls)


def test_stuck_checkbox_falls_back_to_cancel(monkeypatch):
    result, clicked, steps = _run_notice(
        monkeypatch,
        probes=[_dialog()],
        clicks={"notice-checkbox": True, "notice-cancel": True},
        declare_original=True,
    )
    assert result == "取消"
    assert clicked == ["notice-checkbox", "notice-checkbox", "notice-cancel"]
    assert any(
        label == "勾选原创提示不再提醒/同意条款" and not ok and kw.get("reason") == "checkbox_not_checked"
        for label, ok, kw in steps.calls
    )


def test_publish_in_flight_words():
    assert driver._publish_in_flight("正在发表，请稍候")
    assert driver._publish_in_flight("提交中")
    assert not driver._publish_in_flight("点击发表")
