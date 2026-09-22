"""2026-09-22 朋友圈「选素材」可靠性修复的回归测试。

生产事故（客户端 2.0.68/361，工厂机 18:31 / 18:34）：
1. `root.EditControl(AutomationId='1148')` / `ButtonControl('1')` 拿到的是 uiautomation
   的懒控件，不解析就是真值 —— "等选择框出现"的循环第一轮就退出，"找到了吗"恒为真；
2. `select_moments_files` 在写完路径后无条件记 ok（没有回读校验），所以日志上是"假成功"；
3. 关没关选择框用的是"当时的前台窗口"句柄（常常是微信主窗）→ 8 秒后必然判定"未关闭"，
   把**已经加好素材**的一次发布直接判死，文案步骤因此从没执行。
"""
from __future__ import annotations

import pytest

from backend.app.services import native_wechat_engine as engine


class _FakeLazyControl:
    """模拟 uiautomation 的懒控件：不 Exists 时永远"存在"（这正是事故根源）。"""

    def __init__(self, *, exists: bool) -> None:
        self._exists = exists
        self.searched = 0

    def Exists(self, maxSearchSeconds=5, searchIntervalSeconds=0.5, printIfNotExist=False):
        self.searched += 1
        return bool(self._exists)


def test_uia_resolve_treats_missing_control_as_none():
    assert engine._uia_resolve(None) is None
    assert engine._uia_resolve(_FakeLazyControl(exists=False)) is None
    node = _FakeLazyControl(exists=True)
    assert engine._uia_resolve(node) is node
    assert engine._uia_resolve(object()) is not None


def test_file_dialog_filename_edit_returns_none_when_control_missing(monkeypatch):
    missing = _FakeLazyControl(exists=False)

    class FakeRoot:
        def EditControl(self, **kwargs):
            return missing

    monkeypatch.setattr(engine, "_uia_edit_controls", lambda _root: [])
    assert engine._file_dialog_filename_edit(FakeRoot()) is None


def test_file_dialog_filename_edit_resolves_real_control(monkeypatch):
    found = _FakeLazyControl(exists=True)

    class FakeRoot:
        def EditControl(self, **kwargs):
            return found if kwargs.get("AutomationId") == "1148" else None

    monkeypatch.setattr(engine, "_uia_edit_controls", lambda _root: [])
    assert engine._file_dialog_filename_edit(FakeRoot()) is found


def test_file_dialog_open_button_returns_none_when_missing(monkeypatch):
    missing = _FakeLazyControl(exists=False)

    class FakeRoot:
        def ButtonControl(self, **kwargs):
            return missing

    assert engine._file_dialog_open_button(FakeRoot()) is None


def test_find_moments_file_picker_window_requires_real_filename_edit(monkeypatch):
    windows = [
        {"hwnd": 1, "class": "mmui::MainWindow", "title": "微信", "pid": 42},
        {"hwnd": 2, "class": "#32770", "title": "打开", "pid": 42},
    ]
    monkeypatch.setattr(engine, "_top_level_windows", lambda: windows)
    monkeypatch.setattr(engine, "_uia_main_root", lambda hwnd: f"root-{hwnd}")
    monkeypatch.setattr(engine, "_file_dialog_filename_edit", lambda root, *, timeout=0.0: None)
    assert engine._find_moments_file_picker_window(wechat_pid=42, timeout=0.0) is None


def test_find_moments_file_picker_window_returns_dialog(monkeypatch):
    windows = [
        {"hwnd": 1, "class": "mmui::MainWindow", "title": "微信", "pid": 42},
        {"hwnd": 2, "class": "#32770", "title": "打开", "pid": 42},
    ]
    edit = object()
    monkeypatch.setattr(engine, "_top_level_windows", lambda: windows)
    monkeypatch.setattr(engine, "_uia_main_root", lambda hwnd: f"root-{hwnd}")
    monkeypatch.setattr(
        engine,
        "_file_dialog_filename_edit",
        lambda root, *, timeout=0.0: edit if root == "root-2" else None,
    )
    steps: list = []
    found = engine._find_moments_file_picker_window(wechat_pid=42, timeout=0.0, steps=steps)
    assert found is not None
    assert found["hwnd"] == 2
    assert found["edit"] is edit
    assert steps[-1]["step"] == "moments_file_picker_found"


def _picker():
    return {"hwnd": 2, "class": "#32770", "title": "打开", "pid": 42, "root": "root-2", "edit": "edit"}


def _image_files():
    return [{"local_path": "C:/temp/a.jpg", "filename": "a.jpg", "kind": "image", "size": 1}]


def test_select_files_raises_when_picker_missing(monkeypatch):
    steps: list = []
    monkeypatch.setattr(engine, "_find_moments_file_picker_window", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "_top_level_windows", lambda: [])
    with pytest.raises(RuntimeError):
        engine._select_files_in_open_dialog(123, _image_files(), steps)
    assert steps[-1]["step"] == "moments_file_picker_missing"


