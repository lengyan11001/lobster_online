# -*- coding: utf-8 -*-
"""发布计划槽位调度：错过的时段不能一次性补发（用户实测「一下全发出去了」）。"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.app.services.publish_plan_runner import _SLOT_GRACE_SECONDS, _due_slots  # noqa: E402


def _plan(times, fired=None):
    return SimpleNamespace(times=times, fired_slots=fired or {})


def test_all_missed_slots_are_dropped_not_burst():
    now = datetime(2026, 10, 30, 19, 0)
    due, expired = _due_slots(_plan(["09:00", "12:00", "18:00"]), now)
    assert due == []
    assert expired == ["09:00", "12:00", "18:00"]


def test_slot_at_current_minute_fires():
    now = datetime(2026, 10, 30, 9, 0, 30)
    due, expired = _due_slots(_plan(["09:00"]), now)
    assert due == ["09:00"] and expired == []


def test_slot_within_grace_fires():
    now = datetime(2026, 10, 30, 9, 20)
    due, expired = _due_slots(_plan(["09:00"]), now)
    assert due == ["09:00"] and expired == []


def test_slot_beyond_grace_expires():
    now = datetime(2026, 10, 30, 9, 45)
    due, expired = _due_slots(_plan(["09:00"]), now)
    assert due == [] and expired == ["09:00"]


def test_only_one_slot_per_tick():
    now = datetime(2026, 10, 30, 9, 6)
    due, expired = _due_slots(_plan(["09:00", "09:05"]), now)
    assert due == ["09:00"] and expired == []


def test_already_fired_slot_skipped():
    fired = {"2026-10-30|09:00": True}
    now = datetime(2026, 10, 30, 9, 5)
    assert _due_slots(_plan(["09:00"], fired), now) == ([], [])


def test_expired_marker_never_refires():
    fired = {"2026-10-30|09:00": "expired"}
    now = datetime(2026, 10, 30, 9, 5)
    assert _due_slots(_plan(["09:00"], fired), now) == ([], [])


def test_future_slot_untouched():
    now = datetime(2026, 10, 30, 8, 0)
    assert _due_slots(_plan(["09:00"]), now) == ([], [])


def test_invalid_slot_ignored():
    now = datetime(2026, 10, 30, 9, 5)
    assert _due_slots(_plan(["9:00", "", "abc"]), now) == ([], [])


def test_grace_default_is_30_minutes():
    assert _SLOT_GRACE_SECONDS == 1800