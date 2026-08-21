from datetime import datetime, timezone

from backend.app.api.h5_chat_channel import _local_bestseller_workflow_day


WORKFLOW_START = "2026-08-20T00:00:00Z"


def _workflow_source(**params):
    return {
        **params,
        "h5_context": {"workflow_started_at": WORKFLOW_START},
        "schedule_config": {"timezone_offset_minutes": 480},
    }


def test_employee_selected_day_is_the_start_and_advances_each_day():
    source = _workflow_source(start_day=7, day_mode="workflow_elapsed")

    assert _local_bestseller_workflow_day(
        source,
        30,
        now=datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc),
    ) == 7
    assert _local_bestseller_workflow_day(
        source,
        30,
        now=datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc),
    ) == 8


def test_employee_day_wraps_at_the_end_of_the_plan():
    source = _workflow_source(start_day=30, day_mode="workflow_elapsed")

    assert _local_bestseller_workflow_day(
        source,
        30,
        now=datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc),
    ) == 1


def test_legacy_employee_selected_day_advances_without_reactivation():
    source = _workflow_source(day=7, day_mode="activation_selected")

    assert _local_bestseller_workflow_day(
        source,
        30,
        now=datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc),
    ) == 8


def test_one_off_demo_keeps_its_explicit_day():
    source = _workflow_source(day=7)

    assert _local_bestseller_workflow_day(
        source,
        30,
        now=datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc),
    ) == 7