def test_select_files_raises_when_paths_not_verified(monkeypatch):
    steps: list = []
    monkeypatch.setattr(engine, "_activate_window", lambda _hwnd: True)
    monkeypatch.setattr(engine, "_uia_set_text_verified", lambda *_args, **_kwargs: False)
    with pytest.raises(RuntimeError):
        engine._select_files_in_open_dialog(123, _image_files(), steps, picker=_picker())
    assert steps[-1] == {
        "step": "select_moments_files",
        "ok": False,
        "reason": "文件名输入框回读不一致",
    }


def test_select_files_keeps_running_when_editor_ready_after_timeout(monkeypatch):
    """回归：素材其实已经加好、但选择框判定"没关"时，不能再把整次发布判死。"""
    steps: list = []
    monkeypatch.setattr(engine, "_activate_window", lambda _hwnd: True)
    monkeypatch.setattr(engine, "_uia_set_text_verified", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(engine, "_file_dialog_open_button", lambda _root, *, timeout=0.0: None)
    monkeypatch.setattr(engine, "_send_hotkey", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "_wait_window_closed", lambda _hwnd, *, timeout=0.0: False)
    monkeypatch.setattr(engine, "_uia_foreground_or_main_root", lambda _hwnd: "compose")
    monkeypatch.setattr(engine, "_moments_publish_dialog_ready", lambda _root: True)
    monkeypatch.setattr(engine, "_moments_publish_rejection", lambda _root: "")

    engine._select_files_in_open_dialog(123, _image_files(), steps, picker=_picker())

    assert steps[-1] == {
        "step": "close_moments_file_picker",
        "ok": True,
        "method": "editor_ready_after_timeout",
    }
    assert [step for step in steps if step["step"] == "select_moments_files"][0]["ok"] is True


def test_select_files_raises_when_picker_never_closes_and_editor_missing(monkeypatch):
    steps: list = []
    monkeypatch.setattr(engine, "_activate_window", lambda _hwnd: True)
    monkeypatch.setattr(engine, "_uia_set_text_verified", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(engine, "_file_dialog_open_button", lambda _root, *, timeout=0.0: None)
    monkeypatch.setattr(engine, "_send_hotkey", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "_wait_window_closed", lambda _hwnd, *, timeout=0.0: False)
    monkeypatch.setattr(engine, "_uia_foreground_or_main_root", lambda _hwnd: "compose")
    monkeypatch.setattr(engine, "_moments_publish_dialog_ready", lambda _root: False)
    monkeypatch.setattr(engine, "_window_state", lambda _hwnd: {"hwnd": 2})

    with pytest.raises(RuntimeError):
        engine._select_files_in_open_dialog(123, _image_files(), steps, picker=_picker())

    assert steps[-1]["step"] == "close_moments_file_picker"
    assert steps[-1]["ok"] is False


def test_add_moments_publish_files_uses_real_picker_wait(monkeypatch):
    """点了「+」之后必须真的等到选择框，再走选文件。"""
    steps: list = []
    seen: list = []
    monkeypatch.setattr(engine, "_uia_foreground_or_main_root", lambda _hwnd: "compose")
    monkeypatch.setattr(engine, "_find_moments_publish_plus", lambda _root: "plus")
    monkeypatch.setattr(engine, "_uia_click", lambda node: seen.append(("click", node)))
    monkeypatch.setattr(
        engine,
        "_find_moments_file_picker_window",
        lambda **_kwargs: _picker(),
    )
    monkeypatch.setattr(
        engine,
        "_select_files_in_open_dialog",
        lambda _hwnd, _files, _steps, **_kwargs: seen.append(("select", True)),
    )

    engine._add_moments_publish_files(123, _image_files(), steps)

    assert seen == [("click", "plus"), ("select", True)]
    assert steps[0] == {"step": "open_moments_file_picker", "ok": True, "method": "uia"}


def test_publish_failure_cleans_up_leftovers(monkeypatch):
    """失败后必须清场，否则残留的选择框会污染下一次（18:34 事故）。"""
    files = _image_files()
    calls: list = []
    monkeypatch.setattr(engine, "_find_local_account", lambda _account_id: {"hwnd": 123})
    monkeypatch.setattr(engine, "_normalize_attachments", lambda _attachments: files)
    monkeypatch.setattr(engine, "_split_moments_media", lambda _files, **_kwargs: files)
    monkeypatch.setattr(engine, "_enforce_local_moments_publish_rate", lambda _account_id: None)
    monkeypatch.setattr(engine, "_open_local_moments", lambda _hwnd, _steps: None)
    monkeypatch.setattr(engine, "_click_moments_publish_entry", lambda _hwnd, _steps, **_kwargs: 456)
    monkeypatch.setattr(engine, "_find_moments_file_picker_window", lambda **_kwargs: None)

    def _boom(_hwnd, _files, _steps):
        raise RuntimeError("朋友圈素材选择框没打开")

    monkeypatch.setattr(engine, "_add_moments_publish_files", _boom)
    monkeypatch.setattr(engine, "_dismiss_moments_leftovers", lambda _hwnd, _steps: calls.append("cleanup"))

    with pytest.raises(engine._MomentsPublishError):
        engine._publish_moments_local_once("pc-wechat-default", "文案", attachments=files)

    assert calls == ["cleanup"]
