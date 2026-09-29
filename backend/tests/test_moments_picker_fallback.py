"""朋友圈发布：素材选择框多选失败（框里报「找不到文件」导致不关闭）时应退回逐张添加。"""
from __future__ import annotations

import pytest

from backend.app.services import native_wechat_engine as engine


def _files(count: int):
    return [{"local_path": r"D:\wxupload\m%d.jpg" % i, "filename": "m%d.jpg" % i} for i in range(count)]


def test_multi_select_failure_falls_back_to_one_by_one(monkeypatch):
    steps = []
    calls = {"confirm": [], "one_by_one": [], "dismiss": 0}

    monkeypatch.setattr(
        engine,
        "_find_moments_file_picker_window",
        lambda **kwargs: {"hwnd": 111, "root": object()},
    )

    def fake_confirm(dialog_hwnd, root, spec, paths, steps, *, label, timeout=10.0):
        calls["confirm"].append((label, spec, len(paths)))
        if label == "moments_files_multi":
            raise RuntimeError("朋友圈素材选择框没能确认（选择框未关闭：找不到文件）")

    monkeypatch.setattr(engine, "_open_dialog_confirm", fake_confirm)
    monkeypatch.setattr(engine, "_dismiss_open_dialog", lambda hwnd, steps: calls.__setitem__("dismiss", calls["dismiss"] + 1))
    monkeypatch.setattr(engine, "_add_moments_files_one_by_one", lambda hwnd, files, steps: calls["one_by_one"].append(len(files)))

    engine._select_files_in_open_dialog(999, _files(3), steps)

    assert calls["confirm"][0][0] == "moments_files_multi"
    assert calls["confirm"][0][2] == 3
    assert calls["dismiss"] == 1
    assert calls["one_by_one"] == [3]
    assert any(item.get("step") == "moments_multi_select_failed" and item.get("fallback") == "one_by_one" for item in steps)


def test_single_file_uses_absolute_spec_without_fallback(monkeypatch):
    steps = []
    calls = []

    monkeypatch.setattr(engine, "_find_moments_file_picker_window", lambda **kwargs: {"hwnd": 222, "root": object()})
    monkeypatch.setattr(
        engine,
        "_open_dialog_confirm",
        lambda dialog_hwnd, root, spec, paths, steps, *, label, timeout=10.0: calls.append((label, spec)),
    )
    monkeypatch.setattr(engine, "_add_moments_files_one_by_one", lambda *a, **k: pytest.fail("single file should not fall back"))

    engine._select_files_in_open_dialog(999, _files(1), steps)

    assert calls and calls[0][0] == "moments_files_single"
    assert calls[0][1] == '"D:\\wxupload\\m0.jpg"'


def test_multi_spec_uses_folder_then_names(monkeypatch):
    spec = engine._moments_open_dialog_file_spec([r"D:\wxupload\a.jpg", r"D:\wxupload\b.jpg"])
    assert spec.startswith('"D:\\wxupload"')
    assert '"a.jpg"' in spec and '"b.jpg"' in spec


def test_inline_error_reads_dialog_text(monkeypatch):
    class FakeNode:
        def __init__(self, text):
            self._text = text

    monkeypatch.setattr(engine, "_uia_walk", lambda root, **kwargs: [FakeNode("某某某"), FakeNode("找不到文件")])
    monkeypatch.setattr(engine, "_uia_control_text", lambda node: getattr(node, "_text", ""))
    assert engine._moments_open_dialog_inline_error(object()) == "找不到文件"
    assert engine._moments_open_dialog_inline_error(None) == ""
