from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "douyin_origin"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.services import native_wechat_engine as engine  # noqa: E402


class Rect:
    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


class FakeScrollItemPattern:
    def __init__(self, owner: "FakeCell") -> None:
        self.owner = owner

    def ScrollIntoView(self) -> bool:
        self.owner.scroll_calls += 1
        self.owner.rect = self.owner.rect_after_scroll
        return True


class FakeCell:
    """Minimal stand-in for a WeChat session row exposed through UIA."""

    def __init__(
        self,
        *,
        peer_id: str = "peer",
        rect: Rect | None = None,
        rect_after_scroll: Rect | None = None,
        class_name: str = "mmui::ChatSessionCell",
    ) -> None:
        self.Name = peer_id
        self.ClassName = class_name
        self.rect = rect
        self.rect_after_scroll = rect_after_scroll
        self.click_calls: list[bool] = []
        self.scroll_calls = 0
        self.focus_calls = 0

    @property
    def BoundingRectangle(self) -> Rect | None:
        return self.rect

    def GetScrollItemPattern(self) -> FakeScrollItemPattern:
        return FakeScrollItemPattern(self)

    def SetFocus(self) -> bool:
        self.focus_calls += 1
        return True

    def Click(self, simulateMove: bool = True) -> None:
        if self.rect is None:
            raise RuntimeError("Can not move cursor. ListItemControl's BoundingRectangle is (0,0,0,0)")
        self.click_calls.append(simulateMove)


def test_empty_bounds_are_not_clickable():
    assert engine._uia_rect_tuple(FakeCell(rect=None)) is None
    assert engine._uia_rect_tuple(FakeCell(rect=Rect(0, 0, 0, 0))) is None
    assert engine._uia_rect_tuple(FakeCell(rect=Rect(10, 20, 110, 60))) == (10, 20, 110, 60)


def test_scrolled_away_row_is_brought_back_into_view_before_clicking():
    cell = FakeCell(rect=None, rect_after_scroll=Rect(10, 200, 260, 260))

    engine._uia_click(cell)

    assert cell.scroll_calls == 1
    assert cell.click_calls == [False]


def test_row_that_stays_off_screen_never_gets_a_false_click():
    cell = FakeCell(rect=None, rect_after_scroll=None)

    with pytest.raises(RuntimeError):
        engine._uia_click(cell)

    assert cell.click_calls == []
    # No SetFocus fallback: focusing a row could select/scroll the list as a side effect.
    assert cell.focus_calls == 0


def test_visible_row_is_clicked_without_scrolling():
    cell = FakeCell(rect=Rect(10, 20, 260, 80))

    engine._uia_click(cell)

    assert cell.scroll_calls == 0
    assert cell.click_calls == [False]


def test_find_session_cell_prefers_the_visible_duplicate(monkeypatch):
    hidden = FakeCell(peer_id="徐", rect=None)
    visible = FakeCell(peer_id="徐", rect=Rect(0, 100, 250, 160))
    monkeypatch.setattr(engine, "_uia_session_cells", lambda _root: [hidden, visible])
    monkeypatch.setattr(
        engine,
        "_decorate_uia_session_items",
        lambda cells: [{"peer_id": cell.Name} for cell in cells],
    )

    assert engine._find_uia_session_cell(object(), "徐") is visible


def test_find_session_cell_falls_back_when_no_duplicate_is_visible(monkeypatch):
    hidden = FakeCell(peer_id="徐", rect=None)
    monkeypatch.setattr(engine, "_uia_session_cells", lambda _root: [hidden])
    monkeypatch.setattr(
        engine,
        "_decorate_uia_session_items",
        lambda cells: [{"peer_id": cell.Name} for cell in cells],
    )

    assert engine._find_uia_session_cell(object(), "徐") is hidden


def test_next_visible_session_skips_off_screen_rows_instead_of_clicking(monkeypatch):
    off_screen = FakeCell(peer_id="off-screen", rect=None, rect_after_scroll=None)
    visible = FakeCell(peer_id="on-screen", rect=Rect(0, 100, 250, 160))
    monkeypatch.setattr(engine, "_local_wechat_hwnd", lambda _account_id: 1234)
    monkeypatch.setattr(engine, "_restore_local_chat_session_list", lambda _account_id: {"ok": True})
    monkeypatch.setattr(engine, "_uia_session_cells", lambda _root: [off_screen, visible])
    monkeypatch.setattr(
        engine,
        "_decorate_uia_session_items",
        lambda cells: [
            {"peer_id": cell.Name, "display_name": cell.Name, "chat_type": "friend"} for cell in cells
        ],
    )
    monkeypatch.setattr(engine, "_dismiss_local_wechat_session_ghost_windows", lambda _hwnd: None)
    monkeypatch.setattr(engine, "_is_non_private_session_entry", lambda _item: False)
    monkeypatch.setattr(engine, "_module_available", lambda _name: True)
    monkeypatch.setattr(
        engine,
        "_uia_rect_tuple",
        lambda node: None if getattr(node, "rect", None) is None else (0, 0, 1, 1),
    )

    import types

    fake_auto = types.SimpleNamespace(ControlFromHandle=lambda _hwnd: object())
    monkeypatch.setitem(sys.modules, "uiautomation", fake_auto)

    result = engine._open_next_visible_session("pc-wechat-default")

    assert result is not None and result["peer_id"] == "on-screen"
    assert off_screen.click_calls == []
    assert visible.click_calls == [False]
